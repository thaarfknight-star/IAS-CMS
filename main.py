import os
import sys
import time
import math
import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QTabWidget, QMenu, QTreeWidget, QTreeWidgetItem, QInputDialog,
    QComboBox, QGridLayout, QScrollArea, QFrame, QSplitter
)
from PyQt6.QtGui import QImage, QPixmap, QAction, QColor, QFont
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from face_engine import FaceEngine
from scanner import NetworkScanThread
from camera_store import CameraStore
from camera_stream import CameraStreamThread
from add_camera_dialog import AddCameraDialog
from add_nvr_dialog import AddNVRDialog
from face_library_dialog import FaceLibraryDialog
from device_detect import DeviceDetectThread

cv2.setNumThreads(max(1, (os.cpu_count() or 4) // 2))
_PROCESS_EVERY_N = 5 if (os.cpu_count() or 4) >= 6 else 8


class VideoGridCell(QWidget):
    clicked = pyqtSignal(object)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.cam = None
        self.stream_thread = None
        self.latest_raw_frame = None

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(1, 1, 1, 1)

        self.header_label = QLabel(f"کانال {index + 1} (خالی)")
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setStyleSheet("background-color: #2b2b2b; color: #888; font-size: 11px; padding: 2px;")

        self.video_label = QLabel("خالی")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #121212; color: #444; border: 1px solid #333;")
        self.video_label.setMinimumSize(120, 80)

        self.layout.addWidget(self.header_label)
        self.layout.addWidget(self.video_label)

    def set_camera(self, cam: dict, rtsp_url: str, face_engine: FaceEngine, face_callback):
        self.stop()
        self.cam = cam
        self.header_label.setText(cam.get("name", "دوربین"))
        self.header_label.setStyleSheet("background-color: #1f3a52; color: #fff; font-size: 11px; padding: 2px;")
        self.video_label.setText("در حال اتصال...")

        self.stream_thread = CameraStreamThread(rtsp_url, face_engine, process_every_n=_PROCESS_EVERY_N)
        self.stream_thread.frame_ready.connect(self.on_frame_ready)
        self.stream_thread.error_signal.connect(self.on_error)
        self.stream_thread.known_face_signal.connect(
            lambda person: face_callback("known", self.cam["name"], person, self.latest_raw_frame)
        )
        self.stream_thread.unknown_face_signal.connect(
            lambda: face_callback("unknown", self.cam["name"], None, self.latest_raw_frame)
        )
        self.stream_thread.start()

    def on_frame_ready(self, display_frame, raw_frame):
        self.latest_raw_frame = raw_frame
        rgb_image = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        qt_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        self.video_label.setPixmap(
            QPixmap.fromImage(qt_img).scaled(
                self.video_label.width(), self.video_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )

    def on_error(self, msg):
        self.video_label.setText(f"خطا: {msg}")

    def stop(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
            self.stream_thread = None
        self.cam = None
        self.latest_raw_frame = None
        self.header_label.setText(f"کانال {self.index + 1} (خالی)")
        self.header_label.setStyleSheet("background-color: #2b2b2b; color: #888; font-size: 11px; padding: 2px;")
        self.video_label.setText("خالی")
        self.video_label.setPixmap(QPixmap())

    def mousePressEvent(self, event):
        self.clicked.emit(self)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CCTV Management System (CMS) & Face Recognition")
        self.setGeometry(60, 60, 1440, 840)

        self.face_engine = FaceEngine()
        self.camera_store = CameraStore()
        self.network_scan_thread = None
        self.detect_thread = None
        self._scan_ports_by_ip = {}
        self._detect_queue = []
        self.grid_cells = []
        self.selected_cell_index = 0

        self.init_ui()
        self.reload_camera_list()
        self.set_grid_layout(4)

    def init_ui(self):
        main_widget = QWidget()
        outer_layout = QVBoxLayout()
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ------------------------- پنل چپ -------------------------
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # اسکن شبکه با لاگین سراسری
        scan_group = QGroupBox("اسکن شبکه و ورود خودکار")
        scan_layout = QVBoxLayout()

        auth_row = QHBoxLayout()
        self.scan_user_input = QLineEdit("admin")
        self.scan_user_input.setPlaceholderText("یوزرنیم")
        self.scan_pass_input = QLineEdit()
        self.scan_pass_input.setPlaceholderText("پسوورد")
        self.scan_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        auth_row.addWidget(QLabel("ورود:"))
        auth_row.addWidget(self.scan_user_input)
        auth_row.addWidget(self.scan_pass_input)

        self.subnet_input = QLineEdit("192.168.1")
        self.scan_btn = QPushButton("شروع اسکن شبکه")
        self.scan_btn.clicked.connect(self.run_network_scan)

        self.scan_result_list = QListWidget()
        self.add_selected_scan_btn = QPushButton("+ افزودن دستگاه‌های تیک‌خورده")
        self.add_selected_scan_btn.clicked.connect(self.on_add_selected_scan_results)
        self.detect_status_label = QLabel("")
        self.detect_status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")

        scan_layout.addLayout(auth_row)
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        scan_layout.addWidget(QLabel("دستگاه‌های مورد نظر را تیک بزنید:"))
        scan_layout.addWidget(self.scan_result_list)
        scan_layout.addWidget(self.add_selected_scan_btn)
        scan_layout.addWidget(self.detect_status_label)
        scan_group.setLayout(scan_layout)
        left_layout.addWidget(scan_group)

        # لیست دوربین‌ها و NVR
        cam_group = QGroupBox("دوربین‌ها و NVRها")
        cam_layout = QVBoxLayout()
        add_btn_row = QHBoxLayout()
        self.add_camera_btn = QPushButton("+ دوربین تکی")
        self.add_camera_btn.clicked.connect(lambda: self.open_add_camera_dialog())
        self.add_nvr_btn = QPushButton("+ NVR")
        self.add_nvr_btn.clicked.connect(lambda: self.open_add_nvr_dialog())
        add_btn_row.addWidget(self.add_camera_btn)
        add_btn_row.addWidget(self.add_nvr_btn)

        self.camera_list = QTreeWidget()
        self.camera_list.setHeaderHidden(True)
        self.camera_list.itemDoubleClicked.connect(self.on_camera_item_activated)
        self.camera_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.camera_list.customContextMenuRequested.connect(self.show_camera_context_menu)

        cam_layout.addLayout(add_btn_row)
        cam_layout.addWidget(self.camera_list)
        cam_group.setLayout(cam_layout)
        left_layout.addWidget(cam_group)

        self.face_library_btn = QPushButton("مدیریت بانک چهره (Face Library)")
        self.face_library_btn.clicked.connect(self.open_face_library)
        left_layout.addWidget(self.face_library_btn)

        # ------------------------- پنل وسط: تصویر زنده و گرید -------------------------
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)

        top_controls = QHBoxLayout()
        top_controls.addWidget(QLabel("تعداد پنجره نمایش همزمان:"))
        self.grid_combo = QComboBox()
        for count in [1, 4, 9, 16, 20, 36, 64]:
            self.grid_combo.addItem(f"{count} تصویر", count)
        self.grid_combo.setCurrentIndex(1)  # 4 تصویر پیش‌فرض
        self.grid_combo.currentIndexChanged.connect(lambda: self.set_grid_layout(self.grid_combo.currentData()))
        top_controls.addWidget(self.grid_combo)

        clear_all_btn = QPushButton("خالی کردن تمام پنجره‌ها")
        clear_all_btn.clicked.connect(self.clear_all_grid)
        top_controls.addWidget(clear_all_btn)
        top_controls.addStretch()

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(2)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)

        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.grid_container)

        center_layout.addLayout(top_controls)
        center_layout.addWidget(grid_scroll)

        # ------------------------- پنل راست: لاگ و تصاویر چهره‌ها -------------------------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_panel.setMinimumWidth(260)
        right_panel.setMaximumWidth(320)

        face_col_group = QGroupBox("گزارش چهره‌های شناسایی‌شده")
        face_col_layout = QVBoxLayout()
        self.face_feed_list = QListWidget()
        self.face_feed_list.setIconSize(Qt.QSize(54, 54))
        face_col_layout.addWidget(self.face_feed_list)

        clear_feed_btn = QPushButton("پاک‌سازی گزارش چهره‌ها")
        clear_feed_btn.clicked.connect(self.face_feed_list.clear)
        face_col_layout.addWidget(clear_feed_btn)
        face_col_group.setLayout(face_col_layout)

        right_layout.addWidget(face_col_group)

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        splitter.setStretchFactor(2, 2)

        outer_layout.addWidget(splitter)
        main_widget.setLayout(outer_layout)
        self.setCentralWidget(main_widget)

    # ------------------------- مدیریت گرید -------------------------

    def set_grid_layout(self, total_cells: int):
        for cell in self.grid_cells:
            cell.stop()
            self.grid_layout.removeWidget(cell)
            cell.deleteLater()
        self.grid_cells.clear()

        # محاسبه سطر و ستون برای اعداد: 1, 4, 9, 16, 20, 36, 64
        if total_cells == 1:
            rows, cols = 1, 1
        elif total_cells == 4:
            rows, cols = 2, 2
        elif total_cells == 9:
            rows, cols = 3, 3
        elif total_cells == 16:
            rows, cols = 4, 4
        elif total_cells == 20:
            rows, cols = 4, 5
        elif total_cells == 36:
            rows, cols = 6, 6
        elif total_cells == 64:
            rows, cols = 8, 8
        else:
            cols = math.ceil(math.sqrt(total_cells))
            rows = math.ceil(total_cells / cols)

        for i in range(total_cells):
            cell = VideoGridCell(i, self)
            cell.clicked.connect(self.on_cell_clicked)
            r = i // cols
            c = i % cols
            self.grid_layout.addWidget(cell, r, c)
            self.grid_cells.append(cell)

        self.selected_cell_index = 0
        if self.grid_cells:
            self._highlight_cell(self.grid_cells[0])

    def on_cell_clicked(self, cell):
        self.selected_cell_index = cell.index
        self._highlight_cell(cell)

    def _highlight_cell(self, active_cell):
        for cell in self.grid_cells:
            if cell == active_cell:
                cell.setStyleSheet("border: 1px solid #00a8ff;")
            else:
                cell.setStyleSheet("border: none;")

    def clear_all_grid(self):
        for cell in self.grid_cells:
            cell.stop()

    # ------------------------- اتصال دوربین به گرید -------------------------

    def on_camera_item_activated(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data["type"] != "camera":
            return
        cam = self.camera_store.get_camera(data["id"])
        if not cam:
            return

        if not self._ensure_password(cam):
            return

        # قرار دادن استریم در سلول فعال یا اولین سلول خالی
        target_cell = self.grid_cells[self.selected_cell_index]
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        target_cell.set_camera(cam, rtsp_url, self.face_engine, self.log_face_event)

        # انتخاب خودکار سلول بعدی
        self.selected_cell_index = (self.selected_cell_index + 1) % len(self.grid_cells)
        self._highlight_cell(self.grid_cells[self.selected_cell_index])

    def log_face_event(self, kind, camera_name, person, raw_frame):
        timestamp = time.strftime("%H:%M:%S")
        item = QListWidgetItem()

        # استخراج تصویر بندانگشتی چهره
        if raw_frame is not None:
            try:
                rgb = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
                item.setIcon(QPixmap.fromImage(qimg).scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio))
            except Exception:
                pass

        if kind == "known":
            name = person.get("name", "ناشناس")
            item.setText(f"[{timestamp}]\n{camera_name}\nشخص: {name}")
            item.setForeground(QColor("#2ecc71"))
        else:
            item.setText(f"[{timestamp}]\n{camera_name}\n⚠ چهره تعریف‌نشده")
            item.setForeground(QColor("#e74c3c"))

        self.face_feed_list.insertItem(0, item)

    # ------------------------- اسکن شبکه و افزودن دستگاه‌ها -------------------------

    def run_network_scan(self):
        if self.network_scan_thread is not None and self.network_scan_thread.isRunning():
            return
        subnet = self.subnet_input.text().strip()
        self.scan_result_list.clear()
        self.scan_btn.setEnabled(False)
        self.detect_status_label.setText("در حال اسکن ساب‌نت...")

        self.network_scan_thread = NetworkScanThread(subnet, self)
        self.network_scan_thread.finished_signal.connect(self._on_network_scan_finished)
        self.network_scan_thread.start()

    def _on_network_scan_finished(self, devices):
        self.scan_btn.setEnabled(True)
        self.scan_result_list.clear()
        self._scan_ports_by_ip = {}
        self.detect_status_label.setText(f"تعداد {len(devices)} دستگاه یافت شد.")

        for dev in devices:
            self._scan_ports_by_ip[dev["ip"]] = dev["ports"]
            ports_str = ",".join(map(str, dev["ports"]))
            item = QListWidgetItem(f"{dev['ip']} (پورت‌ها: {ports_str})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.scan_result_list.addItem(item)

    def on_add_selected_scan_results(self):
        if self.detect_thread is not None and self.detect_thread.isRunning():
            return

        ips = []
        for i in range(self.scan_result_list.count()):
            item = self.scan_result_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                ip = item.text().split(" ")[0]
                if ip not in ips:
                    ips.append(ip)

        if not ips:
            QMessageBox.information(self, "پیام", "لطفاً تیک حداقل یک دستگاه را بزنید.")
            return

        self._detect_queue = ips[1:]
        self._start_device_detect(ips[0])

    def _start_device_detect(self, ip):
        self._detect_ip = ip
        self.scan_result_list.setEnabled(False)
        self.add_selected_scan_btn.setEnabled(False)
        self.detect_status_label.setText(f"در حال اتصال به {ip}...")

        user = self.scan_user_input.text().strip() or "admin"
        pwd = self.scan_pass_input.text().strip()

        self.detect_thread = DeviceDetectThread(
            ip=ip,
            open_ports=self._scan_ports_by_ip.get(ip, []),
            rtsp_port="554",
            user=user,
            pwd=pwd,
            parent=self,
        )
        self.detect_thread.progress_signal.connect(self.detect_status_label.setText)
        self.detect_thread.detected_signal.connect(self._on_device_detected)
        self.detect_thread.failed_signal.connect(self._on_device_detect_failed)
        self.detect_thread.start()

    def _reset_detect_ui(self):
        self.scan_result_list.setEnabled(True)
        self.detect_status_label.setText("")

    def _advance_detect_queue(self):
        if self._detect_queue:
            next_ip = self._detect_queue.pop(0)
            self._start_device_detect(next_ip)
        else:
            self.add_selected_scan_btn.setEnabled(True)

    def _on_device_detected(self, kind, payload):
        ip = self._detect_ip
        self._reset_detect_ui()
        if kind == "nvr":
            self.open_add_nvr_dialog(
                prefill_ip=ip,
                detected_brand=payload.get("brand"),
                detected_onvif_port=payload.get("onvif_port"),
                prefill_user=self.scan_user_input.text().strip(),
                prefill_pass=self.scan_pass_input.text().strip(),
            )
        else:
            self.open_add_camera_dialog(
                prefill_ip=ip,
                detected_path=payload.get("path"),
                detected_full_url=payload.get("full_url"),
                prefill_user=self.scan_user_input.text().strip(),
                prefill_pass=self.scan_pass_input.text().strip(),
            )
        self._advance_detect_queue()

    def _on_device_detect_failed(self, msg):
        ip = self._detect_ip
        self._reset_detect_ui()
        box = QMessageBox(self)
        box.setWindowTitle("عدم تشخیص خودکار")
        box.setText(f"{msg}\n\nنوع دستگاه {ip} را مشخص کنید:")
        camera_btn = box.addButton("دوربین تکی", QMessageBox.ButtonRole.AcceptRole)
        nvr_btn = box.addButton("NVR (چندکاناله)", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("انصراف", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if box.clickedButton() == camera_btn:
            self.open_add_camera_dialog(prefill_ip=ip, prefill_user=self.scan_user_input.text(), prefill_pass=self.scan_pass_input.text())
        elif box.clickedButton() == nvr_btn:
            self.open_add_nvr_dialog(prefill_ip=ip, prefill_user=self.scan_user_input.text(), prefill_pass=self.scan_pass_input.text())
        self._advance_detect_queue()

    # ------------------------- دیالوگ‌ها و مدیریت استور -------------------------

    def open_add_camera_dialog(self, prefill_ip=None, detected_path=None, detected_full_url=None, prefill_user=None, prefill_pass=None):
        dialog = AddCameraDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user:
            dialog.user_input.setText(prefill_user)
        if prefill_pass:
            dialog.pass_input.setText(prefill_pass)
        if detected_path is not None or detected_full_url is not None:
            dialog.set_detected_stream(path=detected_path, full_url=detected_full_url)
        if dialog.exec():
            data = dialog.get_camera_data()
            self.camera_store.add_camera(
                data["name"], data["ip"], data["port"], data["user"], data["pass"], data["path"],
                full_url=data.get("full_url"),
            )
            self.reload_camera_list()

    def open_add_nvr_dialog(self, prefill_ip=None, detected_brand=None, detected_onvif_port=None, prefill_user=None, prefill_pass=None):
        dialog = AddNVRDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user:
            dialog.user_input.setText(prefill_user)
        if prefill_pass:
            dialog.pass_input.setText(prefill_pass)
        if detected_brand:
            dialog.set_detected_brand(brand=detected_brand, onvif_port=detected_onvif_port)
        if dialog.exec():
            data = dialog.get_nvr_data()
            nvr = self.camera_store.add_nvr(
                name=data["name"], ip=data["ip"], rtsp_port=data["rtsp_port"],
                onvif_port=data["onvif_port"], user=data["user"],
                pwd=data["pass"], brand=data["brand"],
            )
            for entry, default_name in dialog.get_selected_channels():
                if entry["is_full_url"]:
                    self.camera_store.add_channel_camera(nvr, entry["channel"], default_name, path="", full_url=entry["path_or_url"])
                else:
                    self.camera_store.add_channel_camera(nvr, entry["channel"], default_name, path=entry["path_or_url"])
            self.reload_camera_list()

    def reload_camera_list(self):
        self.camera_list.clear()
        for nvr in self.camera_store.nvrs:
            channel_count = len(self.camera_store.cameras_for_nvr(nvr["id"]))
            nvr_item = QTreeWidgetItem([f"🖥 {nvr['name']} ({nvr['ip']}) - {channel_count} کانال"])
            nvr_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "nvr", "id": nvr["id"]})
            self.camera_list.addTopLevelItem(nvr_item)
            for cam in self.camera_store.cameras_for_nvr(nvr["id"]):
                cam_item = QTreeWidgetItem([cam["name"]])
                cam_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "camera", "id": cam["id"]})
                nvr_item.addChild(cam_item)
            nvr_item.setExpanded(True)

        for cam in self.camera_store.standalone_cameras():
            cam_item = QTreeWidgetItem([cam["name"]])
            cam_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "camera", "id": cam["id"]})
            self.camera_list.addTopLevelItem(cam_item)

    def show_camera_context_menu(self, pos):
        item = self.camera_list.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if data["type"] == "camera":
            delete_action = QAction("حذف دوربین", self)
            delete_action.triggered.connect(lambda: self.delete_camera(data["id"]))
            menu.addAction(delete_action)
        else:
            delete_action = QAction("حذف NVR و تمام کانال‌ها", self)
            delete_action.triggered.connect(lambda: self.delete_nvr(data["id"]))
            menu.addAction(delete_action)
        menu.exec(self.camera_list.mapToGlobal(pos))

    def delete_camera(self, cam_id):
        self.camera_store.remove_camera(cam_id)
        self.reload_camera_list()

    def delete_nvr(self, nvr_id):
        self.camera_store.remove_nvr(nvr_id, cascade=True)
        self.reload_camera_list()

    def _ensure_password(self, cam: dict) -> bool:
        nvr = self.camera_store.get_nvr(cam.get("nvr_id")) if cam.get("nvr_id") else None
        source = nvr if nvr is not None else cam
        if source.get("pass"):
            if nvr is not None and not cam.get("pass"):
                cam["pass"] = nvr["pass"]
            return True

        pwd, ok = QInputDialog.getText(
            self, "رمز عبور", f"رمز عبور برای «{source['name']}» را وارد کنید:",
            QLineEdit.EchoMode.Password
        )
        if not ok:
            return False
        source["pass"] = pwd
        if nvr is not None:
            for sibling in self.camera_store.cameras_for_nvr(nvr["id"]):
                sibling["pass"] = pwd
        return True

    def open_face_library(self):
        active_frame = self.grid_cells[self.selected_cell_index].latest_raw_frame if self.grid_cells else None
        dialog = FaceLibraryDialog(self.face_engine, lambda: active_frame, self)
        dialog.exec()

    def closeEvent(self, event):
        for cell in self.grid_cells:
            cell.stop()
        if self.network_scan_thread and self.network_scan_thread.isRunning():
            self.network_scan_thread.wait(2000)
        if self.detect_thread and self.detect_thread.isRunning():
            self.detect_thread.cancel()
            self.detect_thread.wait(2000)
        self.camera_store.clear_all_passwords()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

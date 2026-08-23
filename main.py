import sys
import time

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QTabWidget, QMenu
)
from PyQt6.QtGui import QImage, QPixmap, QAction
from PyQt6.QtCore import Qt, QTimer

from face_engine import FaceEngine
from scanner import scan_subnet
from camera_store import CameraStore
from camera_stream import CameraStreamThread
from add_camera_dialog import AddCameraDialog
from face_library_dialog import FaceLibraryDialog


class CameraTabWidget(QWidget):
    """یک تب نمایش زنده برای یک دوربین مشخص."""

    def __init__(self, cam: dict, face_engine: FaceEngine, parent=None):
        super().__init__(parent)
        self.cam = cam
        self.face_engine = face_engine
        self.latest_raw_frame = None
        self.stream_thread = None

        layout = QVBoxLayout()
        self.status_label = QLabel(f"در حال اتصال به «{cam['name']}»...")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        self.video_label = QLabel("در انتظار تصویر...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #1e1e1e; color: #ffffff; border-radius: 8px;")
        self.video_label.setMinimumSize(800, 480)

        layout.addWidget(self.status_label)
        layout.addWidget(self.video_label)
        self.setLayout(layout)

    def start(self, rtsp_url, log_callback):
        self.stream_thread = CameraStreamThread(rtsp_url, self.face_engine, process_every_n=5)
        self.stream_thread.frame_ready.connect(self.on_frame_ready)
        self.stream_thread.error_signal.connect(self.on_error)
        self.stream_thread.connected_signal.connect(self.on_connected)
        self.stream_thread.known_face_signal.connect(
            lambda person: log_callback("known", self.cam["name"], person)
        )
        self.stream_thread.unknown_face_signal.connect(
            lambda: log_callback("unknown", self.cam["name"], None)
        )
        self.stream_thread.start()

    def on_connected(self):
        self.status_label.setText(f"وضعیت: متصل - پخش زنده «{self.cam['name']}»")

    def on_error(self, msg):
        self.status_label.setText(f"خطا: {msg}")

    def on_frame_ready(self, display_frame, raw_frame):
        self.latest_raw_frame = raw_frame
        import cv2
        rgb_image = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        self.video_label.setPixmap(
            QPixmap.fromImage(qt_img).scaled(
                self.video_label.width(), self.video_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )

    def stop(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CCTV Management System (CMS) & Face Recognition")
        self.setGeometry(100, 100, 1300, 720)

        self.face_engine = FaceEngine()
        self.camera_store = CameraStore()

        self.init_ui()
        self.reload_camera_list()

    # ---------------------------------------------------------------- UI ---

    def init_ui(self):
        main_widget = QWidget()
        outer_layout = QVBoxLayout()
        main_layout = QHBoxLayout()

        left_panel = QVBoxLayout()

        # بخش اسکن شبکه
        scan_group = QGroupBox("اسکن شبکه (Network Scan)")
        scan_layout = QVBoxLayout()
        self.subnet_input = QLineEdit("192.168.1")
        self.subnet_input.setPlaceholderText("پیشوند ساب‌نت (مثلاً 192.168.1)")
        self.scan_btn = QPushButton("اسکن دستگاه‌های مداربسته")
        self.scan_btn.clicked.connect(self.run_network_scan)
        self.scan_result_list = QListWidget()
        self.scan_result_list.itemDoubleClicked.connect(self.on_scan_result_selected)
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        scan_layout.addWidget(QLabel("برای افزودن، روی نتیجه دابل‌کلیک کنید:"))
        scan_layout.addWidget(self.scan_result_list)
        scan_group.setLayout(scan_layout)
        left_panel.addWidget(scan_group)

        # بخش لیست دوربین‌های من (با نام دلخواه)
        cam_group = QGroupBox("دوربین‌های من")
        cam_layout = QVBoxLayout()
        self.add_camera_btn = QPushButton("+ افزودن دوربین جدید")
        self.add_camera_btn.clicked.connect(lambda: self.open_add_camera_dialog())
        self.camera_list = QListWidget()
        self.camera_list.itemDoubleClicked.connect(self.on_camera_item_activated)
        self.camera_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.camera_list.customContextMenuRequested.connect(self.show_camera_context_menu)
        connect_hint = QLabel("برای پخش زنده، دابل‌کلیک کنید. کلیک راست: ویرایش/حذف")
        connect_hint.setStyleSheet("color: #888; font-size: 10px;")
        cam_layout.addWidget(self.add_camera_btn)
        cam_layout.addWidget(self.camera_list)
        cam_layout.addWidget(connect_hint)
        cam_group.setLayout(cam_layout)
        left_panel.addWidget(cam_group)

        # بخش Face Library
        face_group = QGroupBox("مدیریت چهره (Face Library)")
        face_layout = QVBoxLayout()
        self.face_library_btn = QPushButton("باز کردن Face Library")
        self.face_library_btn.clicked.connect(self.open_face_library)
        face_layout.addWidget(self.face_library_btn)
        face_group.setLayout(face_layout)
        left_panel.addWidget(face_group)
        left_panel.addStretch()

        # پنل راست: تب‌های پخش زنده هر دوربین
        right_panel = QVBoxLayout()
        self.alert_banner = QLabel("")
        self.alert_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.alert_banner.setFixedHeight(28)
        self.alert_banner.setStyleSheet("font-weight: bold;")
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_camera_tab)
        right_panel.addWidget(self.alert_banner)
        right_panel.addWidget(self.tabs)

        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 3)

        # لاگ رویدادها (شناسایی چهره‌های شناخته‌شده/ناشناس)
        log_group = QGroupBox("رویدادهای شناسایی چهره")
        log_layout = QVBoxLayout()
        self.event_log = QListWidget()
        self.event_log.setMaximumHeight(140)
        log_layout.addWidget(self.event_log)
        log_group.setLayout(log_layout)

        outer_layout.addLayout(main_layout)
        outer_layout.addWidget(log_group)

        main_widget.setLayout(outer_layout)
        self.setCentralWidget(main_widget)

        self._banner_timer = QTimer(self)
        self._banner_timer.setSingleShot(True)
        self._banner_timer.timeout.connect(lambda: self.alert_banner.setText(""))

    # ------------------------------------------------------- camera list ---

    def reload_camera_list(self):
        self.camera_list.clear()
        for cam in self.camera_store.cameras:
            item = QListWidgetItem(cam["name"])
            item.setData(Qt.ItemDataRole.UserRole, cam["id"])
            self.camera_list.addItem(item)

    def open_add_camera_dialog(self, prefill_ip=None):
        dialog = AddCameraDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if dialog.exec():
            data = dialog.get_camera_data()
            self.camera_store.add_camera(
                data["name"], data["ip"], data["port"], data["user"], data["pass"], data["path"]
            )
            self.reload_camera_list()

    def show_camera_context_menu(self, pos):
        item = self.camera_list.itemAt(pos)
        if not item:
            return
        cam_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        edit_action = QAction("ویرایش", self)
        edit_action.triggered.connect(lambda: self.edit_camera(cam_id))
        delete_action = QAction("حذف", self)
        delete_action.triggered.connect(lambda: self.delete_camera(cam_id))
        menu.addAction(edit_action)
        menu.addAction(delete_action)
        menu.exec(self.camera_list.mapToGlobal(pos))

    def edit_camera(self, cam_id):
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        dialog = AddCameraDialog(self, existing_cam=cam)
        if dialog.exec():
            data = dialog.get_camera_data()
            self.camera_store.update_camera(cam_id, **data)
            self.reload_camera_list()

    def delete_camera(self, cam_id):
        confirm = QMessageBox.question(self, "تأیید حذف", "آیا از حذف این دوربین از لیست مطمئن هستید؟")
        if confirm == QMessageBox.StandardButton.Yes:
            self.camera_store.remove_camera(cam_id)
            self.reload_camera_list()

    def on_camera_item_activated(self, item):
        cam_id = item.data(Qt.ItemDataRole.UserRole)
        cam = self.camera_store.get_camera(cam_id)
        if cam:
            self.open_live_tab(cam)

    def on_scan_result_selected(self, item):
        text = item.text()
        if " " in text:
            ip = text.split(" ")[0]
            self.open_add_camera_dialog(prefill_ip=ip)

    # ------------------------------------------------------------ tabs ----

    def open_live_tab(self, cam: dict):
        # اگر همین دوربین از قبل باز است، فقط به آن تب سوییچ کن.
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, CameraTabWidget) and widget.cam["id"] == cam["id"]:
                self.tabs.setCurrentIndex(i)
                return

        tab = CameraTabWidget(cam, self.face_engine)
        index = self.tabs.addTab(tab, cam["name"])
        self.tabs.setCurrentIndex(index)
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        tab.start(rtsp_url, self.log_face_event)

    def close_camera_tab(self, index):
        widget = self.tabs.widget(index)
        if isinstance(widget, CameraTabWidget):
            widget.stop()
        self.tabs.removeTab(index)

    def get_active_camera_frame(self):
        widget = self.tabs.currentWidget()
        if isinstance(widget, CameraTabWidget):
            return widget.latest_raw_frame
        return None

    # ------------------------------------------------------ face library ---

    def open_face_library(self):
        dialog = FaceLibraryDialog(self.face_engine, self.get_active_camera_frame, self)
        dialog.exec()

    def log_face_event(self, kind, camera_name, person):
        timestamp = time.strftime("%H:%M:%S")
        if kind == "known":
            text = f"[{timestamp}] {camera_name}: چهره شناسایی شد - {person.get('name', '')}"
            self.alert_banner.setStyleSheet("font-weight: bold; color: #2ecc71;")
        else:
            text = f"[{timestamp}] {camera_name}: ⚠ چهره تعریف نشده شناسایی شد!"
            self.alert_banner.setStyleSheet("font-weight: bold; color: #e74c3c;")

        self.event_log.insertItem(0, text)
        self.alert_banner.setText(text)
        self._banner_timer.start(5000)

    # ------------------------------------------------------------- scan ---

    def run_network_scan(self):
        subnet = self.subnet_input.text().strip()
        self.scan_result_list.clear()
        self.scan_result_list.addItem("در حال اسکن شبکه...")
        QApplication.processEvents()

        devices = scan_subnet(subnet)
        self.scan_result_list.clear()
        if not devices:
            self.scan_result_list.addItem("هیچ دوربینی یافت نشد.")
            return

        for dev in devices:
            ports_str = ",".join(map(str, dev["ports"]))
            self.scan_result_list.addItem(f"{dev['ip']} (پورت‌ها: {ports_str})")

    # ------------------------------------------------------------ close ---

    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, CameraTabWidget):
                widget.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

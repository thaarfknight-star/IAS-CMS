import sys
import os
import cv2
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QInputDialog, QMessageBox,
    QGroupBox, QCheckBox
)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from face_engine import FaceEngine
from scanner import scan_subnet

# مسیرهای استاندارد RTSP ویژه Sunell، IAP، Dahua، Hikvision و سایر برندها
CANDIDATE_PATHS = [
    # Sunell
    "live/ch0",
    "snscview",
    "live/main",
    "live/ch1",
    "ch0",
    "h264/ch1/main/av_stream",
    # IAP & Dahua
    "cam/realmonitor?channel=1&subtype=0",
    "cam/realmonitor?channel=1&subtype=1",
    # Hikvision
    "Streaming/Channels/101",
    "Streaming/Channels/1",
    # Generic & XM
    "h264Preview_01_main",
    "stream1",
    "video1",
    "media/video1",
    "onvif1",
    "profile1",
    ""
]

class AutoDetectThread(QThread):
    progress_signal = pyqtSignal(str)
    found_signal = pyqtSignal(str, str)
    failed_signal = pyqtSignal(str)

    def __init__(self, ip, port, user, pwd):
        super().__init__()
        self.ip = ip
        self.port = port
        self.user = user
        self.pwd = pwd
        self._is_cancelled = False

    def run(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;2000000"

        for path in CANDIDATE_PATHS:
            if self._is_cancelled:
                return

            path_display = path if path else "(root)"
            self.progress_signal.emit(f"در حال بررسی مسیر: {path_display}...")

            if self.user and self.pwd:
                url = f"rtsp://{self.user}:{self.pwd}@{self.ip}:{self.port}/{path}" if path else f"rtsp://{self.user}:{self.pwd}@{self.ip}:{self.port}"
            else:
                url = f"rtsp://{self.ip}:{self.port}/{path}" if path else f"rtsp://{self.ip}:{self.port}"

            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    self.found_signal.emit(path, url)
                    return

        self.failed_signal.emit("هیچ مسیر معتبری با این مشخصات یافت نشد. نام کاربری، رمز عبور یا پورت را بررسی کنید.")

    def cancel(self):
        self._is_cancelled = True


class VideoStreamThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    current_frame_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(self, rtsp_url, face_engine: FaceEngine):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.face_engine = face_engine
        self._run_flag = True

    def run(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        
        if not cap.isOpened():
            self.error_signal.emit("خطا در برقراری ارتباط با استریم RTSP.")
            return

        while self._run_flag:
            ret, frame = cap.read()
            if ret and frame is not None:
                processed = self.face_engine.process_frame(frame)
                self.current_frame_signal.emit(frame)

                rgb_image = cv2.cvtColor(processed, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                bytes_per_line = ch * w
                qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                self.change_pixmap_signal.emit(qt_img.scaled(800, 480, Qt.AspectRatioMode.KeepAspectRatio))
            else:
                self.msleep(30)
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CCTV Management System (CMS) & Face Recognition")
        self.setGeometry(100, 100, 1150, 680)

        self.face_engine = FaceEngine()
        self.stream_thread = None
        self.auto_detect_thread = None
        self.latest_raw_frame = None

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()

        left_panel = QVBoxLayout()

        # بخش اسکن شبکه
        scan_group = QGroupBox("اسکن شبکه (Network Scan)")
        scan_layout = QVBoxLayout()
        self.subnet_input = QLineEdit("192.168.1")
        self.subnet_input.setPlaceholderText("پیشوند ساب‌نت (مثلاً 192.168.1)")
        self.scan_btn = QPushButton("اسکن دستگاه‌های مداربسته")
        self.scan_btn.clicked.connect(self.run_network_scan)
        self.device_list = QListWidget()
        self.device_list.itemClicked.connect(self.on_device_selected)
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        scan_layout.addWidget(self.device_list)
        scan_group.setLayout(scan_layout)
        left_panel.addWidget(scan_group)

        # بخش مشخصات اتصال
        conn_group = QGroupBox("مشخصات اتصال RTSP (Connection)")
        conn_layout = QVBoxLayout()
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP دوربین یا NVR")
        self.port_input = QLineEdit("554")
        self.port_input.setPlaceholderText("پورت RTSP (پیش‌فرض 554)")
        self.user_input = QLineEdit("admin")
        self.user_input.setPlaceholderText("نام کاربری")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("رمز عبور")

        self.auto_detect_chk = QCheckBox("تشخیص خودکار مسیر دوربین (Sunell, IAP, ...)")
        self.auto_detect_chk.setChecked(True)
        self.auto_detect_chk.toggled.connect(self.toggle_path_input)

        self.channel_input = QLineEdit("live/ch0")
        self.channel_input.setPlaceholderText("مسیر دستی استریم")
        self.channel_input.setEnabled(False)

        self.status_label = QLabel("وضعیت: آماده")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")

        self.connect_btn = QPushButton("اتصال و پخش تصویر")
        self.connect_btn.clicked.connect(self.handle_connect)

        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(self.port_input)
        conn_layout.addWidget(self.user_input)
        conn_layout.addWidget(self.pass_input)
        conn_layout.addWidget(self.auto_detect_chk)
        conn_layout.addWidget(self.channel_input)
        conn_layout.addWidget(self.status_label)
        conn_layout.addWidget(self.connect_btn)
        conn_group.setLayout(conn_layout)
        left_panel.addWidget(conn_group)

        # بخش تعریف چهره
        face_group = QGroupBox("مدیریت چهره (Face Management)")
        face_layout = QVBoxLayout()
        self.add_face_btn = QPushButton("ثبت چهره از تصویر زنده")
        self.add_face_btn.clicked.connect(self.register_face_from_stream)
        face_layout.addWidget(self.add_face_btn)
        face_group.setLayout(face_layout)
        left_panel.addWidget(face_group)

        # پنل نمایش تصویر
        right_panel = QVBoxLayout()
        self.video_label = QLabel("تصویر دوربین پس از اتصال در اینجا نمایش داده می‌شود")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #1e1e1e; color: #ffffff; border-radius: 8px;")
        self.video_label.setMinimumSize(800, 480)
        right_panel.addWidget(self.video_label)

        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 3)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    def toggle_path_input(self, checked):
        self.channel_input.setEnabled(not checked)

    def run_network_scan(self):
        subnet = self.subnet_input.text().strip()
        self.device_list.clear()
        self.device_list.addItem("در حال اسکن شبکه...")
        QApplication.processEvents()

        devices = scan_subnet(subnet)
        self.device_list.clear()
        if not devices:
            self.device_list.addItem("هیچ دوربینی یافت نشد.")
            return

        for dev in devices:
            ports_str = ",".join(map(str, dev["ports"]))
            self.device_list.addItem(f"{dev['ip']} (پورت‌ها: {ports_str})")

    def on_device_selected(self, item):
        text = item.text()
        if " " in text:
            ip = text.split(" ")[0]
            self.ip_input.setText(ip)
            self.port_input.setText("554")

    def handle_connect(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()

        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip() or "554"
        user = self.user_input.text().strip()
        pwd = self.pass_input.text().strip()

        if not ip:
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP را وارد کنید.")
            return

        if self.auto_detect_chk.isChecked():
            self.connect_btn.setEnabled(False)
            self.status_label.setText("در حال جستجوی خودکار مسیر استریم...")
            self.auto_detect_thread = AutoDetectThread(ip, port, user, pwd)
            self.auto_detect_thread.progress_signal.connect(self.update_detection_status)
            self.auto_detect_thread.found_signal.connect(self.on_path_found)
            self.auto_detect_thread.failed_signal.connect(self.on_path_failed)
            self.auto_detect_thread.start()
        else:
            path = self.channel_input.text().strip()
            if user and pwd:
                rtsp_url = f"rtsp://{user}:{pwd}@{ip}:{port}/{path}" if path else f"rtsp://{user}:{pwd}@{ip}:{port}"
            else:
                rtsp_url = f"rtsp://{ip}:{port}/{path}" if path else f"rtsp://{ip}:{port}"
            self.start_stream_with_url(rtsp_url)

    def update_detection_status(self, msg):
        self.status_label.setText(msg)

    def on_path_found(self, path, full_url):
        self.channel_input.setText(path)
        self.status_label.setText(f"مسیر معتبر یافت شد: {path or '(root)'}")
        self.connect_btn.setEnabled(True)
        self.start_stream_with_url(full_url)

    def on_path_failed(self, err_msg):
        self.status_label.setText("مسیر پیدا نشد.")
        self.connect_btn.setEnabled(True)
        QMessageBox.warning(self, "عدم اتصال", err_msg)

    def start_stream_with_url(self, rtsp_url):
        self.stream_thread = VideoStreamThread(rtsp_url, self.face_engine)
        self.stream_thread.change_pixmap_signal.connect(self.update_image)
        self.stream_thread.current_frame_signal.connect(self.store_latest_frame)
        self.stream_thread.error_signal.connect(self.on_stream_error)
        self.stream_thread.start()

    def on_stream_error(self, err_msg):
        self.status_label.setText("خطا در استریم.")
        QMessageBox.warning(self, "خطای استریم", err_msg)

    def update_image(self, qt_img):
        self.video_label.setPixmap(QPixmap.fromImage(qt_img))

    def store_latest_frame(self, frame):
        self.latest_raw_frame = frame

    def register_face_from_stream(self):
        if self.latest_raw_frame is None:
            QMessageBox.warning(self, "خطا", "استریم ویدیویی فعال نیست.")
            return

        name, ok = QInputDialog.getText(self, "تعریف چهره", "نام فرد را وارد کنید:")
        if ok and name.strip():
            success = self.face_engine.register_face(name.strip(), self.latest_raw_frame)
            if success:
                QMessageBox.information(self, "موفقیت", f"چهره {name} با موفقیت ثبت شد.")
            else:
                QMessageBox.warning(self, "خطا", "چهره‌ای در تصویر تشخیص داده نشد. لطفاً نزدیک‌تر به دوربین قرار بگیرید.")

    def closeEvent(self, event):
        if self.auto_detect_thread and self.auto_detect_thread.isRunning():
            self.auto_detect_thread.cancel()
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

import sys
import cv2
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QInputDialog, QMessageBox, QGroupBox
)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from face_engine import FaceEngine
from scanner import scan_subnet

class VideoStreamThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    current_frame_signal = pyqtSignal(object)

    def __init__(self, rtsp_url, face_engine: FaceEngine):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.face_engine = face_engine
        self._run_flag = True

    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        while self._run_flag:
            ret, frame = cap.read()
            if ret:
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
        self.setGeometry(100, 100, 1100, 650)

        self.face_engine = FaceEngine()
        self.stream_thread = None
        self.latest_raw_frame = None

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()

        # Left control panel
        left_panel = QVBoxLayout()

        # Network Scan Group
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

        # Connection Group
        conn_group = QGroupBox("مشخصات اتصال RTSP (Connection)")
        conn_layout = QVBoxLayout()
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP دوربین یا NVR")
        self.port_input = QLineEdit("554")
        self.port_input.setPlaceholderText("پورت RTSP")
        self.user_input = QLineEdit("admin")
        self.user_input.setPlaceholderText("نام کاربری")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("رمز عبور")
        self.channel_input = QLineEdit("h264Preview_01_main")
        self.channel_input.setPlaceholderText("مسیر استریم (مانند ch1/main)")

        self.connect_btn = QPushButton("اتصال و پخش زنده")
        self.connect_btn.clicked.connect(self.start_stream)
        
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(self.port_input)
        conn_layout.addWidget(self.user_input)
        conn_layout.addWidget(self.pass_input)
        conn_layout.addWidget(self.channel_input)
        conn_layout.addWidget(self.connect_btn)
        conn_group.setLayout(conn_layout)
        left_panel.addWidget(conn_group)

        # Face Management Group
        face_group = QGroupBox("مدیریت چهره (Face Management)")
        face_layout = QVBoxLayout()
        self.add_face_btn = QPushButton("ثبت چهره از فریم زنده")
        self.add_face_btn.clicked.connect(self.register_face_from_stream)
        face_layout.addWidget(self.add_face_btn)
        face_group.setLayout(face_layout)
        left_panel.addWidget(face_group)

        # Right video stream panel
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

    def start_stream(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()

        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()
        user = self.user_input.text().strip()
        pwd = self.pass_input.text().strip()
        path = self.channel_input.text().strip()

        if user and pwd:
            rtsp_url = f"rtsp://{user}:{pwd}@{ip}:{port}/{path}"
        else:
            rtsp_url = f"rtsp://{ip}:{port}/{path}"

        self.stream_thread = VideoStreamThread(rtsp_url, self.face_engine)
        self.stream_thread.change_pixmap_signal.connect(self.update_image)
        self.stream_thread.current_frame_signal.connect(self.store_latest_frame)
        self.stream_thread.start()

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
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

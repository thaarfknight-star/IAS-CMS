from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox, QLabel,
    QDialogButtonBox, QMessageBox
)

from rtsp_utils import build_rtsp_url, probe_stream

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
        for path in CANDIDATE_PATHS:
            if self._is_cancelled:
                return

            path_display = path if path else "(root)"
            self.progress_signal.emit(f"در حال بررسی مسیر: {path_display}...")

            # build_rtsp_url: کاراکترهای خاص URL داخل رمز عبور را encode می‌کند؛
            # probe_stream: با چند تلاش پیاپی، false negative ناشی از تاخیر
            # رسیدن اولین کی‌فریم را رفع می‌کند — رجوع کنید به rtsp_utils.py.
            url = build_rtsp_url(self.ip, self.port, self.user, self.pwd, path)
            if probe_stream(url):
                self.found_signal.emit(path, url)
                return

        self.failed_signal.emit("هیچ مسیر معتبری با این مشخصات یافت نشد. نام کاربری، رمز عبور یا پورت را بررسی کنید.")

    def cancel(self):
        self._is_cancelled = True


class AddCameraDialog(QDialog):
    """افزودن یا ویرایش یک دوربین همراه با نام دلخواه (درب اصلی، حیاط، راهرو و ...)."""

    def __init__(self, parent=None, existing_cam=None):
        super().__init__(parent)
        self.setWindowTitle("ویرایش دوربین" if existing_cam else "افزودن دوربین جدید")
        self.setMinimumWidth(360)
        self.auto_detect_thread = None
        self.detected_path = None
        self.detected_full_url = None

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("مثلاً: درب اصلی، حیاط، راهرو")
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP دوربین یا NVR")
        self.port_input = QLineEdit("554")
        self.user_input = QLineEdit("admin")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.auto_detect_chk = QCheckBox("تشخیص خودکار مسیر دوربین (Sunell, IAP, ...)")
        self.auto_detect_chk.setChecked(True)
        self.auto_detect_chk.toggled.connect(lambda checked: self.path_input.setEnabled(not checked))

        self.path_input = QLineEdit("live/ch0")
        self.path_input.setPlaceholderText("مسیر دستی استریم")
        self.path_input.setEnabled(False)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")

        if existing_cam:
            self.name_input.setText(existing_cam.get("name", ""))
            self.ip_input.setText(existing_cam.get("ip", ""))
            self.port_input.setText(str(existing_cam.get("port", "554")))
            self.user_input.setText(existing_cam.get("user", ""))
            self.pass_input.setText(existing_cam.get("pass", ""))
            self.auto_detect_chk.setChecked(False)
            self.path_input.setText(existing_cam.get("path", ""))
            self.path_input.setEnabled(True)

        form = QFormLayout()
        form.addRow("نام دوربین:", self.name_input)
        form.addRow("آدرس IP:", self.ip_input)
        form.addRow("پورت:", self.port_input)
        form.addRow("نام کاربری:", self.user_input)
        form.addRow("رمز عبور:", self.pass_input)
        form.addRow(self.auto_detect_chk)
        form.addRow("مسیر دستی:", self.path_input)
        form.addRow(self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.handle_accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

    def set_detected_stream(self, path=None, full_url=None):
        """مسیر/آدرس استریم را که از قبل (مثلاً با تشخیص خودکار نوع دستگاه
        بعد از اسکن شبکه - رجوع کنید به device_detect.py) پیدا شده، از پیش پر
        می‌کند تا کاربر مجبور به تکرار دوباره‌ی جستجو نباشد."""
        self.auto_detect_chk.setChecked(False)
        self.auto_detect_chk.setEnabled(False)
        self.path_input.setEnabled(True)
        self.detected_full_url = full_url
        if full_url:
            self.path_input.setText(full_url)
            self.path_input.setEnabled(False)
        elif path is not None:
            self.path_input.setText(path)
        self.status_label.setText("مسیر استریم به‌صورت خودکار شناسایی شد.")

    def handle_accept(self):
        ip = self.ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP را وارد کنید.")
            return

        if self.auto_detect_chk.isChecked():
            self.buttons.setEnabled(False)
            self.status_label.setText("در حال جستجوی خودکار مسیر استریم...")
            self.auto_detect_thread = AutoDetectThread(
                ip, self.port_input.text().strip() or "554",
                self.user_input.text().strip(), self.pass_input.text().strip()
            )
            self.auto_detect_thread.progress_signal.connect(self.status_label.setText)
            self.auto_detect_thread.found_signal.connect(self._on_path_found)
            self.auto_detect_thread.failed_signal.connect(self._on_path_failed)
            self.auto_detect_thread.start()
        else:
            self.detected_path = self.path_input.text().strip()
            self.accept()

    def _on_path_found(self, path, _full_url):
        self.detected_path = path
        self.buttons.setEnabled(True)
        self.accept()

    def _on_path_failed(self, err_msg):
        self.buttons.setEnabled(True)
        self.status_label.setText("مسیر پیدا نشد.")
        QMessageBox.warning(self, "عدم اتصال", err_msg)

    def closeEvent(self, event):
        # اگر تشخیص خودکار هنوز در حال اجراست و کاربر پنجره را (مثلاً از دکمه‌ی
        # ضربدر) می‌بندد، باید ترد پس‌زمینه به‌درستی متوقف شود؛ در غیر این صورت
        # Qt هنگام تخریب یک QThread هنوز در حال اجرا کرش می‌کند.
        if self.auto_detect_thread and self.auto_detect_thread.isRunning():
            self.auto_detect_thread.cancel()
            self.auto_detect_thread.wait(3000)
        event.accept()

    def reject(self):
        if self.auto_detect_thread and self.auto_detect_thread.isRunning():
            self.auto_detect_thread.cancel()
            self.auto_detect_thread.wait(3000)
        super().reject()

    def get_camera_data(self):
        return {
            "name": self.name_input.text().strip() or self.ip_input.text().strip(),
            "ip": self.ip_input.text().strip(),
            "port": self.port_input.text().strip() or "554",
            "user": self.user_input.text().strip(),
            "pass": self.pass_input.text().strip(),
            "path": "" if self.detected_full_url else (
                self.detected_path if self.detected_path is not None else self.path_input.text().strip()
            ),
            "full_url": self.detected_full_url,
        }

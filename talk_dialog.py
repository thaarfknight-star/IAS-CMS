# -*- coding: utf-8 -*-
"""دیالوگ «🔊 صحبت با دوربین» (2.0.81-beta).

اتصال به بلندگوی دوربین‌های مستقیم (بدون NVR) از طریق RTSP Audio Backchannel
استاندارد ONVIF؛ صدای میکروفون سیستم با G.711 μ-law انکد و به‌صورت RTP
به بلندگوی دوربین فرستاده می‌شود.

جریان کار:
1. باز شدن دیالوگ ← شناسایی پشتیبانی بلندگو در ترد جدا (ONVIF).
2. اگر پشتیبانی شد ← هندشیک بک‌چنل در ترد جدا (یک‌بار).
3. دکمه‌ی «🎤 نگه دارید و صحبت کنید» به‌صورت push-to-talk: نگه‌داشتن =
   ضبط و ارسال، رهاکردن = توقف (اتصال باز می‌ماند).
4. بستن دیالوگ = بستن اتصال و آزادسازی میکروفون.
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
)

from camera_talk import TalkSession, detect_talk_support


class _DetectThread(QThread):
    done = pyqtSignal(dict)

    def __init__(self, cam, parent=None):
        super().__init__(parent)
        self._cam = dict(cam)

    def run(self):
        try:
            info = detect_talk_support(self._cam)
        except Exception as e:  # noqa: BLE001
            info = {"supported": False, "onvif_port": None,
                    "error": str(e)[:100]}
        self.done.emit(info)


class _ConnectThread(QThread):
    done = pyqtSignal(bool)  # ok

    def __init__(self, session, cam, parent=None):
        super().__init__(parent)
        self._session = session
        self._cam = dict(cam)

    def run(self):
        ok = self._session.connect_to_camera(self._cam)
        self.done.emit(bool(ok))


class TalkDialog(QDialog):
    # سیگنال‌های داخلی thread-safe برای کال‌بک‌های TalkSession
    _sig_state = pyqtSignal(str)
    _sig_error = pyqtSignal(str)
    _sig_level = pyqtSignal(float)

    def __init__(self, cam: dict, parent=None):
        super().__init__(parent)
        self._cam = dict(cam)
        self._session = TalkSession()
        self._session.on_state = self._sig_state.emit
        self._session.on_error = self._sig_error.emit
        self._session.on_level = self._sig_level.emit
        self._detect_thread = None
        self._connect_thread = None
        self._connected = False
        self._last_level = -1

        self.setWindowTitle(f"🔊 صحبت با دوربین — {self._cam.get('name', '')}")
        self.setMinimumWidth(340)
        self.setModal(True)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("در حال بررسی پشتیبانی بلندگو…")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # میتر سطح میکروفون
        meter_row = QHBoxLayout()
        meter_row.addWidget(QLabel("🎤"))
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedHeight(12)
        meter_row.addWidget(self.level_bar)
        layout.addLayout(meter_row)

        # دکمه‌ی push-to-talk
        self.talk_button = QPushButton("🎤 نگه دارید و صحبت کنید")
        self.talk_button.setEnabled(False)
        self.talk_button.setMinimumHeight(64)
        font = self.talk_button.font()
        font.setPointSize(max(11, font.pointSize() + 1))
        self.talk_button.setFont(font)
        self.talk_button.pressed.connect(self._on_talk_pressed)
        self.talk_button.released.connect(self._on_talk_released)
        layout.addWidget(self.talk_button)

        hint = QLabel(
            "دکمه را نگه دارید و صحبت کنید؛ با رهاکردن، ارسال صدا قطع می‌شود.\n"
            "صدا از بلندگوی خودِ دوربین پخش می‌شود.")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._sig_state.connect(self._on_state)
        self._sig_error.connect(self._on_error)
        self._sig_level.connect(self._on_level)

        self._start_detection()

    # -- شناسایی ------------------------------------------------------------
    def _start_detection(self):
        self._detect_thread = _DetectThread(self._cam, self)
        self._detect_thread.done.connect(self._on_detected)
        self._detect_thread.start()

    def _on_detected(self, info: dict):
        if self._detect_thread:
            self._detect_thread.wait()
            self._detect_thread = None
        if info.get("error") == "nvr_channel":
            self.status_label.setText(
                "⚠ اتصال به بلندگو فعلاً فقط برای دوربین‌های مستقیم\n"
                "(بدون NVR) پشتیبانی می‌شود.")
            return
        if not info.get("supported"):
            err = info.get("error") or "نامشخص"
            self.status_label.setText(
                f"⚠ این دوربین از پخش صدا (بلندگو) پشتیبانی نمی‌کند.\n({err})")
            return
        self.status_label.setText("در حال اتصال به بلندگوی دوربین…")
        self._connect_thread = _ConnectThread(self._session, self._cam, self)
        self._connect_thread.done.connect(self._on_connected)
        self._connect_thread.start()

    def _on_connected(self, ok: bool):
        if self._connect_thread:
            self._connect_thread.wait()
            self._connect_thread = None
        if ok:
            self._connected = True
            self.status_label.setText(
                "✓ متصل شد — دکمه را نگه دارید و صحبت کنید.")
            self.talk_button.setEnabled(True)
        # اگر ناموفق بود، _on_error پیام را نشان داده است

    # -- کال‌بک‌های نشست ------------------------------------------------------
    def _on_state(self, s: str):
        if s == "talking":
            self.status_label.setText("🔴 در حال ارسال صدا…")
            self.talk_button.setText("🎤 در حال صحبت… (رها کنید)")
        elif s == "connected" and self._connected:
            self.status_label.setText(
                "✓ متصل شد — دکمه را نگه دارید و صحبت کنید.")
            self.talk_button.setText("🎤 نگه دارید و صحبت کنید")
            self.level_bar.setValue(0)
            self._last_level = -1

    def _on_error(self, msg: str):
        self.status_label.setText(f"⚠ خطا: {msg}")
        self.talk_button.setEnabled(False)
        self._connected = False

    def _on_level(self, v: float):
        pct = int(v * 100)
        if abs(pct - self._last_level) >= 3:
            self._last_level = pct
            self.level_bar.setValue(pct)

    # -- push-to-talk ---------------------------------------------------------
    def _on_talk_pressed(self):
        if not self._connected:
            return
        if not self._session.start_talking():
            self.status_label.setText("⚠ میکروفون در دسترس نیست.")

    def _on_talk_released(self):
        self._session.stop_talking()

    # -- پاک‌سازی --------------------------------------------------------------
    def _cleanup(self):
        try:
            self._session.disconnect_session()
        except Exception:
            pass
        for th in (self._detect_thread, self._connect_thread):
            try:
                if th and th.isRunning():
                    th.wait(3000)
            except Exception:
                pass
        self._detect_thread = None
        self._connect_thread = None

    def reject(self):
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)

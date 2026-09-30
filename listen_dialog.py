# -*- coding: utf-8 -*-
"""دیالوگ «🎧 شنیدن صدای دوربین» (2.0.82-beta).

شنیدن صدای زنده‌ی دوربین (میکروفون دوربین ← بلندگوی سیستم) از طریق ترک
صوتی استریم RTSP؛ با کنترل ولوم، دکمه‌ی میوت و میتر سطح صدا.

جریان کار:
1. باز شدن دیالوگ ← اتصال و DESCRIBE در ترد جدا (یک‌بار).
2. اگر ترک صوتی بود ← SETUP/PLAY و پخش خودکار صدا.
3. اگر ترک صوتی نبود ← پیام روشن «این دوربین صدا ندارد».
4. بستن دیالوگ = توقف پخش و بستن اتصال.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QSlider,
)

from camera_audio import ListenSession


class ListenDialog(QDialog):
    # سیگنال‌های داخلی thread-safe برای کال‌بک‌های ListenSession
    _sig_state = pyqtSignal(str)
    _sig_error = pyqtSignal(str)
    _sig_level = pyqtSignal(float)

    def __init__(self, cam: dict, parent=None):
        super().__init__(parent)
        self._cam = dict(cam)
        self._session = ListenSession()
        self._session.state_changed = self._sig_state.emit
        self._session.error_occurred = self._sig_error.emit
        self._session.level_changed = self._sig_level.emit
        self._last_level = -1

        self.setWindowTitle(f"🎧 شنیدن صدای دوربین — {self._cam.get('name', '')}")
        self.setMinimumWidth(360)
        self.setModal(True)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("در حال اتصال به دوربین…")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # میتر سطح صدای دریافتی از دوربین
        meter_row = QHBoxLayout()
        meter_row.addWidget(QLabel("🎧"))
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedHeight(12)
        meter_row.addWidget(self.level_bar)
        layout.addLayout(meter_row)

        # ولوم
        vol_row = QHBoxLayout()
        self.mute_button = QPushButton("🔊")
        self.mute_button.setFixedWidth(48)
        self.mute_button.setToolTip("بی‌صدا / باصدا")
        self.mute_button.clicked.connect(self._on_mute_clicked)
        vol_row.addWidget(self.mute_button)
        vol_row.addWidget(QLabel("ولوم:"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.volume_slider, 1)
        self.volume_label = QLabel("۸۰٪")
        self.volume_label.setFixedWidth(44)
        vol_row.addWidget(self.volume_label)
        layout.addLayout(vol_row)

        hint = QLabel(
            "صدای زنده‌ی دوربین از بلندگوی سیستم پخش می‌شود.\n"
            "اگر دوربین میکروفون/ترک صوتی نداشته باشد، پیام می‌بینید.")
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

        # شروع اتصال
        self._session.set_volume(0.8)
        if not self._session.start(self._cam):
            # خطا از طریق _on_error نمایش داده شده است
            pass

    # -- کنترل‌ها ------------------------------------------------------------
    def _on_volume_changed(self, v: int):
        self.volume_label.setText(f"{v}٪")
        self._session.set_volume(v / 100.0)
        if v > 0 and self._session.is_muted():
            self._session.set_muted(False)
            self.mute_button.setText("🔊")

    def _on_mute_clicked(self):
        muted = not self._session.is_muted()
        self._session.set_muted(muted)
        self.mute_button.setText("🔇" if muted else "🔊")

    # -- کال‌بک‌های نشست ------------------------------------------------------
    def _on_state(self, s: str):
        if s == "connecting":
            self.status_label.setText("در حال اتصال به دوربین…")
        elif s.startswith("connecting:"):
            step = s.split(":", 1)[1]
            self.status_label.setText(f"در حال اتصال به دوربین… ({step})")
        elif s == "playing":
            self.status_label.setText("✓ در حال پخش صدای دوربین")
        elif s == "idle":
            self.level_bar.setValue(0)

    def _on_error(self, msg: str):
        self.status_label.setText(f"⚠ خطا: {msg}")

    def _on_level(self, v: float):
        pct = int(v * 100)
        if abs(pct - self._last_level) >= 3:
            self._last_level = pct
            self.level_bar.setValue(pct)

    # -- پاک‌سازی --------------------------------------------------------------
    def _cleanup(self):
        try:
            self._session.stop()
        except Exception:
            pass

    def reject(self):
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)

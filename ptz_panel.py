# -*- coding: utf-8 -*-
"""پنل «🎮 کنترل PTZ» (1.0.0).

به‌جای پنجره‌ی جدا (PTZDialog)، این پنل سمت راست زیر «پنل رویدادها»
قرار می‌گیرد و کنترل‌های PTZ دوربین انتخاب‌شده را نشان می‌دهد.
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSlider, QMessageBox,
)

from ptz_control import PTZController, detect_ptz_support


class _Worker(QThread):
    done = pyqtSignal(bool, object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            ok, res = self._fn()
        except Exception as e:
            ok, res = False, str(e)[:100]
        self.done.emit(ok, res)


class PTZPanel(QWidget):
    """پنل کنترل PTZ که در ستون راست برنامه جاسازی می‌شود."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cam = None
        self.controller = None
        self._speed = 0.5
        self._workers = []
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        self.title_label = QLabel("🎮 کنترل PTZ")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        lay.addWidget(self.title_label)

        self.status_label = QLabel("دوربینی انتخاب نشده است")
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        # دکمه‌های جهت (شبکه ۳x۳)
        grid = QGridLayout()
        self.btn_up = QPushButton("▲")
        self.btn_down = QPushButton("▼")
        self.btn_left = QPushButton("◀")
        self.btn_right = QPushButton("▶")
        self.btn_home = QPushButton("⌂")

        for btn in (self.btn_up, self.btn_down, self.btn_left,
                    self.btn_right, self.btn_home):
            btn.setMinimumSize(48, 40)
            btn.setEnabled(False)

        grid.addWidget(self.btn_up, 0, 1)
        grid.addWidget(self.btn_left, 1, 0)
        grid.addWidget(self.btn_home, 1, 1)
        grid.addWidget(self.btn_right, 1, 2)
        grid.addWidget(self.btn_down, 2, 1)
        lay.addLayout(grid)

        # زوم
        zoom_lay = QHBoxLayout()
        zoom_lay.addWidget(QLabel("زوم:"))
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_out = QPushButton("−")
        for b in (self.btn_zoom_in, self.btn_zoom_out):
            b.setMinimumSize(48, 32)
            b.setEnabled(False)
        zoom_lay.addWidget(self.btn_zoom_in)
        zoom_lay.addWidget(self.btn_zoom_out)
        zoom_lay.addStretch()
        lay.addLayout(zoom_lay)

        # سرعت
        speed_lay = QHBoxLayout()
        speed_lay.addWidget(QLabel("سرعت:"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(1, 10)
        self.speed_slider.setValue(5)
        self.speed_slider.valueChanged.connect(
            lambda v: setattr(self, "_speed", v / 10.0))
        speed_lay.addWidget(self.speed_slider)
        lay.addLayout(speed_lay)

        # اتصال سیگنال‌ها (فشار و رها کردن برای حرکت مداوم)
        self.btn_up.pressed.connect(lambda: self._move("up"))
        self.btn_up.released.connect(self._stop)
        self.btn_down.pressed.connect(lambda: self._move("down"))
        self.btn_down.released.connect(self._stop)
        self.btn_left.pressed.connect(lambda: self._move("left"))
        self.btn_left.released.connect(self._stop)
        self.btn_right.pressed.connect(lambda: self._move("right"))
        self.btn_right.released.connect(self._stop)
        self.btn_home.clicked.connect(self._go_home)
        self.btn_zoom_in.pressed.connect(lambda: self._zoom("in"))
        self.btn_zoom_in.released.connect(self._stop)
        self.btn_zoom_out.pressed.connect(lambda: self._zoom("out"))
        self.btn_zoom_out.released.connect(self._stop)

        lay.addStretch()

    def set_camera(self, cam):
        """تنظیم دوربین برای کنترل PTZ."""
        self.cam = dict(cam) if cam else None
        if not self.cam:
            self.status_label.setText("دوربینی انتخاب نشده است")
            self._set_enabled(False)
            return

        self.title_label.setText(f"🎮 کنترل PTZ — {self.cam.get('name', '')}")
        self.status_label.setText("در حال شناسایی پشتیبانی PTZ...")
        self._set_enabled(False)

        # شناسایی در ترد جدا
        worker = _Worker(lambda: (True, detect_ptz_support(self.cam)))
        worker.done.connect(self._on_detected)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _on_detected(self, ok, info):
        if not ok or not isinstance(info, dict) or not info.get("supported"):
            err = info.get("error", "نامشخص") if isinstance(info, dict) else "نامشخص"
            self.status_label.setText(f"PTZ پشتیبانی نمی‌شود: {err}")
            self._set_enabled(False)
            return
        try:
            self.controller = PTZController(self.cam)
            self.status_label.setText("آماده‌ی کنترل")
            self._set_enabled(True)
        except Exception as e:
            self.status_label.setText(f"خطا: {e}")

    def _set_enabled(self, on):
        for b in (self.btn_up, self.btn_down, self.btn_left,
                  self.btn_right, self.btn_home,
                  self.btn_zoom_in, self.btn_zoom_out):
            b.setEnabled(on)

    def _cleanup_worker(self, worker):
        try:
            self._workers.remove(worker)
        except Exception:
            pass

    def _run_async(self, fn):
        w = _Worker(fn)
        w.done.connect(lambda ok, res: self._on_cmd_done(ok, res))
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _on_cmd_done(self, ok, res):
        if not ok:
            self.status_label.setText(f"خطا: {res}")

    def _move(self, direction):
        if not self.controller:
            return
        speed = self._speed
        moves = {
            "up": (0, speed),
            "down": (0, -speed),
            "left": (-speed, 0),
            "right": (speed, 0),
        }
        pan, tilt = moves.get(direction, (0, 0))
        self._run_async(lambda: (True, self.controller.move(pan, tilt)))

    def _zoom(self, direction):
        if not self.controller:
            return
        z = self._speed if direction == "in" else -self._speed
        self._run_async(lambda: (True, self.controller.zoom(z)))

    def _stop(self):
        if not self.controller:
            return
        self._run_async(lambda: (True, self.controller.stop()))

    def _go_home(self):
        if not self.controller:
            return
        self._run_async(lambda: (True, self.controller.go_home()))

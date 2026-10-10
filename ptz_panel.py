# -*- coding: utf-8 -*-
"""پنل «🎮 کنترل PTZ» (2.2.1) — کنترل کامل.

به‌جای پنجره‌ی جدا (PTZDialog)، این پنل سمت راست زیر «پنل رویدادها»
قرار می‌گیرد و کنترل‌های کامل PTZ دوربین انتخاب‌شده را نشان می‌دهد:
- D-pad ۳x۳ با جهت‌های قطری
- زوم ➕/➖
- فوکوس 🔍+/🔍−
- اسلایدر سرعت
- پریست‌ها (رفتن/ذخیره/حذف)
- بازگشت به خانه
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSlider, QListWidget, QListWidgetItem, QInputDialog,
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
    """پنل کنترل کامل PTZ که در ستون راست برنامه جاسازی می‌شود."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cam = None
        self.controller = None
        self._speed = 0.5
        self._workers = []
        self._move_btns = []
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self.title_label = QLabel("🎮 کنترل PTZ")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        lay.addWidget(self.title_label)

        self.status_label = QLabel("دوربینی انتخاب نشده است")
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        # --- D-pad ۳x۳ با جهت‌های قطری ---
        lay.addWidget(QLabel("چرخش:"))
        grid = QGridLayout()
        grid.setSpacing(4)
        # (ردیف، ستون، pan، tilt، لیبل)
        cells = [
            (0, 0, -1, 1, "↖"), (0, 1, 0, 1, "▲"), (0, 2, 1, 1, "↗"),
            (1, 0, -1, 0, "◀"), (1, 1, 0, 0, "⏹"), (1, 2, 1, 0, "▶"),
            (2, 0, -1, -1, "↙"), (2, 1, 0, -1, "▼"), (2, 2, 1, -1, "↘"),
        ]
        for r, c, pan, tilt, label in cells:
            btn = QPushButton(label)
            btn.setFixedSize(52, 40)
            btn.setEnabled(False)
            if pan == 0 and tilt == 0:
                btn.clicked.connect(self._stop)
            else:
                btn.pressed.connect(
                    lambda p=pan, t=tilt: self._start_move(p, t, 0))
                btn.released.connect(self._stop)
            grid.addWidget(btn, r, c)
            self._move_btns.append(btn)
        lay.addLayout(grid)

        # --- زوم و فوکوس ---
        zf_lay = QHBoxLayout()
        zf_lay.addWidget(QLabel("زوم:"))
        self.btn_zoom_in = self._make_hold_button(
            "➕", lambda: (0, 0, self._speed))
        self.btn_zoom_out = self._make_hold_button(
            "➖", lambda: (0, 0, -self._speed))
        zf_lay.addWidget(self.btn_zoom_in)
        zf_lay.addWidget(self.btn_zoom_out)
        zf_lay.addWidget(QLabel("فوکوس:"))
        self.btn_focus_near = self._make_hold_button("🔍+", "focus+")
        self.btn_focus_far = self._make_hold_button("🔍−", "focus-")
        zf_lay.addWidget(self.btn_focus_near)
        zf_lay.addWidget(self.btn_focus_far)
        zf_lay.addStretch()
        lay.addLayout(zf_lay)

        # --- سرعت ---
        speed_lay = QHBoxLayout()
        speed_lay.addWidget(QLabel("سرعت:"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(10, 100)
        self.speed_slider.setValue(50)
        self.speed_slider.valueChanged.connect(
            lambda v: setattr(self, "_speed", v / 100.0))
        speed_lay.addWidget(self.speed_slider)
        lay.addLayout(speed_lay)

        # --- پریست‌ها ---
        lay.addWidget(QLabel("پریست‌ها:"))
        self.preset_list = QListWidget()
        self.preset_list.setMaximumHeight(110)
        self.preset_list.setEnabled(False)
        self.preset_list.itemDoubleClicked.connect(
            self._goto_preset_from_item)
        lay.addWidget(self.preset_list)
        prow = QHBoxLayout()
        self.preset_goto_btn = QPushButton("رفتن")
        self.preset_goto_btn.setEnabled(False)
        self.preset_goto_btn.clicked.connect(self._goto_selected_preset)
        self.preset_add_btn = QPushButton("➕ ذخیره موقعیت")
        self.preset_add_btn.setEnabled(False)
        self.preset_add_btn.clicked.connect(self._add_preset)
        self.preset_del_btn = QPushButton("🗑 حذف")
        self.preset_del_btn.setEnabled(False)
        self.preset_del_btn.clicked.connect(self._delete_selected_preset)
        prow.addWidget(self.preset_goto_btn)
        prow.addWidget(self.preset_add_btn)
        prow.addWidget(self.preset_del_btn)
        lay.addLayout(prow)

        # --- خانه ---
        self.home_btn = QPushButton("🏠 بازگشت به خانه")
        self.home_btn.setEnabled(False)
        self.home_btn.clicked.connect(self._go_home)
        lay.addWidget(self.home_btn)

        lay.addStretch()

    def _make_hold_button(self, label, action):
        """دکمه‌ی فشاری-رهایشی: فشار=شروع حرکت، رها=توقف.

        action یا تاپل (pan, tilt, zoom) است یا رشته‌ی "focus+"/"focus-".
        """
        btn = QPushButton(label)
        btn.setFixedSize(52, 36)
        btn.setEnabled(False)
        if isinstance(action, str) and action.startswith("focus"):
            is_near = action == "focus+"
            btn.pressed.connect(
                lambda: self._run_async(
                    lambda: self._do_focus(is_near)))
            btn.released.connect(
                lambda: self._run_async(
                    lambda: self._do_focus_stop()))
        else:
            btn.pressed.connect(
                lambda: self._run_async(
                    lambda: self._do_move(*action())))
            btn.released.connect(self._stop_async)
        self._move_btns.append(btn)
        return btn

    def _do_move(self, pan, tilt, zoom):
        s = self._speed
        self.controller.continuous_move(pan * s, tilt * s, zoom * s)
        return True, ""

    def _do_focus(self, near):
        s = self._speed if near else -self._speed
        self.controller.focus_continuous(s)
        return True, ""

    def _do_focus_stop(self):
        self.controller.focus_stop()
        return True, ""

    def _start_move(self, pan, tilt, zoom):
        if not self.controller:
            return
        s = self._speed
        self._run_async(
            lambda: (True, self.controller.continuous_move(
                pan * s, tilt * s, zoom * s)))

    def _stop(self):
        if not self.controller:
            return
        self._stop_async()

    def _stop_async(self):
        self._run_async(lambda: (True, self.controller.stop()))

    def _go_home(self):
        if not self.controller:
            return
        self._run_async(lambda: (True, self.controller.goto_home()))

    # --- پریست‌ها ---

    def _refresh_presets(self):
        if not self.controller:
            return

        def _job():
            try:
                presets = self.controller.get_presets()
                return True, presets
            except Exception as e:
                return False, str(e)[:80]

        def _fill(ok, res):
            self.preset_list.clear()
            if not ok:
                return
            for p in (res or []):
                name = p.get("name", p.get("token", "?"))
                token = p.get("token", "")
                item = QListWidgetItem(str(name))
                item.setData(Qt.ItemDataRole.UserRole, token)
                self.preset_list.addItem(item)

        w = _Worker(_job)
        w.done.connect(_fill)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _goto_preset_from_item(self, item):
        token = item.data(Qt.ItemDataRole.UserRole)
        if token and self.controller:
            self._run_async(
                lambda: (True, self.controller.goto_preset(token)))

    def _goto_selected_preset(self):
        item = self.preset_list.currentItem()
        if item:
            self._goto_preset_from_item(item)

    def _add_preset(self):
        if not self.controller:
            return
        name, ok = QInputDialog.getText(
            self, "ذخیره پریست", "نام موقعیت:")
        if not ok or not name.strip():
            return

        def _job():
            try:
                self.controller.set_preset(name.strip())
                return True, ""
            except Exception as e:
                return False, str(e)[:80]

        def _done(ok, res):
            if ok:
                self._refresh_presets()
            else:
                self.status_label.setText(f"خطا در ذخیره پریست: {res}")

        w = _Worker(_job)
        w.done.connect(_done)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _delete_selected_preset(self):
        item = self.preset_list.currentItem()
        if not item or not self.controller:
            return
        token = item.data(Qt.ItemDataRole.UserRole)

        def _job():
            try:
                self.controller.remove_preset(token)
                return True, ""
            except Exception as e:
                return False, str(e)[:80]

        def _done(ok, res):
            if ok:
                self._refresh_presets()
            else:
                self.status_label.setText(f"خطا در حذف پریست: {res}")

        w = _Worker(_job)
        w.done.connect(_done)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    # --- مدیریت دوربین ---

    def set_camera(self, cam):
        """تنظیم دوربین برای کنترل PTZ."""
        self.cam = dict(cam) if cam else None
        self.controller = None
        self.preset_list.clear()
        if not self.cam:
            self.status_label.setText("دوربینی انتخاب نشده است")
            self._set_enabled(False)
            return

        self.title_label.setText(f"🎮 کنترل PTZ — {self.cam.get('name', '')}")
        self.status_label.setText("در حال شناسایی پشتیبانی PTZ...")
        self._set_enabled(False)

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
            from ptz_control import describe_support
            try:
                desc = describe_support(info)
            except Exception:
                desc = "آماده‌ی کنترل"
            self.status_label.setText(desc)
            self._set_enabled(True)
            # پریست‌ها را هم لود کن
            if info.get("presets"):
                self._refresh_presets()
        except Exception as e:
            self.status_label.setText(f"خطا: {e}")

    def _set_enabled(self, on):
        for b in self._move_btns:
            b.setEnabled(on)
        self.preset_list.setEnabled(on)
        self.preset_goto_btn.setEnabled(on)
        self.preset_add_btn.setEnabled(on)
        self.preset_del_btn.setEnabled(on)
        self.home_btn.setEnabled(on)

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

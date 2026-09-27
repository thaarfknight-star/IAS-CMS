# -*- coding: utf-8 -*-
"""دیالوگ «🎮 کنترل PTZ» (2.0.70-beta).

کنترل زنده‌ی دوربین‌های PTZ و لنز موتورایزد از طریق ONVIF:
صفحه‌جهت (چرخش افقی/عمودی)، زوم، فوکوس، سرعت حرکت، پریست‌ها و خانه.
اگر وضعیت PTZ دوربین هنوز شناسایی نشده باشد، اول در ترد جدا شناسایی
می‌شود و بعد کنترل‌ها ساخته می‌شوند. همه‌ی فراخوانی‌های شبکه در ترد جدا.
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QSlider, QMessageBox, QInputDialog,
)

from ptz_control import (
    PTZController, detect_ptz_support, describe_support,
)


class _Worker(QThread):
    done = pyqtSignal(bool, object)  # ok, result-or-message

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            ok, res = self._fn()
        except Exception as e:  # noqa: BLE001
            ok, res = False, str(e)[:100]
        self.done.emit(ok, res)


class _DetectThread(QThread):
    done = pyqtSignal(dict)

    def __init__(self, cam, parent=None):
        super().__init__(parent)
        self._cam = dict(cam)

    def run(self):
        try:
            info = detect_ptz_support(self._cam)
        except Exception as e:  # noqa: BLE001
            info = {"supported": False, "error": str(e)[:100]}
        self.done.emit(info)


class PTZDialog(QDialog):
    def __init__(self, cam: dict, on_detected=None, parent=None):
        """on_detected: کال‌بک اختیاری info -> None برای ذخیره‌ی نتیجه‌ی شناسایی."""
        super().__init__(parent)
        self.cam = cam
        self._on_detected = on_detected
        self.controller = PTZController(cam)
        self.info = cam.get("ptz") or None
        self._speed = 1.0
        self._workers = []
        self._move_btns = []

        self.setWindowTitle(f"🎮 کنترل PTZ — {cam.get('name') or cam.get('ip') or ''}")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(380, 520)
        self._root = QVBoxLayout(self)

        self.status_label = QLabel("…")
        self.status_label.setWordWrap(True)
        self._root.addWidget(self.status_label)
        self._body = QVBoxLayout()
        self._root.addLayout(self._body)

        if self.info is None:
            self.status_label.setText("⏳ در حال شناسایی پشتیبانی PTZ…")
            self._detect_thread = _DetectThread(cam, self)
            self._detect_thread.done.connect(self._on_detected_info)
            self._detect_thread.start()
        else:
            self._build_controls(self.info)

    # ---------------------------------------------------------- شناسایی ---
    def _on_detected_info(self, info):
        self.info = info
        if self._on_detected:
            try:
                self._on_detected(info)
            except Exception:
                pass
        self._build_controls(info)

    # ------------------------------------------------------------- ساخت ---
    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()

    def _build_controls(self, info):
        self._clear_body()
        self.status_label.setText(describe_support(info))
        if not info.get("supported"):
            retry = QPushButton("🔄 تلاش مجدد شناسایی")
            retry.clicked.connect(self._retry_detect)
            self._body.addWidget(retry)
            hint = QLabel("اگر دوربین PTZ است ولی شناسایی نشد: نام کاربری/رمز\n"
                          "ONVIF را بررسی کنید و مطمئن شوید سرویس ONVIF دوربین فعال است.")
            hint.setWordWrap(True)
            self._body.addWidget(hint)
            self._body.addStretch(1)
            return

        if info.get("pan"):
            self._body.addWidget(QLabel("چرخش:"))
            self._body.addLayout(self._make_dpad())
        if info.get("zoom") or info.get("focus"):
            row = QHBoxLayout()
            if info.get("zoom"):
                row.addWidget(QLabel("زوم:"))
                row.addWidget(self._make_hold_button("➕", lambda: (0, 0, 1)))
                row.addWidget(self._make_hold_button("➖", lambda: (0, 0, -1)))
            if info.get("focus"):
                row.addWidget(QLabel("فوکوس:"))
                row.addWidget(self._make_hold_button("🔍+", lambda: "focus+", True))
                row.addWidget(self._make_hold_button("🔍−", lambda: "focus-", True))
            row.addStretch(1)
            self._body.addLayout(row)

        # سرعت حرکت
        spd_row = QHBoxLayout()
        spd_row.addWidget(QLabel("سرعت:"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(10, 100)
        self.speed_slider.setValue(int(self._speed * 100))
        self.speed_slider.valueChanged.connect(
            lambda v: setattr(self, "_speed", v / 100.0))
        spd_row.addWidget(self.speed_slider)
        self._body.addLayout(spd_row)

        if info.get("presets"):
            self._body.addWidget(QLabel("پریست‌ها:"))
            self.preset_list = QListWidget()
            self.preset_list.itemDoubleClicked.connect(
                lambda it: self._run(lambda: self.controller.goto_preset(
                    it.data(Qt.ItemDataRole.UserRole))))
            self._body.addWidget(self.preset_list)
            prow = QHBoxLayout()
            goto_btn = QPushButton("رفتن")
            goto_btn.clicked.connect(self._goto_selected_preset)
            add_btn = QPushButton("➕ ذخیره‌ی موقعیت فعلی")
            add_btn.clicked.connect(self._add_preset)
            del_btn = QPushButton("🗑 حذف")
            del_btn.clicked.connect(self._delete_selected_preset)
            prow.addWidget(goto_btn)
            prow.addWidget(add_btn)
            prow.addWidget(del_btn)
            self._body.addLayout(prow)
            self._refresh_presets()
        if info.get("home"):
            home_btn = QPushButton("🏠 بازگشت به خانه")
            home_btn.clicked.connect(
                lambda: self._run(self.controller.goto_home))
            self._body.addWidget(home_btn)
        self._body.addStretch(1)

    def _retry_detect(self):
        self.info = None
        self.status_label.setText("⏳ در حال شناسایی پشتیبانی PTZ…")
        self._detect_thread = _DetectThread(self.cam, self)
        self._detect_thread.done.connect(self._on_detected_info)
        self._detect_thread.start()

    # --------------------------------------------------------- صفحه‌جهت ---
    def _make_dpad(self):
        grid = QGridLayout()
        # (ردیف، ستون، pan, tilt) — در Qt، tilt مثبت یعنی بالا
        cells = [
            (0, 0, -1, 1, "↖"), (0, 1, 0, 1, "↑"), (0, 2, 1, 1, "↗"),
            (1, 0, -1, 0, "←"), (1, 1, 0, 0, "⏹"), (1, 2, 1, 0, "→"),
            (2, 0, -1, -1, "↙"), (2, 1, 0, -1, "↓"), (2, 2, 1, -1, "↘"),
        ]
        for r, c, pan, tilt, label in cells:
            btn = QPushButton(label)
            btn.setFixedSize(52, 44)
            btn.setAutoRepeat(False)
            if pan == 0 and tilt == 0:
                btn.clicked.connect(lambda: self._run(self.controller.stop))
            else:
                btn.pressed.connect(
                    lambda p=pan, t=tilt: self._start_move(p, t, 0))
                btn.released.connect(lambda: self._run(self.controller.stop))
            grid.addWidget(btn, r, c)
            self._move_btns.append(btn)
        return grid

    def _make_hold_button(self, label, action, is_focus=False):
        btn = QPushButton(label)
        btn.setFixedSize(52, 40)
        if is_focus:
            btn.pressed.connect(
                lambda a=action: self._run(
                    lambda: self.controller.focus_continuous(
                        self._speed if a == "focus+" else -self._speed)))
            btn.released.connect(lambda: self._run(self.controller.focus_stop))
        else:
            pan, tilt, zoom = action()
            btn.pressed.connect(
                lambda p=pan, t=tilt, z=zoom: self._start_move(p, t, z))
            btn.released.connect(lambda: self._run(self.controller.stop))
        self._move_btns.append(btn)
        return btn

    def _start_move(self, pan, tilt, zoom):
        s = self._speed
        self._run(lambda: self.controller.continuous_move(
            pan * s, tilt * s, zoom * s))

    # -------------------------------------------------------------- ترد ---
    def _run(self, fn):
        """اجرای یک فراخوانی شبکه در ترد جدا؛ خطا در نوار وضعیت نمایش داده می‌شود."""
        w = _Worker(fn, self)
        w.done.connect(self._on_worker_done)
        self._workers.append(w)
        w.finished.connect(lambda: self._workers.remove(w)
                           if w in self._workers else None)
        w.start()

    def _on_worker_done(self, ok, res):
        if not ok:
            self.status_label.setText(f"⚠ خطا: {res}")

    # ------------------------------------------------------------ پریست ---
    def _refresh_presets(self):
        if not hasattr(self, "preset_list"):
            return
        def _job():
            return self.controller.get_presets()
        w = _Worker(_job, self)
        def _fill(ok, res):
            self.preset_list.clear()
            if ok:
                for tok, name in res:
                    item = QListWidgetItem(name)
                    item.setData(Qt.ItemDataRole.UserRole, tok)
                    self.preset_list.addItem(item)
            else:
                self.status_label.setText(f"⚠ خطا در خواندن پریست‌ها: {res}")
        w.done.connect(_fill)
        self._workers.append(w)
        w.start()

    def _goto_selected_preset(self):
        item = self.preset_list.currentItem()
        if not item:
            return
        tok = item.data(Qt.ItemDataRole.UserRole)
        self._run(lambda: self.controller.goto_preset(tok))

    def _add_preset(self):
        name, ok = QInputDialog.getText(self, "ذخیره‌ی پریست", "نام پریست:")
        if not ok or not name.strip():
            return
        def _job():
            ok2, res = self.controller.set_preset(name.strip())
            return ok2, res
        w = _Worker(_job, self)
        w.done.connect(lambda ok2, res: self._refresh_presets() if ok2
                       else self.status_label.setText(f"⚠ خطا: {res}"))
        self._workers.append(w)
        w.start()

    def _delete_selected_preset(self):
        item = self.preset_list.currentItem()
        if not item:
            return
        tok = item.data(Qt.ItemDataRole.UserRole)
        name = item.text()
        if QMessageBox.question(self, "حذف پریست",
                                f"پریست «{name}» حذف شود؟") != QMessageBox.StandardButton.Yes:
            return
        def _job():
            return self.controller.remove_preset(tok)
        w = _Worker(_job, self)
        w.done.connect(lambda ok2, res: self._refresh_presets() if ok2
                       else self.status_label.setText(f"⚠ خطا: {res}"))
        self._workers.append(w)
        w.start()

# -*- coding: utf-8 -*-
"""صفحه‌ی «ردیابی اشخاص» — مثل صفحه‌ی پلاک‌خوان، داخل QStackedWidget.

دو تب:
  👥 اشخاص: چک‌لیست «ردیابی برای کدام دوربین‌ها»، جدول اشخاص ردیابی‌شده
      (کد یکتا، ویژگی‌های ظاهری، اولین/آخرین دیده‌شدن) + پیش‌نمایش تصویر
      + تنظیمات حساسیت تطبیق.
  🗺 مسیر حرکت: انتخاب شخص + فیلتر تاریخ/دوربین -> خط‌زمانی کامل حضورها
      (ورود/خروج هر دوربین با روز و ساعت دقیق) + خروجی CSV.

نکته‌ی مهم: شناسایی «هویتی» نیست — سیستم چهره را نمی‌بیند و اشخاص را فقط
از روی «ظاهر» (رنگ لباس/شلوار/مو) به هم ربط می‌دهد (رجوع کنید به
person_reid.py). دو نفر با لباس خیلی شبیه ممکن است یکی ثبت شوند.
"""

import os

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QListWidget, QListWidgetItem, QGroupBox,
    QPushButton, QComboBox, QDateEdit, QMessageBox, QFileDialog, QSplitter,
    QDoubleSpinBox, QSpinBox, QInputDialog, QAbstractItemView, QTextEdit,
    QDialog,
)

from person_store import person_store


class LiveTrackStatusDialog(QDialog):
    """پنل «🔍 وضعیت زنده‌ی ردیابی (تشخیصی)» — با دکمه باز می‌شود.

    شمارنده‌های زنده‌ی هر دوربین (تیک/باکس/کاندیدا/تأیید/...) + دنباله‌ی
    فایل person_debug.log را نشان می‌دهد؛ معلوم می‌کند مسیر
    candidate→confirmed→store دقیقاً کجا می‌ایستد.
    """

    HINT = (
        "راهنمای خواندن: تیک = تعداد دورهای تشخیص؛ باکس = باکس‌های معتبر؛ "
        "box_invalid = باکس خرابِ دتکتور؛ desc_fail = دفعاتی که توصیف‌گر "
        "ساخته نشد؛ det=FAIL یعنی مدل YOLO بارگذاری نشده (علت اول "
        "«گزارشی ثبت نمی‌شود»). لاگ کامل: person_data/person_debug.log")

    def __init__(self, collect_lines, clear_log, parent=None):
        super().__init__(parent)
        self.setWindowTitle("وضعیت زنده‌ی ردیابی (تشخیصی)")
        self.resize(760, 460)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._collect_lines = collect_lines
        self._clear_log = clear_log

        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet(
            "font-family: monospace; font-size: 11px; direction: ltr; "
            "text-align: left;")
        layout.addWidget(self.text, 1)

        hint = QLabel(self.HINT)
        hint.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btns = QHBoxLayout()
        refresh_btn = QPushButton("🔄 به‌روزرسانی وضعیت")
        refresh_btn.clicked.connect(self.do_refresh)
        btns.addWidget(refresh_btn)
        clear_btn = QPushButton("🗑 پاک‌سازی فایل لاگ")
        clear_btn.clicked.connect(self._on_clear)
        btns.addWidget(clear_btn)
        btns.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        self.do_refresh()

    def do_refresh(self):
        try:
            lines = self._collect_lines()
        except Exception as e:
            lines = [f"خطا در خواندن وضعیت: {e}"]
        self.text.setPlainText("\n".join(lines) if lines else "—")

    def _on_clear(self):
        try:
            msg = self._clear_log()
        except Exception as e:
            msg = f"پاک‌سازی لاگ ناموفق بود: {e}"
        self.do_refresh()
        QMessageBox.information(self, "پاک‌سازی لاگ", msg)


class PersonTrackPage(QWidget):
    """صفحه‌ی «ردیابی اشخاص» داخل QStackedWidget پنجره‌ی اصلی."""

    PERSON_COLUMNS = ["کد شخص", "چهره", "اولین دیده‌شدن", "آخرین دیده‌شدن",
                      "رنگ لباس", "رنگ شلوار", "رنگ مو", "بلندی مو",
                      "تعداد حضور", "دوربین‌ها", "یادداشت"]
    PATH_COLUMNS = ["ردیف", "دوربین (اتاق)", "تاریخ ورود (شمسی)",
                    "ساعت ورود", "ساعت خروج", "مدت حضور"]

    def __init__(self, camera_store, on_person_toggle=None, parent=None,
                 on_show_on_map=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self.on_person_toggle = on_person_toggle  # (cam_id, enabled) -> None
        self.on_show_on_map = on_show_on_map  # (person_id) -> None: نمایش مسیر روی نقشه ساختمان
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        layout = QVBoxLayout(self)
        title = QLabel("👥 ردیابی اشخاص — دنبال‌کردن مسیر حرکت بین دوربین‌ها (با کمک چهره)")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        title.setWordWrap(True)
        layout.addWidget(title)

        hint = QLabel(
            "اشخاص از روی «ظاهر» (رنگ لباس، شلوار و مو) شناسایی می‌شوند و چهره هم "
            "کمک می‌کند: اگر چهره‌ی شخص در «بانک چهره‌ها» تعریف شده باشد، تطبیق "
            "بین دوربین‌ها قطعی است حتی با عوض کردن لباس. برای چهره‌های ناشناس، "
            "اگر دو نفر لباس خیلی شبیه بپوشند ممکن است یکی حساب شوند.")
        hint.setStyleSheet("color: #9e9e9e; font-size: 11px; padding: 2px 4px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # بنر وضعیت موتور تشخیص شخص: علت «گزارشی ثبت نمی‌شود» را بی‌صدا
        # نمی‌گذارد. main.py با _refresh_person_detector_status آن را
        # به‌روز می‌کند (رجوع کنید به set_detector_status پایین).
        self.detector_banner = QLabel("⏳ در حال بررسی وضعیت موتور تشخیص شخص…")
        self.detector_banner.setWordWrap(True)
        self.detector_banner.setStyleSheet(
            "font-size: 12px; padding: 6px 8px; border-radius: 6px; "
            "background: #1c2a33; color: #e2e8f0;")
        layout.addWidget(self.detector_banner)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_persons_tab(), "👥 اشخاص ردیابی‌شده")
        self.tabs.addTab(self._build_path_tab(), "🗺 گزارش مسیر حرکت")
        layout.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.refresh()

    def refresh(self):
        """هر بار که صفحه از هدر باز می‌شود صدا زده می‌شود."""
        self._reload_camera_checklist()
        self.refresh_persons_table()
        self._reload_path_person_combo()
        self._reload_path_camera_combo()
        self.run_path_search()
        self._update_stats()

    def set_detector_status(self, state, detail=""):
        """به‌روزرسانی بنر وضعیت موتور تشخیص شخص (از main.py صدا زده
        می‌شود). state یکی از:
        - "unknown": هنوز معلوم نیست؛
        - "no_camera": هیچ دوربینی در صفحه‌ی اصلی باز/در حال پخش نیست؛
        - "loading": دوربین باز است ولی اولین تلاش تشخیص هنوز انجام نشده؛
        - "ok": موتور روی حداقل یک دوربین فعال است؛
        - "error": بارگذاری موتور ناموفق بود (detail = پیام خطا).
        """
        styles = {
            "unknown": ("#1c2a33", "#e2e8f0"),
            "no_camera": ("#3a2c14", "#fbbf24"),
            "loading": ("#1c2a33", "#e2e8f0"),
            "ok": ("#123324", "#4ade80"),
            "error": ("#3a1414", "#f87171"),
        }
        texts = {
            "unknown": "⏳ در حال بررسی وضعیت موتور تشخیص شخص…",
            "no_camera": ("📷 هیچ دوربینی در «صفحه اصلی» باز نیست — ردیابی اشخاص "
                          "فقط روی دوربین‌های باز و در حال پخش انجام می‌شود؛ "
                          "اول دوربین‌ها را باز کنید."),
            "loading": "⏳ در حال آماده‌سازی موتور تشخیص شخص…",
            "ok": ("✅ موتور تشخیص شخص فعال است — ردیابی و ثبت گزارش در حال "
                   "انجام است."),
            "error": ("❌ موتور تشخیص شخص بارگذاری نشد؛ تا این مشکل حل نشود "
                      "گزارشی ثبت نمی‌شود."),
        }
        bg, fg = styles.get(state, styles["unknown"])
        text = texts.get(state, texts["unknown"])
        if state == "error" and detail:
            text += f"\nعلت: {detail}"
        self.detector_banner.setText(text)
        self.detector_banner.setStyleSheet(
            f"font-size: 12px; padding: 6px 8px; border-radius: 6px; "
            f"background: {bg}; color: {fg};")

    # ============================================================ تب اشخاص --

    def _build_persons_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # --- دوربین‌های فعال ردیابی
        cam_group = QGroupBox("🎥 ردیابی اشخاص برای کدام دوربین‌ها فعال باشد؟")
        cam_layout = QVBoxLayout()
        self.camera_checklist = QListWidget()
        self.camera_checklist.setMaximumHeight(110)
        self.camera_checklist.itemChanged.connect(self._on_camera_check_changed)
        cam_layout.addWidget(self.camera_checklist)
        cam_hint = QLabel(
            "فقط دوربین‌های تیک‌خورده اشخاص را ردیابی می‌کنند (ردیابی در "
            "پس‌زمینه و بدون کند کردن پخش زنده انجام می‌شود؛ مدل سنگین "
            "جدیدی لازم نیست).")
        cam_hint.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        cam_hint.setWordWrap(True)
        cam_layout.addWidget(cam_hint)
        cam_group.setLayout(cam_layout)
        layout.addWidget(cam_group)

        # --- جدول اشخاص + پیش‌نمایش
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.persons_table = QTableWidget(0, len(self.PERSON_COLUMNS))
        self.persons_table.setHorizontalHeaderLabels(self.PERSON_COLUMNS)
        self.persons_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.persons_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.persons_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.persons_table.itemSelectionChanged.connect(
            self._on_person_selected)
        self.persons_table.itemDoubleClicked.connect(self._on_person_double_clicked)
        splitter.addWidget(self.persons_table)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("تصویر ثبت‌شده:"))
        self.thumb_label = QLabel("—")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setMinimumSize(180, 240)
        self.thumb_label.setStyleSheet(
            "border: 1px solid #555; border-radius: 6px;")
        side_layout.addWidget(self.thumb_label)
        self.show_path_btn = QPushButton("🗺 مشاهده‌ی مسیر حرکت این شخص")
        self.show_path_btn.clicked.connect(self._jump_to_path)
        side_layout.addWidget(self.show_path_btn)
        side_layout.addStretch()
        splitter.addWidget(side)
        splitter.setSizes([700, 220])
        layout.addWidget(splitter, 1)

        # --- تنظیمات حساسیت
        set_group = QGroupBox("⚙️ تنظیمات تطبیق ظاهری")
        set_layout = QHBoxLayout()
        set_layout.addWidget(QLabel("آستانه‌ی تطبیق:"))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.30, 0.70)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setToolTip(
            "فاصله‌ی کسینوسی بردار ظاهری نرمال‌شده (۰ = یکسان). کمتر = "
            "سخت‌گیرانه‌تر (کمتر اشتباه می‌گیرد ولی ممکن است یک شخص را دو "
            "نفر حساب کند)؛ بیشتر = بخشنده‌تر (خطر یکی شدن دو شخص "
            "شبیه‌به‌هم). پیش‌فرض ۰.۴۵.")
        set_layout.addWidget(self.threshold_spin)
        set_layout.addWidget(QLabel("پنجره‌ی اتصال (دقیقه):"))
        self.window_spin = QSpinBox()
        self.window_spin.setRange(1, 120)
        self.window_spin.setToolTip(
            "اگر شخصی بعد از این‌همه دقیقه در دوربین دیگری دیده شود، "
            "شخص تازه‌ای ثبت می‌شود")
        set_layout.addWidget(self.window_spin)
        set_layout.addWidget(QLabel("فریم تأیید:"))
        self.confirm_spin = QSpinBox()
        self.confirm_spin.setRange(1, 10)
        self.confirm_spin.setToolTip(
            "شخص باید این‌تعداد فریم پیاپی دیده شود تا ردش تأیید و ثبت شود")
        set_layout.addWidget(self.confirm_spin)
        save_btn = QPushButton("💾 ذخیره‌ی تنظیمات")
        save_btn.clicked.connect(self._save_settings)
        set_layout.addWidget(save_btn)
        set_layout.addStretch()
        set_group.setLayout(set_layout)
        layout.addWidget(set_group)

        # --- وضعیت زنده‌ی تشخیصی: با دکمه باز می‌شود (درخواست طه: «یه دکمه
        # تعریف کن که هر وقت کلیک کرد باز بشه اون پنل»). معلوم می‌کند مسیر
        # candidate→confirmed→store دقیقاً کجا می‌ایستد.
        diag_row = QHBoxLayout()
        diag_row.addWidget(QLabel("🔍"))
        diag_btn = QPushButton("وضعیت زنده‌ی ردیابی (تشخیصی)")
        diag_btn.setToolTip(
            "باز کردن پنل تشخیصی: شمارنده‌های زنده‌ی هر دوربین و دنباله‌ی "
            "فایل لاگ")
        diag_btn.clicked.connect(self._open_live_status)
        diag_row.addWidget(diag_btn)
        diag_row.addStretch()
        layout.addLayout(diag_row)

        self._load_settings_to_ui()
        return tab

    def _open_live_status(self):
        """باز کردن پنل «وضعیت زنده‌ی ردیابی (تشخیصی)» با دکمه."""
        dlg = LiveTrackStatusDialog(
            collect_lines=self._collect_diag_lines,
            clear_log=self._clear_person_log_core,
            parent=self)
        dlg.exec()

    def _person_log_path(self):
        try:
            base = os.path.dirname(person_store.db_path)
            return os.path.join(base, "person_debug.log")
        except Exception:
            return None

    def _collect_diag_lines(self):
        """ساخت خط‌های گزارش تشخیصی زنده (برای پنل «وضعیت زنده‌ی ردیابی»).

        خروجی: لیست خط‌ها — شمارنده‌های هر دوربین + دنباله‌ی فایل لاگ.
        """
        lines = []
        try:
            win = self.window()
            grid = getattr(win, "camera_grid", None)
            slots = getattr(grid, "slots", []) or []
            found = False
            for slot in slots:
                cam = getattr(slot, "cam", None)
                th = getattr(slot, "stream_thread", None)
                if cam is None or th is None:
                    continue
                get_diag = getattr(th, "get_person_diag", None)
                if not callable(get_diag):
                    continue
                found = True
                d = get_diag()
                name = d.get("camera") or "؟"
                if not d.get("tracking_enabled"):
                    lines.append(f"[{name}] ردیابی خاموش است (تیک بزنید).")
                    continue
                det = "ok" if d.get("detector_available") else "FAIL"
                lines.append(
                    f"[{name}] det={det} | تیک={d['ticks']} "
                    f"باکس={d['boxes_total']} (خراب={d['boxes_invalid']}) | "
                    f"رد ساخته‌شده={d['tracks_created']} "
                    f"کاندیدا={d['candidates']} تأیید={d['confirmed']} "
                    f"پایان={d['ended']} بازگشت={d['reactivated']} "
                    f"خطای توصیف‌گر={d['descriptor_failures']} | "
                    f"فعال={d['active_now']} گمشده={d['lost_now']}")
                if d.get("detector_error"):
                    lines.append(f"    خطای دتکتور: {d['detector_error']}")
            if not found:
                lines.append("هیچ دوربینی در صفحه‌ی اصلی باز نیست.")
        except Exception as e:
            lines.append(f"خطا در خواندن وضعیت: {e}")
        # دنباله‌ی فایل لاگ
        try:
            path = self._person_log_path()
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8",
                          errors="replace") as f:
                    tail = f.read().splitlines()[-8:]
                if tail:
                    lines.append("— آخرین خط‌های person_debug.log: —")
                    lines.extend(tail)
        except Exception:
            pass
        return lines if lines else ["—"]

    def _clear_person_log_core(self):
        """پاک‌سازی فایل لاگ ردیابی؛ خروجی: پیام فارسی نتیجه."""
        try:
            path = self._person_log_path()
            if path and os.path.exists(path):
                os.remove(path)
                msg = "🗑 فایل لاگ ردیابی پاک شد."
            else:
                msg = "فایل لاگی وجود ندارد."
        except Exception as e:
            msg = f"پاک‌سازی لاگ ناموفق بود: {e}"
        self.status_label.setText(msg)
        return msg

    def _load_settings_to_ui(self):
        self.threshold_spin.setValue(person_store.match_threshold)
        self.window_spin.setValue(int(person_store.link_window_min))
        self.confirm_spin.setValue(person_store.confirm_frames)

    def _save_settings(self):
        person_store.set_setting("match_threshold",
                                 self.threshold_spin.value())
        person_store.set_setting("link_window_min", self.window_spin.value())
        person_store.set_setting("confirm_frames", self.confirm_spin.value())
        self.status_label.setText("✅ تنظیمات تطبیق ذخیره شد.")
        # به matcher زنده‌ی پنجره‌ی اصلی هم اطلاع بده (اگر ساخته شده باشد)
        try:
            win = self.window()
            matcher = getattr(win, "_person_matcher", None)
            if matcher is not None:
                matcher.configure(
                    threshold=self.threshold_spin.value(),
                    window_s=self.window_spin.value() * 60.0)
        except Exception:
            pass

    # --- چک‌لیست دوربین‌ها (همان الگوی صفحه‌ی پلاک‌خوان) ---
    def _all_cameras(self):
        cams = []
        try:
            for cam in self.camera_store.standalone_cameras():
                cams.append((cam.get("id"), cam.get("name") or cam.get("ip") or "؟"))
            for nvr in self.camera_store.nvrs:
                nvr_name = nvr.get("name") or nvr.get("ip") or ""
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    label = cam.get("name") or f"کانال {cam.get('channel', '')}"
                    cams.append((cam.get("id"), f"{label} ({nvr_name})"))
        except Exception:
            pass
        return cams

    def _reload_camera_checklist(self):
        self.camera_checklist.blockSignals(True)
        self.camera_checklist.clear()
        cam_by_id = {}
        try:
            for cam in self.camera_store.standalone_cameras():
                cam_by_id[cam.get("id")] = cam
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    cam_by_id[cam.get("id")] = cam
        except Exception:
            pass
        for cam_id, label in self._all_cameras():
            cam = cam_by_id.get(cam_id, {})
            item = QListWidgetItem(f"🎥 {label}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if cam.get("person_tracking") else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, cam_id)
            self.camera_checklist.addItem(item)
        self.camera_checklist.blockSignals(False)

    def _on_camera_check_changed(self, item):
        cam_id = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            self.camera_store.update_camera(cam_id, person_tracking=enabled)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره‌ی تنظیم دوربین ناموفق بود:\n{e}")
            return
        if callable(self.on_person_toggle):
            try:
                self.on_person_toggle(cam_id, enabled)
            except Exception:
                pass

    # --- جدول اشخاص ---
    def _selected_person_id(self):
        row = self.persons_table.currentRow()
        if row < 0:
            return None
        item = self.persons_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_persons_table(self):
        persons = person_store.get_persons()
        self.persons_table.setRowCount(0)
        for p in persons:
            row = self.persons_table.rowCount()
            self.persons_table.insertRow(row)
            vals = [p["id"], p.get("face_name") or "—", p["created_j"], p["last_seen_j"],
                    p["shirt_color"] or "—", p["pants_color"] or "—",
                    p["hair_color"] or "—", p["hair_length"] or "—",
                    str(p["sightings_count"]), p["cameras"] or "—",
                    p["notes"] or ""]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, p["id"])
                self.persons_table.setItem(row, col, item)
        self._update_stats()

    def _on_person_selected(self):
        pid = self._selected_person_id()
        if not pid:
            self.thumb_label.setText("—")
            self.thumb_label.setPixmap(QPixmap())
            return
        persons = {p["id"]: p for p in person_store.get_persons()}
        p = persons.get(pid)
        if not p:
            return
        thumb = p.get("thumb_path") or ""
        if thumb and os.path.exists(thumb):
            pix = QPixmap(thumb)
            if not pix.isNull():
                self.thumb_label.setPixmap(pix.scaled(
                    180, 240, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                return
        self.thumb_label.setText("تصویری ثبت نشده")
        self.thumb_label.setPixmap(QPixmap())

    def _on_person_double_clicked(self, item):
        """دابل‌کلیک روی هر ردیف -> ویرایش یادداشت آن شخص."""
        if item.column() != 9:
            # فقط ستون یادداشت قابل‌ویرایش است؛ روی بقیه هم همان دیالوگ باز شود
            pass
        pid = self._selected_person_id()
        if not pid:
            return
        persons = {p["id"]: p for p in person_store.get_persons()}
        old = (persons.get(pid) or {}).get("notes", "")
        text, ok = QInputDialog.getText(
            self, "یادداشت شخص", f"یادداشت برای {pid} (مثلاً: نگهبان شیفت شب):",
            text=old)
        if ok:
            person_store.set_person_notes(pid, text.strip())
            self.refresh_persons_table()

    def _jump_to_path(self):
        pid = self._selected_person_id()
        if not pid:
            QMessageBox.information(self, "مسیر حرکت",
                                    "اول یک شخص را از جدول انتخاب کنید.")
            return
        self.tabs.setCurrentIndex(1)
        idx = self.path_person_combo.findData(pid)
        if idx >= 0:
            self.path_person_combo.setCurrentIndex(idx)
        self.run_path_search()

    def _update_stats(self):
        persons = person_store.get_persons()
        total_sight = sum(p["sightings_count"] for p in persons)
        self.status_label.setText(
            f"👥 {len(persons)} شخص ردیابی‌شده | 🗺 {total_sight} حضور ثبت‌شده")

    # ===================================================== تب مسیر حرکت --

    def _build_path_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        filt = QHBoxLayout()
        filt.addWidget(QLabel("شخص:"))
        self.path_person_combo = QComboBox()
        self.path_person_combo.setMinimumWidth(160)
        filt.addWidget(self.path_person_combo)
        filt.addWidget(QLabel("از تاریخ:"))
        self.path_from = QDateEdit(QDate.currentDate().addDays(-7))
        self.path_from.setCalendarPopup(True)
        self.path_from.setDisplayFormat("yyyy-MM-dd")
        filt.addWidget(self.path_from)
        filt.addWidget(QLabel("تا تاریخ:"))
        self.path_to = QDateEdit(QDate.currentDate())
        self.path_to.setCalendarPopup(True)
        self.path_to.setDisplayFormat("yyyy-MM-dd")
        filt.addWidget(self.path_to)
        filt.addWidget(QLabel("دوربین:"))
        self.path_camera_combo = QComboBox()
        self.path_camera_combo.setMinimumWidth(140)
        filt.addWidget(self.path_camera_combo)
        search_btn = QPushButton("🔍 جست‌وجو")
        search_btn.clicked.connect(self.run_path_search)
        filt.addWidget(search_btn)
        csv_btn = QPushButton("📥 خروجی CSV")
        csv_btn.clicked.connect(self._export_csv)
        filt.addWidget(csv_btn)
        # رفع درخواست «نقشه‌ی تعاملی ساختمان»: نمایش مسیر شخص انتخاب‌شده
        # روی نقشه‌ی طبقات.
        self.map_btn = QPushButton("🗺 نمایش روی نقشه")
        self.map_btn.clicked.connect(self._show_path_on_map)
        filt.addWidget(self.map_btn)
        filt.addStretch()
        layout.addLayout(filt)

        self.path_table = QTableWidget(0, len(self.PATH_COLUMNS))
        self.path_table.setHorizontalHeaderLabels(self.PATH_COLUMNS)
        self.path_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.path_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.path_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.path_table, 1)

        self.path_summary = QLabel("")
        self.path_summary.setStyleSheet(
            "font-size: 12px; padding: 4px; color: #b0bec5;")
        self.path_summary.setWordWrap(True)
        layout.addWidget(self.path_summary)
        return tab

    def _reload_path_person_combo(self):
        cur = self.path_person_combo.currentData()
        self.path_person_combo.clear()
        self.path_person_combo.addItem("همه‌ی اشخاص", None)
        for p in person_store.get_persons():
            label = (f"{p['id']} — لباس {p['shirt_color'] or '؟'} / "
                     f"شلوار {p['pants_color'] or '؟'}")
            self.path_person_combo.addItem(label, p["id"])
        if cur:
            idx = self.path_person_combo.findData(cur)
            if idx >= 0:
                self.path_person_combo.setCurrentIndex(idx)

    def _reload_path_camera_combo(self):
        cur = self.path_camera_combo.currentData()
        self.path_camera_combo.clear()
        self.path_camera_combo.addItem("همه‌ی دوربین‌ها", None)
        for _cam_id, label in self._all_cameras():
            self.path_camera_combo.addItem(label, label)
        if cur:
            idx = self.path_camera_combo.findData(cur)
            if idx >= 0:
                self.path_camera_combo.setCurrentIndex(idx)

    def _camera_name_from_label(self, label):
        # برچسب نمایشی ممکن است «نام (NVR)» باشد؛ نام واقعی دوربین همان است
        # که در person_sightings.camera_name ذخیره شده (نام دوربین).
        if not label:
            return None
        return label.split(" (")[0]

    def run_path_search(self):
        pid = self.path_person_combo.currentData()
        cam_label = self.path_camera_combo.currentData()
        cam = self._camera_name_from_label(cam_label)
        rows = person_store.query_sightings(
            date_from=self.path_from.date().toString("yyyy-MM-dd"),
            date_to=self.path_to.date().toString("yyyy-MM-dd"),
            camera_name=cam, person_id=pid)
        self.path_table.setRowCount(0)
        for i, r in enumerate(rows, 1):
            row = self.path_table.rowCount()
            self.path_table.insertRow(row)
            vals = [str(i), r["camera_name"] or "—", r["date_j"],
                    r["enter_time"], r["exit_time"], r["duration_str"]]
            for col, val in enumerate(vals):
                self.path_table.setItem(row, col, QTableWidgetItem(val))
        # خلاصه‌ی مسیر: ترتیب ورودها از قدیم به جدید
        if pid and rows:
            ordered = sorted(rows, key=lambda r: r["enter_ts"])
            cams = [r["camera_name"] or "؟" for r in ordered]
            first, last = ordered[0], ordered[-1]
            self.path_summary.setText(
                f"🗺 مسیر {pid}: " + "  ←  ".join(cams) +
                f"  |  از {first['date_j']} ساعت {first['enter_time']}"
                f" تا {last['date_j']} ساعت {last['exit_time']}")
        elif rows:
            self.path_summary.setText(f"{len(rows)} حضور در بازه‌ی انتخاب‌شده پیدا شد.")
        else:
            self.path_summary.setText("در بازه‌ی انتخاب‌شده حضوری ثبت نشده است.")

    def _show_path_on_map(self):
        """دکمه‌ی «🗺 نمایش روی نقشه»: مسیر شخص انتخاب‌شده (یا اگر «همه‌ی
        اشخاص» انتخاب شده، مسیر همه‌ی اشخاص — هر کدام با خط‌چینِ رنگ
        مخصوص خودش) را روی نقشه‌ی ساختمان رسم می‌کند (از طریق کال‌بک
        main). فقط وقتی نقشه و دوربین‌ها اضافه شده باشند رسم انجام
        می‌شود."""
        pid = self.path_person_combo.currentData()
        if callable(self.on_show_on_map):
            # pid=None یعنی «همه‌ی اشخاص»
            self.on_show_on_map(pid)
        else:
            QMessageBox.information(
                self, "نقشه در دسترس نیست",
                "صفحه‌ی «نقشه ساختمان» در این نسخه فعال نیست.")

    def _export_csv(self):
        pid = self.path_person_combo.currentData()
        cam_label = self.path_camera_combo.currentData()
        cam = self._camera_name_from_label(cam_label)
        path, _ = QFileDialog.getSaveFileName(
            self, "خروجی CSV مسیر حرکت", "person_path.csv",
            "CSV (*.csv)")
        if not path:
            return
        try:
            person_store.export_sightings_csv(
                path,
                date_from=self.path_from.date().toString("yyyy-MM-dd"),
                date_to=self.path_to.date().toString("yyyy-MM-dd"),
                camera_name=cam, person_id=pid)
            QMessageBox.information(self, "خروجی CSV",
                                    f"فایل با موفقیت ذخیره شد:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره‌ی CSV ناموفق بود:\n{e}")

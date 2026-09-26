# -*- coding: utf-8 -*-
"""settings_page.py — صفحه‌ی «⚙️ تنظیمات»: تم، صداهای هشدار، اعمال آپدیت.

طراحی (2.0.20-beta به دستور کاربر): صفحه اسکرول‌دار است؛ هر بخش ارتفاع
طبیعی خودش را دارد و لازم نیست همه‌ی گزینه‌ها هم‌زمان در یک نما جا شوند —
کاربر اسکرول می‌کند و پایین می‌رود.

- تم: تاریک / روشن / سیستم — بلافاصله اعمال و ذخیره می‌شود.
- صداهای هشدار: سه صدای مستقل (آژیر حریق / بوق ورود به محدوده /
  بوق تخلف طبقاتی) — هر کدام جداگانه فعال/غیرفعال می‌شود.
- امنیت رمزها: ذخیره‌ی امن با DPAPI ویندوز.
- اعمال آپدیت: همان دیالوگ قبلی هدر (updater.show_apply_update_dialog).
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QComboBox,
    QPushButton, QMessageBox, QCheckBox, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt

import app_settings
from theme import apply_theme




class SettingsPage(QWidget):
    def __init__(self, on_apply_update=None, on_sound_changed=None,
                 on_password_save_changed=None, camera_store=None, parent=None):
        super().__init__(parent)
        self.on_apply_update = on_apply_update
        self.on_sound_changed = on_sound_changed
        self.on_password_save_changed = on_password_save_changed
        self.camera_store = camera_store
        self._build()

    # ------------------------------------------------------------------
    # ساختار صفحه: یک QScrollArea تمام‌صفحه که محتوایش (تیتر + گروه‌ها) با
    # ارتفاع طبیعی چیده شده؛ اگر از نما بلندتر شد اسکرول عمودی می‌خورد.
    # ------------------------------------------------------------------
    def _build(self):

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 20, 28, 28)
        layout.setSpacing(18)

        title = QLabel("⚙️ تنظیمات")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        layout.addWidget(self._build_theme_group())
        layout.addWidget(self._build_sound_group())
        layout.addWidget(self._build_security_group())
        layout.addWidget(self._build_license_group())
        layout.addWidget(self._build_update_group())
        layout.addWidget(self._build_uninstall_group())

        layout.addStretch()

    # --- سازنده‌های هر بخش (هر کدام ارتفاع طبیعی خودشان را دارند) ---

    def _build_theme_group(self):
        theme_group = QGroupBox("🎨 تم برنامه")
        tlay = QVBoxLayout()
        tlay.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(QLabel("تم:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("تاریک", "dark")
        self.theme_combo.addItem("روشن", "light")
        self.theme_combo.addItem("سیستم", "system")
        cur = app_settings.get_theme_mode()
        idx = self.theme_combo.findData(cur)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.theme_combo.setMinimumWidth(180)
        row.addWidget(self.theme_combo)
        row.addStretch()
        tlay.addLayout(row)
        hint = QLabel("«سیستم» یعنی همان تم ویندوز.")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        tlay.addWidget(hint)
        theme_group.setLayout(tlay)
        return theme_group

    def _build_sound_group(self):
        # --- صداهای هشدار (هر کدام مستقل) ---
        # هر ردیف: چک‌باکسِ بدون متن + لیبل جداگانه‌ی wrapشونده؛ تا ترتیب
        # ایموجی/متن فارسی با هیچ فونتی به‌هم نریزد و متن هرگز بریده نشود.
        sound_group = QGroupBox("🔊 صداهای هشدار")
        slay = QVBoxLayout()
        slay.setSpacing(8)
        hint = QLabel("هر صدا مستقل است؛ فعال/غیرفعال بودن یکی روی بقیه اثر ندارد.")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        slay.addWidget(hint)
        from alarm_sound import load_config as _load_sound_cfg
        _scfg = _load_sound_cfg()
        self._sound_checks = {}
        for key, label in (("fire", "🔥 آژیر حریق"),
                           ("zone", "🚧 بوق ورود به محدوده"),
                           ("violation", "🚨 بوق تخلف طبقاتی")):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setContentsMargins(2, 4, 2, 4)
            chk = QCheckBox()
            chk.setChecked(bool(_scfg.get(
                {"fire": "fire_enabled", "zone": "zone_enabled",
                 "violation": "violation_enabled"}[key], False)))
            chk.toggled.connect(lambda c, k=key: self._on_sound_toggled(k, c))
            lbl = QLabel(label)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            lbl.mousePressEvent = lambda _e, _c=chk: _c.toggle()
            row.addWidget(chk)
            row.addWidget(lbl, 1)
            slay.addLayout(row)
            self._sound_checks[key] = chk
        sound_group.setLayout(slay)
        return sound_group

    def _build_security_group(self):
        # --- امنیت رمزها (2.0.15-beta به دستور کاربر) ---
        sec_group = QGroupBox("🔐 امنیت رمزها")
        seclay = QVBoxLayout()
        seclay.setSpacing(10)
        secrow = QHBoxLayout()
        secrow.setSpacing(8)
        secrow.setContentsMargins(2, 4, 2, 4)
        self.savepw_check = QCheckBox()
        self.savepw_check.setChecked(bool(app_settings.load_settings().get("save_passwords", True)))
        try:
            import credential_vault as _vault
            _backend = _vault.backend_label()
        except Exception:
            _backend = ""
        if _backend:
            self.savepw_check.setToolTip("موتور رمزنگاری: " + _backend)
        self.savepw_check.toggled.connect(self._on_savepw_toggled)
        secrow.addWidget(self.savepw_check)
        secpw_lbl = QLabel("ذخیره‌ی امن رمزهای دوربین‌ها و NVRها")
        secpw_lbl.setWordWrap(True)
        secpw_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        secpw_lbl.mousePressEvent = lambda _e: self.savepw_check.toggle()
        secrow.addWidget(secpw_lbl, 1)
        seclay.addLayout(secrow)
        sechint = QLabel("رمزها با DPAPI ویندوز رمزنگاری و روی همین سیستم ذخیره می‌شوند "
                               "(فقط همین کاربر ویندوز می‌تواند بخواند)؛ با هر اجرای برنامه دیگر "
                               "لازم نیست دوباره وارد شوند. با خاموش کردن، رمزهای ذخیره‌شده "
                               "کاملاً پاک می‌شوند.")
        sechint.setStyleSheet("color: #888; font-size: 11px;")
        sechint.setWordWrap(True)
        seclay.addWidget(sechint)
        sec_group.setLayout(seclay)
        return sec_group

    def _build_license_group(self):
        # --- لایسنس: فقط بارگذاری فایل لایسنس (.lic)؛ بدون رمز ادمین ---
        lic_group = QGroupBox("🔑 لایسنس")
        liclay = QVBoxLayout()
        liclay.setSpacing(10)
        self.license_status_lbl = QLabel()
        self.license_status_lbl.setWordWrap(True)
        liclay.addWidget(self.license_status_lbl)
        licrow = QHBoxLayout()
        licrow.setSpacing(8)
        licrow.setContentsMargins(2, 4, 2, 4)
        self.license_btn = self._action_button("📂 بارگذاری فایل لایسنس…",
                                               "#2e7d32")
        self.license_btn.clicked.connect(self._on_license_upload)
        licrow.addWidget(self.license_btn)
        lichint = QLabel("فایل لایسنس (.lic) را که از فروشنده گرفته‌اید انتخاب "
                         "کنید؛ فقط همین فایل پذیرفته می‌شود و قابلیت‌های مجاز "
                         "بلافاصله فعال می‌شوند.")
        lichint.setWordWrap(True)
        licrow.addWidget(lichint, 1)
        liclay.addLayout(licrow)
        lic_group.setLayout(liclay)
        self._refresh_license_status()
        return lic_group

    def _refresh_license_status(self):
        try:
            from license import load_license
            st = load_license()
        except Exception:
            st = None
        try:
            if st is not None and st.valid:
                self.license_status_lbl.setText(
                    f"✅ <b>لایسنس معتبر</b> — {st.customer or 'بدون نام'}"
                    f" — انقضا: {st.expires or 'دائمی'}")
            else:
                self.license_status_lbl.setText(
                    "⛔ <b>لایسنس معتبر نیست</b> — برنامه در حالت محدود است "
                    "(نمایش تصویر همه‌ی دوربین‌ها؛ فقط شمارش افراد فعال است).")
        except Exception:
            pass

    def _on_license_upload(self):
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        try:
            from license import (install_license_file, load_license,
                                 show_license_announcement)
        except Exception as e:
            QMessageBox.warning(self, "خطا",
                                f"بارگذاری ماژول لایسنس ناموفق بود:\n{e}")
            return
        try:
            old_state = load_license()
        except Exception:
            old_state = None
        fp, _ = QFileDialog.getOpenFileName(
            self, "انتخاب فایل لایسنس", "", "License (*.lic)")
        if not fp:
            return
        try:
            install_license_file(fp)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"لایسنس پذیرفته نشد:\n{e}")
            return
        try:
            new_state = load_license()
        except Exception:
            new_state = None
        self._refresh_license_status()
        # فعال‌سازی فوری: وضعیت لایسنس پنجره‌ی اصلی هم تازه می‌شود
        try:
            win = self.window()
            if hasattr(win, "refresh_license_state"):
                win.refresh_license_state()
        except Exception:
            pass
        # اعمال سقف‌های لایسنس جدید: اگر سقف قابلیتی کم شده، تیک آن از روی
        # دوربین‌های اضافی برداشته می‌شود و به کاربر اطلاع داده می‌شود
        enforce_lines = []
        try:
            from admin_quota import enforce_quotas
            cam_store = getattr(self, "camera_store", None)
            if cam_store is not None:
                enforce_lines = enforce_quotas(cam_store) or []
        except Exception:
            pass
        # پنجره‌ی اطلاعیه: چه قابلیت‌هایی باز شد + اقدامات خودکار
        try:
            show_license_announcement(self, old_state, new_state,
                                      extra_lines=enforce_lines)
        except Exception:
            QMessageBox.information(self, "انجام شد",
                                    "لایسنس با موفقیت نصب و فعال شد. ✅")

    def _action_button(self, text, color):
        """دکمه‌ی اکشن تمام‌متن: اندازه‌ی طبیعی متن + بدون بریده‌شدن."""
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton{padding: 10px 22px; border-radius: 8px; font-size: 14px; "
            f"background: {color}; color: white; font-weight: bold;}}")
        # حداقل پهنا از روی sizeHint تا متن هیچ‌وقت «...» نشود
        btn.setMinimumWidth(btn.sizeHint().width() + 8)
        return btn

    def _build_update_group(self):
        upd_group = QGroupBox("⬆️ به‌روزرسانی")
        ulay = QVBoxLayout()
        ulay.setSpacing(12)
        desc = QLabel("فایل «آپدیت» را انتخاب و فقط فایل‌های تغییرکرده را جایگزین کنید.")
        desc.setWordWrap(True)
        ulay.addWidget(desc)
        self.update_btn = self._action_button("⬆️ اعمال آپدیت", "#0f7cc1")
        self.update_btn.clicked.connect(self._on_update_clicked)
        urow = QHBoxLayout()
        urow.addWidget(self.update_btn)
        urow.addStretch()
        ulay.addLayout(urow)
        upd_group.setLayout(ulay)
        return upd_group

    def _build_uninstall_group(self):
        un_group = QGroupBox("🗑 حذف نصب")
        nlay = QVBoxLayout()
        nlay.setSpacing(12)
        ndesc = QLabel("برنامه را به‌طور کامل از سیستم حذف کنید (فایل‌ها، تنظیمات و داده‌ها).")
        ndesc.setWordWrap(True)
        nlay.addWidget(ndesc)
        self.uninstall_btn = self._action_button("🗑 حذف نصب برنامه", "#c0392b")
        self.uninstall_btn.clicked.connect(self._on_uninstall_clicked)
        nrow = QHBoxLayout()
        nrow.addWidget(self.uninstall_btn)
        nrow.addStretch()
        nlay.addLayout(nrow)
        un_group.setLayout(nlay)
        return un_group

    def _on_theme_changed(self, index):
        mode = self.theme_combo.itemData(index)
        app_settings.set_theme_mode(mode)
        try:
            from PyQt6.QtWidgets import QApplication
            apply_theme(QApplication.instance(), mode)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"اعمال تم ممکن نشد:\n{e}")

    def _on_sound_toggled(self, kind, checked):
        try:
            from alarm_sound import set_sound_enabled
            set_sound_enabled(kind, bool(checked))
        except Exception:
            pass
        if callable(self.on_sound_changed):
            try:
                self.on_sound_changed()
            except Exception:
                pass

    def _on_savepw_toggled(self, checked):
        cfg = app_settings.load_settings()
        cfg["save_passwords"] = bool(checked)
        app_settings.save_settings(cfg)
        if callable(self.on_password_save_changed):
            try:
                self.on_password_save_changed(bool(checked))
            except Exception:
                pass

    def _on_update_clicked(self):
        if callable(self.on_apply_update):
            self.on_apply_update()
        else:
            try:
                from updater import show_apply_update_dialog
                show_apply_update_dialog(parent=self)
            except Exception as e:
                QMessageBox.warning(self, "خطا", f"باز کردن دیالوگ آپدیت ممکن نشد:\n{e}")

    def _uninstall_exe_path(self):
        """مسیر uninstall.exe کنار فایل اجرایی برنامه (فقط نسخه‌ی نصب‌شده)."""
        import sys as _sys
        from pathlib import Path
        cands = []
        for src in (getattr(_sys, "executable", ""), _sys.argv[0] if _sys.argv else ""):
            try:
                if src:
                    cands.append(Path(src).resolve().parent / "uninstall.exe")
            except Exception:
                continue
        for p in cands:
            try:
                if p.is_file():
                    return str(p)
            except Exception:
                continue
        return None

    def _on_uninstall_clicked(self):
        exe = self._uninstall_exe_path()
        if not exe:
            QMessageBox.information(self, "حذف نصب برنامه",
                                    "فایل حذف‌کننده (uninstall.exe) کنار برنامه یافت نشد.\nاین گزینه فقط در نسخه‌ی نصب‌شده با Setup کار می‌کند.")
            return
        ans = QMessageBox.question(
            self, "حذف نصب برنامه", "«IAS-CMS» به‌طور کامل از سیستم حذف شود؟\n\nهمه‌ی فایل‌ها، تنظیمات، دوربین‌ها، بانک چهره و سوابق پلاک‌ها برای همیشه پاک می‌شوند.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            import subprocess
            subprocess.Popen([exe],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except Exception as e:
            QMessageBox.warning(self, "حذف نصب برنامه",
                                "اجرای حذف‌کننده ممکن نشد:\n{0}".format(e))
            return
        qapp = None
        try:
            from PyQt6.QtWidgets import QApplication as _QA
            qapp = _QA.instance()
        except Exception:
            qapp = None
        if qapp is not None:
            qapp.quit()

    def refresh(self):
        """همگام‌سازی با تنظیمات ذخیره‌شده هنگام هر بار نمایش صفحه."""
        idx = self.theme_combo.findData(app_settings.get_theme_mode())
        if idx >= 0:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)

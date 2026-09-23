# -*- coding: utf-8 -*-
"""settings_page.py — صفحه‌ی «⚙️ تنظیمات»: تم، زبان، صداهای هشدار، اعمال آپدیت.

طراحی (2.0.20-beta به دستور کاربر): صفحه اسکرول‌دار است؛ هر بخش ارتفاع
طبیعی خودش را دارد و لازم نیست همه‌ی گزینه‌ها هم‌زمان در یک نما جا شوند —
کاربر اسکرول می‌کند و پایین می‌رود.

- تم: تاریک / روشن / سیستم — بلافاصله اعمال و ذخیره می‌شود.
- زبان: فارسی / English — ذخیره می‌شود؛ خود این صفحه دوزبانه است و بقیه‌ی
  برنامه بعد از راه‌اندازی مجدد با زبان انتخابی بالا می‌آید.
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
import i18n
from theme import apply_theme


STRINGS = {
    "title": {"fa": "⚙️ تنظیمات", "en": "⚙️ Settings"},
    "theme_group": {"fa": "🎨 تم برنامه", "en": "🎨 Application theme"},
    "theme_label": {"fa": "تم:", "en": "Theme:"},
    "theme_dark": {"fa": "تاریک", "en": "Dark"},
    "theme_light": {"fa": "روشن", "en": "Light"},
    "theme_system": {"fa": "سیستم", "en": "System"},
    "theme_hint": {"fa": "«سیستم» یعنی همان تم ویندوز.",
                   "en": "“System” follows the Windows theme."},
    "lang_group": {"fa": "🌐 زبان", "en": "🌐 Language"},
    "lang_label": {"fa": "زبان برنامه:", "en": "Application language:"},
    "lang_restart": {"fa": "اعمال کامل زبان جدید نیاز به راه‌اندازی مجدد برنامه دارد.",
                     "en": "A restart is required to fully apply the new language."},
    "update_group": {"fa": "⬆️ به‌روزرسانی", "en": "⬆️ Updates"},
    "update_desc": {"fa": "فایل «آپدیت» را انتخاب و فقط فایل‌های تغییرکرده را جایگزین کنید.",
                    "en": "Select an “update” file to replace only the changed files."},
    "apply_update": {"fa": "⬆️ اعمال آپدیت", "en": "⬆️ Apply update"},
    "uninstall_group": {"fa": "🗑 حذف نصب", "en": "🗑 Uninstallation"},
    "uninstall_title": {"fa": "حذف نصب برنامه", "en": "Uninstall application"},
    "uninstall_desc": {"fa": "برنامه را به‌طور کامل از سیستم حذف کنید (فایل‌ها، تنظیمات و داده‌ها).",
                       "en": "Completely remove the application (files, settings and data) from this system."},
    "uninstall_btn": {"fa": "🗑 حذف نصب برنامه", "en": "🗑 Uninstall application"},
    "uninstall_confirm": {"fa": "«IAS-CMS» به‌طور کامل از سیستم حذف شود؟\n\nهمه‌ی فایل‌ها، تنظیمات، دوربین‌ها، بانک چهره و سوابق پلاک‌ها برای همیشه پاک می‌شوند.",
                          "en": "Completely remove “IAS-CMS” from this system?\n\nAll files, settings, cameras, face database and plate history will be permanently deleted."},
    "uninstall_notfound": {"fa": "فایل حذف‌کننده (uninstall.exe) کنار برنامه یافت نشد.\nاین گزینه فقط در نسخه‌ی نصب‌شده با Setup کار می‌کند.",
                           "en": "The uninstaller (uninstall.exe) was not found next to the application.\nThis option only works in the installed (Setup) version."},
    "uninstall_launch_err": {"fa": "اجرای حذف‌کننده ممکن نشد:\n{0}",
                              "en": "Could not start the uninstaller:\n{0}"},
    "sound_group": {"fa": "🔊 صداهای هشدار", "en": "🔊 Alert sounds"},
    "sound_hint": {"fa": "هر صدا مستقل است؛ فعال/غیرفعال بودن یکی روی بقیه اثر ندارد.",
                   "en": "Each sound is independent of the others."},
    "sound_fire": {"fa": "🔥 آژیر حریق", "en": "🔥 Fire siren"},
    "sound_zone": {"fa": "🚧 بوق ورود به محدوده", "en": "🚧 Zone-entry beep"},
    "sound_violation": {"fa": "🚨 بوق تخلف طبقاتی", "en": "🚨 Floor-violation beep"},
    # (2.0.15-beta به دستور کاربر) ذخیره‌ی امن رمزها
    "sec_group": {"fa": "🔐 امنیت رمزها", "en": "🔐 Password security"},
    "sec_save_pw": {"fa": "ذخیره‌ی امن رمزهای دوربین‌ها و NVRها",
                    "en": "Securely save camera & NVR passwords"},
    "sec_save_pw_hint": {"fa": "رمزها با DPAPI ویندوز رمزنگاری و روی همین سیستم ذخیره می‌شوند "
                               "(فقط همین کاربر ویندوز می‌تواند بخواند)؛ با هر اجرای برنامه دیگر "
                               "لازم نیست دوباره وارد شوند. با خاموش کردن، رمزهای ذخیره‌شده "
                               "کاملاً پاک می‌شوند.",
                         "en": "Passwords are encrypted with Windows DPAPI and stored on this PC "
                               "(readable only by this Windows user); no need to re-enter them on "
                               "each launch. Turning off wipes all saved passwords."},
}


class SettingsPage(QWidget):
    def __init__(self, on_apply_update=None, on_sound_changed=None,
                 on_password_save_changed=None, parent=None):
        super().__init__(parent)
        self.on_apply_update = on_apply_update
        self.on_sound_changed = on_sound_changed
        self.on_password_save_changed = on_password_save_changed
        self._build()

    def _t(self, key):
        # (2.0.39-beta) از زیرساخت مشترک i18n استفاده می‌شود؛ زبان از
        # app_settings خوانده می‌شود (در _on_lang_changed قبل از بازسازی
        # ذخیره شده است).
        return i18n.t(STRINGS, key)

    # ------------------------------------------------------------------
    # ساختار صفحه: یک QScrollArea تمام‌صفحه که محتوایش (تیتر + گروه‌ها) با
    # ارتفاع طبیعی چیده شده؛ اگر از نما بلندتر شد اسکرول عمودی می‌خورد.
    # ------------------------------------------------------------------
    def _build(self):
        # جهت چیدمان بر اساس زبان فعلی (فارسی=راست‌به‌چپ)؛ بدون این، ترتیب
        # ایموجی و متن فارسی در چک‌باکس‌ها و عنوان گروه‌ها به‌هم می‌ریزد
        # (مشاهده‌شده روی ویندوز).
        i18n.apply_direction(self)

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

        title = QLabel(self._t("title"))
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        layout.addWidget(self._build_theme_group())
        layout.addWidget(self._build_lang_group())
        layout.addWidget(self._build_sound_group())
        layout.addWidget(self._build_security_group())
        layout.addWidget(self._build_update_group())
        layout.addWidget(self._build_uninstall_group())

        layout.addStretch()

    # --- سازنده‌های هر بخش (هر کدام ارتفاع طبیعی خودشان را دارند) ---

    def _build_theme_group(self):
        theme_group = QGroupBox(self._t("theme_group"))
        tlay = QVBoxLayout()
        tlay.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(QLabel(self._t("theme_label")))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(self._t("theme_dark"), "dark")
        self.theme_combo.addItem(self._t("theme_light"), "light")
        self.theme_combo.addItem(self._t("theme_system"), "system")
        cur = app_settings.get_theme_mode()
        idx = self.theme_combo.findData(cur)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.theme_combo.setMinimumWidth(180)
        row.addWidget(self.theme_combo)
        row.addStretch()
        tlay.addLayout(row)
        hint = QLabel(self._t("theme_hint"))
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        tlay.addWidget(hint)
        theme_group.setLayout(tlay)
        return theme_group

    def _build_lang_group(self):
        lang_group = QGroupBox(self._t("lang_group"))
        llay = QVBoxLayout()
        llay.setSpacing(10)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel(self._t("lang_label")))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("فارسی", "fa")
        self.lang_combo.addItem("English", "en")
        lidx = self.lang_combo.findData(app_settings.get_language())
        if lidx >= 0:
            self.lang_combo.setCurrentIndex(lidx)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        self.lang_combo.setMinimumWidth(180)
        lrow.addWidget(self.lang_combo)
        lrow.addStretch()
        llay.addLayout(lrow)
        lhint = QLabel(self._t("lang_restart"))
        lhint.setStyleSheet("color: #888; font-size: 11px;")
        lhint.setWordWrap(True)
        llay.addWidget(lhint)
        lang_group.setLayout(llay)
        return lang_group

    def _build_sound_group(self):
        # --- صداهای هشدار (هر کدام مستقل) ---
        # هر ردیف: چک‌باکسِ بدون متن + لیبل جداگانه‌ی wrapشونده؛ تا ترتیب
        # ایموجی/متن فارسی با هیچ فونتی به‌هم نریزد و متن هرگز بریده نشود.
        sound_group = QGroupBox(self._t("sound_group"))
        slay = QVBoxLayout()
        slay.setSpacing(8)
        hint = QLabel(self._t("sound_hint"))
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        slay.addWidget(hint)
        from alarm_sound import load_config as _load_sound_cfg
        _scfg = _load_sound_cfg()
        self._sound_checks = {}
        for key, label in (("fire", self._t("sound_fire")),
                           ("zone", self._t("sound_zone")),
                           ("violation", self._t("sound_violation"))):
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
        sec_group = QGroupBox(self._t("sec_group"))
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
        secpw_lbl = QLabel(self._t("sec_save_pw"))
        secpw_lbl.setWordWrap(True)
        secpw_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        secpw_lbl.mousePressEvent = lambda _e: self.savepw_check.toggle()
        secrow.addWidget(secpw_lbl, 1)
        seclay.addLayout(secrow)
        sechint = QLabel(self._t("sec_save_pw_hint"))
        sechint.setStyleSheet("color: #888; font-size: 11px;")
        sechint.setWordWrap(True)
        seclay.addWidget(sechint)
        sec_group.setLayout(seclay)
        return sec_group

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
        upd_group = QGroupBox(self._t("update_group"))
        ulay = QVBoxLayout()
        ulay.setSpacing(12)
        desc = QLabel(self._t("update_desc"))
        desc.setWordWrap(True)
        ulay.addWidget(desc)
        self.update_btn = self._action_button(self._t("apply_update"), "#0f7cc1")
        self.update_btn.clicked.connect(self._on_update_clicked)
        urow = QHBoxLayout()
        urow.addWidget(self.update_btn)
        urow.addStretch()
        ulay.addLayout(urow)
        upd_group.setLayout(ulay)
        return upd_group

    def _build_uninstall_group(self):
        un_group = QGroupBox(self._t("uninstall_group"))
        nlay = QVBoxLayout()
        nlay.setSpacing(12)
        ndesc = QLabel(self._t("uninstall_desc"))
        ndesc.setWordWrap(True)
        nlay.addWidget(ndesc)
        self.uninstall_btn = self._action_button(self._t("uninstall_btn"), "#c0392b")
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

    def _on_lang_changed(self, index):
        lang = self.lang_combo.itemData(index)
        app_settings.set_language(lang)
        # بازسازی متن‌های همین صفحه با زبان جدید
        self._rebuild_texts()

    def _rebuild_texts(self):
        # ساده‌ترین راه مطمئن: بازسازی کامل ویجت؛ اول layout قدیمی را کاملاً
        # حذف می‌کنیم تا _build بتواند یکی تازه روی همین QWidget بسازد
        # (بدون این کار، Qt اخطار setLayout می‌دهد و layout جدید نصب نمی‌شود).
        # تنها آیتم layout بیرونی، QScrollArea است که با deleteLater همراه
        # محتوایش حذف می‌شود.
        old_layout = self.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    # اول مخفی (تا فلش محتوای قدیمی دیده نشود)، بعد حذف
                    # deferred؛ حذف مستقیم امن نیست چون ممکن است از داخل
                    # سیگنال یکی از همین ویجت‌ها (مثلاً کمبوباکس زبان) صدا
                    # زده شده باشیم.
                    w.hide()
                    w.deleteLater()
            from PyQt6 import sip
            sip.delete(old_layout)
        self._build()

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
            QMessageBox.information(self, self._t("uninstall_title"),
                                    self._t("uninstall_notfound"))
            return
        ans = QMessageBox.question(
            self, self._t("uninstall_title"), self._t("uninstall_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            import subprocess
            subprocess.Popen([exe],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except Exception as e:
            QMessageBox.warning(self, self._t("uninstall_title"),
                                self._t("uninstall_launch_err").format(e))
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

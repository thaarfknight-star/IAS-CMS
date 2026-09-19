# -*- coding: utf-8 -*-
"""settings_page.py — صفحه‌ی «⚙️ تنظیمات»: تم، زبان، صداهای هشدار، اعمال آپدیت.

- تم: تاریک / روشن / سیستم — بلافاصله اعمال و ذخیره می‌شود.
- زبان: فارسی / English — ذخیره می‌شود؛ خود این صفحه دوزبانه است و بقیه‌ی
  برنامه بعد از راه‌اندازی مجدد با زبان انتخابی بالا می‌آید.
- صداهای هشدار: سه صدای مستقل (آژیر حریق / بوق ورود به محدوده /
  بوق تخلف طبقاتی) — هر کدام جداگانه فعال/غیرفعال می‌شود.
- اعمال آپدیت: همان دیالوگ قبلی هدر (updater.show_apply_update_dialog).
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QComboBox,
    QPushButton, QMessageBox, QCheckBox,
)
from PyQt6.QtCore import Qt

import app_settings
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
    "sound_group": {"fa": "🔊 صداهای هشدار", "en": "🔊 Alert sounds"},
    "sound_hint": {"fa": "هر صدا مستقل است؛ فعال/غیرفعال بودن یکی روی بقیه اثر ندارد.",
                   "en": "Each sound is independent of the others."},
    "sound_fire": {"fa": "🔥 آژیر حریق", "en": "🔥 Fire siren"},
    "sound_zone": {"fa": "🚧 بوق ورود به محدوده", "en": "🚧 Zone-entry beep"},
    "sound_violation": {"fa": "🚨 بوق تخلف طبقاتی", "en": "🚨 Floor-violation beep"},
}


class SettingsPage(QWidget):
    def __init__(self, on_apply_update=None, on_sound_changed=None, parent=None):
        super().__init__(parent)
        self.on_apply_update = on_apply_update
        self.on_sound_changed = on_sound_changed
        self._lang = app_settings.get_language()
        self._build()

    def _t(self, key):
        return STRINGS[key].get(self._lang, STRINGS[key]["fa"])

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel(self._t("title"))
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        # --- تم ---
        theme_group = QGroupBox(self._t("theme_group"))
        tlay = QVBoxLayout()
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
        layout.addWidget(theme_group)

        # --- زبان ---
        lang_group = QGroupBox(self._t("lang_group"))
        llay = QVBoxLayout()
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel(self._t("lang_label")))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("فارسی", "fa")
        self.lang_combo.addItem("English", "en")
        lidx = self.lang_combo.findData(self._lang)
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
        layout.addWidget(lang_group)

        # --- صداهای هشدار (هر کدام مستقل) ---
        sound_group = QGroupBox(self._t("sound_group"))
        slay = QVBoxLayout()
        hint = QLabel(self._t("sound_hint"))
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        slay.addWidget(hint)
        from alarm_sound import load_config as _load_sound_cfg, set_sound_enabled as _set_sound
        _scfg = _load_sound_cfg()
        self._sound_checks = {}
        for key, label in (("fire", self._t("sound_fire")),
                           ("zone", self._t("sound_zone")),
                           ("violation", self._t("sound_violation"))):
            chk = QCheckBox(label)
            chk.setChecked(bool(_scfg.get(
                {"fire": "fire_enabled", "zone": "zone_enabled",
                 "violation": "violation_enabled"}[key], False)))
            chk.toggled.connect(lambda c, k=key: self._on_sound_toggled(k, c))
            slay.addWidget(chk)
            self._sound_checks[key] = chk
        sound_group.setLayout(slay)
        layout.addWidget(sound_group)

        # --- آپدیت ---
        upd_group = QGroupBox(self._t("update_group"))
        ulay = QVBoxLayout()
        desc = QLabel(self._t("update_desc"))
        desc.setWordWrap(True)
        ulay.addWidget(desc)
        self.update_btn = QPushButton(self._t("apply_update"))
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.setStyleSheet(
            "QPushButton{padding: 10px 22px; border-radius: 8px; font-size: 14px; "
            "background: #0f7cc1; color: white; font-weight: bold;}")
        self.update_btn.clicked.connect(self._on_update_clicked)
        urow = QHBoxLayout()
        urow.addWidget(self.update_btn)
        urow.addStretch()
        ulay.addLayout(urow)
        upd_group.setLayout(ulay)
        layout.addWidget(upd_group)

        layout.addStretch()

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
        self._lang = lang
        # بازسازی متن‌های همین صفحه با زبان جدید
        self._rebuild_texts()

    def _rebuild_texts(self):
        # ساده‌ترین راه مطمئن: بازسازی کامل ویجت؛ اول layout قدیمی را کاملاً
        # حذف می‌کنیم تا _build بتواند یکی تازه روی همین QWidget بسازد
        # (بدون این کار، Qt اخطار setLayout می‌دهد و layout جدید نصب نمی‌شود).
        old_layout = self.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
                lay = item.layout()
                if lay is not None:
                    while lay.count():
                        sub = lay.takeAt(0)
                        sw = sub.widget()
                        if sw is not None:
                            sw.deleteLater()
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

    def _on_update_clicked(self):
        if callable(self.on_apply_update):
            self.on_apply_update()
        else:
            try:
                from updater import show_apply_update_dialog
                show_apply_update_dialog(parent=self)
            except Exception as e:
                QMessageBox.warning(self, "خطا", f"باز کردن دیالوگ آپدیت ممکن نشد:\n{e}")

    def refresh(self):
        """همگام‌سازی با تنظیمات ذخیره‌شده هنگام هر بار نمایش صفحه."""
        idx = self.theme_combo.findData(app_settings.get_theme_mode())
        if idx >= 0:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)

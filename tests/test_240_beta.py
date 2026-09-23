# -*- coding: utf-8 -*-
"""Headless regression test for IAS-CMS 2.0.40-beta.

رفع کرش استارتاپ (گزارش طه از exe ویندوز):
'SettingsPage' object has no attribute '_lang'
در بازنویسی دوزبانه‌ی settings_page.py (2.0.39-beta) مقداردهی self._lang
حذف شده بود ولی _build_lang_group هنوز به آن ارجاع می‌داد؛ برنامه موقع
ساخت صفحه‌ی تنظیمات (init_ui در MainWindow) کرش می‌کرد.

تست‌ها:
۱) SettingsPage در هر دو زبان بدون استثنا ساخته می‌شود.
۲) کمبوباکس زبان، مقدار زبان فعلی را نشان می‌دهد (نه self._lang).
۳) تعویض زبان از کمبوباکس → _on_lang_changed → _rebuild_texts بدون خطا
   انجام می‌شود و جهت صفحه برعکس می‌شود.
"""
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr
sys.modules["cv2"] = MagicMock(name="cv2")
sys.modules["alarm_sound"] = MagicMock(name="alarm_sound")

passed = []
failed = []


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


from PyQt6.QtWidgets import QApplication, QLabel
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

import app_settings
import settings_page as sp

_holder = {"lang": "fa"}
app_settings.get_language = lambda: _holder["lang"]
app_settings.set_language = lambda l: _holder.update(lang=l)


def label_texts(page):
    return [l.text() for l in page.findChildren(QLabel)]


# ۱) ساخت در فارسی (این دقیقاً همان جایی بود که کرش می‌داد)
try:
    page = sp.SettingsPage()
    built_fa = True
except AttributeError as e:
    built_fa = False
    print("AttributeError:", e)
check("fa: SettingsPage builds without _lang crash", built_fa)
check("fa: RTL", page.layoutDirection() == Qt.LayoutDirection.RightToLeft)
check("fa: combo shows current lang",
      page.lang_combo.currentData() == "fa")
check("fa: title", "⚙️ تنظیمات" in label_texts(page))

# ۲) ساخت در انگلیسی
_holder["lang"] = "en"
try:
    page_en = sp.SettingsPage()
    built_en = True
except AttributeError as e:
    built_en = False
    print("AttributeError:", e)
check("en: SettingsPage builds", built_en)
check("en: LTR",
      page_en.layoutDirection() == Qt.LayoutDirection.LeftToRight)
check("en: combo shows current lang",
      page_en.lang_combo.currentData() == "en")
check("en: title", "⚙️ Settings" in label_texts(page_en))

# ۳) تعویض زبان از کمبوباکس و بازسازی صفحه
_holder["lang"] = "fa"
page2 = sp.SettingsPage()
page2.lang_combo.setCurrentIndex(page2.lang_combo.findData("en"))
app.processEvents()
check("switch: combo now en", page2.lang_combo.currentData() == "en")
check("switch: LTR after rebuild",
      page2.layoutDirection() == Qt.LayoutDirection.LeftToRight)
check("switch: en title", "⚙️ Settings" in label_texts(page2))
page2.lang_combo.setCurrentIndex(page2.lang_combo.findData("fa"))
app.processEvents()
check("switch: back to fa",
      page2.lang_combo.currentData() == "fa"
      and page2.layoutDirection() == Qt.LayoutDirection.RightToLeft)
check("switch: fa title", "⚙️ تنظیمات" in label_texts(page2))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

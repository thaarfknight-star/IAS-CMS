# -*- coding: utf-8 -*-
"""Headless regression test for IAS-CMS 2.0.22-beta.

باگ 2.0.20: در بازطراحی اسکرول‌دار صفحه‌ی تنظیمات، ردیف دکمه‌ی قرمز
«🗑 حذف نصب» ساخته می‌شد ولی با nlay.addLayout(nrow) به لی‌اوت گروه
اضافه نمی‌شد؛ دکمه نامرئی بود. این تست تضمین می‌کند دکمه‌ی حذف نصب
واقعاً در سلسله‌مراتب لی‌اوت قابل‌مشاهده است.
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr

passed = []
failed = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" [{extra}]" if extra and not cond else ""))


from PyQt6.QtWidgets import QApplication, QGroupBox
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

from settings_page import SettingsPage

pg = SettingsPage()
pg.resize(900, 700)
pg.show()
app.processEvents()


def in_layout_tree(widget):
    """آیا ویجت از طریق لی‌اوت‌ها به صفحه وصل است (یعنی دیده می‌شود)؟"""
    w = widget
    while w is not None:
        lay = w.layout()
        if lay is not None:
            return True
        w = w.parentWidget()
        if w is pg:
            return False
    return False


check("uninstall button exists", pg.uninstall_btn is not None)
check("uninstall button in layout tree",
      in_layout_tree(pg.uninstall_btn))

# دکمه باید داخل QGroupBox «حذف نصب» باشد
groups = pg.findChildren(QGroupBox)
un_group = next((g for g in groups if "حذف نصب" in g.title()), None)
check("uninstall group found", un_group is not None)
if un_group is not None:
    check("button inside uninstall group",
          pg.uninstall_btn in un_group.findChildren(type(pg.uninstall_btn)))

# دکمه‌ی قرمز است (استایل خطر)
check("uninstall button is red",
      "c0392b" in pg.uninstall_btn.styleSheet().lower())

# دکمه‌ی آپدیت هم در لی‌اوت است (رگرسیون متقابل)
check("update button in layout tree", in_layout_tree(pg.update_btn))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

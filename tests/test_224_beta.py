# -*- coding: utf-8 -*-
"""Headless regression test for IAS-CMS 2.0.24-beta.

موضوع: بعد از «دریافت لیست کانال‌های این NVR»، کاربر تیک می‌زند کدام
کانال‌ها اضافه و متصل شوند (به‌جای Yes/No برای همه).
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


from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

from nvr_channel_select_dialog import NVRChannelSelectDialog

ENTRIES = [
    (1, "دوربین حیاط", "192.168.1.64"),
    (2, "دوربین پارکینگ", ""),
    (3, "دوربین لابی", "192.168.1.66"),
]

# ============================================ ۱) ساختار دیالوگ ==
dlg = NVRChannelSelectDialog("NVR-1", ENTRIES)
check("3 rows listed", dlg.list.count() == 3, str(dlg.list.count()))
check("all checked by default",
      all(dlg.list.item(i).checkState() == Qt.CheckState.Checked
          for i in range(3)))
texts = [dlg.list.item(i).text() for i in range(3)]
check("direct row mentions camera ip",
      "192.168.1.64" in texts[0] and "مستقیم" in texts[0], texts[0])
check("via-nvr row mentions nvr",
      "از طریق NVR" in texts[1], texts[1])

# ============================================ ۲) selected() ==
check("selected default = all", dlg.selected() == [tuple(e) for e in ENTRIES],
      str(dlg.selected()))
dlg.list.item(1).setCheckState(Qt.CheckState.Unchecked)
sel = dlg.selected()
check("uncheck removes entry",
      sel == [(1, "دوربین حیاط", "192.168.1.64"),
              (3, "دوربین لابی", "192.168.1.66")], str(sel))

# ============================================ ۳) انتخاب همه / حذف ==
dlg._set_all(Qt.CheckState.Unchecked)
check("clear all", dlg.selected() == [])
dlg._set_all(Qt.CheckState.Checked)
check("select all", len(dlg.selected()) == 3)

# ============================================ ۴) رکورد خالی ==
dlg2 = NVRChannelSelectDialog("NVR-2", [])
check("empty entries ok", dlg2.list.count() == 0 and dlg2.selected() == [])

# ============================================ ۵) اتصال در main ==
# _on_nvr_channels_fetched باید از دیالوگ استفاده کند نه QMessageBox.question
# (main.py سنگین است و مستقیم ایمپورت نمی‌شود؛ سورس به‌صورت متنی بررسی می‌شود)
with open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "main.py"), encoding="utf-8") as f:
    msrc = f.read()
start = msrc.index("def _on_nvr_channels_fetched")
end = msrc.index("def _on_direct_probe_finished")
src = msrc[start:end]
check("handler uses select dialog", "NVRChannelSelectDialog" in src)
check("handler uses chosen entries",
      "chosen" in src and "dlg.selected()" in src)
check("no all-or-nothing question",
      "QMessageBox.question" not in src)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

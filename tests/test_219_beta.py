"""Headless regression test for IAS-CMS 2.0.19-beta.

۱) صفحه‌ی تنظیمات: جهت RTL، ردیف‌های صداهای هشدار (چک‌باکس بدون متن +
   لیبل wrapشونده‌ی قابل‌کلیک)، چک‌باکس امنیت رمزها با همان الگو.
۲) پنل نقشه: جهت RTL پنل چپ، دکمه‌های ابزار مسیر (شبیه‌سازی/پوشش/هیت‌مپ)
   به‌صورت عمودی (نه ردیف افقی ۳تایی که عرض پنل را می‌شکست).
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


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


from PyQt6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt
from unittest.mock import MagicMock

app = QApplication(sys.argv)

# ---------- ۱) صفحه‌ی تنظیمات ----------
from settings_page import SettingsPage

pg = SettingsPage()
check("settings page is RTL",
      pg.layoutDirection() == Qt.LayoutDirection.RightToLeft)

# سه کلید صدا هنوز در _sound_checks هستند
check("sound checks has 3 keys",
      set(pg._sound_checks.keys()) == {"fire", "zone", "violation"})

# هر ردیف: چک‌باکس بدون متن + لیبل جدا با متن کامل و wrap
expected_labels = {"🔥 آژیر حریق", "🚧 بوق ورود به محدوده", "🚨 بوق تخلف طبقاتی"}
found_labels = {w.text() for w in pg.findChildren(QLabel)
                if w.text() in expected_labels}
rows_ok = True
for key, chk in pg._sound_checks.items():
    if not isinstance(chk, QCheckBox) or chk.text() != "":
        rows_ok = False
        continue
    # هر چک‌باکس باید هم‌ردیف یکی از لیبل‌های wrapشونده باشد
    row_lay = None
    par = chk.parentWidget()
    for lay in par.findChildren(QHBoxLayout):
        for i in range(lay.count()):
            if lay.itemAt(i).widget() is chk:
                row_lay = lay
    if row_lay is None:
        rows_ok = False
        continue
    row_labels = [row_lay.itemAt(i).widget() for i in range(row_lay.count())]
    row_labels = [w for w in row_labels
                  if isinstance(w, QLabel) and w.text() in expected_labels]
    if not row_labels or not row_labels[0].wordWrap():
        rows_ok = False
check("sound rows: indicator-only checkbox + wrapped label", rows_ok)
check("sound labels text intact", found_labels == expected_labels)

# کلیک روی لیبل، چک‌باکس را تاگل می‌کند
chk0 = pg._sound_checks["fire"]
before = chk0.isChecked()
parent = chk0.parentWidget()
lbl0 = next(w for w in parent.findChildren(QLabel)
            if w.text() == "🔥 آژیر حریق")
lbl0.mousePressEvent(None)
check("label click toggles checkbox", chk0.isChecked() == (not before))

# چک‌باکس امنیت رمزها هم همین الگو را دارد (بدون متن روی خود چک‌باکس)
check("savepw checkbox indicator-only", pg.savepw_check.text() == "")

# ---------- ۲) پنل نقشه ----------
import building_map_dialog as bmd

page = bmd.BuildingMapPage(camera_store=MagicMock())
from PyQt6.QtWidgets import QScrollArea
scrolls = page.findChildren(QScrollArea)
check("map has left scroll panel", len(scrolls) >= 1)
inner = scrolls[0].widget()
check("map left panel is RTL",
      inner.layoutDirection() == Qt.LayoutDirection.RightToLeft)

# هر سه دکمه‌ی ابزار مسیر باید داخل یک QVBoxLayout باشند (عمودی)
for attr, text in (("lane_sim_btn", "▶️ شبیه‌سازی"),
                   ("coverage_btn", "📡 پوشش"),
                   ("heatmap_btn", "🔥 هیت‌مپ")):
    btn = getattr(page, attr, None)
    check(f"map tool button {attr} exists with text",
          btn is not None and btn.text() == text)
btns = {page.lane_sim_btn, page.coverage_btn, page.heatmap_btn}
stacked = False
for lay in page.findChildren(QVBoxLayout):
    widgets = {lay.itemAt(i).widget()
               for i in range(lay.count())} - {None}
    if btns <= widgets:
        stacked = True
        break
check("tool buttons stacked vertically", stacked)

print(f"\n==== {len(passed)} passed, {len(failed)} failed ====")
sys.exit(1 if failed else 0)

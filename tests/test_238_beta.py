"""Headless regression test for IAS-CMS 2.0.38-beta.

۱) نصب‌کننده دوزبانه: welcome/header با لوگوی برنامه، MUI_HEADERIMAGE،
   زبان‌های English + Farsi (بدون Persian)، MUI_LANGDLL_DISPLAY در .onInit،
   LangStringهای سفارشی برای هر دو زبان، و تولید BMPها در make_graphics.py.
۲) تاگل پوشش: coverage_btn checkable است؛ _on_coverage_toggled(False)
   تحلیل را متوقف و نقاط را پاک می‌کند؛ _analyze_coverage بدون مسیر False
   برمی‌گرداند و دکمه unchecked می‌ماند.
۳) _clear_geo_tag با floor_id فقط همان طبقه را می‌گردد (بهینه‌سازی کندی).
۴) رسم محدوده: با هر کلیکِ نقطه، draw_progress_changed/tripwire_changed
   شلیک می‌شود؛ has_draw_points و cancel_draw_points کار می‌کنند؛ بعد از
   بستن چندضلعی has_pending_region برقرار است.
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

passed = []
failed = []


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox, QGraphicsEllipseItem
from PyQt6.QtCore import QPointF, Qt, QEvent
from PyQt6.QtGui import QMouseEvent, QPixmap, QColor

app = QApplication(sys.argv)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================ ۱) نصب‌کننده ==
nsi = open(os.path.join(ROOT, "installer", "installer.nsi"),
           encoding="utf-8-sig").read()
gfx = os.path.join(ROOT, "installer", "graphics")

from PIL import Image
w = Image.open(os.path.join(gfx, "welcome_logo.bmp"))
check("welcome_logo.bmp is 164x314", w.size == (164, 314))
h = Image.open(os.path.join(gfx, "header_logo.bmp"))
check("header_logo.bmp is 150x57", h.size == (150, 57))
check("WELCOMEFINISHPAGE_BITMAP uses logo",
      "MUI_WELCOMEFINISHPAGE_BITMAP" in nsi and "welcome_logo.bmp" in nsi)
check("HEADERIMAGE defined", "!define MUI_HEADERIMAGE" in nsi)
check("HEADERIMAGE_BITMAP uses logo",
      "MUI_HEADERIMAGE_BITMAP" in nsi and "header_logo.bmp" in nsi)
check("English language present", '!insertmacro MUI_LANGUAGE "English"' in nsi)
check("Farsi language present", '!insertmacro MUI_LANGUAGE "Farsi"' in nsi)
check("no Persian language", '"Persian"' not in nsi)
check("MUI_LANGDLL_DISPLAY in installer .onInit",
      "MUI_LANGDLL_DISPLAY" in nsi and "Function .onInit" in nsi)
for ls in ["SEC_INSTALL", "RUN_TEXT", "APP_RUNNING_MSG", "UNINSTALL_CONFIRM"]:
    check(f"LangString {ls} EN+FA",
          f"LangString {ls} ${{LANG_ENGLISH}}" in nsi
          and f"LangString {ls} ${{LANG_Farsi}}" in nsi)
check("FINISHPAGE_RUN_TEXT uses LangString",
      '!define MUI_FINISHPAGE_RUN_TEXT "$(RUN_TEXT)"' in nsi)
check('Section uses LangString', 'Section "$(SEC_INSTALL)"' in nsi)
check("DirLeave uses LangString", '"$(APP_RUNNING_MSG)"' in nsi)
check("un.onInit uses LangString", '"$(UNINSTALL_CONFIRM)"' in nsi)
mg = open(os.path.join(ROOT, "installer", "make_graphics.py"),
          encoding="utf-8").read()
check("make_graphics generates logo bmps",
      "welcome_logo.bmp" in mg and "header_logo.bmp" in mg)

# ============================================ ۲) تاگل پوشش ==
import building_map_dialog as bmd

# QMessageBoxها در headless بلاک می‌کنند؛ خنثی‌شان می‌کنیم
QMessageBox.information = lambda *a, **k: None
QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.No

page = bmd.BuildingMapPage(camera_store=MagicMock())
check("coverage_btn is checkable", page.coverage_btn.isCheckable())
page._coverage_live = {"floor_id": "f1"}
page.current_floor = "f1"
sc = QGraphicsScene()
page.scenes["f1"] = {"scene": sc, "to_meter": 1.0, "items": {}}
dot = QGraphicsEllipseItem(-5, -5, 10, 10)
dot.setData(0, "coverage-geo")
sc.addItem(dot)
page.coverage_btn.setChecked(True)
page._on_coverage_toggled(False)
check("toggle off clears _coverage_live", page._coverage_live is None)
check("toggle off unchecks button", not page.coverage_btn.isChecked())
check("toggle off removes coverage dots",
      not [it for it in sc.items() if it.data(0) == "coverage-geo"])
check("toggle off writes report",
      "خاموش" in page.coverage_report.toPlainText())

# _analyze_coverage بدون طبقه/مسیر -> False و دکمه unchecked می‌ماند
page2 = bmd.BuildingMapPage(camera_store=MagicMock())
page2.current_floor = ""
check("_analyze_coverage without floor -> False",
      page2._analyze_coverage() is False)
page2.current_floor = "f1"
bmd.plate_store = MagicMock()
bmd.plate_store.get_lanes.return_value = {}
check("_analyze_coverage without lanes -> False",
      page2._analyze_coverage() is False)
page2.coverage_btn.setChecked(True)
page2._on_coverage_toggled(True)   # تلاش برای روشن شدن بدون مسیر
check("failed toggle-on unchecks button",
      not page2.coverage_btn.isChecked())

# ============================================ ۳) clear_geo_tag محدود به طبقه ==
page3 = bmd.BuildingMapPage(camera_store=MagicMock())
sc1, sc2 = QGraphicsScene(), QGraphicsScene()
page3.scenes["f1"] = {"scene": sc1, "to_meter": 1.0, "items": {}}
page3.scenes["f2"] = {"scene": sc2, "to_meter": 1.0, "items": {}}
d1 = QGraphicsEllipseItem(0, 0, 5, 5); d1.setData(0, "coverage-geo"); sc1.addItem(d1)
d2 = QGraphicsEllipseItem(0, 0, 5, 5); d2.setData(0, "coverage-geo"); sc2.addItem(d2)
page3._clear_geo_tag("coverage-geo", "f1")
check("floor-scoped clear removes f1 dot",
      not [it for it in sc1.items() if it.data(0) == "coverage-geo"])
check("floor-scoped clear keeps f2 dot",
      any(it.data(0) == "coverage-geo" for it in sc2.items()))

# ============================================ ۴) رسم محدوده ==
import main as M

slot = M.CameraSlotWidget(lambda *a: None, lambda *a: None, lambda *a: None)
slot.resize(640, 480)
slot.show()
app.processEvents()
lbl = slot.video_label
pm = QPixmap(640, 480)
pm.fill(QColor("black"))
lbl.setPixmap(pm)
app.processEvents()
lbl.set_draw_mode(True)

fired = []
slot.tripwire_changed.connect(lambda: fired.append(1))

def click(x, y):
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, y),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    lbl.mousePressEvent(ev)
    app.processEvents()

click(100, 100)
check("has_draw_points after first click", slot.has_draw_points())
check("tripwire_changed fired on point add", len(fired) == 1)
click(300, 100)
click(300, 300)
check("three draw points", len(lbl._draw_points) == 3)
slot.cancel_draw_points()
app.processEvents()
check("cancel_draw_points clears", not slot.has_draw_points())

click(100, 100); click(300, 100); click(300, 300); click(100, 300)
ev2 = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(200, 200),
                  Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                  Qt.KeyboardModifier.NoModifier)
lbl.mouseDoubleClickEvent(ev2)
app.processEvents()
check("pending region after close", slot.has_pending_region())
check("tripwire_changed fired on close", len(fired) >= 4)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

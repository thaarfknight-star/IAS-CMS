"""Headless regression test for IAS-CMS 2.0.10-beta (PyQt6 offscreen).

۱) برگشت سیستم «رسم محدوده و ورود به محدوده» به نسخه‌ی اصلی:
   - محدوده‌ها همیشه مرئی‌اند: دکمه‌ی «دیدن محدوده‌ها» (👁) حذف شده؛
     VideoDisplayLabel.set_regions_visible دیگر وجود ندارد.
   - «قانون ورود به محدوده» حذف شده: region_access.py نیست؛
     person_store دیگر جدول/متد محدوده ندارد؛
     تب «🚨 کنترل تردد محدوده‌ها» (ردیابی اشخاص) و
     تب «🚨 تخلفات محدوده‌ها» (گزارش‌ها) نیستند؛
     MainWindow._check_person_region_access نیست.
   - سیگنال region_entered دوباره (number, name) است؛
     _PersonRegionTracker.update دیگر face_results نمی‌گیرد و
     خروجی‌اش لیستی از (number, name) است.
۲) قفل اندازه‌ی کادر دوربین‌ها:
   - اندازه‌ی هر کادر فقط با set_grid_size (تغییر «تعداد نمایش») عوض می‌شود؛
   - toggle_sidebar رفت‌وبرگشت و toggle_fullscreen اندازه‌ی کادرها را
     عوض نمی‌کنند؛
   - دابل‌کلیک (toggle_maximize) رفت‌وبرگشت، اندازه‌ی قفل‌شده را برمی‌گرداند.
"""
import os
import sys
import time
import types
import inspect
import importlib.util

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_locations = lambda *a, **k: []
fr.face_encodings = lambda *a, **k: []
fr.face_distance = lambda *a, **k: []
fr.compare_faces = lambda *a, **k: []
sys.modules["face_recognition"] = fr

import numpy as np

passed = []
failed = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))


sys.argv = ["test"]
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

# ---------- ۱-الف) زنجیره‌ی region ساده ----------
import camera_stream as cs

md = cs._MotionRegionDetector()
h, w = 480, 640
bg = np.zeros((h, w, 3), dtype=np.uint8)
for _ in range(5):
    md.detect(bg)
    md._last_run = 0
frame = bg.copy()
frame[150:350, 280:360] = 255
md._last_run = 0
boxes = md.detect(frame)

tracker = cs._PersonRegionTracker()
regions = [{"id": "r1", "number": 1, "name": "در",
            "points": [(0.4, 0.3), (0.6, 0.3), (0.6, 0.6), (0.4, 0.6)]}]
# دیگر face_results نمی‌گیرد
sig = inspect.signature(tracker.update)
check("tracker.update has no face_results",
      "face_results" not in sig.parameters, str(list(sig.parameters)))
events = tracker.update(boxes, regions, w, h)
check("region tracker fires", len(events) >= 1, f"events={events}")
check("tracker events are (number, name) pairs",
      all(isinstance(e, tuple) and len(e) == 2 for e in events),
      f"events={events}")
if events:
    check("tracker event values", events[0] == (1, "در"), f"events={events}")
check("no _match_face_to_body", not hasattr(cs, "_match_face_to_body"))

import main as m

check("_on_region_entered signature",
      list(inspect.signature(m.CameraSlotWidget._on_region_entered).parameters) == ["self", "number", "name"])
check("on_region_alert signature",
      list(inspect.signature(m.MainWindow.on_region_alert).parameters) == ["self", "cam", "number", "name"])
check("no _check_person_region_access", not hasattr(m.MainWindow, "_check_person_region_access"))
check("no region_access module", importlib.util.find_spec("region_access") is None)

# ---------- ۱-ب) حذف قوانین ورود به محدوده ----------
from person_store import PersonStore
for meth in ("get_person_denied_regions", "set_person_denied_regions",
             "get_work_group_denied_regions", "set_work_group_denied_regions",
             "list_work_group_region_rules", "record_region_violation",
             "list_region_violations", "acknowledge_region_violation",
             "count_unacked_region_violations"):
    check(f"person_store has no {meth}", not hasattr(PersonStore, meth))

# ---------- main window ----------
win = m.MainWindow()
win.resize(1600, 900)
win.show()
app.processEvents()
app.processEvents()  # singleShot اندازه‌گیری کادرها

# ---------- ۱-ج) حذف سیستم مخفی‌بودن محدوده‌ها ----------
slot0 = win.camera_grid.slots[0]
check("no view_regions_btn on slot", not hasattr(slot0, "view_regions_btn"))
check("no set_regions_visible on video label",
      not hasattr(slot0.video_label, "set_regions_visible"))
check("no _regions_visible on video label",
      not hasattr(slot0.video_label, "_regions_visible"))

# تب‌های حذف‌شده در دیالوگ‌ها
from person_track_dialog import PersonTrackPage
ptd = PersonTrackPage(win.camera_store)
tab_texts = [ptd.tabs.tabText(i) for i in range(ptd.tabs.count())]
check("no region access tab in person dialog",
      not any("محدوده" in t for t in tab_texts), str(tab_texts))
check("floor access tab still exists",
      any("طبقاتی" in t for t in tab_texts), str(tab_texts))

from reports_dialog import ReportsPage
from report_store import report_store
rp = ReportsPage(report_store, win.camera_store)
rtab_texts = [rp.tabs.tabText(i) for i in range(rp.tabs.count())]
check("no region violations tab in reports",
      not any("محدوده" in t for t in rtab_texts), str(rtab_texts))
check("floor violations tab still exists",
      any("طبقاتی" in t for t in rtab_texts), str(rtab_texts))
check("events tab still exists (region alerts logged there)",
      any("رویدادها" in t for t in rtab_texts), str(rtab_texts))

# ---------- ۲) قفل اندازه‌ی کادرها ----------
grid = win.camera_grid
frozen = grid.slots[0].size()
check("tiles have measured size", frozen.width() > 100 and frozen.height() > 60,
      f"{frozen.width()}x{frozen.height()}")
check("tile size locked in grid", grid._tile_size == (frozen.width(), frozen.height()),
      str(grid._tile_size))
check("all tiles same fixed size",
      all(s.size() == frozen and s.minimumSize() == s.maximumSize() for s in grid.slots))

# toggle_sidebar رفت‌وبرگشت نباید اندازه‌ی کادرها را عوض کند
win.toggle_sidebar()
app.processEvents()
win.toggle_sidebar()
app.processEvents()
check("tile size frozen across sidebar toggle",
      all(s.size() == frozen for s in grid.slots),
      str([f"{s.size().width()}x{s.size().height()}" for s in grid.slots]))

# toggle_fullscreen نباید اندازه‌ی کادرها را عوض کند
win.toggle_fullscreen()
app.processEvents()
win.toggle_fullscreen()
app.processEvents()
check("tile size frozen across fullscreen toggle",
      all(s.size() == frozen for s in grid.slots))

# تغییر تعداد نمایش -> اندازه عوض می‌شود
grid.set_grid_size(16)
app.processEvents()
s16 = grid.slots[0].size()
check("tile size changes on grid count change", s16 != frozen,
      f"4x4={frozen.width()}x{frozen.height()} 16x16? -> {s16.width()}x{s16.height()}")
check("16-grid tiles smaller", s16.width() < frozen.width())
grid.set_grid_size(4)
app.processEvents()
s4 = grid.slots[0].size()
check("4-grid tiles bigger than 16-grid", s4.width() > s16.width(),
      f"{s4.width()} vs {s16.width()}")

# دابل‌کلیک (maximize) رفت‌وبرگشت -> اندازه‌ی قفل‌شده برمی‌گردد
frozen4 = grid.slots[0].size()
grid.toggle_maximize(0)
app.processEvents()
big = grid.slots[0].size()
check("maximized tile bigger", big.width() >= frozen4.width(),
      f"{frozen4.width()} -> {big.width()}")
grid.toggle_maximize(0)
app.processEvents()
check("un-maximize restores frozen tile size",
      all(s.size() == frozen4 for s in grid.slots),
      str(grid.slots[0].size()))

# ---------- ۳) رگرسیون باگ «سیستم محدوده کار نمی‌کند» ----------
# هشدار ورود به محدوده باید مستقل از سلامت تشخیص چهره کار کند: اگر
# face_engine.recognize خطا بدهد (مثل exe که کتابخانه‌ی چهره‌اش خراب است)،
# بلوک محدوده (که قبل از تشخیص چهره اجرا می‌شود) نباید تحت تأثیر قرار بگیرد.
class _BrokenFaceEngine:
    def recognize(self, frame):
        raise RuntimeError("simulated broken face lib")

    def save_unknown_face(self, crop):
        pass

    def draw_results(self, frame, results):
        pass


from PyQt6.QtCore import Qt as _Qt
_Direct = _Qt.ConnectionType.DirectConnection

_bg = np.full((240, 320, 3), 40, dtype=np.uint8)
_dbg_frames = []
for _i in range(12):
    _f = _bg.copy()
    if _i >= 4:
        _x = 40 + (_i - 4) * 25
        _f[70:170, _x:_x + 50] = 255  # جسم متحرک که وارد محدوده می‌شود
    _dbg_frames.append(_f)

_broken_events = []
_bt = cs.CameraStreamThread("dummy", _BrokenFaceEngine(), process_every_n=5)
_bt.region_entered.connect(
    lambda number, name: _broken_events.append((number, name)), _Direct)
_bt.set_regions([{"id": "r1", "number": 1, "name": "در",
                  "points": [(0.3, 0.2), (0.7, 0.2), (0.7, 0.8), (0.3, 0.8)]}])
for _f in _dbg_frames:
    _bt._run_recognition(_f)
    time.sleep(0.6)  # عبور از cooldown دیتکتور حرکت + شبیه‌سازی تیک واقعی
check("region alert fires despite broken face engine",
      len(_broken_events) >= 1, f"events={_broken_events}")
if _broken_events:
    check("broken-face region event values",
          _broken_events[0] == (1, "در"), f"events={_broken_events}")

# ---------- جمع‌بندی ----------
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)

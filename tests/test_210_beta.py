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
   - toggle_sidebar رفت‌وبرگشت اندازه‌ی کادرها را عوض نمی‌کند؛
     (دکمه‌ی تمام‌صفحه در 2.0.14-beta کاملاً حذف شد)
   - دابل‌کلیک (toggle_maximize) رفت‌وبرگشت، اندازه‌ی قفل‌شده را برمی‌گرداند.
۳) (2.0.14-beta) ردیف رسم محدوده سطر دوم نوار ابزار است و ظاهر/مخفی شدنش
   اندازه‌ی پنجره و کادرها را عوض نمی‌کند؛ «تایید»/«لغو» فقط بعد از رسم
   شدن محدوده (در انتظار/ویرایش) دیده می‌شوند.
۴) (2.0.14-beta) ضد نور سخت‌افزاری (BLC) از طریق ONVIF: get/set_backlight و
   get_imaging_basics در onvif_imaging.py.
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

# ---------- ۲) کادرهای سیال: با فضای در دسترس بزرگ/کوچک می‌شوند ----------
grid = win.camera_grid
s0 = grid.slots[0].size()
check("tiles have real size", s0.width() > 100 and s0.height() > 60,
      f"{s0.width()}x{s0.height()}")
check("tiles are NOT fixed-size",
      all(s.minimumSize() != s.maximumSize() for s in grid.slots),
      str([(s.minimumSize().width(), s.maximumSize().width()) for s in grid.slots][:2]))

# کوچک شدن پنل تشخیص چهره (اسپلیتر) -> کادرها باید بزرگ‌تر شوند (درخواست ۱)
splitter = win.splitter
sizes = splitter.sizes()
face_idx = splitter.indexOf(win.right_splitter)
old_w = grid.slots[0].width()
# پنل راست را حسابی کوچک می‌کنیم؛ فضای آزادشده باید به شبکه‌ی دوربین‌ها برسد
new_sizes = list(sizes)
freed = min(220, max(0, new_sizes[face_idx] - 40))
new_sizes[face_idx] -= freed
splitter.setSizes(new_sizes)
app.processEvents(); app.processEvents()
grown_w = grid.slots[0].width()
check("tiles grow when face panel shrinks", grown_w > old_w,
      f"{old_w} -> {grown_w} (freed {freed}px from panel)")
# پنل نباید له شده باشد (صفر نشده باشد)
check("face panel not crushed to zero", splitter.sizes()[face_idx] > 0,
      str(splitter.sizes()))

# کوچک شدن پنجره -> کادرها کوچک می‌شوند و پنل راست له نمی‌شود (درخواست ۲)
win.resize(1100, 700)
app.processEvents(); app.processEvents()
small_w = grid.slots[0].width()
check("tiles shrink when window shrinks", small_w < grown_w,
      f"{grown_w} -> {small_w}")
check("face panel survives window shrink", splitter.sizes()[face_idx] > 0,
      str(splitter.sizes()))
# برگشت به اندازه‌ی اول -> کادرها دوباره بزرگ می‌شوند
win.resize(1600, 900)
splitter.setSizes(sizes)
app.processEvents(); app.processEvents()
back_w = grid.slots[0].width()
check("tiles grow back on restore", back_w > small_w, f"{small_w} -> {back_w}")

# تغییر تعداد نمایش -> اندازه عوض می‌شود
grid.set_grid_size(16)
app.processEvents()
s16 = grid.slots[0].size()
check("tile size changes on grid count change", s16.width() != s0.width(),
      f"4-grid={s0.width()}x{s0.height()} 16-grid={s16.width()}x{s16.height()}")
check("16-grid tiles smaller", s16.width() < s0.width())
grid.set_grid_size(4)
app.processEvents()
s4 = grid.slots[0].size()
check("4-grid tiles bigger than 16-grid", s4.width() > s16.width(),
      f"{s4.width()} vs {s16.width()}")

# دابل‌کلیک (maximize) رفت‌وبرگشت -> چیدمان شبکه‌ای برمی‌گردد
base4 = grid.slots[0].size()
grid.toggle_maximize(0)
app.processEvents()
big = grid.slots[0].size()
check("maximized tile bigger", big.width() >= base4.width(),
      f"{base4.width()} -> {big.width()}")
check("others hidden when maximized",
      all(not s.isVisible() for i, s in enumerate(grid.slots) if i != 0))
grid.toggle_maximize(0)
app.processEvents()
check("un-maximize restores grid layout",
      all(s.isVisible() for s in grid.slots))
restored = grid.slots[0].size()
check("un-maximize restores tile size (fluid)",
      abs(restored.width() - base4.width()) <= 4,
      f"{base4.width()} -> {restored.width()}")

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

# ---------- ضد نور سخت‌افزاری (BLC) در onvif_imaging.py (2.0.14-beta) ----------
# با سرویس ONVIF ساختگی (بدون شبکه): خواندن/نوشتن BLC و خواندن یکجای
# WDR+BLC، و سازگاری API قدیمی WDR.
try:
    import onvif_imaging as oi
except Exception as e:
    check("onvif_imaging importable", False, str(e))
    oi = None

if oi is not None:
    class _FakeImaging:
        def __init__(self):
            self.settings = {
                "BacklightCompensation": {"Mode": "OFF"},
                "WideDynamicRange": {"Mode": "ON", "Level": 0.5},
            }
            self.options = {
                "BacklightCompensation": {"Mode": ["OFF", "ON"],
                                          "Level": {"Min": 0.0, "Max": 1.0}},
                "WideDynamicRange": {"Mode": ["OFF", "ON"],
                                     "Level": {"Min": 0.0, "Max": 1.0}},
            }
            self.set_calls = []

        def GetImagingSettings(self, req):
            assert req["VideoSourceToken"] == "vs0"
            return dict(self.settings)

        def GetOptions(self, req):
            return dict(self.options)

        def SetImagingSettings(self, req):
            self.set_calls.append(req)
            self.settings = dict(req["ImagingSettings"])

    _fake = _FakeImaging()
    _orig_ports = oi._try_ports
    _orig_pick = oi._pick_video_source
    _orig_avail = oi.is_available
    oi._try_ports = lambda target: (None, _fake, object(), 80, None)
    oi._pick_video_source = lambda media, ch: ("vs0", "")
    oi.is_available = lambda: (True, "ok")
    _cam = {"ip": "192.168.1.50", "user": "admin", "pass": "x"}
    try:
        mode, level = oi._extract_param(
            {"BacklightCompensation": {"Mode": "ON", "Level": 0.7}},
            "BacklightCompensation")
        check("blc extract mode/level", mode == "ON" and abs(level - 0.7) < 1e-9,
              f"{mode},{level}")
        modes, lo, hi = oi._extract_param_options(
            _fake.options, "BacklightCompensation")
        check("blc options parsed",
              modes == ["OFF", "ON"] and lo == 0.0 and hi == 1.0)
        lvl, rng = oi._normalize_level_0_100(0.7, 0.0, 1.0)
        check("blc level normalize 0.7->70", lvl == 70 and rng == (0.0, 1.0))
        r = oi.get_backlight(_cam)
        check("get_backlight ok", r.get("ok") and r.get("supported"),
              str(r)[:100])
        check("get_backlight mode OFF", r.get("mode") == "OFF", r.get("mode"))
        r = oi.get_imaging_basics(_cam)
        check("get_imaging_basics ok", r.get("ok"), str(r)[:100])
        check("basics carries wdr+blc",
              r.get("wdr", {}).get("mode") == "ON"
              and r.get("blc", {}).get("mode") == "OFF"
              and r.get("wdr", {}).get("level") == 50,
              f"wdr={r.get('wdr', {}).get('mode')} blc={r.get('blc', {}).get('mode')}")
        r = oi.set_backlight(_cam, "ON", 80)
        sent = _fake.settings["BacklightCompensation"]
        check("set_backlight ok", r.get("ok") and r.get("mode") == "ON",
              str(r)[:100])
        check("set_backlight level mapped to 0.8",
              abs(sent["Level"] - 0.8) < 1e-9 and sent["Mode"] == "ON", str(sent))
        check("set_backlight ForcePersistence",
              _fake.set_calls[-1].get("ForcePersistence") is True)
        check("set_backlight keeps wdr untouched",
              _fake.settings["WideDynamicRange"]["Mode"] == "ON")
        r = oi.set_backlight(_cam, "MAYBE")
        check("set_backlight rejects invalid mode", not r.get("ok"))
        r = oi.get_wdr(_cam)
        check("get_wdr still ok", r.get("ok") and r.get("mode") == "ON"
              and r.get("level") == 50, str(r)[:100])
        r = oi.set_wdr(_cam, "OFF")
        check("set_wdr still ok",
              r.get("ok") and _fake.settings["WideDynamicRange"]["Mode"] == "OFF")
    finally:
        oi._try_ports = _orig_ports
        oi._pick_video_source = _orig_pick
        oi.is_available = _orig_avail

# ---------- جمع‌بندی ----------
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
"""Headless regression test for IAS-CMS 2.0.21-beta.

پوشش زنده‌ی دوربین روی نقاط مسیر (به دستور کاربر):
۱) lane_coverage: نمونه‌برداری نقاط مسیر، آزمون قطاع دید، تحلیل پوشش.
۲) فشرده‌سازی بازه‌های متراژ در گزارش.
۳) دیالوگ نقشه (آفسکرین): زدن «پوشش» گزارش می‌نویسد + با جابه‌جایی
   دوربین گزارش «در لحظه» به‌روز می‌شود (بدون زدن دوباره‌ی دکمه).
۴) رگرسیون: lane_geometry بدون تغییر.
"""
import os
import sys
import tempfile
import types
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_locations = lambda *a, **k: []
fr.face_encodings = lambda *a, **k: []
fr.face_distance = lambda *a, **k: []
fr.compare_faces = lambda *a, **k: []
sys.modules["face_recognition"] = fr

_cv2 = types.ModuleType("cv2")
_cv2.resize = lambda *a, **k: a[0] if a else None
_cv2.imwrite = lambda *a, **k: True
sys.modules["cv2"] = _cv2

passed = []
failed = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" [{extra}]" if extra and not cond else ""))


sys.argv = ["test"]
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

TMP = tempfile.mkdtemp(prefix="ias221_")

import app_settings
app_settings._SETTINGS_PATH = os.path.join(TMP, "settings.json")

# ============================================ ۱) lane_coverage خالص ==
from lane_coverage import (DEFAULT_SAMPLE_STEP_M, analyze_lane_coverage,
                           point_in_sector, sample_lane_points)

pts = sample_lane_points([(0, 0), (10, 0)], to_meter=1.0, step_m=2.0)
check("sample count", len(pts) == 6, str(len(pts)))
check("sample s_m", [p["s_m"] for p in pts] == [0, 2.0, 4.0, 6.0, 8.0, 10.0])
check("sample coords", abs(pts[3]["x"] - 6.0) < 1e-9 and
      abs(pts[3]["y"]) < 1e-9)
check("sample empty", sample_lane_points([(1, 1)], 1.0) == [])
check("sample step default", DEFAULT_SAMPLE_STEP_M == 2.0)
# مسیر ۵ متری با گام ۲: نقاط ۰، ۲، ۴ + پایان ۵
pts = sample_lane_points([(0, 0), (5, 0)], to_meter=1.0, step_m=2.0)
check("sample end point", [p["s_m"] for p in pts] == [0, 2.0, 4.0, 5.0])

# قطاع دید: دوربین در مبدأ، زاویه‌ی ۰ (شرق)، fov=۹۰، برد ۱۰
check("sector east", point_in_sector(5, 0, 0, 0, 0, 90, 10))
check("sector ne", point_in_sector(5, -5, 0, 0, 0, 90, 10))
check("sector west no", not point_in_sector(-5, 0, 0, 0, 0, 90, 10))
check("sector out of range", not point_in_sector(11, 0, 0, 0, 0, 90, 10))
check("sector on camera", point_in_sector(0, 0, 0, 0, 0, 90, 10))
# زاویه‌ی ۹۰ = شمال (y صحنه وارونه: شمال = y منفی)
check("sector north", point_in_sector(0, -5, 0, 0, 90, 90, 10))
check("sector south no", not point_in_sector(0, 5, 0, 0, 90, 90, 10))
# لبه‌ی قطاع: دقیقاً ۴۵ درجه
import math as _m
ex, ey = 8 * _m.cos(_m.radians(45)), -8 * _m.sin(_m.radians(45))
check("sector edge", point_in_sector(ex, ey, 0, 0, 0, 90, 10))
ex2, ey2 = 8 * _m.cos(_m.radians(46)), -8 * _m.sin(_m.radians(46))
check("sector outside edge", not point_in_sector(ex2, ey2, 0, 0, 0, 90, 10))

lanes = [{"name": "مسیر تست", "points": [[0, 0], [20, 0]], "to_meter": 1.0}]
cams = [{"name": "دوربین ۱", "x": 5, "y": 0, "angle": 0,
         "fov": 90, "view_distance": 8.0}]
res = analyze_lane_coverage(lanes, cams, to_meter=1.0, step_m=2.0)
lr = res["lanes"][0]
check("analyze 11 points", len(lr["points"]) == 11)
unc = sorted(p["s_m"] for p in lr["uncovered"])
check("analyze uncovered", unc == [0, 2.0, 4.0, 14.0, 16.0, 18.0, 20.0],
      str(unc))
cov = res["cameras"][0]["covered"]
check("analyze cam mapping", sorted(c["s_m"] for c in cov) ==
      [6.0, 8.0, 10.0, 12.0], str(sorted(c["s_m"] for c in cov)))
check("analyze covered_by", lr["points"][3]["covered_by"] == ["دوربین ۱"])
check("analyze empty cams", analyze_lane_coverage(
    lanes, [], to_meter=1.0)["lanes"][0]["uncovered"] != [])

# ============================================ ۲) فشرده‌سازی بازه ==
from building_map_dialog import BuildingMapPage
check("compact ranges", BuildingMapPage._compact_ranges(
    [0, 2.0, 4.0, 10.0]) == "0–4، 10م")
check("compact single", BuildingMapPage._compact_ranges([6.0]) == "6م")
check("compact empty", BuildingMapPage._compact_ranges([]) == "—")

# ============================================ ۳) دیالوگ + زنده ==
from building_map import MapStore
import building_map_dialog as bmd

store = MapStore(os.path.join(TMP, "maps.json"))
fl = store.add_floor("طبقه‌ی تست")
fid = fl["id"]
dev = store.add_device(fid, "camera", "دوربین ۱", 5, 0,
                       angle=0.0, fov=90.0, view_distance=8.0)

fake_lanes = {"lane1": {"name": "مسیر تست", "floor_id": fid,
                        "points": [[0, 0], [20, 0]], "to_meter": 1.0,
                        "cameras": []}}

cam_store_stub = types.SimpleNamespace()
page = bmd.BuildingMapPage(camera_store=cam_store_stub)
page.store = store  # ایزوله از maps_data واقعی
page.current_floor = fid

with mock.patch.object(bmd.plate_store, "get_lanes",
                       return_value=fake_lanes):
    page._analyze_coverage()
    check("coverage live on", page._coverage_live is not None and
          page._coverage_live.get("floor_id") == fid)
    txt = page.coverage_report.toPlainText()
    check("report lane line", "«مسیر تست»" in txt and "⛔ 7 بدون پوشش" in txt,
          txt.splitlines()[2] if txt else "")
    check("report uncovered spots", "14م" in txt and "20م" in txt)
    check("report cam mapping", "دوربین ۱" in txt and "«مسیر تست»" in txt)
    # نقطه‌ها روی صحنه رسم شده‌اند: ۱۱ نقطه
    sc = page.scenes[fid]["scene"]
    dots = [it for it in sc.items()
            if hasattr(it, "data") and it.data(0) == "coverage-geo"]
    check("dots drawn", len(dots) == 11, str(len(dots)))

    # جابه‌جایی دوربین -> گزارش باید در لحظه عوض شود (بدون زدن دوباره‌ی دکمه)
    before = page.coverage_report.toPlainText()
    page._on_device_moved(fid, dev["id"], 15.0, 0.0)
    after = page.coverage_report.toPlainText()
    check("live refresh on move", before != after)
    check("live uncovered count", "⛔ 8 بدون پوشش" in after,
          [l for l in after.splitlines() if "بدون پوشش" in l][:2])
    check("live cam ranges moved", "16–20م" in after, after)
    dots2 = [it for it in sc.items()
             if hasattr(it, "data") and it.data(0) == "coverage-geo"]
    check("dots redrawn", len(dots2) == 11, str(len(dots2)))

    # تعویض طبقه -> تحلیل ریست می‌شود
    page._coverage_live = {"floor_id": fid}
    page._activate_floor("nope-floor")
    check("floor change resets", page._coverage_live is None)

# ============================================ ۴) رگرسیون ==
from lane_geometry import polyline_length
check("lane_geometry intact", abs(polyline_length([(0, 0), (3, 4)]) - 5.0) < 1e-9)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

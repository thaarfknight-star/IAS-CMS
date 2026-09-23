"""Headless regression test for IAS-CMS 2.0.18-beta.

۱) lane_geometry: polyline_length، point_at_distance، camera_s_at_order.
۲) plate_store لیست تحت‌نظر: CRUD، نرمال‌سازی، ضدتکرار، اعتبارسنجی.
۳) plate_store آمار: lane_traffic_counts و crossing_stats.
۴) plate_direction: هوک لیست سیاه/سفید -> تخلف + بوق.
۵) map_coverage: analyze_coverage + fallback استخراج درها بدون ezdxf.
۶) رنگ هیت‌مپ: سبز -> زرد -> قرمز.
۷) اسموک آفسکرین: PlateStatsDialog، تب لیست تحت‌نظر.
(شبیه‌ساز مسیر در 2.0.33-beta به دستور کاربر حذف شد.)
"""
import os
import sys
import time
import types
import tempfile

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

TMP = tempfile.mkdtemp(prefix="ias218_")

import app_settings
app_settings._SETTINGS_PATH = os.path.join(TMP, "settings.json")

# ================================================= ۱) lane_geometry ==
from lane_geometry import (polyline_length, point_at_distance,
                           camera_s_at_order)

check("length empty", polyline_length([]) == 0.0)
check("length single", polyline_length([(1, 2)]) == 0.0)
check("length straight", abs(polyline_length([(0, 0), (3, 4)]) - 5.0) < 1e-9)
check("length L", abs(polyline_length([(0, 0), (3, 0), (3, 4)]) - 7.0) < 1e-9)

PTS = [(0, 0), (10, 0), (10, 10)]
x, y = point_at_distance(PTS, 0)
check("pad start", abs(x) < 1e-9 and abs(y) < 1e-9)
x, y = point_at_distance(PTS, 5)
check("pad mid-seg", abs(x - 5) < 1e-9 and abs(y) < 1e-9)
x, y = point_at_distance(PTS, 10)
check("pad joint", abs(x - 10) < 1e-9 and abs(y) < 1e-9)
x, y = point_at_distance(PTS, 15)
check("pad second-seg", abs(x - 10) < 1e-9 and abs(y - 5) < 1e-9)
x, y = point_at_distance(PTS, 20)
check("pad end", abs(x - 10) < 1e-9 and abs(y - 10) < 1e-9)
x, y = point_at_distance(PTS, 999)
check("pad clamp-high", abs(x - 10) < 1e-9 and abs(y - 10) < 1e-9)
x, y = point_at_distance(PTS, -5)
check("pad clamp-low", abs(x) < 1e-9 and abs(y) < 1e-9)
x, y = point_at_distance([], 3)
check("pad empty", (x, y) == (0.0, 0.0))

s = camera_s_at_order(PTS, 4, 0)
check("cam-s on path", s is not None and abs(s - 4.0) < 1e-9, str(s))
s = camera_s_at_order(PTS, 10, 6)
check("cam-s second seg", s is not None and abs(s - 16.0) < 1e-9, str(s))
s = camera_s_at_order([(0, 0)], 1, 1)
check("cam-s invalid path", s is None)

# ============================================ ۲) لیست تحت‌نظر ==
from plate_store import PlateStore, normalize_plate_text

db_path = os.path.join(TMP, "plates.db")
store = PlateStore(db_path=db_path)

wid = store.add_watchlist_entry("۱۲ب۳۴۵", "black", "سرقتی")
check("watch add black", bool(wid))
rows = store.list_watchlist()
check("watch list one", len(rows) == 1 and rows[0]["kind"] == "black")
check("watch normalized",
      rows[0]["plate_text"] == normalize_plate_text("۱۲ب۳۴۵"))
check("watch display fa", "ب" in (rows[0]["plate_display"] or "") and
      "12" in (rows[0]["plate_display"] or ""),
      rows[0]["plate_display"])
check("watch note", rows[0]["note"] == "سرقتی")

# افزودن دوباره‌ی همان پلاک: به‌روزرسانی یادداشت، نه ردیف جدید
store.add_watchlist_entry("۱۲ب۳۴۵", "black", "یادداشت جدید")
rows = store.list_watchlist(kind="black")
check("watch upsert", len(rows) == 1 and rows[0]["note"] == "یادداشت جدید")

# همان پلاک در لیست سفید جدا نگه داشته می‌شود
store.add_watchlist_entry("۱۲ب۳۴۵", "white")
check("watch both lists", store.count_watchlist() == 2)
check("watch count black", store.count_watchlist("black") == 1)

found = store.find_watchlist("۱۲ب۳۴۵")
check("watch find", len(found) == 2)
check("watch find miss", store.find_watchlist("۹۹ب۹۹۹") == [])

try:
    store.add_watchlist_entry("۱۱ب۱۱۱", "gray")
    check("watch bad kind raises", False)
except ValueError:
    check("watch bad kind raises", True)
try:
    store.add_watchlist_entry("", "black")
    check("watch empty raises", False)
except ValueError:
    check("watch empty raises", True)

check("watch remove",
      store.remove_watchlist_entry("۱۲ب۳۴۵", "black") is True)
check("watch remove gone", store.count_watchlist("black") == 0)
check("watch remove miss",
      store.remove_watchlist_entry("۱۲ب۳۴۵", "black") is False)
check("watch search",
      len(store.list_watchlist(search="۱۲ب۳۴۵")) == 1)

# labels
check("watch labels",
      store.WATCHLIST_LABELS.get("black") and store.WATCHLIST_LABELS.get("white"))
check("violation labels watch",
      "watchlist_black" in store.VIOLATION_LABELS and
      "watchlist_white" in store.VIOLATION_LABELS)

# ================================================== ۳) آمار تردد ==
store.set_lanes({
    "laneA": {"name": "مسیر A", "allowed": "going", "floor_id": "f1",
              "points": [[0, 0], [10, 0]], "to_meter": 1.0,
              "cameras": [{"camera_id": "c1", "order": 0}]},
    "laneB": {"name": "مسیر B", "allowed": "return", "floor_id": "f1",
              "points": [[0, 0], [10, 0]], "to_meter": 1.0,
              "cameras": [{"camera_id": "c2", "order": 0}]},
})
store.log_crossing("۱۱ب۱۱۱", camera_id="c1", camera_name="cam-c1",
                   lane_id="laneA", crossing_type="entry", travel="going")
store.log_crossing("۲۲ب۲۲۲", camera_id="c1", camera_name="cam-c1",
                   lane_id="laneA", crossing_type="entry", travel="going")
store.log_crossing("۳۳ب۳۳۳", camera_id="c2", camera_name="cam-c2",
                   lane_id="laneB", crossing_type="exit", travel="return")
store.log_crossing("۴۴ب۴۴۴", camera_id="c9", camera_name="cam-c9",
                   lane_id="", crossing_type="entry", travel="going")

counts = store.lane_traffic_counts()
check("traffic counts A", counts.get("laneA") == 2, str(counts))
check("traffic counts B", counts.get("laneB") == 1, str(counts))
check("traffic counts no-lane excluded", "c9" not in counts and "" not in counts)

rows = store.crossing_stats(lane_id="laneA")
check("stats lane filter", sum(c for _d, _h, c in rows) == 2, str(rows))
rows = store.crossing_stats(crossing_type="exit")
check("stats type filter", sum(c for _d, _h, c in rows) == 1)
rows = store.crossing_stats(date_from="2999-01-01")
check("stats future empty", rows == [])
rows = store.crossing_stats()
check("stats all", sum(c for _d, _h, c in rows) == 4)
check("stats hour fmt",
      all(len(h) == 2 and h.isdigit() for _d, h, _c in rows),
      str(rows[:2]))

# ==================================== ۴) هوک لیست تحت‌نظر در موتور ==
from plate_direction import PlateDirectionEngine

engine = PlateDirectionEngine(store)
beeps = []
engine.set_violation_beep(lambda: beeps.append(1))


def mkcam(cid, role, lane):
    return {"id": cid, "name": f"cam-{cid}", "plate_role": role,
            "lane_id": lane}


def fire(cam, plate):
    data = {"plate_text": plate, "plate_display": plate, "conf": 0.95,
            "crop": None}
    event = {"id": "ev_" + str(time.time_ns()), "plate_display": plate,
             "plate": None, "owner_name": "", "snapshot_path": ""}
    return engine.process(cam, data, event)


cam1 = mkcam("c1", "entry", "laneA")
store.add_watchlist_entry("۵۵ب۵۵۵", "black", "تحت تعقیب")
n0 = len(store.list_violations(search=normalize_plate_text("۵۵ب۵۵۵")))
res = fire(cam1, "۵۵ب۵۵۵")
vts = [r["violation_type"] for r in
       store.list_violations(search=normalize_plate_text("۵۵ب۵۵۵"))]
check("black -> violation", "watchlist_black" in vts, str(vts))
check("black -> beep", len(beeps) >= 1, str(len(beeps)))
check("black returns ids", res and len(res["violations"]) >= 1)

# ضدتکرار ۶۰ثانیه‌ای: عبور دوم همان پلاک، تخلف جدید نسازد
beeps.clear()
n1 = len(store.list_violations(search=normalize_plate_text("۵۵ب۵۵۵")))
fire(cam1, "۵۵ب۵۵۵")
n2 = len(store.list_violations(search=normalize_plate_text("۵۵ب۵۵۵")))
check("black dedupe 60s", n2 == n1, f"{n1}->{n2}")

store.add_watchlist_entry("۶۶ب۶۶۶", "white")
fire(cam1, "۶۶ب۶۶۶")
vts = [r["violation_type"] for r in
       store.list_violations(search=normalize_plate_text("۶۶ب۶۶۶"))]
check("white -> violation", "watchlist_white" in vts, str(vts))

# پلاک عادی: بدون تخلف تحت‌نظر
fire(cam1, "۷۷ب۷۷۷")
vts = [r["violation_type"] for r in
       store.list_violations(search=normalize_plate_text("۷۷ب۷۷۷"))]
check("normal plate clean",
      not any(v.startswith("watchlist") for v in vts), str(vts))

# ============================================ ۵) map_coverage ==
from map_coverage import (analyze_coverage, extract_doors_from_dxf,
                          DEFAULT_COVERAGE_RADIUS_M)

doors = [(0, 0), (10, 0), (100, 100)]
cams = [{"x": 3, "y": 4}, {"x": 50, "y": 50}]
res_cov = analyze_coverage(doors, cams, radius_m=5.0, to_meter=1.0)
check("coverage door0 covered", res_cov[0]["covered"] is True, str(res_cov[0]))
check("coverage door0 dist",
      abs(res_cov[0]["nearest_m"] - 5.0) < 1e-9, str(res_cov[0]))
check("coverage door1 uncovered",
      res_cov[1]["covered"] is False, str(res_cov[1]))
check("coverage door2 uncovered",
      res_cov[2]["covered"] is False and res_cov[2]["nearest_m"] is not None)
res_cov = analyze_coverage([(0, 0)], [], radius_m=5.0)
check("coverage no cameras",
      res_cov[0]["covered"] is False and res_cov[0]["nearest_m"] is None)
res_cov = analyze_coverage([(0, 0)], [{"x": 4, "y": 3}],
                            radius_m=5.0, to_meter=2.0)
check("coverage to_meter scale",
      res_cov[0]["covered"] is False, str(res_cov[0]))
check("default radius", DEFAULT_COVERAGE_RADIUS_M == 5.0)

d, tm = extract_doors_from_dxf("/nonexistent/path.dxf")
check("extract missing file", d == [] and tm == 1.0)
try:
    import ezdxf  # noqa
    has_ezdxf = True
except Exception:
    has_ezdxf = False
check("extract no-ezdxf fallback", (d == [] and not has_ezdxf) or has_ezdxf)

# ============================================ ۶) رنگ هیت‌مپ ==
from building_map_dialog import BuildingMapPage

c0 = BuildingMapPage._heat_color(0.0)
c5 = BuildingMapPage._heat_color(0.5)
c1 = BuildingMapPage._heat_color(1.0)
check("heat green", c0[1] > c0[0] and c0[2] < 150, str(c0))
check("heat red", c1[0] > c1[1] and c1[0] > 200, str(c1))
check("heat mid yellowish", c5[0] > 200 and c5[1] > 150, str(c5))
check("heat clamp", BuildingMapPage._heat_color(5.0) == c1 and
      BuildingMapPage._heat_color(-1.0) == c0)

# ============================================ ۷) اسموک آفسکرین ==
from plate_stats_dialog import PlateStatsDialog, BarChart

stats = PlateStatsDialog(store)
check("stats dialog builds", stats is not None)
stats.refresh()
check("stats rows>0", len(stats._rows) > 0)
check("stats hourly 24", len(stats.hourly._data) == 24)
check("stats summary", "مجموع عبورها" in stats.summary.text(),
      stats.summary.text())
bc = BarChart("تست")
bc.set_data([("a", 3), ("b", 7)])
check("barchart data", len(bc._data) == 2)
bc.set_data([])
check("barchart empty ok", bc._data == [])
stats.close()

# تب لیست تحت‌نظر در صفحه‌ی کتابخانه‌ی پلاک
from plate_library_dialog import PlateLibraryPage


class _FakeCamStore:
    def standalone_cameras(self):
        return []


page = PlateLibraryPage(lambda: None, _FakeCamStore())
tabs = [page.tabs.tabText(i) for i in range(page.tabs.count())]
check("watchlist tab exists", any("تحت‌نظر" in t for t in tabs), str(tabs))
check("watchlist table built", page.watchlist_table.columnCount() == 4 and
      page.watchlist_table.horizontalHeaderItem(0).text() == "پلاک")

print(f"\n==== {len(passed)} passed, {len(failed)} failed ====")
sys.exit(1 if failed else 0)

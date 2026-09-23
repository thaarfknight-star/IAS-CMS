"""Headless regression test for IAS-CMS 2.0.17-beta.

۱) lane_geometry: فاصله تا پاره‌خط، پروجکشن روی چندخطی، تلرانس اتصال،
   ترتیب دوربین‌ها، ساخت رکورد مسیر، اعتبارسنجی نقاط.
۲) plate_store: مسیرهای پیش‌فرض روی دیتابیس خالی، حذف reentry_grace_seconds،
   lane_camera_order، set_plate_lane.
۳) plate_direction قانون د: حرکت معکوس در مسیر رسم‌شده -> wrong_way؛
   حرکت رو به جلو، «ورود مجدد» را خنثی می‌کند؛ قانون د فقط وقتی پلاک داخل
   است و عبور قبلی در همان مسیر بوده.
۵) اسموک آفسکرین رسم مسیر روی نقشه: شروع رسم، ثبت نقاط، تأیید، انتخاب
   دوربین روی خط، لغو.
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

TMP = tempfile.mkdtemp(prefix="ias217_")

import app_settings
app_settings._SETTINGS_PATH = os.path.join(TMP, "settings.json")

# ================================================= ۱) lane_geometry ==
from lane_geometry import (
    dist_point_to_segment, project_point_on_polyline, camera_on_lane,
    order_cameras_on_lane, build_lane_record, validate_lane_points,
    DEFAULT_ATTACH_TOLERANCE_M,
)

d, s = dist_point_to_segment(5, 3, 0, 0, 10, 0)
check("dist to segment", abs(d - 3.0) < 1e-9 and abs(s - 5.0) < 1e-9, f"{d},{s}")
d, s = dist_point_to_segment(15, 0, 0, 0, 10, 0)  # بیرون از بازه -> نزدیک‌ترین سر
check("dist beyond segment end", abs(d - 5.0) < 1e-9 and abs(s - 10.0) < 1e-9)
d, s = dist_point_to_segment(1, 1, 2, 2, 2, 2)  # پاره‌خط تهی
check("degenerate segment", abs(d - 2 ** 0.5) < 1e-9)

pts = [(0, 0), (10, 0), (10, 10)]
d, s = project_point_on_polyline(10, 3, pts)
check("projection on polyline", abs(d) < 1e-9 and abs(s - 13.0) < 1e-9, f"{d},{s}")
d, s = project_point_on_polyline(0, 0, [(0, 0)])
check("invalid polyline -> None", d is None and s is None)

ok, dist_m, s = camera_on_lane(5, 0.004, [(0, 0), (10, 0)], to_meter=1000.0)
check("camera within tolerance", ok and abs(dist_m - 4.0) < 1e-6, str(dist_m))
ok, dist_m, s = camera_on_lane(5, 0.010, [(0, 0), (10, 0)], to_meter=1000.0)
check("camera outside tolerance", not ok and abs(dist_m - 10.0) < 1e-6)
check("default tolerance is 5m", DEFAULT_ATTACH_TOLERANCE_M == 5.0)

cams = [{"camera_id": "c3", "x": 9, "y": 1},
        {"camera_id": "c1", "x": 1, "y": -1},
        {"camera_id": "c2", "x": 5, "y": 0.5}]
ordered = order_cameras_on_lane(cams, [(0, 0), (10, 0)])
check("cameras ordered along lane",
      [c["camera_id"] for c, _s in ordered] == ["c1", "c2", "c3"])

rec = build_lane_record("مسیر A", "going", "f1", [(0, 0), (10, 0), (10, 5)],
                        1.0, cams)
check("lane record cameras ordered",
      [c["camera_id"] for c in rec["cameras"]] == ["c1", "c2", "c3"] and
      [c["order"] for c in rec["cameras"]] == [0, 1, 2])
check("lane record fields",
      rec["name"] == "مسیر A" and rec["allowed"] == "going" and
      rec["floor_id"] == "f1" and len(rec["points"]) == 3)

ok, msg = validate_lane_points([(0, 0)])
check("one point invalid", not ok and msg)
ok, msg = validate_lane_points([(0, 0), (1, 1)])
check("two points valid", ok)

# ==================================== ۲) plate_store: پیش‌فرض‌ها ==
from plate_store import PlateStore, normalize_plate_text

fresh = PlateStore(db_path=os.path.join(TMP, "fresh.db"))
lanes = fresh.get_lanes()
# (2.0.33-beta به دستور کاربر) مسیر پیش‌فرضی ساخته نمی‌شود
check("no default lanes", lanes == {})
# ماندگاری: دیتابیس خالی، خالی می‌ماند
again = PlateStore(db_path=os.path.join(TMP, "fresh.db"))
check("empty persisted", again.get_lanes() == {})
check("no reentry_grace_seconds", not hasattr(fresh, "reentry_grace_seconds"))

lane_geo = {"name": "مسیر A", "allowed": "going", "floor_id": "f1",
            "points": [[0, 0], [10, 0]], "to_meter": 1.0,
            "cameras": [{"camera_id": "c1", "order": 0},
                        {"camera_id": "c2", "order": 1},
                        {"camera_id": "c3", "order": 2}]}
check("lane_camera_order hit", PlateStore.lane_camera_order(lane_geo, "c2") == 1)
check("lane_camera_order miss", PlateStore.lane_camera_order(lane_geo, "cx") is None)
check("lane_camera_order no cameras",
      PlateStore.lane_camera_order({"name": "x"}, "c1") is None)

# ==================================== ۳) قانون د ==
from plate_direction import PlateDirectionEngine

db_path = os.path.join(TMP, "plates.db")
store = PlateStore(db_path=db_path)
store.set_lanes({
    "laneA": dict(lane_geo),
    "laneB": {"name": "مسیر B", "allowed": "return", "floor_id": "f1",
              "points": [[0, 0], [10, 0]], "to_meter": 1.0,
              "cameras": [{"camera_id": "d1", "order": 0},
                          {"camera_id": "d2", "order": 1},
                          {"camera_id": "d3", "order": 2}]},
    "laneC": {"name": "مسیر C", "allowed": "going", "floor_id": "f1",
              "points": [[0, 0], [10, 0]], "to_meter": 1.0,
              "cameras": [{"camera_id": "e1", "order": 0}]},
})
engine = PlateDirectionEngine(store)
PLATE = "۲۲ب۴۵۶"


def mkcam(cid, role, lane):
    return {"id": cid, "name": f"cam-{cid}", "plate_role": role, "lane_id": lane}


def fire(cam, plate=PLATE):
    data = {"plate_text": plate, "plate_display": plate, "conf": 0.95, "crop": None}
    event = {"id": "ev_" + str(time.time_ns()), "plate_display": plate,
             "plate": None, "owner_name": "", "snapshot_path": ""}
    return engine.process(cam, data, event)


def vtypes(plate=PLATE):
    return [r["violation_type"] for r in
            store.list_violations(search=normalize_plate_text(plate))]


canon = normalize_plate_text(PLATE)
c1, c2, c3 = mkcam("c1", "entry", "laneA"), mkcam("c2", "entry", "laneA"), mkcam("c3", "entry", "laneA")

# ورود سالم در ابتدای مسیر
store.set_plate_state(canon, "outside", last_ts=time.time() - 3600)
n0 = len(store.list_violations(search=canon))
res = fire(c1)
check("D1 clean entry at order0", res and not res["violations"])
st = store.get_plate_state(canon)
check("D1 state inside + lane tracked",
      st["state"] == "inside" and st["last_lane_id"] == "laneA" and
      int(st["last_lane_order"]) == 0, str({k: st.get(k) for k in ("state", "last_lane_id", "last_lane_order")}))

# حرکت رو به جلو (۰ -> ۲): تخلف «ورود مجدد» نباید ثبت شود
res = fire(c3)
n1 = len(store.list_violations(search=canon))
check("D2 forward move suppresses reentry", res and not res["violations"] and n1 == n0)
st = store.get_plate_state(canon)
check("D2 lane order updated", int(st["last_lane_order"]) == 2)

# حرکت معکوس (۲ -> ۱): تخلف wrong_way، وضعیت همان داخل
res = fire(c2)
n2 = len(store.list_violations(search=canon))
check("D3 reverse move -> wrong_way",
      res and len(res["violations"]) >= 1 and n2 > n1 and "wrong_way" in vtypes())
check("D3 no duplicate reentry for reverse",
      vtypes().count("reentry_without_exit") == 0)
st = store.get_plate_state(canon)
check("D3 state stays inside", st["state"] == "inside")

# هلپر جهت حرکت در مسیر (پایه‌ی قانون د و استثنای قانون الف)
from plate_direction import lane_direction
check("lane_direction going forward", lane_direction("going", 0, 2) == "forward")
check("lane_direction going backward", lane_direction("going", 2, 1) == "backward")
check("lane_direction going same", lane_direction("going", 1, 1) == "same")
check("lane_direction return forward", lane_direction("return", 2, 0) == "forward")
check("lane_direction return backward", lane_direction("return", 0, 1) == "backward")
check("lane_direction unknown orders", lane_direction("going", None, 1) is None)
check("lane_direction unknown allowed",
      lane_direction("", 0, 1) is None)

# مسیر برگشت چنددوربینه: خروج سالم، ادامه در همان مسیر، بعد حرکت معکوس
d1, d2, d3 = mkcam("d1", "exit", "laneB"), mkcam("d2", "exit", "laneB"), mkcam("d3", "exit", "laneB")
PLATE2 = "۳۳ج۷۸۹"
canon2 = normalize_plate_text(PLATE2)
store.set_plate_state(canon2, "inside", last_ts=time.time() - 3600)
n0 = len(store.list_violations(search=canon2))
res = fire(d3, plate=PLATE2)  # خروج سالم در انتهای مسیر
st2 = store.get_plate_state(canon2)
check("D4 clean exit at order2", res and not res["violations"] and
      st2["state"] == "outside")
res = fire(d1, plate=PLATE2)  # ادامه‌ی رو به جلو در همان مسیر خروج
n1 = len(store.list_violations(search=canon2))
check("D4 same-lane exit continuation suppressed",
      res and not res["violations"] and n1 == n0, str(res and res["violations"]))
res = fire(d2, plate=PLATE2)  # ۰ -> ۱ = معکوس در مسیر برگشت
vt2 = vtypes(PLATE2)
check("D5 backward in exit lane -> exit_without_entry",
      res and len(res["violations"]) >= 1 and "exit_without_entry" in vt2,
      str(vt2))

# قانون د فقط وقتی پلاک «داخل» است: سفر جدید از وسط مسیر تخلف نیست
PLATE3 = "۴۴د۱۲۳"
canon3 = normalize_plate_text(PLATE3)
store.set_plate_state(canon3, "outside", last_ts=time.time() - 3600)
store.set_plate_lane(canon3, "laneA", 2)  # عبور قدیمی در همان مسیر
n0 = len(store.list_violations(search=canon3))
res = fire(c1, plate=PLATE3)  # ولی وضعیت خارج = سفر جدید
n1 = len(store.list_violations(search=canon3))
check("D6 new trip from outside: no wrong_way",
      res and not res["violations"] and n1 == n0)

# قانون د فقط در همان مسیر: ورود در مسیر رفتِ دیگر = ورود مجدد (نه wrong_way)
PLATE4 = "۵۵هـ۴۵۶"
canon4 = normalize_plate_text(PLATE4)
e1 = mkcam("e1", "entry", "laneC")
store.set_plate_state(canon4, "inside", last_ts=time.time() - 3600)
store.set_plate_lane(canon4, "laneA", 2)
n0 = len(store.list_violations(search=canon4))
res = fire(e1, plate=PLATE4)
n1 = len(store.list_violations(search=canon4))
vt = vtypes(PLATE4)
check("D7 entry in other lane -> reentry not wrong_way",
      n1 > n0 and "reentry_without_exit" in vt and "wrong_way" not in vt,
      str(vt))

# دوربینِ دارای lane_id ولی خارج از لیست cameras مسیر: رفتار قدیمی (قانون ب)
PLATE5 = "۶۶و۷۸۹"
canon5 = normalize_plate_text(PLATE5)
cX = mkcam("cX", "entry", "laneA")  # در cameras مسیر نیست
store.set_plate_state(canon5, "inside", last_ts=time.time() - 3600)
res = fire(cX, plate=PLATE5)
check("D8 unordered camera -> plain reentry",
      res and len(res["violations"]) >= 1 and
      "reentry_without_exit" in vtypes(PLATE5))

# ==================================== ۵) اسموک رسم مسیر ==
import building_map_dialog as bmd
from PyQt6.QtCore import QPointF


class _StubCameraStore:
    def __init__(self):
        self._cams = [{"id": "cam1", "name": "ورودی", "plate_role": "entry",
                       "lane_id": ""}]

    def standalone_cameras(self):
        return list(self._cams)

    @property
    def nvrs(self):
        return []

    def cameras_for_nvr(self, _nid):
        return []

    def update_camera(self, cam_id, **fields):
        for c in self._cams:
            if c["id"] == cam_id:
                c.update(fields)

    def get_camera_floor_id(self, _cam_id):
        return ""


_OrigMapStore = bmd.MapStore


class _TmpMapStore(_OrigMapStore):
    def __init__(self, json_path=None):
        super().__init__(json_path=os.path.join(TMP, "building_maps.json"))


bmd.MapStore = _TmpMapStore
try:
    page = bmd.BuildingMapPage(_StubCameraStore())
    page.show()  # در آفسکرین لازم است تا isVisible درست کار کند
    check("map page builds with lane section",
          hasattr(page, "lane_draw_btn") and hasattr(page, "lane_list"))
    check("default lanes listed", page.lane_list.count() >= 2,
          str(page.lane_list.count()))

    # شروع رسم + ۳ نقطه + تأیید
    page._start_lane_draw()
    check("draw mode on", page._lane_mode == "draw" and page.view.lane_drawing)
    check("lane bar visible", page._lane_bar.isVisible())
    page._on_lane_click(QPointF(0, 0))
    check("confirm disabled with 1 point", not page._lane_bar_confirm.isEnabled())
    page._on_lane_click(QPointF(100, 0))
    check("confirm enabled with 2 points", page._lane_bar_confirm.isEnabled())
    page._on_lane_click(QPointF(200, 50))
    n_preview = sum(1 for it in page.view.scene().items()
                    if it.data(0) == "lane-draw")
    check("preview items drawn", n_preview >= 4, str(n_preview))  # خط + ۳ نشان
    page._confirm_lane_points()
    check("pick mode on", page._lane_mode == "pick")

    # گذاشتن دوربین روی خط مسیر و انتخاب آن
    fid = page.current_floor
    dev = page.store.add_device(fid, "camera", "C1", 50, 0, ref_id="cam1")
    page.scenes.pop(fid, None)
    page._activate_floor(fid)
    page._lane_mode = "pick"
    page._toggle_pick_camera(QPointF(50, 0))
    check("camera on lane selected", len(page._lane_pick) == 1)
    # دوربین دور از خط -> رد می‌شود (بدون انتخاب جدید)
    dev2 = page.store.add_device(fid, "camera", "C2", 500, 500, ref_id="cam1")
    page.scenes.pop(fid, None)
    page._activate_floor(fid)
    page._lane_mode = "pick"
    n_before = len(page._lane_pick)
    # پیام‌های مودال را در آفسکرین بی‌اثر می‌کنیم
    _orig_info = bmd.QMessageBox.information
    bmd.QMessageBox.information = lambda *a, **k: None
    try:
        page._toggle_pick_camera(QPointF(500, 500))
    finally:
        bmd.QMessageBox.information = _orig_info
    check("far camera rejected", len(page._lane_pick) == n_before)

    # لغو حالت رسم
    page._cancel_lane_mode()
    check("lane mode cancelled",
          page._lane_mode is None and not page._lane_bar.isVisible())

    # نمایش مسیر ذخیره‌شده روی نقشه
    lanes = page._reload_lane_list()  # noqa - فقط برای پوشش
    from plate_store import plate_store as _ps
    _lanes = _ps.get_lanes()
    _lid = "laneX"
    _lanes[_lid] = {"name": "تست", "allowed": "going", "floor_id": fid,
                    "points": [[0, 0], [100, 0]], "to_meter": 1.0,
                    "cameras": [{"camera_id": "cam1", "order": 0}]}
    _ps.set_lanes(_lanes)
    page._show_lane(_lid)
    n_geo = sum(1 for e in page.scenes.values() for it in e["scene"].items()
                if it.data(0) == "lane-geo")
    check("saved lane drawn on map", n_geo >= 3, str(n_geo))  # خط + پیکان + نشان
    # پاک‌سازی مسیر تستی
    _lanes.pop(_lid, None)
    _ps.set_lanes(_lanes)
    page._clear_lane_geo()
finally:
    bmd.MapStore = _OrigMapStore

# ================================================= گزارش ==
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)

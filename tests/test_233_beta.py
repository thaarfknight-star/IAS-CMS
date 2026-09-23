"""Headless regression test for IAS-CMS 2.0.33-beta.

۱) بدون مسیر پیش‌فرض: PlateStore تازه -> get_lanes() == {}.
۲) حذف کامل شبیه‌سازی: فایل lane_simulator.py نیست؛ BuildingMapPage نه
   lane_sim_btn دارد نه _start_lane_sim؛ دکمه‌ی «✏️ ویرایش» هست.
۳) دابل‌کلیک روی نقطه‌ی رسم‌شده آن را حذف می‌کند (حالت draw).
۴) ویرایش مسیر: _begin_lane_edit نقاط را بار می‌کند؛ درگ دستگیره نقطه را
   جابه‌جا می‌کند؛ دابل‌کلیک نقطه را حذف می‌کند؛ _save_lane_edit روی همان
   lane_id ذخیره می‌کند (مسیر جدید نمی‌سازد) و پوشش زنده را رفرش می‌کند.
۵) رفرش پوشش زنده: بعد از _delete_lane هم _refresh_coverage_live صدا زده می‌شود.
"""
import os
import sys
import types
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr

_cv2 = types.ModuleType("cv2")
_cv2.resize = lambda *a, **k: a[0] if a else None
_cv2.imwrite = lambda *a, **k: True
sys.modules["cv2"] = _cv2

passed = []
failed = []


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox
from PyQt6.QtCore import QPointF, Qt
from unittest.mock import MagicMock

app = QApplication(sys.argv)

TMP = tempfile.mkdtemp(prefix="ias233_")

# ============================================ ۱) بدون مسیر پیش‌فرض ==
from plate_store import PlateStore

fresh = PlateStore(db_path=os.path.join(TMP, "fresh.db"))
check("fresh store has no default lanes", fresh.get_lanes() == {})
again = PlateStore(db_path=os.path.join(TMP, "fresh.db"))
check("empty stays empty", again.get_lanes() == {})

# ============================================ ۲) حذف شبیه‌سازی ==
check("lane_simulator.py deleted",
      not os.path.exists(os.path.join(
          os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
          "lane_simulator.py")))
import building_map_dialog as bmd

src = open(os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))),
    "building_map_dialog.py"), encoding="utf-8").read()
check("no sim references in dialog",
      "lane_simulator" not in src and "_start_lane_sim" not in src)

page = bmd.BuildingMapPage(camera_store=MagicMock())
check("lane_sim_btn removed", getattr(page, "lane_sim_btn", None) is None)
check("no _start_lane_sim method", not hasattr(page, "_start_lane_sim"))
check("lane_edit_btn exists",
      getattr(page, "lane_edit_btn", None) is not None
      and page.lane_edit_btn.text() == "✏️ ویرایش")
check("view has lane_double_click signal",
      hasattr(page.view, "lane_double_click"))

# ============================================ ۳) دابل‌کلیک = حذف نقطه ==
sc = QGraphicsScene()
page.scenes["f1"] = {"scene": sc, "to_meter": 1.0, "items": {}}
page.current_floor = "f1"
page.view.setScene(sc)
page._lane_mode = "draw"
page._lane_points = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]

# کلیک روی نقطه‌ی موجود، نقطه‌ی تکراری نمی‌سازد
page._on_lane_click(QPointF(100.0, 3.0))
check("click on existing point ignored",
      page._lane_points == [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)])
# کلیک دور از نقاط، نقطه‌ی جدید می‌سازد
page._on_lane_click(QPointF(300.0, 50.0))
check("click far adds point", len(page._lane_points) == 4)
page._lane_points = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]
# دابل‌کلیک نزدیک نقطه‌ی وسط -> حذف همان
page._on_lane_double_click(QPointF(100.0, 4.0))
check("double-click deletes nearest point",
      page._lane_points == [(0.0, 0.0), (200.0, 0.0)])
# دابل‌کلیک دور از همه‌ی نقاط -> بدون تغییر
page._on_lane_double_click(QPointF(900.0, 900.0))
check("double-click far changes nothing",
      page._lane_points == [(0.0, 0.0), (200.0, 0.0)])
# بیرون از حالت رسم -> بدون تغییر
page._lane_mode = None
page._on_lane_double_click(QPointF(0.0, 0.0))
check("double-click outside draw ignored",
      page._lane_points == [(0.0, 0.0), (200.0, 0.0)])

# ============================================ ۴) ویرایش مسیر ==
store = PlateStore(db_path=os.path.join(TMP, "lanes.db"))
lid = "lane1"
store.set_lanes({lid: {
    "name": "مسیر تست", "allowed": "going", "floor_id": "f1",
    "points": [[0.0, 0.0], [100.0, 0.0], [200.0, 0.0]], "to_meter": 1.0,
    "cameras": []}})
bmd.plate_store = store

page2 = bmd.BuildingMapPage(camera_store=MagicMock())
page2.scenes["f1"] = {"scene": QGraphicsScene(), "to_meter": 1.0,
                      "items": {}}
page2.current_floor = "f1"
page2.view.setScene(page2.scenes["f1"]["scene"])

lane = store.get_lanes()[lid]
page2._begin_lane_edit(lid, lane)
check("edit mode entered",
      page2._lane_mode == "edit" and page2._lane_edit_id == lid)
check("edit loads points",
      page2._lane_points == [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)])
check("edit handles are movable",
      any(isinstance(it, bmd._LanePointHandle)
          for it in page2.scenes["f1"]["scene"].items()))

# درگ دستگیره‌ی نقطه‌ی دوم
page2._on_lane_handle_moved(1, 150.0, 10.0)
check("handle drag moves point", page2._lane_points[1] == (150.0, 10.0))

# دابل‌کلیک روی نقطه‌ی سوم -> حذف
page2._on_lane_double_click(QPointF(200.0, 2.0))
check("double-click in edit deletes point",
      page2._lane_points == [(0.0, 0.0), (150.0, 10.0)])

# ذخیره: همان lane_id، بدون ساخت مسیر جدید
bmd.QMessageBox.information = lambda *a, **k: None
refreshed = []
page2._refresh_coverage_live = lambda: refreshed.append(1)
page2._save_lane_edit()
saved = store.get_lanes()
check("edit saves on same lane_id",
      set(saved.keys()) == {lid})
check("edit persists points",
      saved[lid]["points"] == [[0.0, 0.0], [150.0, 10.0]])
check("edit exits edit mode",
      page2._lane_mode is None and page2._lane_edit_id is None)
check("edit refreshes live coverage", len(refreshed) == 1)
check("edit cleaned items",
      not [it for it in page2.scenes["f1"]["scene"].items()
           if it.data(0) == "lane-edit"])

# ============================================ ۵) حذف مسیر + پوشش ==
page3 = bmd.BuildingMapPage(camera_store=MagicMock())
page3.scenes["f1"] = {"scene": QGraphicsScene(), "to_meter": 1.0,
                      "items": {}}
page3.current_floor = "f1"
bmd.QMessageBox.question = (
    lambda *a, **k: QMessageBox.StandardButton.Yes)
refreshed3 = []
page3._refresh_coverage_live = lambda: refreshed3.append(1)
# آیتم لیست را دستی انتخاب می‌کنیم
from PyQt6.QtWidgets import QListWidgetItem
page3._reload_lane_list()
for i in range(page3.lane_list.count()):
    it = page3.lane_list.item(i)
    if it.data(Qt.ItemDataRole.UserRole) == lid:
        page3.lane_list.setCurrentItem(it)
        break
page3._delete_lane()
check("lane deleted", lid not in store.get_lanes())
check("delete refreshes live coverage", len(refreshed3) == 1)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

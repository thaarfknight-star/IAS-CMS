# -*- coding: utf-8 -*-
"""تست‌های رگرسیون 2.0.30-beta — «حذف نقشه» (2026-09-22):

درخواست طه: کادر/پس‌زمینه‌ی سفید روی نقشه حذف شود؛ فقط ایموجی دوربین
+ پهنای دید (قطاع دید) + بقیه‌ی تنظیمات باقی بماند و قابل تنظیم/جابه‌جا
باشد.

تغییر: دکمه‌ی «🗑 حذف نقشه» در بخش «🗺 نقشه‌ی طبقه» — با تأیید کاربر،
نقشه‌ی طبقه‌ی فعال جدا می‌شود (تصویر/DXF سفید از صحنه می‌رود) و تجهیزات
(ایموجی + قطاع دید) سر جایشان روی گرید تیره می‌مانند.

اجرا:
  QT_QPA_PLATFORM=offscreen python3 tests/test_230_beta.py
"""
import os
import sys
import types
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_passed = []
_failed = []


def check(name, cond, extra=""):
    (_passed if cond else _failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" | {extra}" if extra and not cond else ""))


for _mod in ("face_recognition", "cv2"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

from PyQt6.QtWidgets import (
    QApplication, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsPathItem, QGraphicsSimpleTextItem, QMessageBox,
)
from PyQt6.QtGui import QImage, QColor

app = QApplication.instance() or QApplication([])

import building_map as bm
import building_map_dialog as bmd

# ---------- ایزوله‌سازی: استور و maps_data موقت، بدون دست‌زدن به واقعی ----------
_tmp = tempfile.mkdtemp(prefix="iascms_230_")
_orig_MapStore = bmd.MapStore
_orig_maps_data_dir = bm.maps_data_dir
bm.maps_data_dir = lambda: _tmp  # import_map_file/floor_map_abs همین‌جا می‌روند
bmd.MapStore = lambda json_path=None: _orig_MapStore(
    os.path.join(_tmp, "building_maps.json"))

# ---------- ۱) سطح استور: جداکردن نقشه ----------
store = _orig_MapStore(os.path.join(_tmp, "store_maps.json"))
fl = store.add_floor("طبقه‌ی تست")
fid = fl["id"]
dev = store.add_device(fid, "camera", "دوربین", 10.0, 5.0)

# ساخت یک تصویر کاملاً سفید و ایمپورت آن به‌عنوان نقشه
img_path = os.path.join(_tmp, "white.png")
_im = QImage(200, 120, QImage.Format.Format_RGB32)
_im.fill(QColor("white"))
_im.save(img_path)
store.import_map_file(fid, img_path)
mp, mk = store.floor_map_abs(store.get_floor(fid))
check("store: white map imported", bool(mp) and mk == "image", f"got {mk}")

store.set_floor_map(fid, "", "")
mp2, mk2 = store.floor_map_abs(store.get_floor(fid))
check("store: set_floor_map('','') clears map", not mp2 and not mk2,
      f"got {mp2},{mk2}")
devs = store.get_floor(fid).get("devices", [])
check("store: devices kept after map removal",
      any(d.get("id") == dev["id"] for d in devs))

# ---------- ۲) سطح دیالوگ: دکمه‌ی «حذف نقشه» ----------
class StubCamStore:
    nvrs = []

page = bmd.BuildingMapPage(StubCamStore())
page.show()
app.processEvents()
check("dialog: delmap_btn exists",
      hasattr(page, "delmap_btn") and page.delmap_btn.text() == "🗑 حذف نقشه")
check("dialog: delmap_btn wired to _remove_map",
      hasattr(page, "_remove_map"))

# ---------- ۳) رفتار _remove_map: نقشه‌ی سفید می‌رود، ایموجی+قطاع می‌مانند ----------
fid2 = page.store.add_floor("طبقه‌ی سفید")["id"]
store2 = page.store
img2 = os.path.join(_tmp, "white2.png")
_im2 = QImage(300, 200, QImage.Format.Format_RGB32)
_im2.fill(QColor("white"))
_im2.save(img2)
# مهاجرت لازم نیست (بدون نقشه‌ی قبلی)؛ مستقیم ثبت
store2.import_map_file(fid2, img2)
cam = store2.add_device(fid2, "camera", "دوربین تست", 12.0, 6.0,
                        ref_id="")
# مقادیر دید دوربین
for d in store2.get_floor(fid2)["devices"]:
    if d["id"] == cam["id"]:
        d.update({"angle": 100.0, "fov": 45.0, "view_distance": 8.0})
store2.save()

page.scenes.pop(fid2, None)
page._activate_floor(fid2)
app.processEvents()
entry = page.scenes.get(fid2)
pixmaps = [it for it in entry["scene"].items()
           if isinstance(it, QGraphicsPixmapItem)]
check("dialog: white map pixmap present before removal", len(pixmaps) >= 1)
item = entry["items"][cam["id"]]
texts = [c for c in item.childItems()
         if isinstance(c, QGraphicsSimpleTextItem)]
check("dialog: emoji glyph only, no white box",
      len(texts) == 1 and texts[0].text() == "🎥" and
      not any(isinstance(c, QGraphicsRectItem) for c in item.childItems()))
check("dialog: FOV sector kept", item._sector is not None and
      isinstance(item._sector, QGraphicsPathItem))
check("dialog: camera in front of map", item.zValue() > pixmaps[0].zValue())

# شبیه‌سازی تأیید کاربر در دیالوگ‌ها (بدون پنجره‌ی واقعی)
_orig_q = QMessageBox.question
_orig_i = QMessageBox.information
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: None)
try:
    # رفرش لیست طبقه‌ها و انتخاب «طبقه‌ی سفید»
    from PyQt6.QtCore import Qt as _Qt
    page._reload_floors()
    for i in range(page.floor_list.count()):
        if page.floor_list.item(i).data(
                _Qt.ItemDataRole.UserRole) == fid2:
            page.floor_list.setCurrentRow(i)
            break
    page._activate_floor(fid2)
    app.processEvents()
    page._remove_map()
    app.processEvents()
finally:
    QMessageBox.question = _orig_q
    QMessageBox.information = _orig_i

mp3, mk3 = store2.floor_map_abs(store2.get_floor(fid2))
check("remove_map: map detached", not mp3 and not mk3, f"got {mp3},{mk3}")
entry2 = page.scenes.get(fid2)
pixmaps2 = [it for it in entry2["scene"].items()
            if isinstance(it, QGraphicsPixmapItem)]
check("remove_map: no pixmap in scene", len(pixmaps2) == 0,
      f"pixmaps={len(pixmaps2)}")
item2 = entry2["items"].get(cam["id"])
check("remove_map: camera item kept", item2 is not None)
if item2 is not None:
    t2 = [c for c in item2.childItems()
          if isinstance(c, QGraphicsSimpleTextItem)]
    check("remove_map: emoji-only after removal",
          len(t2) == 1 and t2[0].text() == "🎥")
    check("remove_map: sector kept after removal",
          item2._sector is not None and
          isinstance(item2._sector, QGraphicsPathItem))
    # انتخاب/درگ/دابل‌کلیک پسرفت نکرده
    check("remove_map: still selectable+moving",
          bool(item2.flags() & item2.GraphicsItemFlag.ItemIsSelectable) and
          bool(item2.flags() & item2.GraphicsItemFlag.ItemIsMovable))
    # تنظیمات دید حفظ شده
    check("remove_map: angle/fov/view_distance kept",
          abs(float(item2.device.get("angle", -1)) - 100.0) < 1e-9 and
          abs(float(item2.device.get("fov", -1)) - 45.0) < 1e-9 and
          abs(float(item2.device.get("view_distance", -1)) - 8.0) < 1e-9)

# ذخیره‌سازی روی دیسک
fresh = _orig_MapStore(os.path.join(_tmp, "building_maps.json"))
ffl = fresh.get_floor(fid2)
check("remove_map: persisted on disk",
      ffl is not None and not ffl.get("dxf") and not ffl.get("image") and
      any(d.get("id") == cam["id"] for d in ffl.get("devices", [])))

bmd.MapStore = _orig_MapStore
bm.maps_data_dir = _orig_maps_data_dir
shutil.rmtree(_tmp, ignore_errors=True)

print(f"\n{len(_passed)} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)

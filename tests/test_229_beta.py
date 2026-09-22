# -*- coding: utf-8 -*-
"""تست‌های رگرسیون 2.0.29-beta — چهار درخواست طه (2026-09-22):

۱) نشان تجهیزات روی نقشه فقط «ایموجی» نوع تجهیز است؛ هیچ بدنه‌ی دایره‌ای،
   خط جهت، کادر یا لیبلی رسم نمی‌شود. قطاع دید دوربین (پوشش) حفظ شده.
۲) دکمه‌های بالا/پایین اسپین‌باکس زاویه: مقدار عوض می‌شود، قطاع دید زنده
   به‌روز می‌شود و بعد از تایمر ذخیره، روی دیسک می‌ماند.
۳) شخصِ در حال حرکت از اولین لحظه ثبت می‌شود: تطبیق دومرحله‌ای
   (tentative با آستانه‌ی بازتر) + تأیید با ۲ هیت.
۴) متن دیالوگ «حالت جایگزین» دیگر از کاربر نصب کتابخانه نمی‌خواهد.

اجرا:
  QT_QPA_PLATFORM=offscreen python3 tests/test_229_beta.py
"""
import os
import sys
import types

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
    QApplication, QGraphicsScene, QGraphicsView,
    QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsPathItem,
    QGraphicsSimpleTextItem, QGraphicsLineItem,
)

app = QApplication.instance() or QApplication([])

import building_map_dialog as bmd
import person_reid as pr


def _children_of(item, cls):
    return [c for c in item.childItems() if isinstance(c, cls)]


# ---------- ۱) نشان ایموجی‌محور ----------
scene = QGraphicsScene()
cam_dev = {"id": "c1", "kind": "camera", "name": "دوربین",
           "angle": 100, "fov": 45, "view_distance": 8.0}
cam = bmd.DeviceItem("f1", cam_dev, 1.0, {})
scene.addItem(cam)

check("camera: no ellipse body",
      len(_children_of(cam, QGraphicsEllipseItem)) == 0)
check("camera: no rect/box",
      len(_children_of(cam, QGraphicsRectItem)) == 0)
check("camera: no lens tick line",
      len(_children_of(cam, QGraphicsLineItem)) == 0)
texts = _children_of(cam, QGraphicsSimpleTextItem)
check("camera: single emoji glyph, no name label",
      len(texts) == 1 and texts[0].text() == "🎥", f"got {[t.text() for t in texts]}")
check("camera: sector kept",
      cam._sector is not None and isinstance(cam._sector, QGraphicsPathItem))
check("camera: tick ref removed", cam._tick is None)
check("camera: label ref removed", cam._label is None)
cam.set_name("x")  # نباید کرش کند

nvr_dev = {"id": "n1", "kind": "nvr", "name": "ان‌وی‌آر"}
nvr = bmd.DeviceItem("f1", nvr_dev, 1.0, {})
scene.addItem(nvr)
check("nvr: no rounded box",
      len(_children_of(nvr, QGraphicsPathItem)) == 0)
ntexts = _children_of(nvr, QGraphicsSimpleTextItem)
check("nvr: single emoji glyph",
      len(ntexts) == 1 and ntexts[0].text() == "🖥",
      f"got {[t.text() for t in ntexts]}")
check("nvr: no sector", nvr._sector is None)

# قطاع دید با تغییر زاویه عوض می‌شود و موقعیت ثابت می‌ماند
pos_before = cam.pos()
path_before = cam._sector.path()
cam_dev["angle"] = 140.0
cam.refresh()
check("camera: sector path changes on angle",
      cam._sector.path() != path_before)
check("camera: position untouched by refresh", cam.pos() == pos_before)

# ---------- ۲) دکمه‌های زاویه ----------
class StubCamStore:
    nvrs = []
bmd.DeviceConfigDialog._camera_entries = staticmethod(lambda cs: [])

page = bmd.BuildingMapPage(StubCamStore())
page.show()
app.processEvents()
fid = page.store.floors()[0]["id"]
dev = page.store.add_device(fid, "camera", "دوربین", 10.0, 5.0)
page.scenes.pop(fid, None)
page._activate_floor(fid)
app.processEvents()
item = page.scenes[fid]["items"][dev["id"]]
item.setSelected(True)
app.processEvents()
p_before = item._sector.path()
page.prop_angle_num.stepBy(1)  # شبیه‌سازی کلیک دکمه‌ی ▲
app.processEvents()
check("angle btn: spinbox value changes", page.prop_angle_num.value() == 1)
check("angle btn: slider synced", page.prop_angle.value() == 1)
check("angle btn: device angle updated",
      abs(item.device.get("angle", 0) - 1.0) < 1e-9)
check("angle btn: sector refreshed live", item._sector.path() != p_before)
page.prop_angle_num.stepBy(-1)
app.processEvents()
check("angle btn: down button works too",
      page.prop_angle_num.value() == 0 and
      abs(item.device.get("angle", 0)) < 1e-9)
# ذخیره‌سازی (تایمر ۷۰۰ms)
page._prop_save_timer.timeout.emit()
app.processEvents()
fresh = bmd.MapStore(page.store.json_path)
found = [d for fl in fresh.floors() for d in fl.get("devices", [])
         if d.get("id") == dev["id"]]
check("angle btn: persisted on disk",
      bool(found) and abs(float(found[0].get("angle", -1))) < 1e-9,
      f"got {found[0].get('angle') if found else None}")

# ---------- ۳) شخص در حال حرکت ----------
import numpy as np

# cv2 اینجا نصب نیست؛ منطق ردیاب (تطبیق/تأیید) را با توصیف‌گر ساختگی
# تست می‌کنیم (خود describe_person واحد جداست).
_fake_desc = {"vector": np.ones(8, dtype=np.float32) / np.sqrt(8),
              "shirt_color": "آبی"}
pr.PersonLocalTracker._describe_box = lambda self, f, b: dict(_fake_desc)

frame = np.zeros((240, 320, 3), dtype=np.uint8)
trk = pr.PersonLocalTracker(confirm_frames=2)
# شخص با سرعت وارد می‌شود: هر تیک باکس ۲۴ پیکسل جابه‌جا می‌شود
# (IoU بین دو تشخیص پیاپی ≈۰٫۲۵: زیر آستانه‌ی قدیمی ۰٫۳۰ ولی بالای ۰٫۲۰)
boxes = [
    (20, 80, 120, 40),    # t0
    (20, 104, 120, 64),   # t1: جابه‌جا شده
    (20, 128, 120, 88),   # t2: باز هم جابه‌جا
]
events = []
for i, b in enumerate(boxes):
    ev, _draw = trk.update([b], frame, ts=1000.0 + i)
    events.extend(ev)
kinds = [e[0] for e in events]
check("moving person: confirmed within 3 ticks", "confirmed" in kinds,
      f"kinds={kinds}")
confirmed = [e[1] for e in events if e[0] == "confirmed"]
check("moving person: descriptor built",
      bool(confirmed) and confirmed[0].get("descriptor") is not None)
check("moving person: first_seen kept",
      bool(confirmed) and abs(confirmed[0]["first_seen"] - 1000.0) < 1e-9)

# سناریوی تکه‌تکه‌شدن قدیمی: با آستانه‌ی باز، رد tentative حفظ می‌شود
trk2 = pr.PersonLocalTracker(confirm_frames=2)
trk2.update([(20, 80, 120, 40)], frame, ts=2000.0)
n_tracks_1 = len(trk2._tracks)
trk2.update([(20, 104, 120, 64)], frame, ts=2001.0)  # IoU≈۰٫۲۵
check("moving person: tentative track not fragmented",
      len(trk2._tracks) == n_tracks_1 == 1 and
      trk2._tracks[0]["hits"] == 2,
      f"tracks={len(trk2._tracks)}")

# ---------- ۴) متن دیالوگ ----------
import re
with open(os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "main.py"),
        encoding="utf-8") as f:
    main_src = f.read()
m = re.search(r"محدوده ذخیره شد \(حالت جایگزین\)(.{0,800})", main_src,
              re.DOTALL)
dlg = m.group(0) if m else ""
check("dialog: no install instruction",
      "ultralytics" in dlg and "نصب است" not in dlg and
      "pip install" not in dlg and "نیازی به نصب هیچ‌چیز" in dlg)

print(f"\n{_passed.__len__()} passed, {_failed.__len__()} failed")
sys.exit(1 if _failed else 0)

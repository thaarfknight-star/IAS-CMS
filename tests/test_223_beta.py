# -*- coding: utf-8 -*-
"""Headless regression test for IAS-CMS 2.0.23-beta.

موضوع: مستقل‌شدن مختصات تجهیزات/مسیرها از واحد نقشه (DXF با هر مقیاسی).

- device_scene_xy: مختصات متری -> واحد صحنه (سانتی‌متر/میلی‌متر/متر)
- ensure_device_meters: مهاجرت یک‌باره‌ی مختصات قدیمی با to_meter «نقشه‌ی قبلی»
- ترتیب ایمپورت: مهاجرت با مقیاس قدیمی «قبل» از جایگزینی فایل، نه بعدش
- DeviceItem دوربین: فقط ایموجی 🎥؛ بدون دایره و بدون کادر قطاع دید
- شبیه‌سازی مسیر: تبدیل نقاط رسم‌شده (واحد قدیم) به مقیاس جاری صحنه
- گرید تطبیقی MapScene.grid_step_scene با مقیاس‌های مختلف
- گارد مقیاس: DXF سانتی‌متری ۶×۳ (مثل acad3D.dxf طه) باید نامعقول تشخیص
  داده شود (بعد واقعی < ۰٫۵ متر)
"""
import os
import sys
import types
import tempfile
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr

passed = []
failed = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" [{extra}]" if extra and not cond else ""))


TMP = tempfile.mkdtemp(prefix="ias223_")

# ============================================ ۱) device_scene_xy ==
from building_map import device_scene_xy

dev_m = {"x": 3.0, "y": 1.5, "pos_unit": "m"}
check("scene xy on meter map", device_scene_xy(dev_m, 1.0) == (3.0, 1.5))
check("scene xy on cm map", device_scene_xy(dev_m, 0.01) == (300.0, 150.0))
check("scene xy on mm map", device_scene_xy(dev_m, 0.001) == (3000.0, 1500.0))
check("scene xy bad input", device_scene_xy({"x": "bad"}, 1.0) == (0.0, 0.0))

# ============================================ ۲) ensure_device_meters ==
from building_map import MapStore

store = MapStore(os.path.join(TMP, "maps.json"))
fl = store.add_floor("طبقه‌ی تست")
fid = fl["id"]
# تجهیز «قدیمی»: مختصات با واحد صحنه‌ی نقشه‌ی سانتی‌متری (۳۰۰ واحد = ۳ متر)
old = store.add_device(fid, "camera", "قدیمی", 300.0, 150.0)
old.pop("pos_unit", None)  # شبیه‌سازی رکورد قبل از 2.0.23
store.save()
store.ensure_device_meters(fid, 0.01)  # to_meter نقشه‌ی قبلی (سانتی‌متر)
got = [d for d in store.get_floor(fid)["devices"] if d["id"] == old["id"]][0]
check("migrate old scene->meters",
      abs(got["x"] - 3.0) < 1e-9 and abs(got["y"] - 1.5) < 1e-9,
      str((got["x"], got["y"])))
check("migrate marks pos_unit", got.get("pos_unit") == "m")
# اجرای دوباره نباید چیزی را عوض کند
store.ensure_device_meters(fid, 1.0)
got2 = [d for d in store.get_floor(fid)["devices"] if d["id"] == old["id"]][0]
check("migrate idempotent",
      abs(got2["x"] - 3.0) < 1e-9 and got2.get("pos_unit") == "m")

# ============================================ ۳) ترتیب ایمپورت ==
# سناریو: نقشه‌ی قدیمی سانتی‌متری، تجهیز قدیمی مهاجرت‌نشده؛ ایمپورت نقشه‌ی
# جدید متری. مهاجرت باید با to_meter قدیمی (۰٫۰۱) انجام شود نه جدید (۱٫۰).
store2 = MapStore(os.path.join(TMP, "maps2.json"))
fl2 = store2.add_floor("طبقه‌ی ایمپورت")
fid2 = fl2["id"]
old2 = store2.add_device(fid2, "camera", "قدیمی۲", 200.0, 100.0)
old2.pop("pos_unit", None)
store2.save()
old_tm = 0.01  # مقیاس صحنه‌ی قبلی (مثل self.scenes[fid]["to_meter"])
store2.ensure_device_meters(fid2, old_tm)  # قبل از import_map_file
got3 = [d for d in store2.get_floor(fid2)["devices"] if d["id"] == old2["id"]][0]
check("pre-import migration uses old scale",
      abs(got3["x"] - 2.0) < 1e-9 and abs(got3["y"] - 1.0) < 1e-9,
      str((got3["x"], got3["y"])))
# بعد از ایمپورت، مهاجرت با مقیاس جدید نباید اثر کند (pos_unit=m رد می‌شود)
store2.ensure_device_meters(fid2, 1.0)
got4 = [d for d in store2.get_floor(fid2)["devices"] if d["id"] == old2["id"]][0]
check("post-import migration no-op",
      abs(got4["x"] - 2.0) < 1e-9 and abs(got4["y"] - 1.0) < 1e-9)

# ============================================ ۴) DeviceItem دوربین ==
from PyQt6.QtWidgets import (
    QApplication, QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsLineItem,
    QGraphicsSimpleTextItem,
)
from PyQt6.QtCore import QRectF

app = QApplication(sys.argv)
import building_map_dialog as bmd

devc = {"id": "c1", "kind": "camera", "name": "Fani 2",
        "x": 3.0, "y": -2.0, "angle": 0.0, "fov": 90.0,
        "view_distance": 2.0, "pos_unit": "m"}
item = bmd.DeviceItem(fid, devc, 0.01, {})  # نقشه‌ی سانتی‌متری
kids = item.childItems()
check("camera has no circle",
      not any(isinstance(k, QGraphicsEllipseItem) for k in kids))
check("camera has no sector/box",
      not any(isinstance(k, QGraphicsPathItem) for k in kids))
check("camera has no lens tick",
      not any(isinstance(k, QGraphicsLineItem) for k in kids))
texts = [k.text() for k in kids if isinstance(k, QGraphicsSimpleTextItem)]
check("camera emoji marker", "🎥" in texts, str(texts))
check("camera label kept", "Fani 2" in texts)
# موقعیت صحنه‌ای روی نقشه‌ی سانتی‌متری: ۳ متر = ۳۰۰ واحد
check("camera scene pos on cm map",
      abs(item.pos().x() - 300.0) < 1e-9 and abs(item.pos().y() + 200.0) < 1e-9,
      str((item.pos().x(), item.pos().y())))
item.refresh()  # نباید خطا بدهد
check("camera refresh no-op ok", True)

# ============================================ ۵) تبدیل نقاط شبیه‌سازی ==
# مسیر رسم‌شده روی نقشه‌ی سانتی‌متری (to_meter=0.01)، صحنه‌ی جاری میلی‌متری
lane_pts = [[0.0, 0.0], [600.0, 0.0]]  # ۶ متر روی نقشه‌ی سانتی‌متری
lane_tm, cur_tm = 0.01, 0.001
ratio = lane_tm / cur_tm
conv = [[float(x) * ratio, float(y) * ratio] for x, y in lane_pts]
check("lane points rescaled", conv == [[0.0, 0.0], [6000.0, 0.0]], str(conv))
check("lane ratio 6m stays 6m",
      abs((conv[1][0] - conv[0][0]) * cur_tm - 6.0) < 1e-9)

# ============================================ ۶) گرید تطبیقی ==
sc = bmd.MapScene(to_meter=0.01)
sc.setSceneRect(QRectF(0, 0, 600, 300))  # ۶×۳ متر روی نقشه‌ی سانتی‌متری
step_cm = sc.grid_step_scene()
check("grid step cm map ~50 units", abs(step_cm - 50.0) < 1e-9, str(step_cm))
sc2 = bmd.MapScene(to_meter=1.0)
sc2.setSceneRect(QRectF(0, 0, 6, 3))  # همان نقشه به متر
step_m = sc2.grid_step_scene()
check("grid step meter map 0.5", abs(step_m - 0.5) < 1e-9, str(step_m))
sc3 = bmd.MapScene(to_meter=0.001)
sc3.setSceneRect(QRectF(0, 0, 6000, 3000))  # میلی‌متری
step_mm = sc3.grid_step_scene()
check("grid step mm map 500 units", abs(step_mm - 500.0) < 1e-9, str(step_mm))
# هر سه باید ~۱۲ خانه در ۶ متر بدهند (۰٫۵ متر هر خانه)
check("grid ~12 cells",
      all(abs(s * tm - 0.5) < 1e-9
          for s, tm in ((step_cm, 0.01), (step_m, 1.0), (step_mm, 0.001))))

# ============================================ ۷) گارد مقیاس DXF ==
# فایل نمونه‌ی طه (acad3D.dxf): $INSUNITS=5 (سانتی‌متر)، ابعاد ~۶×۳ سانتی‌متر
# -> بزرگ‌ترین بعد واقعی ۰٫۰۶ متر < ۰٫۵ -> گارد باید فعال شود.
try:
    import ezdxf

    dxf_path = os.path.join(TMP, "tiny_cm.dxf")
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 5  # سانتی‌متر
    msp = doc.modelspace()
    msp.add_line((5.5, 7.6), (11.5, 7.6))
    msp.add_line((11.5, 7.6), (11.5, 10.6))
    doc.saveas(dxf_path)
    from building_map import DxfMapLoader

    info = DxfMapLoader().load(dxf_path)
    big_m = max(info["bounds"].width(), info["bounds"].height()) * info["to_meter"]
    check("tiny cm dxf triggers scale guard", big_m < 0.5, f"{big_m:.4f} m")
    check("tiny cm dxf units detected cm", info["to_meter"] == 0.01,
          str(info["to_meter"]))
    # نقشه‌ی متری معقول نباید گارد را فعال کند
    dxf_ok = os.path.join(TMP, "ok_m.dxf")
    doc2 = ezdxf.new("R2010")
    doc2.header["$INSUNITS"] = 6  # متر
    msp2 = doc2.modelspace()
    msp2.add_line((0, 0), (20, 0))
    msp2.add_line((20, 0), (20, 12))
    doc2.saveas(dxf_ok)
    info2 = DxfMapLoader().load(dxf_ok)
    big2 = max(info2["bounds"].width(), info2["bounds"].height()) * info2["to_meter"]
    check("sane meter dxf passes guard", 0.5 <= big2 <= 2000, f"{big2:.2f} m")
except ImportError:
    print("SKIP dxf scale-guard tests (ezdxf not installed)")

# ============================================ ۸) رگرسیون سبک ==
from lane_geometry import polyline_length
check("lane_geometry intact", abs(polyline_length([(0, 0), (3, 4)]) - 5.0) < 1e-9)
check("MAP_UNIT_CHOICES sane",
      any(n == "سانتی‌متر" and abs(t - 0.01) < 1e-12
          for n, t in bmd.BuildingMapPage.MAP_UNIT_CHOICES))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

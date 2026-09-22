# -*- coding: utf-8 -*-
"""تست‌های رگرسیون 2.0.28-beta — برگرداندن قطاع دید دوربین روی نقشه ساختمان.

پس‌زمینه: در 2.0.23-beta قطاع دید (مخروط دید) دوربین کامل حذف و فقط
ایموجی باقی مانده بود؛ در نتیجه تغییر زاویه/پهنای دید/فاصله دید در پنل
مشخصات هیچ بازخورد بصری نداشت و دوربین «درست قرار نمی‌گرفت».
در 2.0.28-beta قطاع دید + بدنه + خط جهت لنز برگردانده شد (ایموجی حفظ شد)
و refresh() دوباره هندسه را درجا به‌روز می‌کند — بدون اینکه موقعیت
صحنه‌ای دست بخورد (رگرسیون باگ قدیمی «پرش دوربین»).

اجرا:
  QT_QPA_PLATFORM=offscreen ~/workspace/testvenv/bin/python tests/test_228_beta.py
"""
import math
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


for _mod in ("face_recognition",):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

import building_map_dialog as bmd
from building_map import device_scene_xy


def make_item(angle=30.0, fov=90.0, view_distance=8.0, to_meter=1.0,
              kind="camera"):
    dev = {
        "id": "d1", "kind": kind, "name": "CAM-1",
        "x": 5.0, "y": 3.0, "pos_unit": "m",
        "angle": angle, "fov": fov, "view_distance": view_distance,
    }
    return bmd.DeviceItem("f1", dev, to_meter, {}), dev


def sector_radius(item):
    path = item._sector.path()
    return max(math.hypot(path.elementAt(i).x, path.elementAt(i).y)
               for i in range(path.elementCount()))


# ============================== وجود قطاع دید ==============================
item, _ = make_item()
check("sector exists for camera", item._sector is not None)
check("tick exists for camera", item._tick is not None)

item_nc, _ = make_item(kind="nvr")
check("no sector for non-camera", item_nc._sector is None)
try:
    item_nc.refresh()
    check("refresh on non-camera does not crash", True)
except Exception as e:  # noqa: BLE001
    check("refresh on non-camera does not crash", False, str(e))

# ============================== مقیاس شعاع ==============================
item, _ = make_item(view_distance=8.0, to_meter=1.0)
check("sector radius == view_distance (tm=1)",
      abs(sector_radius(item) - 8.0) < 0.05)

item, _ = make_item(view_distance=8.0, to_meter=0.001)
check("sector radius scales with to_meter (mm map)",
      abs(sector_radius(item) - 8000.0) / 8000.0 < 0.01)

# ============================== به‌روزرسانی زنده ==============================
item, dev = make_item(angle=30.0)
p0 = item._sector.path()
mid = p0.elementCount() // 2
x0, y0 = p0.elementAt(mid).x, p0.elementAt(mid).y
dev["angle"] = 120.0
item.refresh()
p1 = item._sector.path()
x1, y1 = p1.elementAt(mid).x, p1.elementAt(mid).y
check("angle change moves sector",
      math.hypot(x1 - x0, y1 - y0) > 0.5)

item, dev = make_item(view_distance=8.0)
dev["view_distance"] = 20.0
item.refresh()
check("range change resizes sector",
      abs(sector_radius(item) - 20.0) < 0.05)

item, dev = make_item(fov=60.0)
dev["fov"] = 150.0
item.refresh()
path = item._sector.path()
n = path.elementCount()
fx, fy = path.elementAt(1).x, path.elementAt(1).y
# آخرین عنصر closeSubpath است (برگشت به مرکز)؛ آخرین نقطه‌ی کمان یکی قبل‌تر است
lx, ly = path.elementAt(n - 2).x, path.elementAt(n - 2).y
mouth = math.hypot(lx - fx, ly - fy)
check("fov change widens sector mouth",
      abs(mouth - 2 * 8.0 * math.sin(math.radians(75))) < 0.3)

item, dev = make_item(angle=0.0)
dev["angle"] = 90.0
item.refresh()
line = item._tick.line()
check("tick follows angle (90deg -> up)",
      line.y2() < -1.0 and abs(line.x2()) < 1.0,
      f"({line.x2():.2f}, {line.y2():.2f})")

# ============================== عدم جابه‌جایی موقعیت ==============================
item, dev = make_item()
sx, sy = device_scene_xy(dev, 1.0)
dev["angle"] = 200.0
dev["fov"] = 120.0
dev["view_distance"] = 15.0
item.refresh()
check("refresh does not move item position",
      abs(item.pos().x() - sx) < 1e-9 and abs(item.pos().y() - sy) < 1e-9)

# ---------- جمع‌بندی ----------
print(f"\n{len(_passed)} passed, {len(_failed)} failed")
if _failed:
    print("FAILED:", _failed)
    sys.exit(1)

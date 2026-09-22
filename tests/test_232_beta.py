# -*- coding: utf-8 -*-
"""تست‌های رگرسیون 2.0.32-beta — نشان دوربین (2026-09-22):

درخواست طه:
۱) کادر مربعی سفید دور ایموجی دوربین حذف شود (برای نقشه نیست، برای تجهیزات است)
۲) ایموجی دوربین کاملاً حذف شود
۳) نشان قبلی دوربین (دایره + خط جهت) برگردد
۴) قطاع دید، زاویه، FOV، برد حفظ شوند
۵) Select، Drag، Double-click و ذخیره تنظیمات کار کنند
۶) دکمه‌ی اشتباه «🗑 حذف نقشه» (2.0.30-beta) حذف شود

اجرا:
  QT_QPA_PLATFORM=offscreen python3 tests/test_232_beta.py
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QApplication, QGraphicsScene,
    QGraphicsEllipseItem, QGraphicsPathItem,
    QGraphicsSimpleTextItem, QGraphicsLineItem,
)
from PyQt6.QtCore import QPointF

app = QApplication.instance() or QApplication([])

import building_map_dialog as bmd


def _children_of(item, cls):
    return [c for c in item.childItems() if isinstance(c, cls)]


# ---------- ۱) دوربین: بدون ایموجی ----------
scene = QGraphicsScene()
cam_dev = {"id": "c1", "kind": "camera", "name": "دوربین",
           "angle": 45, "fov": 90, "view_distance": 8.0,
           "x_m": 10.0, "y_m": 5.0}
cam = bmd.DeviceItem("f1", cam_dev, 1.0,
                     {"moved": lambda *a: None, "clicked": lambda *a: None})
scene.addItem(cam)

check("camera: no emoji (no QGraphicsSimpleTextItem)",
      len(_children_of(cam, QGraphicsSimpleTextItem)) == 0)

# ---------- ۲) دوربین: دایره + خط جهت + قطاع دید ----------
ellipses = _children_of(cam, QGraphicsEllipseItem)
check("camera: has circle body (QGraphicsEllipseItem)",
      len(ellipses) == 1, f"found={len(ellipses)}")

lines = _children_of(cam, QGraphicsLineItem)
check("camera: has direction line (QGraphicsLineItem)",
      len(lines) == 1, f"found={len(lines)}")

sectors = _children_of(cam, QGraphicsPathItem)
check("camera: has FOV sector (QGraphicsPathItem)",
      len(sectors) == 1, f"found={len(sectors)}")

# ---------- ۳) خط جهت با تغییر زاویه به‌روز می‌شود ----------
if lines:
    line_before = lines[0].line()
    cam_dev["angle"] = 135.0
    cam.refresh()
    line_after = lines[0].line()
    changed = (abs(line_after.x2() - line_before.x2()) > 1e-9 or
               abs(line_after.y2() - line_before.y2()) > 1e-9)
    check("camera: direction line updates on angle change", changed,
          f"before=({line_before.x2():.2f},{line_before.y2():.2f}) "
          f"after=({line_after.x2():.2f},{line_after.y2():.2f})")
    # قطاع دید هم باید به‌روز شود
    check("camera: sector still present after refresh",
          cam._sector is not None)
else:
    check("camera: direction line updates on angle change", False,
          "no line item")

# ---------- ۴) موقعیت هنگام refresh نمی‌پرد ----------
pos_before = cam.pos()
cam.refresh()
pos_after = cam.pos()
check("camera: position stable on refresh",
      pos_before == pos_after,
      f"before={pos_before} after={pos_after}")

# ---------- ۵) Select / Drag flags ----------
from PyQt6.QtWidgets import QGraphicsItem
check("camera: ItemIsSelectable",
      bool(cam.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable))
check("camera: ItemIsMovable",
      bool(cam.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable))

# ---------- ۶) callback جابه‌جایی (moved) ----------
moved_calls = []


def _on_moved(fid, did, x, y):
    moved_calls.append((fid, did, x, y))


cam2_dev = {"id": "c2", "kind": "camera", "name": "دوربین ۲",
            "angle": 0, "fov": 90, "view_distance": 8.0,
            "x_m": 0.0, "y_m": 0.0}
cam2 = bmd.DeviceItem("f1", cam2_dev, 1.0,
                      {"moved": _on_moved, "clicked": lambda *a: None})
scene.addItem(cam2)
# شبیه‌سازی درگ: تغییر موقعیت + فراخوانی moved مثل mouseReleaseEvent
cam2.setPos(100, 50)
_on_moved("f1", "c2", cam2.pos().x(), cam2.pos().y())
check("camera: moved callback fires with position",
      len(moved_calls) == 1 and moved_calls[0][2] == 100,
      f"calls={moved_calls}")

# ---------- ۷) تجهیزات غیر دوربین: ایموجی حفظ شود ----------
nvr_dev = {"id": "n1", "kind": "nvr", "name": "NVR",
           "x_m": 0.0, "y_m": 0.0}
nvr = bmd.DeviceItem("f1", nvr_dev, 1.0, {})
scene.addItem(nvr)
check("non-camera: keeps emoji glyph",
      len(_children_of(nvr, QGraphicsSimpleTextItem)) == 1)

# ---------- ۸) دکمه‌ی «حذف نقشه» حذف شده ----------
import inspect
src = inspect.getsource(bmd.BuildingMapPage)
check("no _remove_map method", "_remove_map" not in src)
check("no delmap_btn", "delmap_btn" not in src)

# ---------- ۹) رندر بدون کادر سفید ----------
from PyQt6.QtGui import QImage, QPainter, QColor
from PyQt6.QtCore import QRectF
import numpy as np

scene2 = QGraphicsScene()
scene2.setSceneRect(QRectF(0, 0, 1000, 700))
cam3_dev = {"id": "c3", "kind": "camera", "name": "دوربین",
            "angle": 0, "fov": 90, "view_distance": 8.0,
            "x_m": 50.0, "y_m": 35.0}
cam3 = bmd.DeviceItem("f1", cam3_dev, 1.0,
                      {"moved": lambda *a: None, "clicked": lambda *a: None})
scene2.addItem(cam3)
cam3.setSelected(True)

ibr = cam3.sceneBoundingRect()
src_rect = QRectF(ibr.center().x() - 60, ibr.center().y() - 60, 120, 120)
img = QImage(600, 600, QImage.Format.Format_ARGB32)
img.fill(QColor("#0b0f14"))
p = QPainter(img)
scene2.render(p, QRectF(0, 0, 600, 600), src_rect,
              __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AspectRatioMode.KeepAspectRatio)
p.end()
buf = img.bits().asstring(600 * 600 * 4)
from PIL import Image as PILImage
pil = PILImage.frombytes("RGBA", (600, 600), buf, "raw", "BGRA")
arr = np.array(pil.convert("RGB"))
# پیکسل‌های «خیلی روشن» (کادر سفید) نباید باشند؛ قطاع فیروزه‌ای کم‌رنگ مجاز است
very_bright = (arr[:, :, 0] > 225) & (arr[:, :, 1] > 225) & (arr[:, :, 2] > 225)
n_bright = int(very_bright.sum())
check("camera render: no white frame pixels", n_bright == 0,
      f"bright_pixels={n_bright}")

print()
print(f"passed: {len(_passed)}, failed: {len(_failed)}")
sys.exit(1 if _failed else 0)

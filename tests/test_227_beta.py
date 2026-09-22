# -*- coding: utf-8 -*-
"""تست‌های رگرسیون 2.0.27-beta — رفع باگ «ویرایش شکل تشخیص هوشمند زمین
هنگام تایید نادیده گرفته می‌شد».

باگ: بعد از «🧭 تشخیص هوشمند زمین (AI)»، کاربر گوشه‌های شکل را با ماوس
جابه‌جا می‌کرد، ولی با زدن «✅ تایید» شکلِ خامِ تشخیص (نه شکل ویرایش‌شده)
ذخیره می‌شد — چون CameraSlotWidget.confirm_region نقاط را از لیست قدیمیِ
self.pending_points می‌خواند، در حالی که ویرایش ماوس فقط روی کپیِ داخل
VideoDisplayLabel اعمال می‌شود. (ویرایشِ دوم روی محدوده‌ی تاییدشده درست
کار می‌کرد چون save_region_edit از video_label می‌خواند.)

رفع: confirm_region حالا نقاط را از video_label.pending_points_norm()
می‌خواند — دقیقاً مثل save_region_edit.

اجرا:
  LD_LIBRARY_PATH=/tmp/eglextract/usr/lib/x86_64-linux-gnu \
  QT_QPA_PLATFORM=offscreen ~/workspace/testvenv/bin/python tests/test_227_beta.py
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# --- استاب وابستگی‌های سنگین (همان الگوی test_217_beta.py، ولی عمومی‌تر) ---
class _Dummy:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Dummy()

    def __getattr__(self, n):
        if n.startswith("__") and n.endswith("__"):
            raise AttributeError(n)
        return _Dummy()


fr = types.ModuleType("face_recognition")
fr.__getattr__ = lambda n: (lambda *a, **k: [])
sys.modules["face_recognition"] = fr

_cv2 = types.ModuleType("cv2")
_cv2.__getattr__ = lambda n: _Dummy()
_cv2.resize = lambda *a, **k: a[0] if a else None
_cv2.imwrite = lambda *a, **k: True
sys.modules["cv2"] = _cv2

_passed = []
_failed = []


def check(name, cond, extra=""):
    (_passed if cond else _failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" | {extra}" if extra and not cond else ""))


from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

import main as m

# ============================== سناریوی باگ ==============================
# ۱) خروجی فرضی «تشخیص هوشمند زمین» روی خانه قرار می‌گیرد
slot = m.CameraSlotWidget(lambda *a: None, lambda *a: None)
ai_points = [(0.10, 0.60), (0.90, 0.60), (0.95, 0.95), (0.05, 0.95)]
ok = slot.start_ai_floor_region(ai_points)
check("start_ai_floor_region accepts AI polygon", ok is True)

# ۲) کاربر گوشه‌ی اول را با ماوس جابه‌جا می‌کند — دقیقاً همان کاری که
#    VideoDisplayLabel.mouseMoveEvent انجام می‌دهد (فقط کپیِ داخل لیبل
#    عوض می‌شود، نه self.pending_points خانه)
edited = (0.30, 0.40)
slot.video_label._pending_points[0] = edited
check("label holds edited points",
      slot.video_label.pending_points_norm()[0] == edited)
check("slot.pending_points still has stale AI points (precondition)",
      slot.pending_points[0] == ai_points[0])

# ۳) تایید — باید شکلِ ویرایش‌شده ذخیره شود، نه شکل خام تشخیص
region = slot.confirm_region("زمین")
check("confirm_region returns a region", region is not None)
saved = region["points"] if region else None
check("confirmed region keeps user's edit (not raw AI shape)",
      saved is not None and saved[0] == edited,
      f"saved[0]={saved[0] if saved else None} expected={edited}")
check("confirmed region keeps unedited corners",
      saved is not None and saved[1:] == [tuple(p) for p in ai_points[1:]])
check("pending cleared after confirm",
      slot.pending_points is None
      and slot.video_label.pending_points_norm() is None)

# ============================== بدون ویرایش ==============================
slot2 = m.CameraSlotWidget(lambda *a: None, lambda *a: None)
slot2.start_ai_floor_region(ai_points)
region2 = slot2.confirm_region("زمین ۲")
check("confirm without edit keeps AI polygon",
      region2 is not None
      and region2["points"] == [tuple(p) for p in ai_points])

# ============================== ویرایش محدوده‌ی تاییدشده ================
# (رفتار قبلیِ درستِ save_region_edit نباید پسرفت کند)
slot3 = m.CameraSlotWidget(lambda *a: None, lambda *a: None)
slot3.start_ai_floor_region(ai_points)
r3 = slot3.confirm_region("زمین ۳")
rid = r3["id"]
check("start_edit_region works", slot3.start_edit_region(rid) is True)
slot3.video_label._pending_points[2] = (0.80, 0.80)
r3b = slot3.save_region_edit()
check("save_region_edit keeps edit",
      r3b is not None and r3b["points"][2] == (0.80, 0.80))

# ---------- جمع‌بندی ----------
print(f"\n{len(_passed)} passed, {len(_failed)} failed")
if _failed:
    print("FAILED:", _failed)
    sys.exit(1)

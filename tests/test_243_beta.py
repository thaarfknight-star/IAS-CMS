# -*- coding: utf-8 -*-
"""تست‌های 2.0.50-beta: تازه‌سازی استریم‌های زنده بعد از آپلود لایسنس.

سناریوی گزارش‌شده (باگ ۲ طه): لایسنس بدون پلاک‌خوان بارگذاری شد ولی
پلاک‌خوانِ دوربینِ باز همچنان فعال بود — چون enforce_quotas فقط رکورد
ذخیره‌شده را اصلاح می‌کرد و موتور در حال اجرا دست‌نخورده می‌ماند.
رفع: MainWindow.reapply_live_feature_flags بعد از آپلود صدا زده می‌شود و
وضعیت موتورهای باز را از روی رکورد تازه بازنویسی می‌کند.

همچنین: دکمه‌ی «مشاهده‌ی لایسنس» در صفحه‌ی تنظیمات باید به
open_license_dialog وصل باشد (قبلاً یتیم بود و مشتری به HWID دسترسی نداشت).
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


MAIN_SRC = Path(__file__).resolve().parent.parent / "main.py"
SETTINGS_SRC = Path(__file__).resolve().parent.parent / "settings_page.py"


def _extract_method(cls_name, method_name):
    """استخراج سورس یک متد از روی AST و اجرای آن روی آبجکت تقلبی."""
    tree = ast.parse(MAIN_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and item.name == method_name:
                    mod = ast.Module(body=[item], type_ignores=[])
                    ns = {}
                    exec(compile(mod, "<extracted>", "exec"), ns)
                    return ns[method_name]
    raise AssertionError(f"method {cls_name}.{method_name} not found")


class FakeStreamState:
    """وضعیت موتور یک اسلات باز (جای stream_thread)."""

    def __init__(self):
        self.calls = []

    def set_plate_detection(self, v):
        self.calls.append(("plate", bool(v)))

    def set_fire_detection(self, v):
        self.calls.append(("fire", bool(v)))

    def set_person_tracking(self, v):
        self.calls.append(("person", bool(v)))


class FakeSlot:
    def __init__(self, cam):
        self.cam = cam
        self.state = FakeStreamState()

    def set_plate_detection(self, v):
        self.state.set_plate_detection(v)

    def set_fire_detection(self, v):
        self.state.set_fire_detection(v)

    def set_person_tracking(self, v):
        self.state.set_person_tracking(v)


class FakeGrid:
    def __init__(self, slots):
        self.slots = slots


class FakeStore:
    def __init__(self, cams):
        self._cams = {c["id"]: c for c in cams}

    def get_camera(self, cam_id):
        return self._cams.get(cam_id)


class FakeWin:
    def __init__(self, grid, store):
        self.camera_grid = grid
        self.camera_store = store


print("== reapply_live_feature_flags ==")
reapply = _extract_method("MainWindow", "reapply_live_feature_flags")

# ۱) دوربین باز که تیک پلاک‌خوانش برداشته شده → موتور خاموش می‌شود
cam_rec = {"id": "c1", "name": "دوربین ۱",
           "plate_detection": False, "fire_detection": True,
           "person_tracking": False}
slot = FakeSlot(cam_rec)  # همان رفرنس دیکشنری (مثل رفتار واقعی)
win = FakeWin(FakeGrid([slot]), FakeStore([cam_rec]))
reapply(win)
calls = dict((k, v) for k, v in slot.state.calls)
check("پلاک‌خوان روی استریم باز خاموش شد", calls.get("plate") is False)
check("حریق روشن ماند", calls.get("fire") is True)
check("ردیابی اشخاص خاموش ماند", calls.get("person") is False)

# ۲) هر سه موتور روی دو اسلات جدا اعمال می‌شود
c1 = {"id": "c1", "plate_detection": True, "fire_detection": False,
      "person_tracking": True}
c2 = {"id": "c2", "plate_detection": False, "fire_detection": False,
      "person_tracking": False}
s1, s2 = FakeSlot(c1), FakeSlot(c2)
win = FakeWin(FakeGrid([s1, s2]), FakeStore([c1, c2]))
reapply(win)
d1 = dict((k, v) for k, v in s1.state.calls)
d2 = dict((k, v) for k, v in s2.state.calls)
check("اسلات ۱: هر سه پرچم اعمال شد",
      d1 == {"plate": True, "fire": False, "person": True})
check("اسلات ۲: هر سه پرچم اعمال شد",
      d2 == {"plate": False, "fire": False, "person": False})

# ۳) اسلات خالی (cam=None) نباید کرش بدهد
s_empty = FakeSlot(None)
win = FakeWin(FakeGrid([s_empty]), FakeStore([]))
try:
    reapply(win)
    check("اسلات خالی کرش نمی‌دهد", True)
except Exception:
    check("اسلات خالی کرش نمی‌دهد", False)

# ۴) دوربینی که از استور حذف شده → از همان cam اسلات استفاده می‌شود
c3 = {"id": "c3", "plate_detection": True, "fire_detection": True,
      "person_tracking": True}
s3 = FakeSlot(c3)
win = FakeWin(FakeGrid([s3]), FakeStore([]))  # رکوردی در استور نیست
reapply(win)
d3 = dict((k, v) for k, v in s3.state.calls)
check("رکورد حذف‌شده: از cam اسلات استفاده شد",
      d3 == {"plate": True, "fire": True, "person": True})

print("== settings: دکمه مشاهده لایسنس ==")
src = SETTINGS_SRC.read_text(encoding="utf-8")
check("دکمه «مشاهده‌ی لایسنس» ساخته می‌شود",
      "مشاهده‌ی لایسنس" in src)
check("به open_license_dialog وصل است",
      "open_license_dialog" in src and "_on_license_view" in src)
check("بعد از آپلود، reapply_live_feature_flags صدا زده می‌شود",
      "reapply_live_feature_flags" in src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

# -*- coding: utf-8 -*-
"""تست‌های 2.0.49-beta: enforce_quotas — کم‌شدن سقف لایسنس.

سناریوی گزارش‌شده: ۲ دوربین پلاک‌خوان با لایسنس نامحدود، بعد لایسنس جدید
با سقف ۱ → تیک پلاک‌خوان باید از روی یک دوربین برداشته شود و به کاربر
اطلاع داده شود.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import admin_quota
import license as licmod

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


class FakeStore:
    def __init__(self, cams):
        self.cams = cams

    def get_cameras(self):
        return self.cams

    def update_camera(self, cam_id, **fields):
        for c in self.cams:
            if c["id"] == cam_id:
                c.update(fields)
                return c
        return None


def _cam(name, **flags):
    d = {"id": "id-" + name, "name": name, "ip": "192.168.1.1"}
    d.update(flags)
    return d


_orig_valid = admin_quota._license_valid
_orig_eq = licmod.effective_quotas
_orig_iqe = licmod.is_quota_enabled


def patch(valid=True, quotas=None, qenabled=None):
    q = quotas or {"cameras": 0, "plate": 0, "fire": 0, "person_tracking": 0}
    qe = qenabled or {}
    admin_quota._license_valid = lambda: valid
    licmod.effective_quotas = lambda state=None: dict(q)
    licmod.is_quota_enabled = lambda k, state=None: qe.get(k, True)


def unpatch():
    admin_quota._license_valid = _orig_valid
    licmod.effective_quotas = _orig_eq
    licmod.is_quota_enabled = _orig_iqe


try:
    print("== enforce_quotas ==")

    # ۱) سناریوی طه: ۲ پلاک‌خوان، سقف جدید ۱
    patch(quotas={"cameras": 0, "plate": 1, "fire": 0, "person_tracking": 0})
    store = FakeStore([_cam("دوربین ۱", plate_detection=True),
                       _cam("دوربین ۲", plate_detection=True)])
    lines = admin_quota.enforce_quotas(store)
    kept = [c for c in store.cams if c.get("plate_detection")]
    removed = [c for c in store.cams if not c.get("plate_detection")]
    check("دقیقاً یک دوربین پلاک‌خوان می‌ماند", len(kept) == 1)
    check("یک دوربین تیکش برداشته شد", len(removed) == 1)
    check("گزارش یک خط دارد", len(lines) == 1)
    check("گزارش اسم دوربین حذف‌شده را دارد",
          lines and "دوربین ۲" in lines[0])
    check("گزارش سقف جدید را می‌گوید", lines and "سقف لایسنس جدید: 1" in lines[0])

    # ۲) نامحدود → بدون تغییر
    patch(quotas={"cameras": 0, "plate": 0, "fire": 0, "person_tracking": 0})
    store = FakeStore([_cam("A", plate_detection=True),
                       _cam("B", plate_detection=True)])
    lines = admin_quota.enforce_quotas(store)
    check("نامحدود: بدون تغییر و بدون گزارش",
          lines == [] and all(c.get("plate_detection") for c in store.cams))

    # ۳) سهمیه کلاً غیرفعال (صفر واقعی) → همه‌ی تیک‌ها برداشته می‌شود
    patch(quotas={"cameras": 0, "plate": 5, "fire": 0, "person_tracking": 0},
          qenabled={"plate": False})
    store = FakeStore([_cam("A", plate_detection=True),
                       _cam("B", plate_detection=True)])
    lines = admin_quota.enforce_quotas(store)
    check("غیرفعال: همه‌ی تیک‌ها برداشته شد",
          all(not c.get("plate_detection") for c in store.cams))
    check("گزارش «غیرفعال است» را می‌گوید",
          lines and "غیرفعال است" in lines[0])

    # ۴) در سقف بودن → بدون تغییر
    patch(quotas={"cameras": 0, "plate": 0, "fire": 1, "person_tracking": 0})
    store = FakeStore([_cam("A", fire_detection=True),
                       _cam("B", fire_detection=False)])
    lines = admin_quota.enforce_quotas(store)
    check("در سقف: بدون تغییر", lines == [] and store.cams[0]["fire_detection"])

    # ۵) سقف تعداد کل دوربین‌ها → فقط هشدار، بدون حذف
    patch(quotas={"cameras": 2, "plate": 0, "fire": 0, "person_tracking": 0})
    store = FakeStore([_cam("A"), _cam("B"), _cam("C")])
    lines = admin_quota.enforce_quotas(store)
    check("دوربین‌ها حذف نمی‌شوند", len(store.cams) == 3)
    check("هشدار حذف دستی داده می‌شود",
          len(lines) == 1 and "حذف کنید" in lines[0])

    # ۶) بدون لایسنس معتبر → هیچ کاری
    patch(valid=False)
    store = FakeStore([_cam("A", plate_detection=True)])
    lines = admin_quota.enforce_quotas(store)
    check("بدون لایسنس: بدون تغییر",
          lines == [] and store.cams[0]["plate_detection"])
finally:
    unpatch()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

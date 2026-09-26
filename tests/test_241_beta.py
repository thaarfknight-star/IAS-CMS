# -*- coding: utf-8 -*-
"""تست‌های 2.0.48-beta (بخش دوم): پنجره‌های اطلاع‌رسانی.

- describe_license_changes: تفاوت وضعیت قبلی/جدید لایسنس به فارسی
- changelog_between / get_update_notice / clear_update_notice در updater.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from license import (LicenseState, describe_license_changes,
                     effective_features, effective_quotas)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


def _lic(valid=True, features=None, quotas=None, qe=None, customer="مشتری تست",
         expires="2027-01-01"):
    return LicenseState(
        valid=valid, customer=customer, issued="2026-09-26", expires=expires,
        quotas=quotas or {"cameras": 0, "plate": 0, "fire": 0,
                          "person_tracking": 0},
        hwid=None,
        features=features or {"people_counting": True,
                              "face_recognition": True,
                              "zone_alerts": True},
        quota_enabled=qe or {"cameras": True, "plate": True, "fire": True,
                             "person_tracking": True},
        path="x")


print("== describe_license_changes ==")
no_lic = LicenseState(valid=False, error="یافت نشد")
full = _lic()

lines = describe_license_changes(no_lic, full)
text = "\n".join(lines)
check("بدون لایسنس -> کامل: چهره‌خوان فعال شد",
      any("چهره‌خوان" in l and "فعال شد" in l for l in lines))
check("بدون لایسنس -> کامل: هشدار محدوده فعال شد",
      any("هشدار ورود به محدوده" in l and "فعال شد" in l for l in lines))
check("بدون لایسنس -> کامل: سهمیه پلاک فعال شد",
      any("پلاک‌خوان" in l and "فعال شد" in l for l in lines))
check("سقف نامحدود با «نامحدود» نمایش داده می‌شود",
      any("نامحدود" in l for l in lines))

# فعال‌سازی یک سهمیه‌ی غیرفعال با سقف عددی
old = _lic(qe={"cameras": True, "plate": False, "fire": True,
               "person_tracking": True})
new = _lic(quotas={"cameras": 0, "plate": 4, "fire": 0, "person_tracking": 0})
lines = describe_license_changes(old, new)
check("فعال‌شدن پلاک با سقف ۴",
      any("پلاک‌خوان" in l and "فعال شد" in l and "4" in l for l in lines))

# تغییر سقف ۲ -> نامحدود
old2 = _lic(quotas={"cameras": 0, "plate": 2, "fire": 0, "person_tracking": 0})
new2 = _lic()
lines = describe_license_changes(old2, new2)
check("تغییر سقف ۲ -> نامحدود گزارش می‌شود",
      any("سقف" in l and "2" in l and "نامحدود" in l for l in lines))

# غیرفعال‌شدن یک قابلیت
old3 = _lic()
new3 = _lic(features={"people_counting": True, "face_recognition": False,
                      "zone_alerts": True})
lines = describe_license_changes(old3, new3)
check("غیرفعال‌شدن چهره‌خوان گزارش می‌شود",
      any("چهره‌خوان" in l and "غیرفعال شد" in l for l in lines))

# بدون تغییر
lines = describe_license_changes(full, _lic())
check("لایسنس یکسان -> بدون خط تغییر", lines == [])

print("== updater: changelog_between / notice ==")
import updater

entries = updater.changelog_between("2.0.47-beta", "2.0.48-beta")
check("ورودی 2.0.48 بین 2.0.47 و 2.0.48 پیدا شد",
      any(v == "2.0.48-beta" for v, d, b in entries))
check("ورودی 2.0.47 جزو بازه نیست",
      all(v != "2.0.47-beta" for v, d, b in entries))

entries = updater.changelog_between("2.0.48-beta", "2.0.48-beta")
check("بازه‌ی خالی -> لیست خالی", entries == [])

entries = updater.changelog_between("", "2.0.48-beta")
check("بدون نسخه‌ی قبلی -> فقط تا سقف کاراکتر",
      len(entries) >= 1 and all(
          updater._ver_tuple(v) <= updater._ver_tuple("2.0.48-beta")
          for v, d, b in entries))

# نشانگر آپدیت با دایرکتوری موقت
tmp = Path(tempfile.mkdtemp())
_orig = updater.get_install_dir
updater.get_install_dir = lambda: tmp
try:
    check("بدون نشانگر -> None", updater.get_update_notice() is None)
    (tmp / "update_applied.json").write_text(
        json.dumps({"prev_version": "2.0.47-beta",
                    "new_version": "2.0.48-beta"}), encoding="utf-8")
    n = updater.get_update_notice()
    check("نشانگر خوانده می‌شود",
          n and n.get("new_version") == "2.0.48-beta")
    updater.clear_update_notice()
    check("بعد از clear نشانگر پاک است",
          updater.get_update_notice() is None
          and not (tmp / "update_applied.json").exists())
finally:
    updater.get_install_dir = _orig

print(f"\n{ PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

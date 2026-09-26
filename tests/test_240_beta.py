"""Headless regression test for IAS-CMS 2.0.48-beta (quota_enabled).

۱) لایسنس جدید با quota_enabled (حریق غیرفعال): امضا معتبر،
   is_quota_enabled درست کار می‌کند.
۲) check_quota برای سهمیه‌ی غیرفعال → (False, used, -1).
۳) quota_denied_message با ۱- → پیام «غیرفعال است».
۴) لایسنس قدیمی بدون quota_enabled (دوباره امضاشده) → همه فعال (عقب‌رو).
۵) دست‌کاری quota_enabled → امضا باطل می‌شود.
۶) سهمیه‌ی غیرفعال دوربین‌ها → افزودن دوربین مسدود.
۷) رفتار قبلی (سهمیه‌ی عددی) دست‌نخورده مانده.
"""
import json
import os
import sys
import tempfile
import types
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr
sys.modules["cv2"] = MagicMock(name="cv2")

passed = []
failed = []


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


import license_crypto as lc
import license as licmod
import admin_quota as aq

TEST_SK, TEST_PK = lc.generate_keypair()
_orig_pk = licmod.LICENSE_PUBLIC_KEY
licmod.LICENSE_PUBLIC_KEY = TEST_PK


def make_payload(**kw):
    p = {
        "customer": "تست",
        "issued": str(date.today()),
        "expires": None,
        "hwid": None,
        "quotas": {"cameras": 4, "plate": 2, "fire": 1, "person_tracking": 0},
        "quota_enabled": {"cameras": True, "plate": True,
                          "fire": False, "person_tracking": True},
        "features": {"people_counting": True, "face_recognition": False,
                     "zone_alerts": True},
    }
    p.update(kw)
    return p


def sign(payload):
    return licmod.create_license_file(payload, TEST_SK)


class FakeStore:
    def __init__(self, cams):
        self._cams = cams

    def get_cameras(self):
        return self._cams


tmp = tempfile.mkdtemp()
lic_path = os.path.join(tmp, "license.lic")
with open(lic_path, "w", encoding="utf-8") as f:
    json.dump(sign(make_payload()), f, ensure_ascii=False)

with patch.object(licmod, "find_license_file", return_value=lic_path):
    st = licmod.load_license()
    check("license with quota_enabled loads valid", st.valid)

    # ۱) is_quota_enabled
    check("fire disabled in license",
          not licmod.is_quota_enabled("fire"))
    check("plate enabled in license",
          licmod.is_quota_enabled("plate"))
    check("cameras enabled in license",
          licmod.is_quota_enabled("cameras"))

    # ۲) check_quota برای سهمیه‌ی غیرفعال
    store = FakeStore([{"id": 1, "fire_detection": True}])
    ok, used, quota = aq.check_quota("fire", store)
    check("disabled quota -> denied with -1",
          not ok and quota == -1)

    # ۳) پیام غیرفعال بودن
    msg = aq.quota_denied_message("fire", 1, -1)
    check("disabled message says inactive", "غیرفعال است" in msg)

    # ۷) رفتار عددی قبلی سر جایش است
    store2 = FakeStore([{"id": 1, "plate_detection": True},
                        {"id": 2, "plate_detection": True}])
    ok, used, quota = aq.check_quota("plate", store2, exclude_cam_id=99)
    check("numeric quota still enforced", not ok and quota == 2)
    ok, used, quota = aq.check_quota("person_tracking", store2)
    check("unlimited (0) still allowed", ok and quota == 0)

# ۴) لایسنس قدیمی بدون quota_enabled → همه فعال
legacy = sign(make_payload())
del legacy["payload"]["quota_enabled"]
legacy = licmod.create_license_file(legacy["payload"], TEST_SK)  # امضای دوباره
legacy_path = os.path.join(tmp, "legacy.lic")
with open(legacy_path, "w", encoding="utf-8") as f:
    json.dump(legacy, f, ensure_ascii=False)
with patch.object(licmod, "find_license_file", return_value=legacy_path):
    st = licmod.load_license()
    check("legacy license loads valid", st.valid)
    check("legacy: all quotas enabled",
          all(licmod.is_quota_enabled(k)
              for k in licmod.QUOTA_ORDER))

# ۵) دست‌کاری quota_enabled → امضا باطل
tampered = json.loads(json.dumps(sign(make_payload())))
tampered["payload"]["quota_enabled"]["fire"] = True
p5, err5 = licmod.verify_license_data(tampered)
check("tampered quota_enabled rejected", p5 is None and err5 is not None)

# ۶) غیرفعال بودن سهمیه‌ی دوربین‌ها → افزودن دوربین مسدود
nocam = sign(make_payload(
    quota_enabled={"cameras": False, "plate": True,
                   "fire": True, "person_tracking": True}))
nocam_path = os.path.join(tmp, "nocam.lic")
with open(nocam_path, "w", encoding="utf-8") as f:
    json.dump(nocam, f, ensure_ascii=False)
with patch.object(licmod, "find_license_file", return_value=nocam_path):
    ok, used, quota = aq.check_quota("cameras", FakeStore([]))
    check("disabled cameras quota blocks adding",
          not ok and quota == -1)
    msg = aq.quota_denied_message("cameras", 0, -1)
    check("cameras disabled message", "غیرفعال است" in msg)

licmod.LICENSE_PUBLIC_KEY = _orig_pk

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

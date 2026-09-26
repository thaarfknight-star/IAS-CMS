"""Headless regression test for IAS-CMS 2.0.45-beta (full licensing).

۱) چرخه‌ی امضا/تأیید لایسنس با جفت‌کلید تازه (نه کلید واقعی فروشنده):
   ساخت payload، امضا، تأیید موفق.
۲) دست‌کاری payload (تغییر سهمیه) → امضا باطل می‌شود.
۳) لایسنس منقضی → نامعتبر.
۴) قفل سخت‌افزاری ناهماهنگ → نامعتبر.
۵) بدون فایل لایسنس → حالت محدود (effective_quotas = سقف نمایشی).
۶) check_quota در حالت محدود: قابلیت هوشمند مسدود، دوربین دوم مسدود.
۷) رگرسیون: verify_admin_password و quota_title و guard_feature_enable
   با لایسنس معتبر مثل قبل کار می‌کنند (سهمیه‌ی پر → مسدود).
"""
import json
import os
import sys
import tempfile
import types
from datetime import date, timedelta
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
from license import (canonical_bytes, create_license_file, verify_license_data,
                     check_payload_validity, effective_quotas, LicenseState,
                     DEMO_QUOTAS)

# --- جفت‌کلید تازه فقط برای تست (کلید واقعی فروشنده استفاده نمی‌شود) ---
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
    }
    p.update(kw)
    return p


def sign(payload):
    return create_license_file(payload, TEST_SK)


# ۱) چرخه‌ی سالم
lic = sign(make_payload())
payload, err = verify_license_data(lic)
check("valid license verifies", err is None and payload["customer"] == "تست")
check("no expiry error for lifetime", check_payload_validity(payload) is None)

# ۲) دست‌کاری
tampered = json.loads(json.dumps(lic))
tampered["payload"]["quotas"]["plate"] = 999
p2, err2 = verify_license_data(tampered)
check("tampered payload rejected", p2 is None and err2 is not None)

# امضای فایل دیگر روی payload این فایل
other = sign(make_payload(customer="دیگر"))
lic_bad_sig = dict(lic)
lic_bad_sig["signature"] = other["signature"]
p3, err3 = verify_license_data(lic_bad_sig)
check("foreign signature rejected", p3 is None)

# ۳) انقضا
exp = sign(make_payload(expires=str(date.today() - timedelta(days=1))))
pe, _ = verify_license_data(exp)
check("expired license detected",
      pe is not None and check_payload_validity(pe) is not None)

# ۴) قفل سخت‌افزاری
hw = sign(make_payload(hwid="WIN-FAKE-GUID-1234"))
ph, _ = verify_license_data(hw)
check("hwid mismatch rejected",
      ph is not None and check_payload_validity(ph) is not None)

# ۵) بدون فایل → حالت محدود
with patch.object(licmod, "find_license_file", return_value=None):
    st = licmod.load_license()
    check("missing license -> invalid state",
          not st.valid and "یافت نشد" in st.error)
    check("missing license -> demo quotas",
          effective_quotas(st) == DEMO_QUOTAS)

# ۶) check_quota در حالت محدود
import admin_quota as aq


class FakeStore:
    def __init__(self, cams):
        self._cams = cams

    def get_cameras(self):
        return self._cams


with patch.object(licmod, "find_license_file", return_value=None):
    store = FakeStore([{"id": 1}])
    ok, used, quota = aq.check_quota("plate", store)
    check("restricted: plate blocked", not ok)
    ok, used, quota = aq.check_quota("cameras", store)
    check("restricted: 2nd camera blocked", not ok and quota == 1)
    ok, used, quota = aq.check_quota("cameras", FakeStore([]))
    check("restricted: 1st camera allowed", ok)
    msg = aq.quota_denied_message("plate", 0, 0)
    check("restricted message mentions license", "لایسنس" in msg)

# ۷) رگرسیون با لایسنس معتبر (امضاشده با کلید تست)
tmp = tempfile.mkdtemp()
lic_path = os.path.join(tmp, "license.lic")
with open(lic_path, "w", encoding="utf-8") as f:
    json.dump(sign(make_payload()), f, ensure_ascii=False)

with patch.object(licmod, "find_license_file", return_value=lic_path):
    st = licmod.load_license()
    check("test-signed license loads valid", st.valid)
    check("quotas come from license",
          licmod.effective_quotas()["plate"] == 2)
    store = FakeStore([
        {"id": 1, "plate_detection": True},
        {"id": 2, "plate_detection": True},
        {"id": 3, "plate_detection": False},
    ])
    ok, used, quota = aq.check_quota("plate", store, exclude_cam_id=3)
    check("quota full -> blocked", not ok and used == 2 and quota == 2)
    ok, used, quota = aq.check_quota("fire", store)
    check("fire quota 1 free -> allowed", ok and quota == 1)
    ok, used, quota = aq.check_quota("person_tracking", store)
    check("unlimited quota (0) -> allowed", ok and quota == 0)
    msg = aq.quota_denied_message("plate", 2, 2)
    check("denied message has quota info", "2 از 2" in msg)

# رگرسیون رمز ادمین
check("quota_title works", aq.quota_title("plate") == "دوربین پلاک‌خوان")
check("wrong admin password rejected",
      not aq.verify_admin_password("definitely-wrong-password"))

licmod.LICENSE_PUBLIC_KEY = _orig_pk

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

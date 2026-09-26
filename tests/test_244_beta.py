"""Headless regression test for IAS-CMS 2.0.51-beta.

رگرسیون باگ واقعی گزارش‌شده توسط طه (2026-09-26):
«با لایسنس پلاک=۱ تونستم پلاک‌خوان رو روی ۲ دوربین فعال کنم.»

علت ریشه‌ای: `check_quota`/`count_usage` در admin_quota.py صدا می‌زدند
`camera_store.get_cameras()` ولی همچین متدی روی کلاس واقعی CameraStore
وجود نداشت! AttributeError داخل try/except قورت داده می‌شد، مصرف همیشه
۰ حساب می‌شد و سهمیه هیچ‌وقت پر نمی‌شد. تست‌های قبلی (test_239) با
FakeStore که get_cameras داشت تست می‌کردند — برای همین باگ دیده نشد.

این تست از کلاس واقعی CameraStore استفاده می‌کند:
۱) CameraStore.get_cameras وجود دارد و همه‌ی دوربین‌ها را برمی‌گرداند.
۲) count_usage مصرف واقعی را می‌شمارد (نه همیشه ۰).
۳) check_quota با لایسنس واقعی (امضاشده با کلید تست): پلاک=۱ و یک دوربین
   فعال → فعال‌سازی روی دوربین دوم بلاک می‌شود (used=1, quota=1).
۴) ذخیره‌ی مجدد همان دوربین فعال بلاک نمی‌شود (exclude_cam_id).
۵) guard_feature_enable تیک را برمی‌گرداند و False می‌دهد.
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
from camera_store import CameraStore

TEST_SK, TEST_PK = lc.generate_keypair()
_orig_pk = licmod.LICENSE_PUBLIC_KEY
licmod.LICENSE_PUBLIC_KEY = TEST_PK

# --- استور واقعی با مسیرهای موقت ---
tmp = tempfile.mkdtemp()
store = CameraStore(path=os.path.join(tmp, "cameras.json"),
                    nvr_path=os.path.join(tmp, "nvrs.json"))

# ۱) متد get_cameras روی کلاس واقعی وجود دارد
check("CameraStore has get_cameras", hasattr(store, "get_cameras"))

store.add_camera("دوربین ۱", "192.168.1.10", 554, "admin", "", "live/ch0")
store.add_camera("دوربین ۲", "192.168.1.11", 554, "admin", "", "live/ch0")
cams = store.get_cameras()
check("get_cameras returns both cameras", len(cams) == 2)
cam1, cam2 = cams[0]["id"], cams[1]["id"]
store.update_camera(cam1, plate_detection=True)

# ۲) شمارش مصرف واقعی است (باگ: همیشه ۰ بود)
check("count_usage counts real usage", aq.count_usage(store, "plate") == 1)

# --- لایسنس تست: تعداد کل=۲، پلاک=۱ ---
payload = {
    "customer": "تست",
    "issued": str(date.today()),
    "expires": None,
    "hwid": None,
    "quotas": {"cameras": 2, "plate": 1, "fire": 0, "person_tracking": 0},
    "features": {"people_counting": True, "face_recognition": True,
                 "zone_alerts": True},
    "quota_enabled": {"cameras": True, "plate": True, "fire": True,
                      "person_tracking": True},
}
lic_path = os.path.join(tmp, "license.lic")
with open(lic_path, "w", encoding="utf-8") as f:
    json.dump(licmod.create_license_file(payload, TEST_SK), f,
              ensure_ascii=False)

with patch.object(licmod, "find_license_file", return_value=lic_path):
    st = licmod.load_license()
    check("test license valid", st.valid)

    # ۳) سناریوی طه: پلاک=۱، یک دوربین فعال → دومی بلاک
    ok, used, quota = aq.check_quota("plate", store, exclude_cam_id=cam2)
    check("second plate camera blocked", not ok)
    check("usage is 1 (not 0)", used == 1)
    check("quota is 1", quota == 1)

    # ۴) همان دوربین فعال: ذخیره‌ی مجدد بلاک نمی‌شود
    ok, _u, _q = aq.check_quota("plate", store, exclude_cam_id=cam1)
    check("re-saving enabled camera allowed", ok)

    # ۵) guard_feature_enable: تیک برمی‌گردد و False (اگر Qt در دسترس باشد)
    try:
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        from PyQt6.QtCore import Qt
        _qt_ok = True
    except Exception:
        _qt_ok = False
    if _qt_ok:
        lst = QListWidget()
        item = QListWidgetItem("🎥 دوربین ۲")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, cam2)
        with patch("PyQt6.QtWidgets.QMessageBox.warning"):
            res = aq.guard_feature_enable("plate", store, lst, item, None)
        check("guard returns False when full", res is False)
        check("guard reverts the tick",
              item.checkState() == Qt.CheckState.Unchecked)
        # دیتای دوربین دوم دست‌نخورده مانده (فعال نشده)
        check("cam2 flag untouched",
              store.get_camera(cam2).get("plate_detection") in (False, None))
    else:
        print("SKIP qt guard test (no Qt in this environment)")

licmod.LICENSE_PUBLIC_KEY = _orig_pk

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

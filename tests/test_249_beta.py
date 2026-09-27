"""تست 2.0.61-beta: گروه‌بندی دوربین‌ها + هشدار قطع تصویر.

گروه‌بندی (camera_store):
- فیلد group روی دوربین و NVR ذخیره می‌شود (سازگاری عقب‌رو: پیش‌فرض "")
- get_groups: لیست مرتب یکتا از همه‌ی گروه‌ها
- rename_group / clear_group

هشدار قطع تصویر (video_loss — منطق خالص بدون Qt):
- transition: فقط connected->reconnecting هشدار "lost" و
  reconnecting->connected هشدار "recovered" می‌دهد
- cooldown_ok: کول‌داون ۵ دقیقه‌ای ضداسپم

صدا (alarm_sound):
- kind جدید "videoloss" با کلید "videoloss_enabled"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile

from camera_store import CameraStore
from video_loss import transition, cooldown_ok, COOLDOWN_SECONDS
from alarm_sound import _SOUND_KEYS, DEFAULT_CONFIG


def _make_store():
    tmp = tempfile.mkdtemp(prefix="ias_test_249_")
    return CameraStore(path=os.path.join(tmp, "cameras.json"),
                       nvr_path=os.path.join(tmp, "nvrs.json"))


# ---------------- گروه‌بندی ----------------

def test_camera_group_saved(tmp_path):
    store = _make_store()
    cam = store.add_camera("دوربین پارکینگ", "192.168.1.10", "554", "admin", "x",
                           "/path", group="پارکینگ")
    assert store.get_camera(cam["id"])["group"] == "پارکینگ"


def test_camera_group_default_empty(tmp_path):
    # سازگاری عقب‌رو: دوربین‌های قدیمی/بدون گروه -> ""
    store = _make_store()
    cam = store.add_camera("دوربین قدیمی", "192.168.1.11", "554", "admin", "x",
                           "/path")
    assert store.get_camera(cam["id"])["group"] == ""


def test_nvr_group_saved(tmp_path):
    store = _make_store()
    nvr = store.add_nvr(name="NVR طبقه اول", ip="192.168.1.20", rtsp_port="554",
                        onvif_port="80", user="admin", pwd="x",
                        group="طبقه اول")
    assert store.get_nvr(nvr["id"])["group"] == "طبقه اول"


def test_get_groups_sorted_unique(tmp_path):
    store = _make_store()
    store.add_camera("c1", "192.168.1.10", "554", "a", "x", "/p", group="پارکینگ")
    store.add_camera("c2", "192.168.1.11", "554", "a", "x", "/p", group="پارکینگ")
    store.add_camera("c3", "192.168.1.12", "554", "a", "x", "/p", group="محوطه")
    store.add_camera("c4", "192.168.1.13", "554", "a", "x", "/p")  # بدون گروه
    store.add_nvr(name="n1", ip="192.168.1.20", rtsp_port="554",
                  onvif_port="80", user="a", pwd="x", group="طبقه اول")
    groups = store.get_groups()
    assert groups == sorted(groups), f"مرتب نیست: {groups}"
    assert groups == ["طبقه اول", "محوطه", "پارکینگ"], groups


def test_update_camera_group(tmp_path):
    store = _make_store()
    cam = store.add_camera("c1", "192.168.1.10", "554", "a", "x", "/p")
    store.update_camera(cam["id"], group="انبار")
    assert store.get_camera(cam["id"])["group"] == "انبار"
    assert store.get_groups() == ["انبار"]


def test_rename_group(tmp_path):
    store = _make_store()
    c1 = store.add_camera("c1", "192.168.1.10", "554", "a", "x", "/p",
                          group="قدیمی")
    n1 = store.add_nvr(name="n1", ip="192.168.1.20", rtsp_port="554",
                       onvif_port="80", user="a", pwd="x", group="قدیمی")
    c2 = store.add_camera("c2", "192.168.1.11", "554", "a", "x", "/p",
                          group="دست‌نخورده")
    count = store.rename_group("قدیمی", "جدید")
    assert count == 2, count
    assert store.get_camera(c1["id"])["group"] == "جدید"
    assert store.get_nvr(n1["id"])["group"] == "جدید"
    assert store.get_camera(c2["id"])["group"] == "دست‌نخورده"
    assert store.get_groups() == ["جدید", "دست‌نخورده"]


def test_clear_group_keeps_members(tmp_path):
    store = _make_store()
    c1 = store.add_camera("c1", "192.168.1.10", "554", "a", "x", "/p",
                          group="موقت")
    count = store.clear_group("موقت")
    assert count == 1, count
    # عضو حذف نشده، فقط بدون گروه شده
    assert store.get_camera(c1["id"]) is not None
    assert store.get_camera(c1["id"])["group"] == ""
    assert store.get_groups() == []


def test_group_whitespace_ignored(tmp_path):
    store = _make_store()
    store.add_camera("c1", "192.168.1.10", "554", "a", "x", "/p", group="   ")
    assert store.get_groups() == []


# ---------------- هشدار قطع تصویر ----------------

def test_transition_lost(tmp_path):
    assert transition("connected", "reconnecting") == "lost"


def test_transition_recovered(tmp_path):
    assert transition("reconnecting", "connected") == "recovered"


def test_transition_initial_failure_no_alert(tmp_path):
    # تلاش اولیه‌ی ناموفق (بدون اتصال قبلی) نباید هشدار قطع تصویر بدهد
    assert transition("", "reconnecting") is None
    assert transition("connecting", "reconnecting") is None


def test_transition_no_spurious(tmp_path):
    assert transition("connected", "connected") is None
    assert transition("reconnecting", "reconnecting") is None
    assert transition("", "connected") is None
    assert transition("connected", "") is None


def test_cooldown(tmp_path):
    assert COOLDOWN_SECONDS == 300
    assert cooldown_ok(0, 100) is False      # هنوز در کول‌داون
    assert cooldown_ok(0, 400) is True       # کول‌داون تمام شده
    assert cooldown_ok(1000, 1000 + 299) is False
    assert cooldown_ok(1000, 1000 + 300) is True
    assert cooldown_ok(1000, 1000 + 5000) is True


# ---------------- صدای قطع تصویر ----------------

def test_videoloss_sound_kind(tmp_path):
    assert _SOUND_KEYS.get("videoloss") == "videoloss_enabled"
    assert "videoloss_enabled" in DEFAULT_CONFIG

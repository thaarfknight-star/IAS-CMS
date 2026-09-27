# -*- coding: utf-8 -*-
"""تست‌های 2.0.64-beta — سیستم حرفه‌ای مدیریت پهنای باند:

  ۱) موتور تخصیص allocate_bandwidth: ثابت/سهم برابر/خودکار + هشدار سرریز
  ۲) فیلد bitrate_kbps در camera_store (ذخیره، پیش‌فرض رکوردهای قدیمی)
  ۳) snapshot با targets: پرچم «پرمصرف» نسبت به هدف هر دوربین
  ۴) AST: واحد kbps کنار کادرها، سیم‌کشی دیالوگ‌ها و تنظیمات
"""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# stub موقت cv2 (روی این ماشین نصب نیست؛ مثل تست ۲۴۹)
import types
if "cv2" not in sys.modules:
    try:
        import cv2  # noqa
    except Exception:
        _cv2 = types.ModuleType("cv2")
        _cv2.CAP_FFMPEG = 1900

        class _Cap:
            def __init__(self, *a, **k):
                pass

            def isOpened(self):
                return False

            def read(self):
                return False, None

            def release(self):
                pass

        _cv2.VideoCapture = _Cap
        sys.modules["cv2"] = _cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mk(id_, want=0, label=None):
    return {"id": id_, "label": label or id_, "bitrate_kbps": want}


# ------------------------------------------------------- موتور تخصیص

def test_allocate_all_auto_equal_share():
    from bandwidth import allocate_bandwidth
    cams = [_mk("a"), _mk("b"), _mk("c"), _mk("d")]
    r = allocate_bandwidth(cams, 20)
    assert r["n_fixed"] == 0 and r["n_auto"] == 4
    assert r["equal_share_kbps"] == 5000.0
    for cid in "abcd":
        a = r["allocations"][cid]
        assert a["mode"] == "equal" and a["target_kbps"] == 5000.0
    assert r["warnings"] == []


def test_allocate_fixed_gets_exact_value():
    from bandwidth import allocate_bandwidth
    cams = [_mk("a", 8000), _mk("b"), _mk("c"), _mk("d")]
    r = allocate_bandwidth(cams, 20)
    assert r["allocations"]["a"]["mode"] == "fixed"
    assert r["allocations"]["a"]["target_kbps"] == 8000.0
    # باقی‌مانده‌ی ۱۲۰۰۰ بین ۳ دوربین
    assert r["equal_share_kbps"] == 4000.0
    assert r["allocations"]["b"]["target_kbps"] == 4000.0
    assert r["total_kbps"] == 8000.0
    assert r["warnings"] == []


def test_allocate_over_cap_warns_but_keeps_fixed():
    from bandwidth import allocate_bandwidth
    cams = [_mk("a", 15000), _mk("b", 10000), _mk("c")]
    r = allocate_bandwidth(cams, 20)
    assert len(r["warnings"]) == 1
    assert r["allocations"]["a"]["target_kbps"] == 15000.0  # ثابت دست‌نخورده
    assert r["allocations"]["b"]["target_kbps"] == 10000.0
    assert r["allocations"]["c"]["target_kbps"] == 0.0  # سهمی نمانده


def test_allocate_unlimited_cap():
    from bandwidth import allocate_bandwidth
    cams = [_mk("a", 4096), _mk("b")]
    r = allocate_bandwidth(cams, 0)
    assert r["allocations"]["a"]["target_kbps"] == 4096.0
    assert r["allocations"]["a"]["mode"] == "fixed"
    assert r["allocations"]["b"]["target_kbps"] is None
    assert r["allocations"]["b"]["mode"] == "auto"
    assert r["warnings"] == []


def test_allocate_empty_and_garbage():
    from bandwidth import allocate_bandwidth
    r = allocate_bandwidth([], 20)
    assert r["allocations"] == {} and r["warnings"] == []
    r = allocate_bandwidth([{"id": "x", "bitrate_kbps": "abc"},
                            {"id": "y", "bitrate_kbps": None}], 10)
    assert r["allocations"]["x"]["mode"] == "equal"
    assert r["allocations"]["y"]["mode"] == "equal"
    assert r["equal_share_kbps"] == 5000.0


# ------------------------------------------------------- camera_store

def test_camera_store_bitrate_persist(tmp_path):
    from camera_store import CameraStore
    store = CameraStore(path=str(tmp_path / "c.json"),
                        nvr_path=str(tmp_path / "n.json"))
    cam = store.add_camera("n", "1.2.3.4", "554", "admin", "p", "live/ch0",
                           bitrate_kbps=4096)
    assert cam["bitrate_kbps"] == 4096
    store2 = CameraStore(path=str(tmp_path / "c.json"),
                         nvr_path=str(tmp_path / "n.json"))
    got = store2.get_camera(cam["id"])
    assert got["bitrate_kbps"] == 4096
    store2.update_camera(cam["id"], bitrate_kbps=8192)
    assert store2.get_camera(cam["id"])["bitrate_kbps"] == 8192


def test_camera_store_bitrate_default_zero(tmp_path):
    from camera_store import CameraStore
    store = CameraStore(path=str(tmp_path / "c.json"),
                        nvr_path=str(tmp_path / "n.json"))
    cam = store.add_camera("n", "1.2.3.4", "554", "admin", "p", "live/ch0")
    assert cam["bitrate_kbps"] == 0
    # رکورد قدیمی بدون فیلد
    assert CameraStore.get_bitrate_kbps({"id": "old"}) == 0
    assert CameraStore.get_bitrate_kbps({"bitrate_kbps": "2048"}) == 2048


# ------------------------------------------------------- snapshot با targets

def test_snapshot_over_flag_uses_per_camera_target():
    from bandwidth import BandwidthMonitor
    m = BandwidthMonitor()
    m.update("a", 2500, "A")   # هدف ۲۰۰۰ → پرمصرف
    m.update("b", 8100, "B")   # هدف ۸۰۰۰ → عادی (نه پرمصرف نسبت به سهم کلی)
    snap = m.snapshot(total_mbps=20, targets={"a": 2000, "b": 8000})
    by_id = {i["cam_id"]: i for i in snap["items"]}
    assert by_id["a"]["status"] == "over"
    assert by_id["b"]["status"] == "ok"


def test_snapshot_falls_back_to_share_without_targets():
    from bandwidth import BandwidthMonitor
    m = BandwidthMonitor()
    m.update("a", 6000, "A")
    snap = m.snapshot(total_mbps=20)  # سهم = ۲۰۰۰۰/۱
    assert snap["items"][0]["status"] == "ok"


# ------------------------------------------------------- AST سیم‌کشی UI

def _src(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def test_add_camera_dialog_has_bitrate_spin_with_unit():
    src = _src("add_camera_dialog.py")
    assert "bitrate_spin" in src
    assert 'setSuffix(" kbps")' in src          # واحد کنار کادر
    assert "بیت‌ریت درخواستی (kbps)" in src     # واحد در لیبل
    assert '"bitrate_kbps"' in src or "'bitrate_kbps'" in src
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "AddCameraDialog" in names


def test_bandwidth_dialog_has_per_camera_controls():
    src = _src("bandwidth_dialog.py")
    assert "allocate_bandwidth" in src
    assert "ذخیره‌ی بیت‌ریت‌ها" in src
    assert "اعمال روی دوربین‌ها (ONVIF)" in src
    assert "بیت‌ریت درخواستی (kbps)" in src
    assert 'setSuffix(" kbps")' in src
    assert "apply_target_bitrate_blocking" in src


def test_settings_bandwidth_group_has_units():
    src = _src("settings_page.py")
    assert "مگابیت/ثانیه (Mbps)" in src   # واحد کنار کادر سقف کلی
    assert "بیت‌ریت درخواستی" in src


def test_main_passes_bitrate_to_add_camera():
    src = _src("main.py")
    assert "bitrate_kbps=data.get(\"bitrate_kbps\", 0)" in src

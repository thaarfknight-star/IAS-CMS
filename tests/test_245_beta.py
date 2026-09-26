# -*- coding: utf-8 -*-
"""تست‌های 2.0.52-beta:
  ۱) سیستم مدیریت پهنای باند (سهم برابر، وضعیت پرمصرف/کهنه، ذخیره تنظیمات)
  ۲) قالب دلخواه + قالب‌های استاندارد آدرس پخش بازبینی NVR
  ۳) تولید مسیر کانال برای «افزودن مستقیم از NVR»
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bandwidth
from bandwidth import BandwidthMonitor, fair_share_kbps
import nvr_playback
from nvr_playback import build_playback_urls, _custom_template_url


# ---------------------------------------------------------------- پهنای باند

def test_fair_share_basic():
    assert fair_share_kbps(100, 4) == 25000.0
    assert fair_share_kbps(50, 10) == 5000.0


def test_fair_share_unlimited_or_no_cams():
    assert fair_share_kbps(0, 4) is None      # سقف نامحدود
    assert fair_share_kbps(100, 0) is None    # دوربینی نیست
    assert fair_share_kbps(None, 4) is None


def test_monitor_snapshot_share_and_over():
    mon = BandwidthMonitor()
    mon.update("c1", 20000, "دوربین ۱")
    mon.update("c2", 20000, "دوربین ۲")
    snap = mon.snapshot(total_mbps=100)  # سهم برابر = ۵۰٬۰۰۰
    assert snap["n_active"] == 2
    assert snap["share_kbps"] == 50000.0
    assert snap["total_kbps"] == 40000.0
    assert all(i["status"] == "ok" for i in snap["items"])

    # پرمصرف: بیش از ۱٫۱۵ برابر سهم
    mon.update("c1", 60000, "دوربین ۱")
    snap = mon.snapshot(total_mbps=100)
    by_id = {i["cam_id"]: i for i in snap["items"]}
    assert by_id["c1"]["status"] == "over"
    assert by_id["c2"]["status"] == "ok"


def test_monitor_stale():
    mon = BandwidthMonitor()
    mon.update("c1", 5000, "دوربین ۱")
    # قدیمی‌سازی دستی
    mon._data["c1"]["ts"] -= 60
    snap = mon.snapshot(total_mbps=100)
    assert snap["items"][0]["status"] == "stale"
    assert snap["n_active"] == 0  # کهنه‌ها در شمارش سهم نیستند


def test_bw_settings_roundtrip(tmp_path, monkeypatch):
    fake = tmp_path / "app_settings.json"
    monkeypatch.setattr(bandwidth, "SETTINGS_PATH", str(fake))
    assert bandwidth.save_bw_settings(True, 80.5)
    cfg = bandwidth.load_bw_settings()
    assert cfg == {"enabled": True, "total_mbps": 80.5}
    # پیش‌فرض وقتی فایل نیست
    fake.unlink()
    cfg = bandwidth.load_bw_settings()
    assert cfg == {"enabled": False, "total_mbps": 0.0}


# ------------------------------------------------------- پخش بازبینی NVR

def _nvr(**kw):
    base = {"ip": "192.168.1.100", "rtsp_port": "554",
            "user": "admin", "pass": "1234", "onvif_port": "80"}
    base.update(kw)
    return base


def _dt(y=2026, mo=9, d=26, h=14, mi=42, s=49):
    from datetime import datetime
    return datetime(y, mo, d, h, mi, s)


def test_playback_standard_templates():
    urls = build_playback_urls(_nvr(), 6, _dt(), _dt(s=0, mi=43))
    labels = [l for l, _ in urls]
    assert labels == ["Hikvision", "Dahua"]
    hik = urls[0][1]
    assert "Streaming/tracks/601" in hik
    assert "starttime=20260926T144249Z" in hik
    dahua = urls[1][1]
    assert "cam/playback?channel=6" in dahua
    assert "starttime=2026_09_26_14_42_49" in dahua


def test_playback_custom_template_first():
    nvr = _nvr(playback_template="rtsp://{ip}:{port}/live/ch{channel}?start={start}&end={end}")
    urls = build_playback_urls(nvr, 3, _dt(), _dt(mi=43))
    assert urls[0][0] == "قالب دلخواه"
    # چون نام‌کاربری در قالب نیست، خودش تزریق می‌شود
    assert urls[0][1] == ("rtsp://admin:1234@192.168.1.100:554/live/ch3"
                          "?start=20260926T144249Z&end=20260926T144349Z")
    # استانداردها هنوز به‌عنوان fallback هستند
    assert [l for l, _ in urls][1:] == ["Hikvision", "Dahua"]
    assert len(urls) == 3


def test_playback_no_template_no_custom():
    urls = build_playback_urls(_nvr(playback_template=""), 1, _dt(), _dt())
    assert [l for l, _ in urls] == ["Hikvision", "Dahua"]


def test_playback_no_channel():
    assert build_playback_urls(_nvr(), None, _dt(), _dt()) == []


def test_custom_template_bad_graceful():
    # قالب خراب نباید کرش بدهد
    nvr = _nvr(playback_template="rtsp://{ip}/{nonexistent_var}")
    assert _custom_template_url(nvr, 1, _dt(), _dt()) is None


# --------------------------------------------- افزودن مستقیم کانال از NVR

def test_direct_channel_paths():
    import sys
    from unittest.mock import MagicMock
    # nvr_scanner از طریق rtsp_utils به cv2 وابسته است؛ headless استاب می‌کنیم
    sys.modules.setdefault("cv2", MagicMock(name="cv2"))
    sys.modules.setdefault("numpy", MagicMock(name="numpy"))
    from nvr_scanner import CHANNEL_TEMPLATES, _format_templates
    hik = _format_templates([CHANNEL_TEMPLATES["hikvision"][0]], 3)[0]
    assert hik == "Streaming/Channels/301"
    dahua = _format_templates([CHANNEL_TEMPLATES["dahua_iap"][0]], 6)[0]
    assert dahua == "cam/realmonitor?channel=6&subtype=0"
    generic = CHANNEL_TEMPLATES.get("auto") or CHANNEL_TEMPLATES["generic"]
    assert generic  # برند auto باید به generic برگردد

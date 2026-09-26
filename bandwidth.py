# -*- coding: utf-8 -*-
"""سیستم مدیریت پهنای باند (2.0.52-beta).

هدف: همه‌ی دوربین‌های فعال «به یک اندازه» از پهنای باند شبکه استفاده کنند و
افت بیت‌ریت/قطعی تصویر به‌خاطر ازدحام پیش نیاید.

اجزا:
  1) مانیتور زنده: هر CameraSlotWidget هر ~۵ ثانیه kbps/fps استریمش را از
     طریق on_stream_stats گزارش می‌دهد؛ BandwidthMonitor آخرین مقدار هر
     دوربین را نگه می‌دارد.
  2) سقف کلی + سهم برابر: کاربر در تنظیمات یک سقف کلی (مگابیت/ثانیه) تعیین
     می‌کند؛ سهم برابر هر دوربین = سقف ÷ تعداد دوربین‌های فعال. سقف صفر یعنی
     فقط «نمایش» بدون قضاوت (حالت نامحدود).
  3) اعمال سهم برابر (ONVIF): دکمه‌ی «اعمال سهم برابر» در دیالوگ زنده، برای
     هر دوربین مستقیم (نه کانال NVR) تلاش می‌کند از طریق ONVIF استاندارد
     (SetVideoEncoderConfiguration) بیت‌ریت انکدر دوربین را روی سهم برابر
     تنظیم کند. Best-effort است: دوربینی که ONVIF ندهد یا خطا بدهد، در گزارش
     مشخص می‌شود و بقیه ادامه پیدا می‌کنند.
  4) کانال‌های NVR از طریق ONVIF خود NVR قابل تنظیم نیستند (پروفایل‌های NVR
     لزوماً به کانال‌ها نگاشت نمی‌شوند)؛ برای آن‌ها سهم برابر فقط «نمایشی و
     هشداری» است.

نکته‌ی مهم صداقت: بیت‌ریت واقعیِ ارسالی هر دوربین را فقط خودِ دوربین (انکدرش)
تعیین می‌کند؛ این برنامه بدون ONVIF نمی‌تواند آن را «مجبور» کند. پس اگر
دوربینی ONVIF نداشت، دیالوگ آن را «غیرقابل مدیریت» نشان می‌دهد تا کاربر
دستی از پنل خود دوربین بیت‌ریتش را کم کند.
"""

import json
import os
import time

APP_DIR = os.path.join(os.path.expanduser("~"), ".ias_cms")
SETTINGS_PATH = os.path.join(APP_DIR, "app_settings.json")
SETTINGS_KEY = "bandwidth"

# پورت‌های رایج ONVIF برای دوربین‌های مستقیم (وقتی پورت ONVIF جداگانه‌ای
# در رکورد دوربین ذخیره نشده).
COMMON_ONVIF_PORTS = [80, 8000, 8080, 2020]

# اگر دوربینی بیش از این درصد از سهم برابرش مصرف کند، «پرمصرف» حساب می‌شود.
OVER_SHARE_RATIO = 1.15

# داده‌ای که بیش از این ثانیه قدیمی باشد، «کهنه» حساب می‌شود (استریم قطع شده).
STALE_AFTER_SEC = 15.0


def _read_settings_file():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_settings_file(data):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_bw_settings():
    """{"enabled": bool, "total_mbps": float} — total_mbps صفر یعنی نامحدود."""
    cfg = _read_settings_file().get(SETTINGS_KEY, {})
    try:
        total = float(cfg.get("total_mbps", 0) or 0)
    except Exception:
        total = 0.0
    return {"enabled": bool(cfg.get("enabled", False)), "total_mbps": max(0.0, total)}


def save_bw_settings(enabled, total_mbps):
    data = _read_settings_file()
    data[SETTINGS_KEY] = {"enabled": bool(enabled),
                          "total_mbps": max(0.0, float(total_mbps or 0))}
    return _write_settings_file(data)


def fair_share_kbps(total_mbps, n_cams):
    """سهم برابر هر دوربین به کیلوبیت/ثانیه؛ None یعنی سقف نامحدود/نامشخص."""
    try:
        n = int(n_cams or 0)
        total = float(total_mbps or 0)
    except Exception:
        return None
    if n <= 0 or total <= 0:
        return None
    return (total * 1000.0) / n


class BandwidthMonitor:
    """نگه‌داشت آخرین kbps هر دوربین فعال + محاسبه‌ی سهم برابر و وضعیت."""

    def __init__(self):
        # cam_id -> {"kbps": float, "ts": monotonic, "label": str}
        self._data = {}

    def update(self, cam_id, kbps, label=""):
        if not cam_id:
            return
        try:
            kbps = float(kbps or 0)
        except Exception:
            kbps = 0.0
        self._data[cam_id] = {"kbps": max(0.0, kbps),
                              "ts": time.monotonic(),
                              "label": label or str(cam_id)}

    def remove(self, cam_id):
        self._data.pop(cam_id, None)

    def snapshot(self, total_mbps=0):
        """وضعیت لحظه‌ای؛ هر آیتم: label, kbps, status.

        status: "ok" | "over" (پرمصرف) | "stale" (داده کهنه/قطع)
        """
        now = time.monotonic()
        items = []
        for cam_id, d in self._data.items():
            age = now - d["ts"]
            if age > STALE_AFTER_SEC:
                status = "stale"
            else:
                status = "ok"
            items.append({"cam_id": cam_id, "label": d["label"],
                          "kbps": round(d["kbps"], 1), "status": status,
                          "age": round(age, 1)})
        n = len([i for i in items if i["status"] != "stale"])
        share = fair_share_kbps(total_mbps, n)
        total_kbps = round(sum(i["kbps"] for i in items if i["status"] != "stale"), 1)
        if share:
            for i in items:
                if i["status"] == "ok" and i["kbps"] > share * OVER_SHARE_RATIO:
                    i["status"] = "over"
        items.sort(key=lambda i: i["kbps"], reverse=True)
        return {"total_kbps": total_kbps, "share_kbps": round(share, 1) if share else None,
                "n_active": n, "items": items}


_monitor_instance = None


def get_monitor():
    """نمونه‌ی سراسری مانیتور پهنای باند (بین پنجره‌ی اصلی و تنظیمات مشترک)."""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = BandwidthMonitor()
    return _monitor_instance


def _set_encoder_bitrate_onvif(ip, onvif_port, user, pwd, bitrate_kbps, timeout=10):
    """تنظیم بیت‌ریت انکدر دوربین از طریق ONVIF. موفق/ناموفق + پیام برمی‌گرداند."""
    from onvif import ONVIFCamera

    bitrate = max(64, int(bitrate_kbps))
    cam = ONVIFCamera(ip, int(onvif_port), user, pwd)
    # timeout دستی: مثل nvr_scanner، فراخوانی‌های zeep ممکن است بلاک شوند.
    media = cam.create_media_service()
    profiles = media.GetProfiles()
    if not profiles:
        return False, "پروفایل ONVIF پیدا نشد"
    ok, msgs = 0, []
    for p in profiles:
        try:
            vec = p.VideoEncoderConfiguration
            req = media.create_type("SetVideoEncoderConfiguration")
            vec.Bitrate = bitrate
            # بعضی دوربین‌ها GuaranteedFrameRate/کیفیت را هم می‌خواهند؛ فقط
            # Bitrate را عوض می‌کنیم تا بقیه‌ی تنظیمات دست نخورند.
            req.Configuration = vec
            req.ForcePersistence = True
            media.SetVideoEncoderConfiguration(req)
            ok += 1
        except Exception as e:
            msgs.append(str(e)[:80])
    if ok:
        return True, f"{ok} پروفایل روی {bitrate} کیلوبیت/ثانیه تنظیم شد"
    return False, "; ".join(msgs) or "خطای نامشخص ONVIF"


def apply_fair_share_blocking(cam, share_kbps):
    """تلاش بلاکینگ برای یک دوربین مستقیم؛ (ok, message) برمی‌گرداند.

    در ترد جدا صدا زده می‌شود، نه ترد اصلی UI.
    """
    ip = (cam.get("ip") or "").strip()
    user = cam.get("user", "") or ""
    pwd = cam.get("pass", "") or ""
    if not ip:
        return False, "IP ندارد"
    ports = []
    for key in ("onvif_port", "port"):
        try:
            v = int(cam.get(key) or 0)
            if v > 0 and v not in ports:
                ports.append(v)
        except Exception:
            pass
    for p in COMMON_ONVIF_PORTS:
        if p not in ports:
            ports.append(p)
    last_err = ""
    for port in ports:
        try:
            ok, msg = _set_encoder_bitrate_onvif(ip, port, user, pwd, share_kbps)
            if ok:
                return True, f"پورت {port}: {msg}"
            last_err = f"پورت {port}: {msg}"
        except Exception as e:
            last_err = f"پورت {port}: {str(e)[:80]}"
    return False, last_err or "ONVIF پاسخ نداد"

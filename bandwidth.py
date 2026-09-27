# -*- coding: utf-8 -*-
"""سیستم مدیریت پهنای باند (2.0.52-beta؛ توسعه‌یافته در 2.0.64-beta).

هدف: هر دوربین «به یک اندازه» از پهنای باند شبکه استفاده کند و اگر برای
دوربینی «بیت‌ریت درخواستی» تعیین شده بود، دقیقاً همان مقدار به آن برسد.

اجزا:
  1) مانیتور زنده: هر CameraSlotWidget هر ~۵ ثانیه kbps/fps استریمش را از
     طریق on_stream_stats گزارش می‌دهد؛ BandwidthMonitor آخرین مقدار هر
     دوربین را نگه می‌دارد.
  2) سقف کلی + تخصیص دقیق (allocate_bandwidth): کاربر در تنظیمات یک سقف کلی
     (مگابیت/ثانیه) تعیین می‌کند و برای هر دوربین می‌تواند «بیت‌ریت درخواستی»
     (کیلوبیت/ثانیه) بگذارد. قانون تخصیص: اول دوربین‌های دارای بیت‌ریت
     درخواستی دقیقاً همان مقدار را می‌گیرند؛ باقی‌مانده‌ی سقف به‌تساوی بین
     بقیه تقسیم می‌شود؛ اگر سقف نامحدود باشد، بقیه «خودکار» می‌مانند.
  3) اعمال تخصیص (ONVIF): دکمه‌ی «اعمال روی دوربین‌ها» در دیالوگ زنده، برای
     هر دوربین مستقیم (نه کانال NVR) تلاش می‌کند از طریق ONVIF استاندارد
     (SetVideoEncoderConfiguration) بیت‌ریت انکدر دوربین را روی «سهم نهایی»
     همان دوربین تنظیم کند. Best-effort است: دوربینی که ONVIF ندهد یا خطا
     بدهد، در گزارش مشخص می‌شود و بقیه ادامه پیدا می‌کنند.
  4) کانال‌های NVR از طریق ONVIF خود NVR قابل تنظیم نیستند (پروفایل‌های NVR
     لزوماً به کانال‌ها نگاشت نمی‌شوند)؛ برای آن‌ها تخصیص فقط «نمایشی و
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


# ---------------------------------------------------------------------------
# موتور تخصیص پهنای باند (2.0.64-beta)
# ---------------------------------------------------------------------------
# قانون تخصیص — دقیق و قطعی:
#   ۱) هر دوربینی که «بیت‌ریت درخواستی» دارد (bitrate_kbps > 0)، دقیقاً همان
#      مقدار را می‌گیرد (حالت fixed)؛ چه سقف کلی تعیین شده باشد چه نه.
#   ۲) اگر سقف کلی تعیین شده باشد: مجموع ثابت‌ها از سقف کم می‌شود و
#      باقی‌مانده «به‌تساوی» بین دوربین‌های بدون بیت‌ریت درخواستی تقسیم
#      می‌شود (حالت equal).
#   ۳) اگر سقف کلی نامحدود (صفر) باشد: دوربین‌های بدون بیت‌ریت درخواستی
#      «خودکار» می‌مانند (حالت auto) و برنامه برایشان تصمیمی نمی‌گیرد.
#   ۴) اگر مجموع ثابت‌ها از سقف بیشتر شود، تخصیص همان مقادیر ثابت است ولی
#      هشدار «over_allocated» ثبت می‌شود تا کاربر سقف را بالا ببرد یا
#      بیت‌ریت‌ها را کم کند (برنامه خودسرانه کم نمی‌کند).

def allocate_bandwidth(cameras, total_mbps):
    """تخصیص قطعی پهنای باند.

    cameras: لیستی از dict با کلیدهای:
        id, label, bitrate_kbps (عدد؛ ۰ یا None یعنی خودکار), nvr (bool)
    total_mbps: سقف کلی (۰ یعنی نامحدود).

    برمی‌گرداند:
        {"allocations": {cam_id: {"target_kbps": float|None,
                                  "mode": "fixed"|"equal"|"auto",
                                  "label": str}},
         "total_kbps": مجموع تخصیص‌یافته‌ی قطعی (فقط fixedها؛ float)،
         "equal_share_kbps": سهم برابر باقی‌مانده (یا None)،
         "n_fixed": int, "n_auto": int,
         "warnings": [str]}
    """
    allocs = {}
    fixed_sum = 0.0
    fixed_ids = []
    auto_ids = []
    try:
        total = max(0.0, float(total_mbps or 0))
    except Exception:
        total = 0.0

    for cam in cameras or []:
        cid = str(cam.get("id") or "")
        if not cid:
            continue
        try:
            want = max(0, int(float(cam.get("bitrate_kbps") or 0)))
        except Exception:
            want = 0
        label = cam.get("label") or cam.get("name") or cam.get("ip") or cid
        if want > 0:
            allocs[cid] = {"target_kbps": float(want), "mode": "fixed",
                           "label": label}
            fixed_sum += want
            fixed_ids.append(cid)
        else:
            auto_ids.append(cid)
            allocs[cid] = {"target_kbps": None, "mode": "auto", "label": label}

    warnings = []
    equal_share = None
    if total > 0:
        remaining = total * 1000.0 - fixed_sum
        if remaining < 0:
            warnings.append(
                f"مجموع بیت‌ریت‌های ثابت ({fixed_sum:,.0f} kbps) از سقف کلی "
                f"({total:g} Mbps) بیشتر است؛ دوربین‌های خودکار سهمی نمی‌گیرند.")
            remaining = 0.0
        if auto_ids:
            equal_share = remaining / len(auto_ids)
            for cid in auto_ids:
                allocs[cid]["target_kbps"] = round(equal_share, 1)
                allocs[cid]["mode"] = "equal"
    return {"allocations": allocs,
            "total_kbps": round(fixed_sum, 1),
            "equal_share_kbps": round(equal_share, 1) if equal_share else None,
            "n_fixed": len(fixed_ids), "n_auto": len(auto_ids),
            "warnings": warnings}


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

    def snapshot(self, total_mbps=0, targets=None):
        """وضعیت لحظه‌ای؛ هر آیتم: label, kbps, status.

        status: "ok" | "over" (پرمصرف) | "stale" (داده کهنه/قطع)
        targets (اختیاری، 2.0.64-beta): دیکشنری cam_id -> target_kbps؛ اگر
        برای دوربینی هدف مشخص باشد، «پرمصرف» نسبت به همان هدف سنجیده
        می‌شود، وگرنه مثل قبل نسبت به سهم برابر کلی.
        """
        now = time.monotonic()
        targets = targets or {}
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
        for i in items:
            if i["status"] != "ok":
                continue
            ref = targets.get(i["cam_id"])
            if not ref:
                ref = share
            if ref and i["kbps"] > ref * OVER_SHARE_RATIO:
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
    from app_paths import get_onvif_wsdl_dir

    bitrate = max(64, int(bitrate_kbps))
    cam = ONVIFCamera(ip, int(onvif_port), user, pwd,
                      wsdl_dir=get_onvif_wsdl_dir())
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


def apply_target_bitrate_blocking(cam, bitrate_kbps):
    """(2.0.64-beta) تلاش بلاکینگ برای تنظیم بیت‌ریت انکدر یک دوربین مستقیم
    روی مقدار دقیق درخواستی؛ (ok, message) برمی‌گرداند.

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
            ok, msg = _set_encoder_bitrate_onvif(ip, port, user, pwd,
                                                 bitrate_kbps)
            if ok:
                return True, f"پورت {port}: {msg}"
            last_err = f"پورت {port}: {msg}"
        except Exception as e:
            last_err = f"پورت {port}: {str(e)[:80]}"
    return False, last_err or "ONVIF پاسخ نداد"


def apply_fair_share_blocking(cam, share_kbps):
    """تلاش بلاکینگ برای یک دوربین مستقیم؛ (ok, message) برمی‌گرداند.

    در ترد جدا صدا زده می‌شود، نه ترد اصلی UI.
    (2.0.64-beta: حالا فقط یک wrapper روی apply_target_bitrate_blocking است.)
    """
    return apply_target_bitrate_blocking(cam, share_kbps)

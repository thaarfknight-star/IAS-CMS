"""تنظیمات سخت‌افزاری تصویر دوربین از طریق سرویس ONVIF Imaging.

این ماژول WDR «واقعی» دوربین (پارامتر سخت‌افزاری، نه پردازش نرم‌افزاری
تصویر نمایشی) را می‌خواند و می‌نویسد. برخلاف image_profile.py که فقط
نمایش را تغییر می‌دهد، تغییر اینجا روی خود دوربین اعمال و ماندگار می‌شود
(ForcePersistence) و روی استریم همه‌ی بیننده‌ها اثر می‌گذارد.

نکات طراحی:
- وابستگی onvif-zeep اختیاری است؛ اگر نصب نباشد همه‌ی توابع خطای
  ساخت‌یافته‌ی فارسی برمی‌گردانند و برنامه crash نمی‌کند.
- هیچ فراخوانی شبکه‌ای در ترد GUI انجام ندهید! همه‌ی توابع این ماژول
  ممکن است چند ثانیه بلاک شوند؛ از ترد کارگر (رجوع کنید به
  image_settings_dialog.py) صدا بزنید.
- دوربین‌های زیر NVR: اگر IP واقعی دوربین (camera_ip) شناسایی شده باشد،
  مستقیم به خود دوربین وصل می‌شویم؛ وگرنه از طریق NVR تلاش می‌شود (بعضی
  NVRها تنظیمات تصویر هر کانال را از طریق ONVIF می‌دهند، بعضی نه).
"""

import os
import concurrent.futures as _fut

try:
    from nvr_scanner import COMMON_ONVIF_PORTS as _COMMON_PORTS, ONVIF_TIMEOUT_SEC as _TIMEOUT
except Exception:
    _COMMON_PORTS = [80, 8000, 8080, 2020]
    _TIMEOUT = 5

# پورت‌های رایج ONVIF برای حدس هنگام نبود پورت ذخیره‌شده.
PROBE_PORTS = [80, 8000, 8080, 2020, 8899]
for _p in _COMMON_PORTS:
    if _p not in PROBE_PORTS:
        PROBE_PORTS.append(_p)

_PER_PORT_TIMEOUT = 4
_OVERALL_TIMEOUT = 20

WDR_MODE_OFF = "OFF"
WDR_MODE_ON = "ON"


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------

def is_available():
    """آیا onvif-zeep (به‌همراه فایل‌های wsdl) در دسترس است؟

    خروجی: (ok: bool, reason: str) که reason یکی از این‌هاست:
    "ok" | "not_installed" | "wsdl_missing"
    """
    try:
        import onvif  # noqa: F401
    except Exception:
        return False, "not_installed"
    try:
        from onvif import ONVIFCamera  # noqa: F401
        import os as _os
        _wsdl_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
            __import__("onvif", fromlist=["__file__"]).__file__)), "wsdl")
        if not _os.path.isfile(_os.path.join(_wsdl_dir, "imaging.wsdl")):
            return False, "wsdl_missing"
    except Exception:
        return False, "not_installed"
    return True, "ok"


def availability_message():
    ok, reason = is_available()
    if ok:
        return ""
    if reason == "wsdl_missing":
        return ("کتابخانه‌ی onvif-zeep نصب است ولی فایل‌های wsdl آن پیدا نشد؛ "
                "پوشه‌ی wsdl را از مخزن python-onvif-zeep کنار بسته‌ی onvif کپی کنید.")
    return ("برای WDR سخت‌افزاری بسته‌ی onvif-zeep لازم است:\n"
            "pip install onvif-zeep")


# ---------------------------------------------------------------------------
# target resolution
# ---------------------------------------------------------------------------

def resolve_target(cam, camera_store=None):
    """تعیین مقصد ONVIF برای یک دوربین.

    خروجی: dict با کلیدهای host/port/user/pwd/via/channel/label که via یکی از
    "direct" (دوربین مستقل)، "camera_ip" (IP واقعی دوربین زیر NVR) یا
    "nvr" (از طریق خود NVR) است. در صورت نبود اطلاعات کافی،
    {"error": "پیام فارسی"} برمی‌گردد.
    """
    cam = cam or {}
    nvr = None
    nvr_id = cam.get("nvr_id")
    if nvr_id and camera_store is not None:
        try:
            nvr = camera_store.get_nvr(nvr_id)
        except Exception:
            nvr = None

    # ۱) IP واقعی دوربینِ زیر NVR (شناسایی‌شده هنگام افزودن کانال)
    camera_ip = (cam.get("camera_ip") or "").strip()
    if camera_ip:
        user = cam.get("user", "") or (nvr.get("user", "") if nvr else "")
        pwd = cam.get("pass", "") or (nvr.get("pass", "") if nvr else "")
        return {"host": camera_ip, "port": None, "user": user, "pwd": pwd,
                "via": "camera_ip", "channel": cam.get("channel"),
                "label": "اتصال مستقیم به دوربین (IP شناسایی‌شده)"}

    # ۲) از طریق خود NVR (برای کانال‌های NVR)
    if nvr:
        return {"host": (nvr.get("ip") or "").strip(),
                "port": nvr.get("onvif_port") or None,
                "user": nvr.get("user", "") or "", "pwd": nvr.get("pass", "") or "",
                "via": "nvr", "channel": cam.get("channel"), "nvr": nvr,
                "label": "از طریق NVR «%s»" % (nvr.get("name") or nvr.get("ip") or "")}

    # ۳) دوربین مستقل
    host = (cam.get("ip") or "").strip()
    if not host:
        return {"error": "IP دوربین مشخص نیست."}
    return {"host": host, "port": None,
            "user": cam.get("user", "") or "", "pwd": cam.get("pass", "") or "",
            "via": "direct", "channel": None, "label": "اتصال مستقیم به دوربین"}


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------

def _new_camera(host, port, user, pwd):
    """ساخت ONVIFCamera بدون عبور از پروکسی (تجهیزات LAN نباید از پروکسی بروند)."""
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(var, None)
    from onvif import ONVIFCamera
    return ONVIFCamera(host, int(port), user or "", pwd or "")


def _friendly_error(exc):
    msg = str(exc)
    low = msg.lower()
    if "notauthorized" in low or "unauthorized" in low or "401" in low:
        return "نام کاربری یا رمز ONVIF اشتباه است."
    if "doesn`t support service: imaging" in low or "doesnt support service" in low:
        return "این دستگاه سرویس Imaging (تنظیم تصویر) ONVIF را پشتیبانی نمی‌کند."
    if "no such file" in low and "wsdl" in low:
        return availability_message()
    if "timed out" in low or "timeout" in low or "max retries" in low:
        return "دستگاه در مهلت تعیین‌شده پاسخ نداد."
    if "name or service not known" in low or "nodename nor servname" in low:
        return "آدرس IP دستگاه در شبکه پیدا نشد."
    if "connection refused" in low:
        return "اتصال رد شد؛ پورت ONVIF دستگاه را بررسی کنید."
    short = msg.strip().split("\n")[0]
    return "خطای ONVIF: %s" % (short[:160] if short else type(exc).__name__)


def _as_dict(obj):
    """تبدیل بازگشتی شیء zeep به dict/list/مقدار ساده (برای خواندن امن فیلدها)."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_as_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    # شیء zeep (CompoundValue) یا آبجکت ساده
    try:
        from zeep.helpers import serialize_object
        return _as_dict(serialize_object(obj))
    except Exception:
        pass
    try:
        return {k: _as_dict(v) for k, v in vars(obj).items() if not k.startswith("_")}
    except Exception:
        return str(obj)


def _pick_video_source(media, channel_hint):
    """انتخاب VideoSourceToken مناسب؛ برای NVR چندکاناله با راهنمای شماره کانال."""
    try:
        sources = media.GetVideoSources()
    except Exception:
        sources = None
    if not sources:
        return None, "منبع ویدیویی از دستگاه گرفته نشد."
    infos = []
    for vs in sources:
        d = _as_dict(vs)
        token = d.get("token") or d.get("_token") or ""
        name = str(d.get("Name") or d.get("name") or "")
        infos.append((str(token), name))
    if len(infos) == 1:
        return infos[0][0], ""
    # چند منبع (معمولاً NVR چندکاناله): تلاش برای تطبیق با شماره کانال
    if channel_hint:
        ch = str(channel_hint)
        for token, name in infos:
            if ch and (ch in token or ch in name):
                return token, ""
        try:
            idx = int(ch) - 1
            if 0 <= idx < len(infos) and infos[idx][0]:
                return infos[idx][0], ""
        except Exception:
            pass
    return None, ("چند منبع ویدیویی پیدا شد و منبع کانال %s تشخیص داده نشد."
                  % (channel_hint or "؟"))


def _try_ports(target, timeout=_PER_PORT_TIMEOUT):
    """اتصال به پورت‌های ONVIF؛ اولین پورتی که جواب داد برمی‌گردد.

    خروجی: (camera, imaging, media, port, error)
    """
    ports = []
    if target.get("port"):
        try:
            ports.append(int(target["port"]))
        except Exception:
            pass
    ports += [p for p in PROBE_PORTS if p not in ports]

    def attempt(port):
        cam = _new_camera(target["host"], port, target["user"], target["pwd"])
        imaging = cam.create_imaging_service()
        media = cam.create_media_service()
        return cam, imaging, media, port

    errors = []
    with _fut.ThreadPoolExecutor(max_workers=min(len(ports), 5)) as ex:
        future_map = {ex.submit(attempt, p): p for p in ports}
        try:
            for fut in _fut.as_completed(future_map, timeout=_OVERALL_TIMEOUT):
                port = future_map[fut]
                try:
                    return (*fut.result(), None)
                except Exception as e:
                    errors.append((port, _friendly_error(e)))
        except _fut.TimeoutError:
            pass
    # اگر همه شکست خوردند، پرتکرارترین/اولین خطا را گزارش بده
    if errors:
        errors.sort(key=lambda x: x[0])
        return None, None, None, None, errors[0][1]
    return None, None, None, None, "اتصال ONVIF برقرار نشد."


# ---------------------------------------------------------------------------
# WDR get/set
# ---------------------------------------------------------------------------

def _extract_wdr(settings_dict):
    """استخراج (mode, level) از دیکشنری ImagingSettings20."""
    wdr = (settings_dict or {}).get("WideDynamicRange") or {}
    mode = wdr.get("Mode")
    level = wdr.get("Level")
    if isinstance(mode, dict):
        mode = mode.get("_value_") or mode.get("value")
    try:
        level = float(level) if level is not None else None
    except Exception:
        level = None
    if isinstance(mode, str):
        mode = mode.strip().upper()
    return mode or None, level


def _extract_wdr_options(options_dict):
    """استخراج (modes, level_min, level_max) از ImagingOptions20."""
    wdr = (options_dict or {}).get("WideDynamicRange") or {}
    modes = wdr.get("Mode") or []
    if isinstance(modes, str):
        modes = [modes]
    modes = [str(m).strip().upper() for m in modes if str(m).strip()]
    lvl = wdr.get("Level") or {}
    try:
        lo = float(lvl.get("Min")) if lvl.get("Min") is not None else None
        hi = float(lvl.get("Max")) if lvl.get("Max") is not None else None
    except Exception:
        lo = hi = None
    return modes, lo, hi


def get_wdr(cam, camera_store=None, timeout=_OVERALL_TIMEOUT):
    """خواندن WDR سخت‌افزاری دوربین.

    خروجی موفق:
      {"ok": True, "supported": True, "mode": "OFF"/"ON"/..., "level": 0..100|None,
       "modes": [...], "host":..., "port":..., "via":..., "video_source":...}
    اگر دستگاه WDR را پشتیبانی نکند: {"ok": True, "supported": False, ...}
    در صورت خطا: {"ok": False, "error": "پیام فارسی"}
    """
    ok, reason = is_available()
    if not ok:
        return {"ok": False, "error": availability_message()}

    target = resolve_target(cam, camera_store)
    if "error" in target:
        return {"ok": False, "error": target["error"]}

    def work():
        camera, imaging, media, port, err = _try_ports(target)
        if err:
            return {"ok": False, "error": err}
        token, pick_err = _pick_video_source(media, target.get("channel"))
        if not token:
            return {"ok": False, "error": pick_err}
        try:
            settings = _as_dict(imaging.GetImagingSettings({"VideoSourceToken": token}))
        except Exception as e:
            return {"ok": False, "error": _friendly_error(e)}
        mode, level = _extract_wdr(settings)
        modes, lo, hi = [], None, None
        try:
            options = _as_dict(imaging.GetOptions({"VideoSourceToken": token}))
            modes, lo, hi = _extract_wdr_options(options)
        except Exception:
            pass
        supported = bool(mode) or bool(modes)
        result = {"ok": True, "supported": supported, "mode": mode,
                  "modes": modes or ([WDR_MODE_OFF, WDR_MODE_ON] if supported else []),
                  "host": target["host"], "port": port, "via": target["via"],
                  "label": target["label"], "video_source": token}
        # نرمال‌سازی level به بازه‌ی ۰..۱۰۰ برای اسلایدر رابط کاربری
        if level is not None and lo is not None and hi is not None and hi > lo:
            result["level"] = round((level - lo) / (hi - lo) * 100)
            result["level_range"] = (lo, hi)
        elif level is not None:
            # بدون اطلاعات بازه: فرض بازه‌ی استاندارد ۰..۱
            result["level"] = round(max(0.0, min(1.0, level)) * 100)
            result["level_range"] = (0.0, 1.0)
        else:
            result["level"] = None
            result["level_range"] = None
        return result

    with _fut.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(work)
        try:
            return fut.result(timeout=timeout)
        except _fut.TimeoutError:
            return {"ok": False, "error": "دستگاه در مهلت تعیین‌شده پاسخ نداد."}
        except Exception as e:
            return {"ok": False, "error": _friendly_error(e)}


def set_wdr(cam, mode, level_0_100=None, camera_store=None, timeout=_OVERALL_TIMEOUT):
    """تنظیم WDR سخت‌افزاری دوربین.

    mode: "OFF" یا "ON" (حروف بزرگ). level_0_100: شدت ۰..۱۰۰ (اختیاری؛
    اگر None باشد، سطح فعلی/پیش‌فرض دستگاه دست‌نخورده می‌ماند).
    خروجی: {"ok": True, ...} یا {"ok": False, "error": "پیام فارسی"}
    """
    ok, _ = is_available()
    if not ok:
        return {"ok": False, "error": availability_message()}
    mode = (mode or "").strip().upper()
    if mode not in (WDR_MODE_OFF, WDR_MODE_ON):
        return {"ok": False, "error": "حالت نامعتبر WDR: %s" % mode}

    target = resolve_target(cam, camera_store)
    if "error" in target:
        return {"ok": False, "error": target["error"]}

    def work():
        camera, imaging, media, port, err = _try_ports(target)
        if err:
            return {"ok": False, "error": err}
        token, pick_err = _pick_video_source(media, target.get("channel"))
        if not token:
            return {"ok": False, "error": pick_err}
        try:
            current = _as_dict(imaging.GetImagingSettings({"VideoSourceToken": token}))
        except Exception as e:
            return {"ok": False, "error": _friendly_error(e)}

        # بازه‌ی سطح دستگاه (برای نگاشت ۰..۱۰۰ رابط کاربری به مقیاس واقعی)
        lo, hi = 0.0, 1.0
        try:
            options = _as_dict(imaging.GetOptions({"VideoSourceToken": token}))
            _modes, olo, ohi = _extract_wdr_options(options)
            if olo is not None and ohi is not None and ohi > olo:
                lo, hi = olo, ohi
        except Exception:
            pass

        wdr = dict(current.get("WideDynamicRange") or {})
        wdr["Mode"] = mode
        if level_0_100 is not None and mode == WDR_MODE_ON:
            try:
                pct = max(0.0, min(100.0, float(level_0_100)))
            except Exception:
                pct = 50.0
            wdr["Level"] = lo + (hi - lo) * (pct / 100.0)
        elif "Level" in wdr and mode == WDR_MODE_OFF:
            # هنگام خاموش‌کردن، سطح قبلی را نگه می‌داریم تا با روشن‌کردن
            # بعدی همان شدت برگردد (اگر دستگاه اجازه دهد).
            pass
        new_settings = dict(current)
        new_settings["WideDynamicRange"] = wdr
        try:
            imaging.SetImagingSettings({
                "VideoSourceToken": token,
                "ImagingSettings": new_settings,
                "ForcePersistence": True,
            })
        except Exception as e:
            return {"ok": False, "error": _friendly_error(e)}
        return {"ok": True, "mode": mode,
                "level": level_0_100, "host": target["host"], "port": port,
                "via": target["via"], "label": target["label"]}

    with _fut.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(work)
        try:
            return fut.result(timeout=timeout)
        except _fut.TimeoutError:
            return {"ok": False, "error": "دستگاه در مهلت تعیین‌شده پاسخ نداد."}
        except Exception as e:
            return {"ok": False, "error": _friendly_error(e)}

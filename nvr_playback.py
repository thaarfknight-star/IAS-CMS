# -*- coding: utf-8 -*-
"""ساخت آدرس RTSP «پخش بازبینی» (Playback - نه پخش زنده) یک کانال روی NVR،
برای یک بازه‌ی زمانی مشخص.

هدف این ماژول: وقتی کاربر یک ردیف از «گزارش‌ها» (report_store.py) را باز
می‌کند، امکان دیدن ویدیوی *واقعیِ همان لحظه* که روی خودِ NVR ضبط شده فراهم
شود - بدون این‌که فایل/تصویر گزارش را روی NVR بارگذاری کنیم (که NVRها به‌طور
عمومی API نوشتنِ فایل دلخواه ندارند)، بلکه با اتصال RTSP مستقیم به بخش
بازبینیِ خودِ NVR در بازه‌ی زمانی موردنظر.

مثل بقیه‌ی این پروژه (nvr_http_api.py، nvr_storage_api.py)، برند دستگاه از
قبل فیلتر نمی‌شود: هر دو قالب رایج آدرس RTSP پخش بازبینی (Hikvision و Dahua)
ساخته و برگردانده می‌شوند تا کد فراخوان هر دو را امتحان کند و هرکدام واقعاً
فریم داد همان انتخاب شود (رجوع کنید به nvr_playback_dialog.py).

قالب‌های RTSP پخش بازبینی (مستند رسمی سازنده‌ها، نه ISAPI/CGI که فقط برای
جست‌وجوی فهرست بازه‌هاست - رجوع کنید به nvr_storage_api.py):

  Hikvision:
    rtsp://ip:554/Streaming/tracks/<trackID>?starttime=<YYYYMMDDTHHMMSSZ>&endtime=<...>
    trackID = channel*100 + 1  (دقیقاً همان قرارداد nvr_storage_api._hikvision_search_recordings)

  Dahua:
    rtsp://ip:554/cam/playback?channel=<channel>&subtype=0&starttime=<YYYY_MM_DD_HH_MM_SS>&endtime=<...>

(2.0.52-beta) دو مسیر جدید برای NVRهایی که هیچ‌کدام از این دو قالب را
پشتیبانی نمی‌کنند:
  ۱) قالب دلخواه پخش بازبینی (nvr["playback_template"]): کاربر از منوی
     راست‌کلیک NVR الگوی آدرس دستگاه خودش را با متغیرهای {ip} {port} {user}
     {pass} {channel} {start} {end} (و نسخه‌های آماده‌ی {start_hik}/
     {end_hik}/{start_dahua}/{end_dahua}) وارد می‌کند؛ اگر ست شده باشد،
     «اول» از همه امتحان می‌شود.
  ۲) ONVIF Replay استاندارد (try_onvif_replay_url): آدرس دقیق بازبینی از خود
     دستگاه پرسیده می‌شود؛ مستقل از برند. best-effort است و هر خطایی بی‌صدا
     None برمی‌گرداند تا کاندیداهای استاتیک امتحان شوند.
"""

import threading
from urllib.parse import quote


def _auth_prefix(user: str, pwd: str) -> str:
    if user and pwd:
        return f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
    return ""


def _hikvision_playback_url(ip, rtsp_port, user, pwd, channel, start_dt, end_dt):
    track_id = int(channel) * 100 + 1
    start_s = start_dt.strftime("%Y%m%dT%H%M%SZ")
    end_s = end_dt.strftime("%Y%m%dT%H%M%SZ")
    auth = _auth_prefix(user, pwd)
    return (
        f"rtsp://{auth}{ip}:{rtsp_port}/Streaming/tracks/{track_id}"
        f"?starttime={start_s}&endtime={end_s}"
    )


def _dahua_playback_url(ip, rtsp_port, user, pwd, channel, start_dt, end_dt):
    start_s = start_dt.strftime("%Y_%m_%d_%H_%M_%S")
    end_s = end_dt.strftime("%Y_%m_%d_%H_%M_%S")
    auth = _auth_prefix(user, pwd)
    return (
        f"rtsp://{auth}{ip}:{rtsp_port}/cam/playback"
        f"?channel={int(channel)}&subtype=0&starttime={start_s}&endtime={end_s}"
    )


def _custom_template_url(nvr: dict, channel, start_dt, end_dt):
    """آدرس از روی قالب دلخواه کاربر؛ None اگر قالبی ست نشده باشد."""
    template = (nvr.get("playback_template") or "").strip()
    if not template:
        return None
    ip = nvr.get("ip", "")
    rtsp_port = nvr.get("rtsp_port") or 554
    user = nvr.get("user", "")
    pwd = nvr.get("pass", "")
    ch = int(channel)
    start_hik = start_dt.strftime("%Y%m%dT%H%M%SZ")
    end_hik = end_dt.strftime("%Y%m%dT%H%M%SZ")
    start_dahua = start_dt.strftime("%Y_%m_%d_%H_%M_%S")
    end_dahua = end_dt.strftime("%Y_%m_%d_%H_%M_%S")
    try:
        url = template.format(
            ip=ip, port=rtsp_port, user=quote(user, safe=""), pass_=quote(pwd, safe=""),
            channel=ch, ch=ch,
            start=start_hik, end=end_hik,
            start_hik=start_hik, end_hik=end_hik,
            start_dahua=start_dahua, end_dahua=end_dahua,
        )
    except Exception:
        return None
    # اگر کاربر نام‌کاربری/رمز را در قالب نگذاشته ولی آدرس کامل نیست، اضافه کن
    if "://" in url and "@" not in url.split("://", 1)[1].split("/", 1)[0] and user:
        scheme, rest = url.split("://", 1)
        url = f"{scheme}://{_auth_prefix(user, pwd)}{rest}"
    return url or None


def try_onvif_replay_url(nvr: dict, channel, start_dt, timeout=10):
    """تلاش best-effort برای گرفتن آدرس پخش بازبینی از طریق ONVIF Replay.

    ``(برچسب, آدرس)`` یا None. چون فراخوانی SOAP ممکن است بلاک شود، حتماً در
    ترد جدا صدا زده شود (این تابع خودش هم با timeout داخلی محافظت می‌شود).
    """
    result = {}

    def _work():
        try:
            result["value"] = _onvif_replay_url_blocking(nvr, channel, start_dt)
        except Exception as e:
            result["error"] = str(e)[:120]

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout)
    return result.get("value")


def _onvif_replay_url_blocking(nvr: dict, channel, start_dt):
    from onvif import ONVIFCamera

    ip = (nvr.get("ip") or "").strip()
    user = nvr.get("user", "") or ""
    pwd = nvr.get("pass", "") or ""
    if not ip:
        return None
    ports = []
    try:
        p = int(nvr.get("onvif_port") or 0)
        if p > 0:
            ports.append(p)
    except Exception:
        pass
    for p in (80, 8000, 8080, 2020):
        if p not in ports:
            ports.append(p)

    ch = int(channel)
    # توکن‌های احتمالی ضبط برای این کانال (استاندارد مشخصی ندارد؛ چند حدس رایج)
    token_candidates = [str(ch), f"{ch:02d}", f"{ch:03d}", str(ch - 1), "0"]

    for port in ports:
        try:
            cam = ONVIFCamera(ip, port, user, pwd)
            replay = cam.create_replay_service()
            stream_setup = {"Stream": "RTP-Unicast",
                            "Transport": {"Protocol": "RTSP"}}
            for token in token_candidates:
                try:
                    req = replay.create_type("GetReplayUri")
                    # نام فیلدها را در ران‌تایم کشف کن تا با هر نسخه‌ی WSDL کار کند
                    field_names = set()
                    try:
                        xsd_type = req._xsd_type
                        for el in xsd_type.elements:
                            field_names.add(el[0] if isinstance(el, tuple) else
                                            getattr(el, "name", ""))
                    except Exception:
                        pass
                    if "StreamSetup" in field_names or not field_names:
                        try:
                            req.StreamSetup = stream_setup
                        except Exception:
                            pass
                    token_set = False
                    for tf in ("RecordingToken", "ProfileToken"):
                        if tf in field_names or not field_names:
                            try:
                                setattr(req, tf, token)
                                token_set = True
                                break
                            except Exception:
                                continue
                    if not token_set and field_names:
                        continue
                    if "StartTime" in field_names or not field_names:
                        try:
                            req.StartTime = start_dt
                        except Exception:
                            pass
                    resp = replay.GetReplayUri(req)
                    uri = getattr(resp, "Uri", "") or ""
                    if uri:
                        return (f"ONVIF Replay (پورت {port})", str(uri))
                except Exception:
                    continue
        except Exception:
            continue
    return None


def build_playback_urls(nvr: dict, channel, start_dt, end_dt):
    """لیستی از ``(برچسب, آدرس RTSP)`` برای امتحان کردن برمی‌گرداند - به‌ترتیب
    اولویت. ``channel`` باید همان شماره‌ای باشد که در cameras.json برای این
    دوربین ذخیره شده (رجوع کنید به camera_store.add_channel_camera)."""
    ip = nvr.get("ip", "")
    rtsp_port = nvr.get("rtsp_port") or 554
    user = nvr.get("user", "")
    pwd = nvr.get("pass", "")
    if channel in (None, ""):
        return []
    urls = []
    # (2.0.52-beta) قالب دلخواه کاربر اول از همه
    custom = _custom_template_url(nvr, channel, start_dt, end_dt)
    if custom:
        urls.append(("قالب دلخواه", custom))
    urls.extend([
        ("Hikvision", _hikvision_playback_url(ip, rtsp_port, user, pwd, channel, start_dt, end_dt)),
        ("Dahua", _dahua_playback_url(ip, rtsp_port, user, pwd, channel, start_dt, end_dt)),
    ])
    return urls

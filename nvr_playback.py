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
"""

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
    return [
        ("Hikvision", _hikvision_playback_url(ip, rtsp_port, user, pwd, channel, start_dt, end_dt)),
        ("Dahua", _dahua_playback_url(ip, rtsp_port, user, pwd, channel, start_dt, end_dt)),
    ]

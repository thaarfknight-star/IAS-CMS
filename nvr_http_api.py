"""کشف کانال‌های NVR از طریق API پیکربندی وب رسمی سازنده (نه RTSP/ONVIF).

چرا این روش اضافه شد (بخشی از بازنویسی سیستم جست‌وجوی NVR):
--------------------------------------------------------------
هم ONVIF و هم روش قدیمی brute-force الگوهای RTSP، نهایتاً «حدس» می‌زنند:
یا باید دستگاه از ONVIF پشتیبانی کند، یا باید الگوی URL درست حدس زده شود
(و اگر NVR/دوربین‌های متصل الگوی غیرمعمول داشته باشند، حدس شکست می‌خورد).

اکثر NVRهای Dahua/Hikvision و برندهای سازگار با آن‌ها (OEM/کلون) یک API
پیکربندی وب ساده هم دارند که مستقیماً تعداد و نام واقعی کانال‌های ورودی
ویدیو را از خود پیکربندی دستگاه برمی‌گرداند - نه با حدس زدن:

  - Hikvision (و سازگارها): ISAPI -> /ISAPI/System/Video/inputs/channels
  - Dahua (و سازگارها):      CGI   -> /cgi-bin/configManager.cgi?action=
                                        getConfig&name=ChannelTitle

مزیت این روش نسبت به دو روش دیگر:
  - سریع‌تر است: یک درخواست HTTP به‌جای امتحان ده‌ها الگوی RTSP روی هر کانال.
  - دقیق‌تر است: نام و تعداد واقعی کانال‌ها را می‌دهد، نه صرفاً حدس الگو.

این یک «میان‌بر» اختیاری است: اگر دستگاه این API را نداشته باشد، پورت‌ها
بسته باشند، یا احراز هویت رد شود، به‌سادگی None برمی‌گردد و NVRScanThread
بی‌صدا به ONVIF و در نهایت به روش brute-force سوییچ می‌کند - هیچ رفتار
قبلی حذف نشده، فقط یک لایه‌ی سریع‌تر/دقیق‌تر قبل از آن‌ها اضافه شده است.
"""

import re

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

DEFAULT_TIMEOUT = 4

# پورت‌های رایج وب/CGI که این APIها معمولاً رویشان اجرا می‌شوند (پورت وب
# اصلی دستگاه، معمولاً مستقل از پورت سرویس ONVIF).
COMMON_HTTP_PORTS = [80, 8080]


def _auth_variants(user, pwd):
    # اول Digest امتحان می‌شود چون اکثر Dahua/Hikvision امروزی به‌طور
    # پیش‌فرض Basic را برای مسیرهای پیکربندی غیرفعال می‌کنند؛ اگر شکست خورد،
    # Basic هم به‌عنوان جایگزین امتحان می‌شود (برای مدل‌های قدیمی‌تر/OEM).
    return [HTTPDigestAuth(user, pwd), HTTPBasicAuth(user, pwd)]


def _get(url, user, pwd, timeout):
    for auth in _auth_variants(user, pwd):
        try:
            resp = requests.get(url, auth=auth, timeout=timeout)
        except requests.RequestException:
            continue
        if resp.status_code == 200 and resp.text:
            return resp
    return None


def _try_hikvision_isapi(ip, port, user, pwd, timeout):
    url = f"http://{ip}:{port}/ISAPI/System/Video/inputs/channels"
    resp = _get(url, user, pwd, timeout)
    if resp is None:
        return None

    # ساختار XML واقعی بین برند/فریمور کمی فرق دارد (namespace، ترتیب فیلدها)؛
    # به‌جای یک پارسر سخت‌گیر XML که ممکن است روی نمونه‌های OEM خطا بدهد، از
    # regex ساده برای استخراج id و name هر ``<VideoInputChannel>`` استفاده
    # می‌شود که در عمل روی همه‌ی نسخه‌ها کار می‌کند.
    ids = re.findall(r"<id>(\d+)</id>", resp.text)
    names = re.findall(r"<name>([^<]*)</name>", resp.text)
    if not ids:
        return None

    channels = []
    for i, ch_id in enumerate(ids):
        name = (names[i].strip() if i < len(names) and names[i].strip() else f"کانال {i + 1}")
        # id در ISAPI معمولاً یک عدد صفرمبنا یا کد کانال (۱، ۲، ...) است؛
        # مسیر استریم Hikvision از فرمت {ch}01 پیروی می‌کند.
        ch_num = int(ch_id) if int(ch_id) < 100 else int(ch_id) // 100
        ch_num = ch_num or (i + 1)
        channels.append({
            "channel": ch_num,
            "name": name,
            "path": f"Streaming/Channels/{ch_num}01",
        })
    return channels or None


def _try_dahua_cgi(ip, port, user, pwd, timeout):
    url = f"http://{ip}:{port}/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"
    resp = _get(url, user, pwd, timeout)
    if resp is None:
        return None

    # فرمت پاسخ متنی ساده است، مثلاً یک خط به‌ازای هر کانال:
    #   table.ChannelTitle[0].Name=دوربین ورودی
    matches = re.findall(r"ChannelTitle\[(\d+)\]\.Name=(.*)", resp.text)
    if not matches:
        return None

    channels = []
    for idx_str, name in sorted(matches, key=lambda m: int(m[0])):
        ch_num = int(idx_str) + 1  # این اندیس صفرمبناست
        clean_name = name.strip() or f"کانال {ch_num}"
        channels.append({
            "channel": ch_num,
            "name": clean_name,
            "path": f"cam/realmonitor?channel={ch_num}&subtype=0",
        })
    return channels or None


def discover_channels_http(ip, user, pwd, ports=None, timeout=DEFAULT_TIMEOUT):
    """تلاش برای گرفتن لیست واقعی کانال‌ها از API وب سازنده.

    خروجی موفق: ``[{"channel": int, "name": str, "path": str}, ...]``.
    در غیر این صورت ``None`` (یعنی فراخوان باید به ONVIF/brute-force
    سوییچ کند - این تابع هیچ‌وقت استثنا پرتاب نمی‌کند).
    """
    if not user:
        return None
    for port in (ports or COMMON_HTTP_PORTS):
        for prober in (_try_hikvision_isapi, _try_dahua_cgi):
            try:
                result = prober(ip, port, user, pwd, timeout)
            except Exception:
                result = None
            if result:
                return result
    return None

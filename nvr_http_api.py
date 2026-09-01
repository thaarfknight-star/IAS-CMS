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
            "camera_ip": "",
        })
    return channels or None


def _try_hikvision_ip_channels(ip, port, user, pwd, timeout):
    """رفع درخواست: علاوه بر شماره/نام کانال، IP واقعی دوربین شبکه‌ای متصل به
    هر کانال (IP-channel/InputProxy) را هم از خود NVR می‌گیرد.

    این اطلاعات در ``/ISAPI/System/Video/inputs/channels`` وجود ندارد (آن
    endpoint فقط شماره/نام کانال ورودی ویدیو را می‌دهد، چه آنالوگ چه دیجیتال)؛
    IP واقعی دوربین‌های شبکه‌ای (IP camera) که به‌عنوان کانال به NVR اضافه
    شده‌اند، در ISAPI جدای دیگری به نام InputProxy نگه‌داری می‌شود.

    خروجی: ``{channel_num: ip_str}`` یا ``{}`` (هیچ‌وقت None/استثنا - در
    صورت نبود پشتیبانی، دیکشنری خالی برمی‌گردد تا کد فراخوان بدون مشکل ادامه
    دهد؛ NVRهایی با کانال آنالوگ صرف، این endpoint را ندارند که طبیعی است).
    """
    url = f"http://{ip}:{port}/ISAPI/ContentMgmt/InputProxy/channels"
    resp = _get(url, user, pwd, timeout)
    if resp is None:
        return {}

    result = {}
    # هر بلوک ``<InputProxyChannel>`` شامل id کانال و ``<ipAddress>`` دستگاه
    # منبع است؛ به‌جای پارس کامل XML، هر بلوک را جدا کرده و id/ipAddress آن
    # را با regex می‌گیریم (مقاوم‌تر در برابر تفاوت namespace/ترتیب فیلدها).
    for block in re.findall(r"<InputProxyChannel>.*?</InputProxyChannel>", resp.text, re.S):
        id_match = re.search(r"<id>(\d+)</id>", block)
        ip_match = re.search(r"<ipAddress>([^<]+)</ipAddress>", block)
        if not id_match or not ip_match:
            continue
        ch_num = int(id_match.group(1))
        ch_num = ch_num if ch_num < 100 else ch_num // 100
        cam_ip = ip_match.group(1).strip()
        if cam_ip:
            result[ch_num] = cam_ip
    return result


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
            "camera_ip": "",
        })
    return channels or None


def _try_dahua_remote_devices(ip, port, user, pwd, timeout):
    """معادل Dahua برای ``_try_hikvision_ip_channels``: IP واقعی دوربین‌های
    شبکه‌ای متصل به هر کانال را از پیکربندی ``RemoteDevice`` می‌گیرد (کانال‌های
    آنالوگ صرف این مقدار را ندارند و به‌سادگی نادیده گرفته می‌شوند).

    فرمت پاسخ متنی ساده است، مثلاً:
        table.RemoteDevice[0].Address=192.168.1.108
    که اندیس ``[0]`` صفرمبناست و به کانال ۱ نگاشت می‌شود (دقیقاً مثل
    ``_try_dahua_cgi`` بالا).
    """
    url = f"http://{ip}:{port}/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice"
    resp = _get(url, user, pwd, timeout)
    if resp is None:
        return {}

    result = {}
    for idx_str, addr in re.findall(r"RemoteDevice\[(\d+)\]\.Address=(.*)", resp.text):
        cam_ip = addr.strip()
        if cam_ip:
            result[int(idx_str) + 1] = cam_ip
    return result


# نگاشت هر تابع کشف کانال به تابع متناظر گرفتن IP دوربین‌های آن کانال‌ها
# (هر دو روی یک برند اجرا می‌شوند، فقط endpoint متفاوتی را می‌خوانند).
_CAMERA_IP_PROBERS = {
    _try_hikvision_isapi: _try_hikvision_ip_channels,
    _try_dahua_cgi: _try_dahua_remote_devices,
}


def discover_channels_http(ip, user, pwd, ports=None, timeout=DEFAULT_TIMEOUT):
    """تلاش برای گرفتن لیست واقعی کانال‌ها از API وب سازنده.

    خروجی موفق: ``[{"channel": int, "name": str, "path": str, "camera_ip": str}, ...]``.
    ``camera_ip`` رفع درخواست است: IP واقعی دوربین شبکه‌ای متصل به آن کانال
    (نه IP خود NVR)؛ اگر کانال آنالوگ باشد یا دستگاه این اطلاعات را ندهد،
    رشته‌ی خالی می‌ماند - هیچ‌وقت باعث شکست کل کشف کانال‌ها نمی‌شود.
    در صورت عدم موفقیت کلی، ``None`` برمی‌گردد (یعنی فراخوان باید به
    ONVIF/brute-force سوییچ کند - این تابع هیچ‌وقت استثنا پرتاب نمی‌کند).
    """
    if not user:
        return None
    for port in (ports or COMMON_HTTP_PORTS):
        for prober in (_try_hikvision_isapi, _try_dahua_cgi):
            try:
                result = prober(ip, port, user, pwd, timeout)
            except Exception:
                result = None
            if not result:
                continue

            # مرحله‌ی دوم (اختیاری): IP واقعی دوربین هر کانال را جدا می‌گیرد
            # و در همان دیکشنری کانال‌ها ادغام می‌کند. اگر این endpoint وجود
            # نداشته باشد (مثلاً NVR فقط کانال آنالوگ دارد) یا خطا بدهد، بی‌صدا
            # نادیده گرفته می‌شود - چون خودِ لیست کانال‌ها از قبل معتبر است.
            ip_prober = _CAMERA_IP_PROBERS.get(prober)
            if ip_prober:
                try:
                    camera_ips = ip_prober(ip, port, user, pwd, timeout)
                except Exception:
                    camera_ips = {}
                for ch in result:
                    if ch["channel"] in camera_ips:
                        ch["camera_ip"] = camera_ips[ch["channel"]]

            return result
    return None

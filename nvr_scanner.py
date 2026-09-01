import concurrent.futures
import ipaddress
import os
import re
import threading
from urllib.parse import urlparse

from PyQt6.QtCore import QThread, pyqtSignal

from nvr_http_api import discover_channels_http, COMMON_HTTP_PORTS
from rtsp_probe import describe_probe
from rtsp_utils import build_rtsp_url, probe_stream
from add_camera_dialog import CANDIDATE_PATHS

# --------------------------------------------------------------------------
# معماری جدید جست‌وجو (بازنویسی کامل - قبلاً فقط ONVIF -> brute-force RTSP بود):
#
#   ۱) API وب سازنده (nvr_http_api.discover_channels_http): سریع‌ترین و
#      دقیق‌ترین روش - تعداد/نام واقعی کانال‌ها را مستقیم از پیکربندی خود
#      دستگاه می‌گیرد (Hikvision ISAPI / Dahua CGI)، سپس هر کانال با یک
#      DESCRIBE سریع تایید می‌شود.
#   ۲) ONVIF (try_onvif_discovery): در صورت پشتیبانی دستگاه، آدرس واقعی هر
#      استریم را از طریق پروتکل استاندارد ONVIF می‌گیرد.
#   ۳) brute-force الگوهای RTSP شناخته‌شده (CHANNEL_TEMPLATES): آخرین راه‌حل؛
#      برای هر کانال ابتدا یک DESCRIBE سریع (rtsp_probe) و فقط در صورت
#      پاسخ نامشخص (نه رد قطعی)، یک بار هم با OpenCV/FFmpeg (probe_stream)
#      تایید می‌شود تا هم سرعت بالا برود و هم false-negative کم شود.
#
# در هر مرحله، اگر نتیجه‌ای پیدا نشود بی‌صدا به مرحله‌ی بعد سوییچ می‌شود؛
# در پایان اگر هیچ‌کدام جواب ندهند، از تشخیص‌های جمع‌آوری‌شده (401/404/
# timeout) یک پیام خطای دقیق به کاربر ساخته می‌شود (رجوع کنید به
# _summarize_diagnostics) تا مشخص شود مشکل از رمز/کاربری، الگوی برند، یا
# اصلاً دسترسی شبکه است.
# --------------------------------------------------------------------------

CHANNEL_TEMPLATES = {
    "dahua_iap": [
        "cam/realmonitor?channel={ch}&subtype=0",   # main stream
        "cam/realmonitor?channel={ch}&subtype=1",   # sub stream (کم‌حجم‌تر)
    ],
    "hikvision": [
        "Streaming/Channels/{ch}01",   # e.g. 101, 201, 301 = کانال 1، 2، 3 استریم اصلی
        "Streaming/Channels/{ch}02",
    ],
    "sunell": [
        "live/ch{ch0}",
        "live/ch{ch}",
        "h264/ch{ch}/main/av_stream",
    ],
    "generic": [
        "cam/realmonitor?channel={ch}&subtype=0",
        "Streaming/Channels/{ch}01",
        "live/ch{ch0}",
        "h264Preview_{ch2}_main",
        "video{ch}",
        "onvif{ch}",
        "profile{ch}",
    ],
}

BRAND_LABELS = {
    "auto": "تشخیص خودکار (همه برندها)",
    "dahua_iap": "Dahua / IAP",
    "hikvision": "Hikvision",
    "sunell": "Sunell",
    "generic": "سایر / عمومی",
}

# حداکثر زمان (ثانیه) قابل قبول برای کل مرحله‌ی کشف ONVIF. کتابخانه‌ی
# onvif-zeep/zeep به‌صورت پیش‌فرض timeout ندارد و اگر دستگاه در دسترس نباشد
# (یا پورت اشتباه باشد)، فراخوانی SOAP می‌تواند برای مدت نامحدود بلاک شود؛
# این دقیقاً یکی از علت‌های اصلی «NVR شناسایی می‌شود ولی وصل/اسکن نمی‌شود» و
# قفل‌شدن کامل دیالوگ بود. با اجرای آن در یک ترد جدا و گرفتن نتیجه با timeout،
# این بلاک‌شدن نامحدود از بین می‌رود.
ONVIF_TIMEOUT_SEC = 5

# پورت‌های رایج ONVIF که علاوه بر پورت وارد‌شده توسط کاربر امتحان می‌شوند (رفع
# باگ: خیلی از NVRها روی پورت 80 پاسخ HTTP معمولی می‌دهند نه ONVIF - سرویس
# ONVIF واقعی روی 8000/8080/2020 است - و چون فقط یک پورت امتحان می‌شد، ONVIF
# همیشه شکست می‌خورد و کد بی‌دلیل مستقیم به روش brute-force می‌رفت).
COMMON_ONVIF_PORTS = [80, 8000, 8080, 2020]

# حداکثر تعداد کانالی که هم‌زمان (موازی) بررسی می‌شود.
# روی سیستم‌های ضعیف (رم کم/بدون کارت‌گرافیک) تعداد هسته‌ی CPU معمولاً کم است؛
# باز کردن بیش از حد اتصال RTSP هم‌زمان روی چنین سیستمی باعث ازدحام CPU/شبکه و
# در نتیجه timeout کاذب برای کانال‌هایی می‌شود که واقعاً موجودند. تعداد ترد به
# نسبت هسته‌های موجود تنظیم می‌شود (حداقل 3، حداکثر 6).
CHANNEL_PROBE_WORKERS = max(3, min(6, (os.cpu_count() or 4)))


def _format_templates(templates, ch):
    return [t.format(ch=ch, ch0=ch - 1, ch2=f"{ch:02d}") for t in templates]


def _guess_direct_path(nvr_channel_path):
    """اگر پروب مستقیم روی خود دوربین (_find_direct_camera_path) هیچ مسیری
    پیدا نکرد، به‌عنوان بهترین حدس، همان مسیر کانال NVR (مثلاً
    ``Streaming/Channels/301`` یا ``cam/realmonitor?channel=3&subtype=0``) به
    معادل «کانال ۱» تبدیل می‌شود؛ چون از دید خودِ دوربین (وقتی مستقیماً به
    IP خودش وصل می‌شویم، نه از طریق NVR) این تنها/اولین کانالش است."""
    m = re.match(r"^Streaming/Channels/(\d+)(\d{2})$", nvr_channel_path)
    if m:
        return f"Streaming/Channels/1{m.group(2)}"
    if "cam/realmonitor" in nvr_channel_path:
        return re.sub(r"channel=\d+", "channel=1", nvr_channel_path)
    return nvr_channel_path


def _find_direct_camera_path(ip, user, pwd, rtsp_port="554"):
    """رفع درخواست: وقتی IP واقعی دوربین یک کانال شناسایی می‌شود، اتصال باید
    دقیقاً مثل افزودن یک دوربین تکی، به یک مسیر واقعاً تست‌شده (نه صرفاً
    حدس زده‌شده بر اساس الگوی کانال NVR) روی خود IP دوربین وصل شود؛ چون
    اتصال از طریق NVR ممکن است اصلاً کار نکند (پورت/پروکسی RTSP خودِ NVR
    خطا بدهد) در حالی که دوربین به‌صورت مستقیم کاملاً در دسترس است.

    دقیقاً همان لیست الگوهای ``add_camera_dialog.CANDIDATE_PATHS`` (که برای
    تشخیص خودکار مسیر یک دوربین تکی استفاده می‌شود) روی IP خود دوربین امتحان
    می‌شود؛ اولین مسیری که واقعاً پاسخ می‌دهد برگردانده می‌شود، یا در صورت
    شکست همه، ``None``."""
    for path in CANDIDATE_PATHS:
        result = describe_probe(ip, rtsp_port, path, user, pwd)
        if result:
            return path
        if result.status in ("timeout", "error", "refused"):
            try:
                if probe_stream(build_rtsp_url(ip, rtsp_port, user, pwd, path)):
                    return path
            except Exception:
                pass
    return None


def _extract_camera_ip_from_uri(uri, nvr_ip):
    """رفع درخواست: در بسیاری از NVRها (بیشتر Hikvision/Dahua برای کانال‌های
    IP)، آدرس RTSP برگشتی از ``GetStreamUri`` مستقیماً به IP واقعی خودِ
    دوربین شبکه‌ای اشاره می‌کند - نه IP خود NVR (NVR فقط پروفایل/متادیتا را
    از طریق ONVIF می‌دهد، ولی استریم را مستقیماً از دوربین می‌گیرد). اگر
    میزبان URI یک IP معتبر و متفاوت از IP خود NVR باشد، همان را به‌عنوان IP
    دوربین برمی‌گرداند؛ در غیر این صورت (میزبان = خود NVR، یعنی کانال آنالوگ
    یا NVR خودش استریم را پروکسی می‌کند) رشته‌ی خالی برمی‌گرداند."""
    try:
        host = urlparse(uri).hostname
        if not host:
            return ""
        ipaddress.ip_address(host)  # صرفاً معتبر بودن IP را بررسی می‌کند
    except (ValueError, TypeError):
        return ""
    return host if host != nvr_ip else ""


def _discover_onvif_channels(ip, onvif_port, user, pwd):
    """بدنه‌ی اصلی کشف ONVIF؛ این تابع می‌تواند برای مدتی نامحدود بلاک شود، به
    همین دلیل توسط try_onvif_discovery با یک مهلت زمانی ثابت فراخوانی می‌شود."""
    from onvif import ONVIFCamera

    cam = ONVIFCamera(ip, int(onvif_port), user, pwd)
    media = cam.create_media_service()
    profiles = media.GetProfiles()
    if not profiles:
        return None

    channels = []
    for idx, profile in enumerate(profiles, start=1):
        try:
            token = getattr(profile, "token", None) or getattr(profile, "_token", None)
            req = media.create_type("GetStreamUri")
            req.ProfileToken = token
            req.StreamSetup = {
                "Stream": "RTP-Unicast",
                "Transport": {"Protocol": "RTSP"},
            }
            uri = media.GetStreamUri(req).Uri
            name = getattr(profile, "Name", None) or f"کانال {idx}"
            camera_ip = _extract_camera_ip_from_uri(uri, ip)
            channels.append({"channel": idx, "name": str(name), "url": uri, "camera_ip": camera_ip})
        except Exception:
            continue

    return channels or None


def _try_onvif_single_port(ip, onvif_port, user, pwd, timeout):
    try:
        import onvif  # noqa: F401  - فقط برای بررسی نصب بودن کتابخانه
    except ImportError:
        return None

    # اجرای فراخوانی SOAP در یک ترد جدا با مهلت زمانی مشخص؛ بدون این کار، اگر
    # دستگاه به درخواست پاسخ ندهد، برنامه برای مدت نامحدود (تا زمان timeout
    # پیش‌فرض TCP سیستم‌عامل که می‌تواند دقیقه‌ها طول بکشد) قفل می‌ماند.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_discover_onvif_channels, ip, onvif_port, user, pwd)
        try:
            return future.result(timeout=timeout)
        except Exception:
            # هم شامل TimeoutError و هم هر خطای دیگر (اتصال رد شد، احراز هویت
            # ناموفق، دستگاه ONVIF را پشتیبانی نمی‌کند و ...).
            return None


def try_onvif_discovery(ip, onvif_port, user, pwd, timeout=ONVIF_TIMEOUT_SEC):
    """تلاش برای کشف کانال‌های واقعی NVR از طریق پروتکل استاندارد ONVIF.

    اگر کتابخانه‌ی onvif-zeep نصب نباشد، NVR از ONVIF پشتیبانی نکند، یا مهلت
    زمانی (timeout) به پایان برسد، None برمی‌گرداند تا کد فراخوان به روش
    بعدی سوییچ کند.

    رفع باگ: قبلاً فقط همان یک پورتی که کاربر در فیلد «پورت ONVIF» وارد کرده
    بود امتحان می‌شد؛ چون سرویس ONVIF واقعی اغلب روی پورتی غیر از 80 (که
    پیش‌فرض فرم است) اجرا می‌شود، این تلاش تقریباً همیشه شکست می‌خورد. حالا
    اگر پورت وارد‌شده جواب نداد، پورت‌های رایج دیگر هم امتحان می‌شوند.
    """
    ports_to_try = [onvif_port] if onvif_port else []
    ports_to_try += [p for p in COMMON_ONVIF_PORTS if p != onvif_port]

    for port in ports_to_try:
        result = _try_onvif_single_port(ip, port, user, pwd, timeout)
        if result:
            return result
    return None


class NVRScanThread(QThread):
    """کانال‌های (دوربین‌های) متصل به یک NVR را شناسایی می‌کند.

    به‌ترتیب سه روش را امتحان می‌کند (رجوع کنید به توضیح بالای فایل):
    API وب سازنده -> ONVIF -> brute-force الگوهای RTSP (با پروب سریع
    DESCRIBE + تایید نهایی OpenCV در صورت نیاز). اجرای موازی مرحله‌ی سوم
    باعث می‌شود جستجو برای NVRهایی با تعداد کانال زیاد به‌جای چند دقیقه، چند
    ثانیه طول بکشد و کاربر احساس «قفل‌شدن»/«وصل نشدن» نکند.
    """

    progress_signal = pyqtSignal(str)
    # channel, name, path/url, camera_ip, direct (رفع درخواست: اگر True، یعنی
    # باید مستقیماً به camera_ip وصل شد - نه از طریق NVR)
    channel_found_signal = pyqtSignal(int, str, str, str, bool)
    finished_signal = pyqtSignal(int)                  # تعداد کل کانال‌های یافت‌شده
    failed_signal = pyqtSignal(str)

    def __init__(self, ip, rtsp_port, onvif_port, user, pwd, brand="auto",
                 camera_brand="auto", max_channels=16, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.rtsp_port = rtsp_port
        self.onvif_port = onvif_port
        self.user = user
        self.pwd = pwd
        self.brand = brand
        # رفع درخواست: علاوه بر برند خود دستگاه NVR، برند دوربین‌های متصل به
        # آن هم به‌عنوان معیار جستجوی الگوی URL هر کانال در نظر گرفته می‌شود
        # (دقیقاً مثل بخش تشخیص خودکار مسیر یک دوربین تکی در add_camera_dialog.py).
        self.camera_brand = camera_brand
        self.max_channels = max_channels
        self._is_cancelled = False
        self._diagnostics = []               # [(channel, detail_str), ...]
        self._diagnostics_lock = threading.Lock()

    def cancel(self):
        self._is_cancelled = True

    def _build_url(self, path):
        return build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, path)

    def _probe_path(self, path):
        # probe_stream (رجوع کنید به rtsp_utils.py): هم از تداخل (race condition)
        # با پخش زنده‌ی هم‌زمان سایر دوربین‌ها روی متغیر محیطی FFmpeg جلوگیری
        # می‌کند، و هم چند بار تلاش می‌کند تا اولین کی‌فریم برسد.
        return probe_stream(self._build_url(path))

    def _record_diagnostic(self, channel, detail):
        with self._diagnostics_lock:
            self._diagnostics.append((channel, detail))

    def _http_ports(self):
        ports = list(COMMON_HTTP_PORTS)
        try:
            onvif_port_int = int(self.onvif_port)
            if onvif_port_int not in ports:
                ports.append(onvif_port_int)
        except (TypeError, ValueError):
            pass
        return ports

    # -------------------------------------------------- روش ۱: API وب ---

    def _try_http_api(self):
        """رجوع کنید به nvr_http_api.py.

        رفع درخواست («از طریق NVR وصل نمی‌شن و خطا داره»): کانال‌هایی که IP
        واقعی دوربین‌شان از قبل شناسایی شده (از طریق discover_channels_http)
        دیگر اصلاً از طریق پروکسی RTSP خود NVR تایید/متصل نمی‌شوند - چون
        همان پروکسی است که خطا می‌دهد؛ به‌جای آن، دقیقاً مثل افزودن یک
        دوربین تکی، مسیر واقعی روی خود IP دوربین پیدا می‌شود
        (_verify_direct_channels). فقط کانال‌های بدون IP شناسایی‌شده (مثلاً
        آنالوگ) طبق روال قبلی از طریق مسیر روی خود NVR تایید می‌شوند."""
        try:
            api_channels = discover_channels_http(self.ip, self.user, self.pwd, ports=self._http_ports())
        except Exception:
            api_channels = None
        if not api_channels or self._is_cancelled:
            return []

        self.progress_signal.emit("کانال‌ها از طریق API وب NVR پیدا شد؛ در حال تایید...")
        direct_targets = [ch for ch in api_channels if ch.get("camera_ip")]
        proxy_targets = [ch for ch in api_channels if not ch.get("camera_ip")]

        verified = []
        if direct_targets and not self._is_cancelled:
            verified.extend(self._verify_direct_channels(direct_targets))
        if proxy_targets and not self._is_cancelled:
            verified.extend(self._verify_proxy_channels(proxy_targets))
        verified.sort(key=lambda c: c["channel"])
        return verified

    def _verify_proxy_channels(self, channels):
        """تایید کانال‌های بدون IP دوربین شناسایی‌شده: مثل قبل، با یک
        DESCRIBE روی همان مسیر/پورت خود NVR."""
        verified = []
        for ch in channels:
            if self._is_cancelled:
                break
            result = describe_probe(self.ip, self.rtsp_port, ch["path"], self.user, self.pwd)
            if result:
                ch["direct"] = False
                verified.append(ch)
            else:
                self._record_diagnostic(ch["channel"], result.detail)
        return verified

    def _verify_direct_channels(self, channels):
        """رفع درخواست («به‌جای IP خود NVR، IP خود دوربین‌ها قرار بگیرد»):
        برای کانال‌هایی که IP واقعی دوربین‌شان معلوم است، همیشه همان IP
        (نه IP خود NVR) استفاده می‌شود - حتی اگر پروب مستقیم مسیر واقعی را
        پیدا نکند. ابتدا با همان الگوریتم افزودن دوربین تکی
        (_find_direct_camera_path) یک مسیر واقعاً تست‌شده روی خودِ دوربین پیدا
        می‌شود؛ اگر پیدا نشد، به‌عنوان بهترین حدس از تبدیل مسیر کانال NVR به
        معادل «کانال ۱» استفاده می‌شود (_guess_direct_path) - در هر دو حالت
        IP نهایی همان IP دوربین است، هرگز IP خود NVR."""
        self.progress_signal.emit("در حال یافتن مسیر مستقیم دوربین‌های شناسایی‌شده...")
        verified = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=CHANNEL_PROBE_WORKERS) as executor:
            future_to_ch = {
                executor.submit(_find_direct_camera_path, ch["camera_ip"], self.user, self.pwd): ch
                for ch in channels
            }
            for future in concurrent.futures.as_completed(future_to_ch):
                if self._is_cancelled:
                    continue
                ch = future_to_ch[future]
                try:
                    direct_path = future.result()
                except Exception:
                    direct_path = None

                ch["path"] = direct_path if direct_path is not None else _guess_direct_path(ch["path"])
                ch["direct"] = True
                if direct_path is None:
                    self._record_diagnostic(
                        ch["channel"],
                        f"دوربین {ch['camera_ip']}: مسیر واقعی تایید نشد؛ از مسیر پیش‌فرض حدسی استفاده شد."
                    )
                verified.append(ch)
        return verified

    # -------------------------------------------------- روش ۳: brute ---

    def _resolve_brands_to_try(self):
        """ترکیب برند NVR و برند دوربین‌های متصل به‌عنوان معیار جستجوی
        الگوی هر کانال (چون در عمل برند NVR فقط رابط خودش را تعیین می‌کند؛
        دوربین‌های متصل ممکن است برند دیگری داشته باشند)."""
        all_brands = ["dahua_iap", "hikvision", "sunell", "generic"]

        ordered = []
        for b in (self.brand, self.camera_brand):
            if b and b != "auto" and b not in ordered:
                ordered.append(b)

        if self.brand == "auto" or self.camera_brand == "auto":
            for b in all_brands:
                if b not in ordered:
                    ordered.append(b)

        return ordered or all_brands

    def _probe_channel(self, ch, brands_to_try):
        """یک کانال را با تمام الگوهای برندهای موردنظر تست می‌کند.

        هر مسیر ابتدا با یک DESCRIBE سریع (rtsp_probe) بررسی می‌شود؛ فقط
        وقتی نتیجه نامشخص است (نه رد قطعی مثل 401/404) یک بار هم با روش
        قدیمی‌تر OpenCV/FFmpeg (probe_stream) دوباره تایید می‌شود - برخی
        دستگاه‌های عجیب به DESCRIBE خام درست پاسخ نمی‌دهند ولی پخش واقعی
        کار می‌کند.

        خروجی: (channel, name, path) در صورت موفقیت، یا None."""
        last_detail = None
        for brand in brands_to_try:
            if self._is_cancelled:
                return None
            for path in _format_templates(CHANNEL_TEMPLATES[brand], ch):
                if self._is_cancelled:
                    return None
                result = describe_probe(self.ip, self.rtsp_port, path, self.user, self.pwd)
                if result:
                    return (ch, f"کانال {ch}", path)
                if result.status in ("timeout", "error", "refused"):
                    try:
                        if self._probe_path(path):
                            return (ch, f"کانال {ch}", path)
                    except Exception:
                        pass
                last_detail = result.detail
        if last_detail:
            self._record_diagnostic(ch, last_detail)
        return None

    def _summarize_diagnostics(self):
        """از تشخیص‌های جمع‌آوری‌شده حین اسکن (401/404/timeout و ...) یک
        پیام راهنمای دقیق‌تر از پیام عمومی «هیچ کانالی یافت نشد» می‌سازد."""
        if not self._diagnostics:
            return (
                "هیچ کانالی یافت نشد. IP، پورت RTSP، نام کاربری/رمز عبور را بررسی "
                "کنید یا برند NVR را به‌صورت دستی انتخاب کنید."
            )

        details = [d for _, d in self._diagnostics]
        unauthorized = sum(1 for d in details if "401" in d or "رمز" in d)
        refused_or_timeout = sum(1 for d in details if "timeout" in d.lower() or "رد شد" in d)

        if unauthorized and unauthorized >= len(details) / 2:
            return (
                "هیچ کانالی تایید نشد: بیشتر پاسخ‌ها «نام کاربری/رمز عبور اشتباه» "
                "(401) بودند. نام کاربری و رمز عبور را دوباره بررسی کنید."
            )
        if refused_or_timeout and refused_or_timeout >= len(details) / 2:
            return (
                "هیچ کانالی تایید نشد: بیشتر تلاش‌ها با timeout یا رد اتصال مواجه "
                "شدند. IP/پورت RTSP و اتصال شبکه به NVR را بررسی کنید."
            )
        return (
            "هیچ کانالی یافت نشد؛ مسیرهای امتحان‌شده برای این دستگاه معتبر "
            "نبودند (404). ممکن است برند NVR/دوربین با الگوهای شناخته‌شده فرق "
            "داشته باشد - برند را به‌صورت دستی امتحان کنید یا از ONVIF Device "
            "Manager برای گرفتن آدرس دقیق کانال استفاده کنید."
        )

    def run(self):
        found_count = 0

        # ۱) سریع‌ترین و دقیق‌ترین روش: API وب سازنده (Hikvision ISAPI / Dahua CGI)
        if not self._is_cancelled:
            self.progress_signal.emit("در حال بررسی API وب NVR...")
            http_channels = self._try_http_api()
            if http_channels:
                for ch in http_channels:
                    if self._is_cancelled:
                        break
                    self.channel_found_signal.emit(
                        ch["channel"], ch["name"], ch["path"], ch.get("camera_ip", ""),
                        bool(ch.get("direct"))
                    )
                    found_count += 1
                self.finished_signal.emit(found_count)
                return

        if self._is_cancelled:
            self.failed_signal.emit("جستجو لغو شد.")
            self.finished_signal.emit(found_count)
            return

        # ۲) تلاش برای کشف دقیق از طریق ONVIF (در صورت پشتیبانی، با مهلت زمانی محدود)
        if self.onvif_port and not self._is_cancelled:
            self.progress_signal.emit("در حال تلاش برای کشف کانال‌ها از طریق ONVIF...")
            onvif_channels = try_onvif_discovery(self.ip, self.onvif_port, self.user, self.pwd)
            if onvif_channels:
                for ch in onvif_channels:
                    if self._is_cancelled:
                        break
                    self.channel_found_signal.emit(
                        ch["channel"], ch["name"], ch["url"], ch.get("camera_ip", ""), True
                    )
                    found_count += 1
                self.finished_signal.emit(found_count)
                return

        if self._is_cancelled:
            self.failed_signal.emit("جستجو لغو شد.")
            self.finished_signal.emit(found_count)
            return

        # ۳) روش جایگزین: تست الگوهای RTSP شناخته‌شده برای هر کانال، به‌صورت موازی.
        brands_to_try = self._resolve_brands_to_try()

        self.progress_signal.emit(f"در حال بررسی {self.max_channels} کانال به‌صورت هم‌زمان...")
        channels = list(range(1, self.max_channels + 1))

        with concurrent.futures.ThreadPoolExecutor(max_workers=CHANNEL_PROBE_WORKERS) as executor:
            future_to_ch = {
                executor.submit(self._probe_channel, ch, brands_to_try): ch for ch in channels
            }
            checked = 0
            # نتایج به ترتیب شماره‌ی کانال مرتب می‌شوند تا لیست نهایی منظم باشد.
            results = {}
            for future in concurrent.futures.as_completed(future_to_ch):
                ch = future_to_ch[future]
                checked += 1
                self.progress_signal.emit(f"بررسی شد {checked} از {self.max_channels} کانال...")
                if self._is_cancelled:
                    continue
                try:
                    result = future.result()
                except Exception:
                    result = None
                if result:
                    results[ch] = result

            for ch in sorted(results):
                found_ch, name, path = results[ch]
                # روش brute-force صرفاً الگوی مسیر را حدس می‌زند و اطلاعی از IP
                # واقعی دوربین پشت این کانال ندارد (بر خلاف API وب/ONVIF)، پس
                # camera_ip همیشه خالی است.
                self.channel_found_signal.emit(found_ch, name, path, "", False)
                found_count += 1

        if self._is_cancelled:
            self.failed_signal.emit("جستجو لغو شد.")
        elif found_count == 0:
            self.failed_signal.emit(self._summarize_diagnostics())

        self.finished_signal.emit(found_count)

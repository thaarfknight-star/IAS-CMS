import concurrent.futures
import os

from PyQt6.QtCore import QThread, pyqtSignal

from rtsp_utils import build_rtsp_url, probe_stream

# --------------------------------------------------------------------------
# الگوهای استاندارد RTSP برای هر کانال یک NVR، به تفکیک برند.
# {ch}   -> شماره کانال یک‌مبنایی (1, 2, 3, ...)
# {ch0}  -> شماره کانال صفرمبنایی (0, 1, 2, ...)
# {ch2}  -> شماره کانال با دو رقم (01, 02, ...)
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
            channels.append({"channel": idx, "name": str(name), "url": uri})
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
    برندی (brute force) سوییچ کند. این روش دقیق‌ترین راه است چون تعداد و آدرس
    واقعی کانال‌ها را مستقیماً از خود دستگاه می‌گیرد (نه حدس زدن الگوی URL).

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

    ابتدا ONVIF را امتحان می‌کند (سریع و دقیق، در صورت پشتیبانی NVR و نصب بودن
    کتابخانه، با مهلت زمانی محدود)؛ در غیر این صورت هر کانال از 1 تا
    max_channels را به‌صورت **موازی** (چند کانال هم‌زمان) با الگوهای RTSP
    شناخته‌شده‌ی برندهای رایج تست می‌کند. اجرای موازی باعث می‌شود جستجو برای
    NVRهایی با تعداد کانال زیاد به‌جای چند دقیقه، چند ثانیه طول بکشد و کاربر
    احساس «قفل‌شدن»/«وصل نشدن» نکند.
    """

    progress_signal = pyqtSignal(str)
    channel_found_signal = pyqtSignal(int, str, str)   # channel, name, path/url
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
        # چرا لازم است: برند NVR فقط تعیین‌کننده‌ی نرم‌افزار/رابط خود NVR است؛
        # در عمل خیلی از NVRها (به‌خصوص مدل‌های عمومی/OEM) از دوربین‌های
        # برندهای دیگر هم پشتیبانی می‌کنند و مسیر واقعی استریم هر کانال از
        # الگوی برند *دوربین* متصل پیروی می‌کند، نه لزوماً برند NVR. قبلاً فقط
        # الگوهای برند NVR (یا در حالت "auto" همه‌ی الگوها) امتحان می‌شد و اگر
        # دوربین‌های متصل برند دیگری داشتند، کانال‌ها اشتباهاً «یافت نشد»
        # گزارش می‌شدند.
        self.camera_brand = camera_brand
        self.max_channels = max_channels
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _build_url(self, path):
        return build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, path)

    def _probe_path(self, path):
        # probe_stream (رجوع کنید به rtsp_utils.py): هم از تداخل (race condition)
        # با پخش زنده‌ی هم‌زمان سایر دوربین‌ها روی متغیر محیطی FFmpeg جلوگیری
        # می‌کند، و هم چند بار تلاش می‌کند تا اولین کی‌فریم برسد (رفع false
        # negative که باعث «کانال هست ولی پیدا نمی‌شود» بود).
        return probe_stream(self._build_url(path))

    def _resolve_brands_to_try(self):
        """رفع درخواست: ترکیب برند NVR و برند دوربین‌های متصل به‌عنوان معیار
        جستجوی الگوی هر کانال (رجوع کنید به توضیح camera_brand در __init__).

        - اگر هر دو برند صراحتاً انتخاب شده باشند (نه "auto")، الگوهای هر دو
          برند به‌ترتیب امتحان می‌شوند (بدون تکرار اگر یکسان باشند).
        - اگر حداقل یکی از این دو روی "auto" باشد، الگوهای برند(های)
          صراحتاً انتخاب‌شده ابتدا (اولویت) و سپس بقیه‌ی برندهای شناخته‌شده هم
          امتحان می‌شوند تا هیچ کانال واقعی از قلم نیفتد.
        """
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
        خروجی: (channel, name, path) در صورت موفقیت، یا None."""
        for brand in brands_to_try:
            if self._is_cancelled:
                return None
            for path in _format_templates(CHANNEL_TEMPLATES[brand], ch):
                if self._is_cancelled:
                    return None
                try:
                    if self._probe_path(path):
                        return (ch, f"کانال {ch}", path)
                except Exception:
                    continue
        return None

    def run(self):
        found_count = 0

        # ۱) تلاش برای کشف دقیق از طریق ONVIF (در صورت پشتیبانی، با مهلت زمانی محدود)
        if self.onvif_port and not self._is_cancelled:
            self.progress_signal.emit("در حال تلاش برای کشف کانال‌ها از طریق ONVIF...")
            onvif_channels = try_onvif_discovery(self.ip, self.onvif_port, self.user, self.pwd)
            if onvif_channels:
                for ch in onvif_channels:
                    if self._is_cancelled:
                        break
                    self.channel_found_signal.emit(ch["channel"], ch["name"], ch["url"])
                    found_count += 1
                self.finished_signal.emit(found_count)
                return

        if self._is_cancelled:
            self.failed_signal.emit("جستجو لغو شد.")
            self.finished_signal.emit(found_count)
            return

        # ۲) روش جایگزین: تست الگوهای RTSP شناخته‌شده برای هر کانال، به‌صورت موازی.
        # الگوهای امتحان‌شده هم برند خود NVR و هم برند دوربین‌های متصل را
        # پوشش می‌دهند (رجوع کنید به _resolve_brands_to_try).
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
                self.channel_found_signal.emit(found_ch, name, path)
                found_count += 1

        if self._is_cancelled:
            self.failed_signal.emit("جستجو لغو شد.")
        elif found_count == 0:
            self.failed_signal.emit(
                "هیچ کانالی یافت نشد. IP، پورت RTSP، نام کاربری/رمز عبور را بررسی کنید "
                "یا برند NVR را به‌صورت دستی انتخاب کنید."
            )

        self.finished_signal.emit(found_count)

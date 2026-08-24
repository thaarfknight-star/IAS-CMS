"""ابزار مشترک برای باز کردن اتصال RTSP از طریق OpenCV/FFmpeg.

چرا این فایل لازم است (رفع باگ «NVR شناسایی می‌شود ولی وصل نمی‌شود»):
--------------------------------------------------------------------
تنظیمات FFmpeg (رفع latency، timeout و ...) در OpenCV فقط از طریق متغیر محیطی
سراسری ``OPENCV_FFMPEG_CAPTURE_OPTIONS`` قابل تنظیم است. این متغیر محیطی برای
*کل پروسه* مشترک است، نه هر ترد به‌صورت جداگانه (thread-local نیست).

در نسخه‌ی قبلی برنامه، سه بخش مختلف هم‌زمان (و در تردهای جدا) این متغیر را با
مقادیر متفاوت ست می‌کردند:
  - پخش زنده‌ی هر دوربین باز (``camera_stream.CameraStreamThread``)
  - تشخیص خودکار مسیر یک دوربین تکی (``add_camera_dialog.AutoDetectThread``)
  - جستجوی کانال‌های یک NVR (``nvr_scanner.NVRScanThread``)

اگر کاربر همزمان یک دوربین را باز داشته باشد و در همان لحظه یک NVR جدید اضافه
کند (یا برعکس)، این تردها مقدار متغیر محیطی را روی هم بازنویسی می‌کنند؛ نتیجه
این می‌شود که یکی از دو طرف با تنظیمات اشتباه (مثلاً timeout بسیار کوتاه یا
transport اشتباه) تلاش برای اتصال می‌کند و اتصال RTSP بی‌دلیل و به‌طور
متناوب شکست می‌خورد — دقیقاً همان رفتار «شناسایی می‌شود ولی وصل نمی‌شود».

راه‌حل: تمام محل‌هایی که یک ``cv2.VideoCapture`` جدید با گزینه‌های FFmpeg باز
می‌کنند، باید از ``open_capture()`` در این فایل استفاده کنند؛ این تابع ست کردن
متغیر محیطی + باز کردن Capture را زیر یک قفل مشترک (``CAPTURE_OPEN_LOCK``)
انجام می‌دهد تا دو ترد هرگز همزمان این متغیر را عوض نکنند. توجه: قفل فقط حین
*باز کردن* اتصال گرفته می‌شود، نه در طول کل پخش زنده؛ چون گزینه‌های FFmpeg فقط
لحظه‌ی ``open()`` خوانده می‌شوند، این برای رفع race کاملاً کافی است و پخش
هم‌زمان چند دوربین را کند نمی‌کند.
"""

import concurrent.futures
import os
import threading
from urllib.parse import quote

import cv2

# قفل سراسری: فقط حین «ست کردن env var + باز کردن VideoCapture» گرفته می‌شود.
CAPTURE_OPEN_LOCK = threading.Lock()

# گزینه‌ی پیش‌فرض برای عملیات جستجو/تشخیص خودکار (اتصال کوتاه‌مدت فقط برای تست).
# نکته (رفع باگ «کانال/دوربین پیدا نمی‌شود»): stimeout قبلاً 2 ثانیه بود که برای
# NVRهای ضعیف یا شبکه‌های شلوغ (سیستم‌های کم‌رم که همزمان چند کانال را بررسی
# می‌کنند) کافی نبود و باعث می‌شد کانال‌های واقعاً موجود، به‌اشتباه «یافت نشد»
# گزارش شوند؛ به 3.5 ثانیه افزایش یافت.
#
# رفع باگ «اسکن کانال‌های NVR برای همیشه روی 'در حال جستجو...' می‌ماند و هیچ
# کانالی اضافه نمی‌شود»: گزینه‌ی ``stimeout`` فقط توسط نسخه‌های قدیمی‌تر FFmpeg
# شناخته می‌شود؛ در بسیاری از بیلدهای جدید FFmpeg (از جمله ffmpeg باندل‌شده با
# نسخه‌های اخیر opencv-python) این گزینه به ``timeout`` تغییر نام یافته و
# ``stimeout`` صرفاً نادیده گرفته می‌شود (بدون خطا). نتیجه: هیچ timeout واقعی
# اعمال نمی‌شود و cv2.VideoCapture روی هر کانال/IP بی‌پاسخ، تا timeout پیش‌فرض
# سیستم‌عامل برای اتصال TCP (که می‌تواند ده‌ها ثانیه تا چند دقیقه طول بکشد) بلاک
# می‌ماند - دقیقاً همان رفتار «برای همیشه روی جستجو می‌ماند». هر دو نام گزینه با
# هم پاس داده می‌شوند تا صرف‌نظر از نسخه‌ی FFmpeg، حداقل یکی از آن‌ها اثر کند.
PROBE_FFMPEG_OPTS = "rtsp_transport;tcp|stimeout;3500000|timeout;3500000"

# گزینه‌ی کم‌تاخیر برای پخش زنده‌ی طولانی‌مدت.
STREAM_FFMPEG_OPTS = (
    "rtsp_transport;tcp|stimeout;5000000|timeout;5000000|max_delay;300000|"
    "buffer_size;102400|fflags;nobuffer|flags;low_delay"
)

# رفع همان باگ از زاویه‌ی دوم (محافظ سخت، مستقل از اینکه گزینه‌ی FFmpeg بالا
# واقعاً اثر کند یا نه): چون نمی‌توان به تنظیمات FFmpeg به‌تنهایی اطمینان کرد،
# probe_stream/frames_look_identical عملیات مسدودکننده (باز کردن
# cv2.VideoCapture + خواندن فریم) را در یک ترد جداگانه اجرا می‌کنند و حداکثر
# HARD_PROBE_TIMEOUT_SEC ثانیه منتظر می‌مانند؛ در صورت عبور از این مهلت، فوراً
# به فراخوان کنترل برگردانده می‌شود (نتیجه: کانال یافت نشد) و ترد داخلی به‌صورت
# پس‌زمینه به کار خودش تا پایان طبیعی (که ممکن است دیرتر برسد) ادامه می‌دهد -
# بدون اینکه اسکن NVR یا تشخیص خودکار را قفل کند.
#
# توجه مهم: عمداً از ``with ThreadPoolExecutor() as executor`` استفاده نشده.
# متد ``__exit__`` آن (shutdown(wait=True)) همچنان منتظر پایان *واقعی* ترد
# داخلی می‌ماند، یعنی حتی با ``future.result(timeout=...)``، خودِ تابع فراخوان
# در عمل تا پایان کامل عملیات مسدودکننده بلاک می‌ماند - دقیقاً همین ظرافت در
# try_onvif_discovery (nvr_scanner.py) هم بود. اینجا از یک executor مشترک و
# پایدار (بدون shutdown هم‌زمان) استفاده می‌شود تا این مشکل تکرار نشود.
HARD_PROBE_TIMEOUT_SEC = 6.0
_PROBE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=64, thread_name_prefix="ias-rtsp-probe"
)


def _run_with_hard_timeout(func, timeout, *args, **kwargs):
    """func را در ترد جداگانه اجرا می‌کند و حداکثر timeout ثانیه صبر می‌کند.
    در صورت عبور از مهلت (یا هر خطای دیگر)، بدون بلاک‌شدن، مقدار پیش‌فرض
    (None) برمی‌گرداند."""
    future = _PROBE_EXECUTOR.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return None
    except Exception:
        return None

# حداکثر تعداد تلاش برای خواندن یک فریم بعد از باز شدن اتصال، حین «کشف/تست»
# (نه پخش زنده‌ی مداوم). رفع باگ «دوربین/کانال هست ولی پیدا نمی‌شود»:
# بعد از cap.isOpened()==True، اولین cap.read() اغلب False برمی‌گرداند چون
# دیکودر هنوز به اولین کی‌فریم (I-frame) نرسیده؛ قبلاً کد فقط یک بار read()
# را امتحان می‌کرد و با اولین شکست، مسیر/کانال درست را هم «یافت نشد» گزارش
# می‌داد. با چند تلاش پیاپی (با فاصله‌ی کوتاه)، این false negative برطرف می‌شود.
PROBE_READ_ATTEMPTS = 4


def open_capture(url: str, ffmpeg_options: str = PROBE_FFMPEG_OPTS) -> cv2.VideoCapture:
    """یک cv2.VideoCapture امن در برابر race condition متغیر محیطی باز می‌کند."""
    with CAPTURE_OPEN_LOCK:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_options
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    return cap


def build_rtsp_url(ip: str, port, user: str = "", pwd: str = "", path: str = "") -> str:
    """آدرس RTSP را می‌سازد و نام‌کاربری/رمزعبور را URL-encode می‌کند.

    رفع باگ: قبلاً user/pass مستقیماً و بدون escape داخل URL جایگذاری می‌شد
    (f"rtsp://{user}:{pwd}@{ip}...")؛ اگر رمز عبور (که خیلی از دوربین‌ها به‌طور
    پیش‌فرض شامل کاراکترهایی مثل @ # : / % است، مثلاً "Admin@123") شامل یکی از
    این کاراکترهای خاص URL بود، آدرس RTSP به‌صورت نامعتبر ساخته می‌شد (مثلاً @
    داخل پسورد به‌عنوان جداکننده‌ی user:pass از host تفسیر می‌شد) و اتصال بدون
    هیچ پیام خطای روشنی شکست می‌خورد - دقیقاً یکی از منابع اصلی «دوربین/کانال
    پیدا نمی‌شود» با اینکه اطلاعات ورود درست است.
    """
    if user and pwd:
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
    else:
        auth = ""
    return f"rtsp://{auth}{ip}:{port}/{path}" if path else f"rtsp://{auth}{ip}:{port}"


def _probe_stream_blocking(url: str, ffmpeg_options: str, attempts: int) -> bool:
    cap = open_capture(url, ffmpeg_options)
    try:
        if not cap.isOpened():
            return False
        for _ in range(max(1, attempts)):
            ret, frame = cap.read()
            if ret and frame is not None:
                return True
        return False
    finally:
        cap.release()


def probe_stream(url: str, ffmpeg_options: str = PROBE_FFMPEG_OPTS, attempts: int = PROBE_READ_ATTEMPTS) -> bool:
    """بررسی می‌کند آیا یک آدرس RTSP معتبر است و فریم واقعی برمی‌گرداند یا نه.

    برخلاف یک cap.read() تکی، چند بار پیاپی تلاش می‌کند تا false negative
    ناشی از تاخیر رسیدن اولین کی‌فریم رخ ندهد (رجوع کنید به PROBE_READ_ATTEMPTS).

    با مهلت زمانی سخت (HARD_PROBE_TIMEOUT_SEC) اجرا می‌شود تا در صورت بی‌اثر
    بودن گزینه‌ی timeout/stimeout روی بیلد FFmpeg نصب‌شده، اسکن برای همیشه
    معلق نماند (رجوع کنید به توضیح بالای فایل).
    """
    result = _run_with_hard_timeout(
        _probe_stream_blocking, HARD_PROBE_TIMEOUT_SEC, url, ffmpeg_options, attempts
    )
    return bool(result)


def _grab_probe_frame(cap, attempts: int = PROBE_READ_ATTEMPTS):
    for _ in range(max(1, attempts)):
        ret, frame = cap.read()
        if ret and frame is not None:
            return frame
    return None


# آستانه‌ی اختلاف میانگین (روی مقیاس ۰ تا ۲۵۵، بعد از grayscale + کوچک‌سازی)
# که پایین‌تر از آن دو فریم «عملاً یکسان» در نظر گرفته می‌شوند. عمداً کمی
# بالاست چون فشرده‌سازی H.264/نویز حسگر حتی بین دو فریم پیاپی از یک منبع
# واحد هم اختلاف جزئی ایجاد می‌کند.
FRAME_DIFF_IDENTICAL_THRESHOLD = 8.0


def frames_look_identical(url_a: str, url_b: str, diff_threshold: float = FRAME_DIFF_IDENTICAL_THRESHOLD) -> bool:
    """بررسی می‌کند آیا دو آدرس RTSP عملاً یک تصویر (یک فید) برمی‌گردانند یا نه.

    رفع باگ «دوربین تکی به‌اشتباه NVR تشخیص داده می‌شود» (device_detect.py):
    خیلی از دوربین‌های تکی ارزان‌قیمت (فریمورهای عمومی مبتنی بر Hisilicon که
    هم روی دوربین‌های تکی و هم روی NVR استفاده می‌شوند) پارامتر channel داخل
    URL را اصلاً بررسی نمی‌کنند و صرف‌نظر از مقدارش (channel=1 یا channel=2)
    همیشه همان تک استریم موجود را برمی‌گردانند. تا اینجا در کد فقط «باز شدن»
    اتصال RTSP کانال دوم بررسی می‌شد که برای این دوربین‌ها همیشه true است -
    نتیجه: دوربین تکی به‌اشتباه به‌عنوان NVR (چندکاناله) شناسایی می‌شد.

    این تابع علاوه بر باز شدن اتصال، یک فریم واقعی از هر دو آدرس می‌گیرد و
    محتوای تصویر را مقایسه می‌کند؛ فقط وقتی تصویر واقعاً متفاوت باشد (یعنی
    واقعاً دو منبع/دوربین جدا هستند) کانال دوم به‌عنوان کانال واقعی تایید
    می‌شود.

    مثل probe_stream، با مهلت زمانی سخت اجرا می‌شود تا در صورت بی‌اثر بودن
    گزینه‌ی timeout روی FFmpeg نصب‌شده، تشخیص خودکار نوع دستگاه معلق نماند.
    """

    def _grab_both():
        cap_a = open_capture(url_a)
        cap_b = open_capture(url_b)
        try:
            return _grab_probe_frame(cap_a), _grab_probe_frame(cap_b)
        finally:
            cap_a.release()
            cap_b.release()

    result = _run_with_hard_timeout(_grab_both, HARD_PROBE_TIMEOUT_SEC * 2)
    frame_a, frame_b = result if result is not None else (None, None)

    if frame_a is None or frame_b is None:
        # اگر نتوانستیم از یکی از دو طرف فریمی بگیریم، نمی‌توان با اطمینان
        # گفت یکسان‌اند؛ محافظه‌کارانه فرض می‌شود متفاوت‌اند (به نفع تشخیص NVR
        # به‌جای از دست دادن یک کانال واقعی).
        return False

    small_a = cv2.resize(frame_a, (48, 32), interpolation=cv2.INTER_AREA)
    small_b = cv2.resize(frame_b, (48, 32), interpolation=cv2.INTER_AREA)
    gray_a = cv2.cvtColor(small_a, cv2.COLOR_BGR2GRAY).astype("float32")
    gray_b = cv2.cvtColor(small_b, cv2.COLOR_BGR2GRAY).astype("float32")
    mean_diff = float(abs(gray_a - gray_b).mean())
    return mean_diff < diff_threshold

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
PROBE_FFMPEG_OPTS = "rtsp_transport;tcp|stimeout;3500000"

# گزینه‌ی کم‌تاخیر برای پخش زنده‌ی طولانی‌مدت.
STREAM_FFMPEG_OPTS = (
    "rtsp_transport;tcp|stimeout;5000000|max_delay;300000|"
    "buffer_size;102400|fflags;nobuffer|flags;low_delay"
)

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


def probe_stream(url: str, ffmpeg_options: str = PROBE_FFMPEG_OPTS, attempts: int = PROBE_READ_ATTEMPTS) -> bool:
    """بررسی می‌کند آیا یک آدرس RTSP معتبر است و فریم واقعی برمی‌گرداند یا نه.

    برخلاف یک cap.read() تکی، چند بار پیاپی تلاش می‌کند تا false negative
    ناشی از تاخیر رسیدن اولین کی‌فریم رخ ندهد (رجوع کنید به PROBE_READ_ATTEMPTS).
    """
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

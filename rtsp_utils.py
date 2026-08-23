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

import cv2

# قفل سراسری: فقط حین «ست کردن env var + باز کردن VideoCapture» گرفته می‌شود.
CAPTURE_OPEN_LOCK = threading.Lock()

# گزینه‌ی پیش‌فرض برای عملیات جستجو/تشخیص خودکار (اتصال کوتاه‌مدت فقط برای تست).
PROBE_FFMPEG_OPTS = "rtsp_transport;tcp|stimeout;2000000"

# گزینه‌ی کم‌تاخیر برای پخش زنده‌ی طولانی‌مدت.
STREAM_FFMPEG_OPTS = (
    "rtsp_transport;tcp|stimeout;5000000|max_delay;300000|"
    "buffer_size;102400|fflags;nobuffer|flags;low_delay"
)


def open_capture(url: str, ffmpeg_options: str = PROBE_FFMPEG_OPTS) -> cv2.VideoCapture:
    """یک cv2.VideoCapture امن در برابر race condition متغیر محیطی باز می‌کند."""
    with CAPTURE_OPEN_LOCK:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_options
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    return cap

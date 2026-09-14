import os
import threading
import concurrent.futures

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

import time

from rtsp_utils import open_capture, STREAM_FFMPEG_OPTS
from fire_config import get_params as get_fire_params
from image_profile import apply_profile as _apply_image_profile, is_neutral as _is_image_profile_neutral
from small_flame_detector import (
    SmallFlameDetector, DetectionConfirmer, merge_detections, cascade_yolo_confirm,
)

# نکته: person_detector و fire_smoke_detector عمداً در بالای فایل import
# نمی‌شوند. این دو ماژول ultralytics/torch را بالا می‌آورند (چند صد مگابایت
# رم + چند ثانیه زمان)؛ پس فقط در اولین تیک تشخیص (در ترد پس‌زمینه، نه ترد
# اصلی) بارگذاری می‌شوند - رجوع کنید به _get_person_detector و
# _get_fire_detector پایین‌تر. تا قبل از آن، برنامه با حداقل رم بالا می‌آید.

# نکته کلیدی برای رفع مشکل «Live نبودن»:
#   nobuffer / low_delay / max_delay کوچک از تجمع فریم در بافر داخلی FFmpeg جلوگیری می‌کنند.
#   بدون این تنظیمات، اگر پردازش (تشخیص چهره) کندتر از رسیدن فریم‌های شبکه باشد،
#   بافر به مرور پر شده و تصویر نمایش داده‌شده مربوط به چند ثانیه قبل می‌شود.
# نکته‌ی مهم دیگر: باز کردن Capture اکنون از طریق rtsp_utils.open_capture انجام
# می‌شود تا با تردهای دیگر (اسکن NVR، تشخیص خودکار دوربین تکی) روی متغیر محیطی
# مشترک FFmpeg دچار race condition نشود؛ رجوع کنید به توضیحات rtsp_utils.py.
FFMPEG_LOW_LATENCY_OPTS = STREAM_FFMPEG_OPTS


# ---------------------------------------------------------------------------
# حالت سبک (سیستم‌های ضعیف: ۴ گیگ رم / بدون کارت گرافیک)
# ---------------------------------------------------------------------------
# با IAS_LITE_MODE=1 اجباری، با IAS_LITE_MODE=0 غیرفعال؛ در غیر این صورت
# خودکار: اگر رم کل سیستم کمتر از ۶ گیگابایت باشد، حالت سبک روشن می‌شود
# (فاصله‌ی تشخیص بیشتر، حداکثر یک تشخیص هم‌زمان، بدون محدودیت نخ torch تا
# همان یک زنجیره با حداکثر سرعت کار کند).
def _total_ram_gb():
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        pass
    try:  # Windows
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        _st = _MS()
        _st.dwLength = ctypes.sizeof(_MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(_st)):
            return _st.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass
    try:  # Linux
        with open("/proc/meminfo", "r", encoding="utf-8") as _f:
            for _line in _f:
                if _line.startswith("MemTotal:"):
                    return int(_line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    return None


def is_low_spec_mode():
    _env = os.environ.get("IAS_LITE_MODE", "").strip().lower()
    if _env in ("1", "true", "yes"):
        return True
    if _env in ("0", "false", "no"):
        return False
    _ram = _total_ram_gb()
    return _ram is not None and _ram < 6.0


_LOW_SPEC = is_low_spec_mode()

# ---------------------------------------------------------------------------
# بهینه‌سازی سرعت: محدودیت سراسری هم‌زمانی تشخیص‌های سنگین
# ---------------------------------------------------------------------------
# هر دوربین یک worker پس‌زمینه دارد که در هر تیک، چهره (dlib) + شخص (YOLO) +
# حریق (YOLO تمام‌فریم + YOLO آبشاری روی کراپ‌ها) را پشت‌سرهم اجرا می‌کند.
# بدون این محدودیت، با چند دوربین هم‌زمان، چند پایپ‌لاین سنگین روی CPU با هم
# رقابت می‌کنند (thrashing: تعویض مداوم کانتکست + خرابی کش) و همه‌چیز - از
# جمله خودِ پخش زنده - کندتر می‌شود. با این سمافور، حداکثر
# _MAX_CONCURRENT_DETECTIONS دوربین هم‌زمان تشخیص می‌دهند؛ بقیه آن تیک را
# بی‌صدا رد می‌کنند (acquire غیرمسدودکننده) تا تیک بعدی - یعنی با زیاد شدن
# دوربین‌ها، نرخ تشخیص هر دوربین به‌آرامی کم می‌شود به‌جای اینکه کل سیستم
# قفل کند. در حالت سبک (۴ گیگ رم) فقط یک تشخیص هم‌زمان مجاز است تا رم/CPU
# برای پخش زنده بماند.
_CPU_COUNT = os.cpu_count() or 4
if _LOW_SPEC:
    _MAX_CONCURRENT_DETECTIONS = 1
else:
    _MAX_CONCURRENT_DETECTIONS = 2 if _CPU_COUNT >= 4 else 1
_DETECTION_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT_DETECTIONS)

# بارگذاری تنبل تشخیص‌دهنده‌های سنگین (ultralytics/torch):
_person_detector = None
_person_detector_failed = False
_fire_detector = None
_fire_detector_failed = False
_torch_configured = False


def _configure_torch_threads():
    """محدود کردن نخ‌های داخلی torch (فقط روی سیستم‌های قوی و فقط یک‌بار).

    هر استنتاج torch به‌صورت پیش‌فرض از همه‌ی هسته‌ها نخ می‌سازد؛ وقتی دو
    زنجیره‌ی تشخیص هم‌زمان اجرا می‌شوند، این نخ‌های داخلی با هم رقابت
    می‌کنند. در حالت سبک عمداً دست نمی‌زنیم تا همان یک زنجیره‌ی فعال با
    حداکثر سرعت کار کند."""
    global _torch_configured
    if _torch_configured or _LOW_SPEC:
        return
    _torch_configured = True
    try:
        import torch
        if _CPU_COUNT >= 4:
            torch.set_num_threads(max(1, _CPU_COUNT // 4))
            torch.set_num_interop_threads(1)
    except Exception:
        pass


def _get_person_detector():
    """دسترسی تنبل به PersonDetector؛ اولین فراخوانی مدل را بارگذاری می‌کند."""
    global _person_detector, _person_detector_failed
    if _person_detector is None and not _person_detector_failed:
        try:
            from person_detector import person_detector as _pd
            _person_detector = _pd
            _configure_torch_threads()
        except Exception as e:
            _person_detector_failed = True
            print(f"بارگذاری تشخیص شخص ممکن نشد: {e}")
    if _person_detector_failed or _person_detector is None:
        raise ImportError("person_detector در دسترس نیست")
    return _person_detector


def _get_fire_detector():
    """دسترسی تنبل به FireSmokeDetector؛ اولین فراخوانی مدل را بارگذاری می‌کند."""
    global _fire_detector, _fire_detector_failed
    if _fire_detector is None and not _fire_detector_failed:
        try:
            from fire_smoke_detector import fire_smoke_detector as _fd
            _fire_detector = _fd
            _configure_torch_threads()
        except Exception as e:
            _fire_detector_failed = True
            print(f"بارگذاری تشخیص حریق ممکن نشد: {e}")
    if _fire_detector_failed or _fire_detector is None:
        raise ImportError("fire_smoke_detector در دسترس نیست")
    return _fire_detector


_plate_detector = None
_plate_detector_failed = False


def _get_plate_detector():
    """دسترسی تنبل به PlateDetector (plate_detector.py)؛ اولین فراخوانی مدل
    پلاک را بارگذاری می‌کند - فقط وقتی پلاک‌خوانِ حداقل یک دوربین فعال باشد.
    الگو دقیقاً مثل _get_person_detector/_get_fire_detector است."""
    global _plate_detector, _plate_detector_failed
    if _plate_detector is None and not _plate_detector_failed:
        try:
            from plate_detector import get_shared_plate_detector as _get_shared
            _plate_detector = _get_shared()
            if _plate_detector is not None and _plate_detector.available:
                _configure_torch_threads()
        except Exception as e:
            _plate_detector_failed = True
            print(f"بارگذاری تشخیص پلاک ممکن نشد: {e}")
    if (_plate_detector_failed or _plate_detector is None
            or not _plate_detector.available):
        raise ImportError("plate_detector در دسترس نیست")
    return _plate_detector


def _get_plate_ocr():
    """دسترسی تنبل به موتور OCR پلاک (فقط در اولین خوانش واقعی)."""
    from plate_detector import get_shared_plate_ocr as _get_ocr
    return _get_ocr()


# ---------------------------------------------------------------------------
# شمارش افراد (Real Time People Counting)
# ---------------------------------------------------------------------------
# تاریخچه‌ی این بخش (برای اینکه بعداً معلوم باشد چرا الان این شکلی است):
#   نسخه‌ی اول از HOGDescriptor + SVM پیش‌فرض OpenCV استفاده می‌کرد که فقط
#   برای افراد ایستاده/در حال راه‌رفتن و کاملاً داخل قاب آموزش دیده بود -
#   برای دوربین‌های داخلی که فرد نشسته/نیم‌خیز/نیمه‌پنهان پشت میز است،
#   همیشه ۰ می‌داد.
#   نسخه‌ی دوم HOGDescriptor را کامل حذف کرد و به‌جایش «تعداد چهره‌های
#   شناسایی‌شده» (FaceEngine.recognize) را به‌عنوان تعداد افراد نمایش
#   می‌داد. این افراد نشسته/نیم‌خیز رو به دوربین را درست می‌شمرد، اما یک
#   محدودیت ذاتی داشت: اگر چهره‌ی فرد اصلاً دیده نشود (پشتش به دوربین،
#   مشغول قفسه/میز، سرش پایین و ...) باز هم ۰ می‌ماند - دقیقاً همان چیزی که
#   در تصویر ارسالی شما (فردی که کاملاً پشتش به دوربین است) رخ می‌داد.
#
# راه‌حل این نسخه: به‌جای شمردن «چهره»، از یک تشخیص‌دهنده‌ی *شخص/بدن کامل*
# (PersonDetector در person_detector.py، بر پایه‌ی YOLOv8n) استفاده می‌شود
# که روی هزاران تصویر واقعی از افراد در تمام حالت‌های بدن (ایستاده، نشسته،
# نیم‌خیز، خم‌شده، پشت به دوربین و ...) آموزش دیده - یعنی دیگر لازم نیست
# چهره اصلاً دیده شود. «تعداد افراد فعلی» برابر تعداد باکس‌های این
# تشخیص‌دهنده در self._last_person_boxes است (رجوع کنید به run() پایین‌تر).
# اگر به هر دلیل (کتابخانه‌ی ultralytics نصب نباشد، یا دانلود وزن مدل شکست
# بخورد) این تشخیص‌دهنده در دسترس نباشد، به همان روش قبلی (شمارش بر پایه‌ی
# چهره) برمی‌گردیم تا برنامه هرگز کرش نکند و شمارش کاملاً از کار نیفتد -
# رجوع کنید به self._person_detector_available.
#
# محدودیت باقی‌مانده (صادقانه): اگر فرد آن‌قدر پشت اثاثیه/دیوار پنهان باشد
# که تقریباً هیچ بخشی از بدنش در تصویر دیده نشود، هیچ تشخیص‌دهنده‌ی
# مبتنی‌بر تصویری (نه فقط این یکی) نمی‌تواند او را بشمارد.


def _crop_face(frame, box):
    """برش تصویر یک چهره از روی frame کامل بر اساس باکس (top, right, bottom, left).
    برای نمایش thumbnail در پنل تشخیص چهره استفاده می‌شود. در صورت نامعتبر بودن
    باکس (مثلاً بعد از resize شدن پنجره) None برمی‌گرداند."""
    top, right, bottom, left = box
    h, w = frame.shape[:2]
    top = max(0, top)
    left = max(0, left)
    bottom = min(h, bottom)
    right = min(w, right)
    if bottom <= top or right <= left:
        return None
    return frame[top:bottom, left:right].copy()


def _box_center(box):
    top, right, bottom, left = box
    return ((left + right) / 2.0, (top + bottom) / 2.0)


def _box_avg_size(box):
    top, right, bottom, left = box
    return max(1.0, ((right - left) + (bottom - top)) / 2.0)


class _FaceTracker:
    """رفع باگ «برچسب/رنگ کادر ناپایدار - سبز/قرمز عوض می‌شه»: تشخیص چهره
    روی تک‌تک فریم‌ها مستقل اجرا می‌شود؛ وقتی فاصله‌ی (distance) چهره‌ی یک
    نفر با نزدیک‌ترین چهره‌ی تعریف‌شده در بانک دقیقاً نزدیک آستانه‌ی tolerance
    باشد (مثلاً به‌خاطر زاویه‌ی کمی متفاوت سر در هر فریم)، ممکن است یک دور
    «شناخته‌شده» و دور بعد «تعریف‌نشده» تشخیص داده شود - نتیجه، چشمک‌زدن رنگ/
    برچسب است، بدون اینکه واقعاً کسی عوض شده باشد.

    این کلاس هر چهره‌ی تازه‌تشخیص‌داده‌شده را (بر اساس نزدیکی موقعیت کادر، نه
    هویت) به نزدیک‌ترین «ردِ» چهره‌ی همان دوربین در فریم‌های قبلی وصل می‌کند و
    یک هیستررزیس ساده اعمال می‌کند: برچسبِ نمایش‌داده‌شده فقط وقتی عوض می‌شود
    که هویت جدید حداقل ۲ دور پیاپی تکرار شود؛ در غیر این صورت همان برچسب قبلی
    (که معمولاً درست است) نگه داشته می‌شود. موقعیت کادر همیشه فوری به‌روز
    می‌شود تا دنبال‌کردن حرکت شخص تاخیر نداشته باشد - فقط «برچسب» است که
    پایدارتر می‌شود.

    هر ردِ گم‌شده (چهره‌ای که این دور تشخیص داده نشد) هم بلافاصله حذف نمی‌شود؛
    فقط بعد از چند دور پیاپیِ گم‌بودن پاک می‌شود (رفع چشمک‌زدن ظاهر/محو کادر)."""

    SWITCH_STREAK = 2   # چند دور پیاپی برای پذیرفتن تعویض برچسب
    MISS_LIMIT = 2       # چند دور پیاپی برای پاک‌کردن یک ردِ گم‌شده

    def __init__(self):
        self.tracks = []  # هر رد: box, displayed_person, candidate_person, candidate_streak, miss_streak

    def update(self, results):
        """results: خروجی خام FaceEngine.recognize (لیستی از {"box","person"}).
        خروجی: همان شکل، ولی با برچسب پایدارشده و شامل ردهای اخیراً گم‌شده هم
        (تا محو کادر هم با کمی تاخیر انجام شود)."""
        unmatched_tracks = list(self.tracks)
        for r in results:
            box, person = r["box"], r["person"]
            center = _box_center(box)
            size = _box_avg_size(box)

            best_track, best_dist = None, None
            for t in unmatched_tracks:
                d = ((center[0] - _box_center(t["box"])[0]) ** 2 +
                     (center[1] - _box_center(t["box"])[1]) ** 2) ** 0.5
                if d < size * 0.7 and (best_dist is None or d < best_dist):
                    best_track, best_dist = t, d

            if best_track is not None:
                unmatched_tracks.remove(best_track)
                best_track["box"] = box
                best_track["miss_streak"] = 0
                new_id = person["id"] if person else None
                displayed_id = best_track["displayed_person"]["id"] if best_track["displayed_person"] else None
                if new_id == displayed_id:
                    best_track["candidate_person"] = None
                    best_track["candidate_streak"] = 0
                else:
                    cand_id = best_track["candidate_person"]["id"] if best_track["candidate_person"] else None
                    if cand_id != new_id:
                        best_track["candidate_person"] = person
                        best_track["candidate_streak"] = 1
                    else:
                        best_track["candidate_streak"] += 1
                    if best_track["candidate_streak"] >= self.SWITCH_STREAK:
                        best_track["displayed_person"] = best_track["candidate_person"]
                        best_track["candidate_person"] = None
                        best_track["candidate_streak"] = 0
            else:
                # چهره‌ی کاملاً تازه - بدون تاخیر با همان برچسب اولش نمایش داده می‌شود.
                self.tracks.append({
                    "box": box,
                    "displayed_person": person,
                    "candidate_person": None,
                    "candidate_streak": 0,
                    "miss_streak": 0,
                })

        for t in unmatched_tracks:
            t["miss_streak"] += 1
        self.tracks = [t for t in self.tracks if t["miss_streak"] < self.MISS_LIMIT]

        return [{"box": t["box"], "person": t["displayed_person"]} for t in self.tracks]


# ---------------------------------------------------------------------------
# محدوده‌ی هشدار (Zone/ROI) و تشخیص ورود شخص به آن
# ---------------------------------------------------------------------------
# رفع درخواست: قبلاً کاربر با drag کردن یک مستطیل می‌کشید. حالا به‌جای آن،
# کاربر با کلیک‌های متوالی روی نقاط دلخواه (مثلاً گوشه‌های واقعی زمین/اتاق در
# تصویر دوربین - که لزوماً مستطیل نیستند، مثلاً یک راهرو کج یا فضای چندضلعی)
# یک چندضلعی (Polygon) دلخواه تعریف می‌کند؛ برنامه نقاط را به‌ترتیب به هم وصل
# می‌کند. به همین دلیل، هر منطقه (region) دیگر یک "rect" چهارگوشه‌ی ساده
# نیست، بلکه یک "points": [[x,y], [x,y], ...] (حداقل ۳ نقطه، نرمال‌شده‌ی
# 0..1) است. برای سازگاری با محدوده‌های قدیمی‌ای که قبلاً به‌صورت مستطیل
# ("rect": [x1,y1,x2,y2]) ذخیره شده‌اند، region_to_polygon همچنان از آن‌ها
# پشتیبانی می‌کند (با تبدیل به همان ۴ گوشه به‌صورت چندضلعی).
def region_to_polygon(region):
    """نقاط چندضلعیِ نرمال‌شده‌ی (0..1) یک region را برمی‌گرداند - چه به شکل
    جدید ("points") ذخیره شده باشد چه به شکل قدیمیِ مستطیلی ("rect"،
    برای فایل‌های ذخیره‌شده‌ی نسخه‌های قبلی برنامه)."""
    points = region.get("points")
    if points:
        return [(float(p[0]), float(p[1])) for p in points]
    rect = region.get("rect")
    if rect:
        x1, y1, x2, y2 = rect
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return []


def _point_in_polygon(point, polygon_px):
    """آیا نقطه‌ی point=(x,y) داخل چندضلعیِ polygon_px (لیستی از (x,y) به
    پیکسل، حداقل ۳ نقطه) است؟ الگوریتم استاندارد «پرتوافکنی» (ray casting):
    از نقطه یک پرتوی افقی به راست رسم می‌کنیم و تعداد برخوردش با یال‌های
    چندضلعی را می‌شماریم - فرد بودن یعنی داخل است. برخلاف تست مستطیلی قبلی،
    این الگوریتم برای هر چندضلعیِ دلخواه (نه فقط مستطیل) درست کار می‌کند."""
    if len(polygon_px) < 3:
        return False
    x, y = point
    inside = False
    n = len(polygon_px)
    x1, y1 = polygon_px[-1]
    for x2, y2 in polygon_px:
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-9) + x1):
            inside = not inside
        x1, y1 = x2, y2
    return inside


class _PersonRegionTracker:
    """ردیابی سبک مرکز کادر هر «شخص» (خروجی PersonDetector) بین دو دور
    متوالی تشخیص، فقط برای تشخیص «ورود به یکی از محدوده‌های هشدار» - نه یک
    ردیاب هویت کامل (دقیقاً همان الگوی _FaceTracker/PersonLineTracker قبلی).

    برخلاف خط فرضیِ قبلی (که فقط یک عبور لحظه‌ای را بین دو فریم بررسی
    می‌کرد)، اینجا باید وضعیت «داخل محدوده بودن» هر شخص بین چند دور متوالی
    حفظ شود تا وقتی کسی چند دور پشت سر هم داخل یک محدوده می‌ماند، فقط یک‌بار
    (لحظه‌ی ورود) هشدار صادر شود - نه هر دور که هنوز داخل است. به همین دلیل،
    برخلاف تطبیق بدون سقف مسافتِ نسخه‌ی قبلی، اینجا یک سقف مسافت (بر پایه‌ی
    اندازه‌ی باکس) برای تطبیق دو دور در نظر گرفته شده تا دو نفر مختلف به
    اشتباه به‌جای هم تشخیص داده نشوند."""

    def __init__(self):
        self.tracks = []  # هر رد: {"center": (x, y), "size": s, "inside": set(region_id)}

    def update(self, boxes, regions, frame_w, frame_h):
        """boxes: خروجی PersonDetector.detect (پیکسل خام، (top,right,bottom,left)).
        regions: لیستی از دیکشنری {"id","number","name","points"} (یا قدیمی
        "rect") که نقاطش نرمال‌شده‌ی 0..1 (نسبت به عرض/ارتفاع فریم خام) است.
        خروجی: لیستی از (number, name) برای هر «ورود تازه» در همین دور."""
        entered = []
        unmatched = list(self.tracks)
        new_tracks = []

        regions_px = []
        for r in (regions or []):
            polygon_norm = region_to_polygon(r)
            if len(polygon_norm) < 3:
                continue
            polygon_px = [(px * frame_w, py * frame_h) for px, py in polygon_norm]
            regions_px.append((r.get("id"), r.get("number"), r.get("name", ""), polygon_px))

        for box in boxes:
            top, right, bottom, left = box
            center = ((left + right) / 2.0, (top + bottom) / 2.0)
            size = max(1.0, ((right - left) + (bottom - top)) / 2.0)

            best, best_d = None, None
            for t in unmatched:
                d = ((center[0] - t["center"][0]) ** 2 + (center[1] - t["center"][1]) ** 2) ** 0.5
                if d < size * 1.5 and (best_d is None or d < best_d):
                    best, best_d = t, d

            prev_inside = set()
            if best is not None:
                unmatched.remove(best)
                prev_inside = best["inside"]

            cur_inside = set()
            for region_id, number, name, polygon_px in regions_px:
                if _point_in_polygon(center, polygon_px):
                    cur_inside.add(region_id)
                    if region_id not in prev_inside:
                        entered.append((number, name))

            new_tracks.append({"center": center, "size": size, "inside": cur_inside})

        self.tracks = new_tracks
        return entered


class CameraStreamThread(QThread):
    frame_ready = pyqtSignal(object, object)   # (frame_for_display, raw_frame)
    error_signal = pyqtSignal(str)
    connected_signal = pyqtSignal()
    # پنل تشخیص چهره (main.py) برای هر چهره‌ی دیده‌شده (چه تعریف‌شده چه تعریف‌نشده)
    # یک رویداد دریافت می‌کند: (person dict یا None، تصویر برش‌خورده‌ی چهره یا None)
    face_event_signal = pyqtSignal(object, object)
    # رفع درخواست: شمارش افراد Real Time. هر بار تعداد افراد تازه شمارش‌شده
    # تغییر کند (یا هربار محاسبه شود)، تعداد فعلی از این سیگنال ارسال می‌شود
    # تا در بالای پنجره‌ی همان دوربین نمایش داده شود. (از نسخه‌ی فعلی به بعد
    # این عدد از روی تعداد چهره‌های شناسایی‌شده محاسبه می‌شود - رجوع کنید به
    # توضیح بالای فایل - و دیگر هرگز با خطا مواجه نمی‌شود.)
    people_count_signal = pyqtSignal(int)
    # رفع درخواست: محدوده‌ی هشدار (Zone). هر بار شخصی وارد یکی از محدوده‌های
    # رسم‌شده توسط کاربر شود، این سیگنال با شماره و نام همان محدوده ارسال
    # می‌شود تا در main.py کادر دوربین قرمز شود، صدای آلارم پخش شود و پیام
    # مربوطه (مثلاً «ورود به محدوده شماره ۱ / اتاق سرور») نمایش داده شود.
    region_entered = pyqtSignal(int, str)  # (number, name)
    # رفع درخواست: «محدوده رسم می‌شود ولی هشدار هیچ‌وقت فعال نمی‌شود». علت
    # ریشه‌ای این بود که کل زنجیره‌ی هشدار (region_entered بالا) به بارگذاری
    # موفق مدل YOLOv8 در person_detector.py وابسته است، ولی وقتی آن مدل
    # بارگذاری نمی‌شد (ultralytics نصب نبود، فایل وزن پیدا نمی‌شد و ...)،
    # تنها ردی که می‌ماند یک print() در کنسول بود - که در نسخه‌ی exe نهایی
    # (build.yml: windows-console-mode=disable) اصلاً دیده نمی‌شود، پس
    # کاربر بی‌هیچ توضیحی فقط می‌دید محدوده رسم می‌شود ولی هرگز هشدار
    # نمی‌دهد. این سیگنال یک‌بار (بعد از اولین تلاش واقعی برای بارگذاری
    # مدل) وضعیت را به‌صورت صریح به main.py اطلاع می‌دهد تا به‌جای سکوت،
    # پیامی روی خودِ خانه‌ی دوربین نمایش داده شود.
    person_detector_status_signal = pyqtSignal(bool, str)  # (available, error_message)
    # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: برای هر ناحیه‌ی آتش/دود
    # تازه‌شناسایی‌شده (بعد از اعمال کول‌داون - رجوع کنید به
    # _FIRE_ALERT_COOLDOWN پایین‌تر) این سیگنال با نوع ('fire'/'smoke')،
    # تصویر برش‌خورده‌ی همان ناحیه و درصد اطمینان ارسال می‌شود.
    fire_event_signal = pyqtSignal(str, object, float)  # (kind, crop_frame, confidence)
    # دقیقاً معادل person_detector_status_signal برای تشخیص‌دهنده‌ی آتش/دود:
    # فقط یک‌بار (بعد از اولین تلاش واقعی بارگذاری مدل) ارسال می‌شود تا در
    # نبود وزن مدل، کاربر پیام روشنی ببیند نه سکوت.
    fire_detector_status_signal = pyqtSignal(bool, str)  # (available, error_message)
    # رفع درخواست «سیستم پلاک‌خوان»: برای هر پلاک تازه‌تأییدشده (بعد از
    # تأیید چندفریمی و کول‌داون - رجوع کنید به plate_detector.py) این سیگنال
    # با یک دیکشنری ارسال می‌شود:
    #   {"box": (x1,y1,x2,y2), "plate_text": کانونیکال, "plate_display": نمایشی,
    #    "conf": اطمینان, "crop": تصویر برش‌خورده‌ی پلاک}
    # تطبیق با پلاک‌های تعریف‌شده و ثبت رویداد در main.py انجام می‌شود.
    plate_event_signal = pyqtSignal(object)
    # وضعیت در دسترس بودن مدل پلاک (فقط یک‌بار بعد از اولین تلاش واقعی) تا
    # در نبود مدل، روی تایل پیام روشن نمایش داده شود نه سکوت.
    plate_detector_status_signal = pyqtSignal(bool, str)  # (available, error_message)

    # حداقل فاصله (ثانیه) بین دو رویداد پیاپی از یک نوع (آتش یا دود) برای
    # همان دوربین - جلوگیری از سیل رویداد/بنر/بیپ در هر دور تشخیص وقتی آتش/
    # دود همچنان در تصویر باقی است؛ دقیقاً همان نیاز کول‌دانی که برای ذخیره‌ی
    # «چهره‌ی تعریف‌نشده» در face_engine.py وجود دارد.
    _FIRE_ALERT_COOLDOWN = 15.0

    def __init__(self, rtsp_url, face_engine, process_every_n=5, parent=None):
        super().__init__(parent)
        self.rtsp_url = rtsp_url
        self.face_engine = face_engine
        # محدوده‌های هشدار: لیستی از {"id","number","name","rect"} با rect
        # نرمال‌شده‌ی 0..1 نسبت به ابعاد فریم خام. چون از ترد اصلی (بعد از
        # رسم/تایید/حذف توسط کاربر) نوشته می‌شود ولی از ترد پس‌زمینه‌ی تشخیص
        # خوانده می‌شود، با یک قفل ساده محافظت می‌شود.
        self._regions_lock = threading.Lock()
        self.regions = []
        self._region_tracker = _PersonRegionTracker()
        # این مقدار دیگر تعیین‌کننده‌ی «تاخیر» نیست (چون تشخیص چهره async است)،
        # فقط فاصله‌ی ارسال فریم‌های جدید برای پردازش تشخیص چهره را کنترل می‌کند.
        self.process_every_n = max(1, process_every_n)
        self._run_flag = True
        # پروفایل «تنظیمات تصویر» این دوربین (روشنایی/WDR/ضد مه/...)؛ None یعنی
        # خنثی (بدون پردازش). با set_image_profile از ترد GUI به‌روزرسانی می‌شود.
        self._image_profile = None
        self._last_results = []
        # نتیجه‌ی خام PersonDetector (کادر کل بدن، فارغ از حالت/چهره) روی
        # آخرین فریم پردازش‌شده؛ رجوع کنید به توضیح بالای فایل.
        self._last_person_boxes = []
        # فقط بعد از اولین تلاش برای بارگذاری مدل (در ترد پس‌زمینه‌ی تشخیص،
        # نه ترد اصلی) مقداردهی واقعی می‌شود؛ تا وقتی نامشخص است، شمارش از
        # روی چهره (روش قبلی) به‌عنوان جایگزین امن استفاده می‌شود.
        self._person_detector_available = False
        # رفع باگ چشمک‌زدن کادر/برچسب: به‌جای جایگزینی مستقیم نتیجه‌ی خام هر
        # دور تشخیص، از _FaceTracker (تعریف بالای فایل) برای پایدارسازی
        # موقعیت و برچسب استفاده می‌شود.
        self._face_tracker = _FaceTracker()

        # --- رفع ریشه‌ای تاخیر Live ---
        # قبلاً تشخیص چهره (face_engine.recognize) مستقیماً و به‌صورت همزمان (blocking)
        # داخل همین حلقه‌ی خواندن فریم اجرا می‌شد. چون تشخیص چهره کند است (چند ده تا چند
        # صد میلی‌ثانیه)، در همان بازه cap.read() فراخوانی نمی‌شد و فریم‌های شبکه در بافر
        # RTSP/FFmpeg انباشته می‌شدند؛ نتیجه، تاخیر فزاینده‌ی تصویر بود، مستقل از تنظیمات
        # low_delay. راه‌حل: تشخیص چهره در یک ترد جداگانه (اجراکننده) به‌صورت ناهمزمان
        # (async) انجام می‌شود؛ حلقه‌ی اصلی هرگز منتظر پایان آن نمی‌ماند و فریم جدید را
        # بلافاصله می‌خواند و نمایش می‌دهد. اگر پردازش قبلی هنوز تمام نشده باشد، فریم
        # فعلی صرفاً برای تشخیص نادیده گرفته می‌شود (frame skipping) نه اینکه گیرنده‌ی
        # ویدیو را متوقف کند.
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._recognize_busy = threading.Event()

        # --- شمارش افراد (اختیاری، پیش‌فرض خاموش) ---
        # تِرد جداگانه‌ای ندارد: تشخیص شخص (person_detector) همان‌جایی که
        # تشخیص چهره اجرا می‌شود (_run_recognition، در ترد پس‌زمینه‌ی
        # موجود) انجام می‌شود؛ اینجا فقط طول نتیجه‌ی از قبل محاسبه‌شده
        # (self._last_person_boxes، مستقل از این تنظیم همیشه به‌روزرسانی
        # می‌شود) خوانده و نمایش داده می‌شود - رجوع کنید به run().
        self.count_people_enabled = False
        self._last_people_count = -1  # برای فرستادن سیگنال فقط وقتی عدد واقعاً عوض شود

        # رفع درخواست: فرستادن person_detector_status_signal فقط یک‌بار (بعد
        # از اولین تلاش واقعی بارگذاری مدل) - نه در هر دور تشخیص - تا سیگنال
        # اسپم نشود.
        self._detector_status_emitted = False

        # --- تشخیص تصویری آتش/دود (اختیاری، رجوع کنید به fire_smoke_detector.py) ---
        self._last_fire_detections = []  # [(box, kind, conf), ...] - فقط تأییدشده‌های چندفریمی
        self._fire_detector_available = False
        self._fire_detector_status_emitted = False
        self._last_fire_alert_ts = {"fire": 0.0, "smoke": 0.0}
        # آشکارساز شعله‌ی کوچک (کلاسیک/سوسو - حالت زمانی دارد، پس نمونه‌ی جدا
        # برای هر دوربین) + تأییدکننده‌ی چندفریمی خروجی نهایی.
        self._small_flame_detector = SmallFlameDetector()
        self._fire_confirmer = DetectionConfirmer()

        # --- سیستم پلاک‌خوان (اختیاری، پیش‌فرض خاموش؛ رجوع کنید به plate_detector.py) ---
        # مثل SmallFlameDetector، ردیاب چندفریمی برای هر دوربین نمونه‌ی جداست
        # (حالت زمانی دارد). خودِ مدل YOLO و موتور OCR بین همه‌ی دوربین‌ها
        # مشترک‌اند (lazy singleton در plate_detector.py).
        self.plate_detection_enabled = False
        self._plate_tracker = None
        self._plate_ocr = None
        self._last_plate_detections = []  # [(box, text, is_defined)] برای رسم روی تصویر
        self._plate_detector_available = False
        self._plate_detector_status_emitted = False

    def set_plate_detection(self, enabled: bool):
        """روشن/خاموش کردن پلاک‌خوان برای این دوربین (از صفحه‌ی پلاک‌خوان یا
        شروع پخش با cam["plate_detection"])."""
        self.plate_detection_enabled = bool(enabled)
        if self.plate_detection_enabled and self._plate_tracker is None:
            try:
                from plate_detector import PlateTracker
                # کول‌داون از تنظیمات plate_store خوانده می‌شود (پیش‌فرض ۴۵ ثانیه)
                cooldown = 45.0
                try:
                    from plate_store import plate_store as _ps
                    cooldown = float(_ps.cooldown_seconds)
                except Exception:
                    pass
                self._plate_tracker = PlateTracker(cooldown_s=cooldown)
            except Exception:
                self._plate_tracker = None
        if not self.plate_detection_enabled:
            self._last_plate_detections = []

    def set_people_counting(self, enabled: bool):
        """روشن/خاموش کردن شمارش افراد Real Time برای این دوربین."""
        self.count_people_enabled = bool(enabled)
        self._last_people_count = -1
        if not self.count_people_enabled:
            self.people_count_signal.emit(0)

    def set_image_profile(self, profile):
        """تنظیم پروفایل «تنظیمات تصویر» (روشنایی/WDR/ضد مه/...) برای این دوربین.

        از ترد GUI صدا زده می‌شود و بلافاصله روی فریم‌های بعدی اعمال می‌گردد.
        تعویض، اتمیک (جایگزینی ارجاع) است و دیکشنری هیچ‌وقت درجا جهش داده
        نمی‌شود، پس قفل لازم نیست. پروفایل خنثی/None یعنی بدون پردازش (مسیر سریع).
        فقط روی فریم نمایشی اثر می‌گذارد؛ فریم خامِ ارسالی برای تشخیص دست‌نخورده می‌ماند.
        """
        if profile is None or _is_image_profile_neutral(profile):
            self._image_profile = None
        else:
            self._image_profile = dict(profile)

    def set_regions(self, regions):
        """تنظیم لیست کامل محدوده‌های هشدار برای این دوربین. regions لیستی
        از دیکشنری {"id","number","name","rect"} است (یا [] برای غیرفعال
        کردن کامل). با هر تغییر، ردیاب داخلی از نو ساخته می‌شود تا وضعیتِ
        «داخل بودن» محاسبه‌شده با محدوده‌های قبلی باعث هشدار اشتباه نشود."""
        with self._regions_lock:
            self.regions = list(regions or [])
            self._region_tracker = _PersonRegionTracker()

    def _submit_recognition(self, frame):
        if self._recognize_busy.is_set():
            return  # پردازش قبلی هنوز در حال اجراست؛ این فریم را برای تشخیص رد می‌کنیم
        if not _DETECTION_SEMAPHORE.acquire(blocking=False):
            return  # ظرفیت سراسری تشخیص پر است (دوربین‌های دیگر مشغول‌اند)؛
                    # این تیک رد می‌شود تا تیک بعدی - به‌جای رقابت همه با هم.
        self._recognize_busy.set()
        # یک کپی سبک برای پردازش پس‌زمینه؛ حلقه‌ی اصلی نباید منتظرش بماند.
        frame_copy = frame.copy()
        self._executor.submit(self._run_recognition, frame_copy)

    def _run_recognition(self, frame):
        try:
            # بارگذاری تنبل مدل‌های سنگین (فقط در اولین تیک تشخیص، در همین
            # ترد پس‌زمینه - نه در زمان بالا آمدن برنامه). اگر ماژولی نصب
            # نباشد، _get_* خطا می‌دهد و همان رفتار قبلی (available=False)
            # را شبیه‌سازی می‌کنیم تا برنامه کرش نکند.
            try:
                _pd = _get_person_detector()
            except Exception:
                _pd = None
            try:
                _fd = _get_fire_detector()
            except Exception:
                _fd = None

            results, unknown_event, known_events = self.face_engine.recognize(frame)            # رفع باگ «کادر چشمک می‌زنه» و «برچسب/رنگ ناپایدار (سبز/قرمز عوض
            # می‌شه)»: نتیجه‌ی خام هر دور تشخیص مستقیماً نمایش داده نمی‌شود؛
            # از _FaceTracker (تعریف بالای فایل) عبور می‌کند که هم ظاهر/محو
            # ناگهانی کادر را (با نگه‌داشتن چند دور) میرا می‌کند، هم برچسب هر
            # چهره را فقط بعد از تکرار پیاپی یک هویت جدید عوض می‌کند - نه با
            # اولین نوسان لحظه‌ای تشخیص.
            self._last_results = self._face_tracker.update(results)

            # --- تشخیص شخص (کل بدن، مستقل از حالت/دیده‌بودن چهره) ---
            # عمداً در همین ترد پس‌زمینه‌ی تشخیص (نه ترد اصلی خواندن فریم)
            # و بعد از تشخیص چهره اجرا می‌شود - دقیقاً همان الگویی که برای
            # تشخیص چهره استفاده شده (رجوع کنید به توضیح بالای run()):
            # حلقه‌ی اصلی هرگز منتظر این پردازش نمی‌ماند، و چون هر دو در یک
            # ترد پس‌زمینه‌ی تک‌کارگر پشت‌سرهم اجرا می‌شوند، دو تشخیص با هم
            # روی CPU رقابت نمی‌کنند.
            self._last_person_boxes = _pd.detect(frame) if _pd is not None else []
            self._person_detector_available = bool(_pd is not None and _pd.available)
            if not self._detector_status_emitted:
                self._detector_status_emitted = True
                self.person_detector_status_signal.emit(
                    self._person_detector_available, (_pd.load_error if _pd else "") or ""
                )

            # --- محدوده‌ی هشدار: بعد از هر دور تشخیص شخص، بررسی می‌شود که
            # آیا مرکز یکی از افراد تازه وارد یکی از محدوده‌های تعریف‌شده‌ی
            # کاربر شده یا نه (رجوع کنید به _PersonRegionTracker بالا).
            with self._regions_lock:
                regions = self.regions
            if regions and self._person_detector_available:
                h, w = frame.shape[:2]
                for number, name in self._region_tracker.update(self._last_person_boxes, regions, w, h):
                    self.region_entered.emit(number, name)

            # --- تشخیص تصویری آتش/دود: خط لوله‌ی سه‌مرحله‌ای ---
            # ۱) YOLO روی تمام فریم (آتش/دود بزرگ - رفتار قبلی، با آستانه‌ی حساسیت)
            # ۲) آشکارساز کلاسیک شعله‌ی کوچک (روشنایی+سوسو - فندک/شمع/کبریت)
            # ۳) آبشار: کراپ+بزرگ‌نمایی ناحیه‌های مشکوک و اجرای مجدد YOLO روی آن‌ها
            # خروجی نهایی همه از «تأیید چندفریمی» می‌گذرد: شیء باید در k فریم از
            # n فریم آخر دیده شده باشد تا آلارم/کادر بدهد (رجوع کنید به
            # small_flame_detector.py). دقیقاً همان الگوی تشخیص شخص بالا: در
            # همین ترد پس‌زمینه‌ی تشخیص (نه ترد اصلی خواندن فریم) اجرا می‌شود تا
            # پخش زنده هرگز منتظرش نماند.
            fparams = get_fire_params()
            _yolo_dets = _fd.detect(frame, conf=fparams["yolo_conf"]) if _fd is not None else []
            _small_dets = self._small_flame_detector.detect(frame)
            _cascade_dets = cascade_yolo_confirm(frame, _small_dets, conf=fparams["yolo_conf"])
            _merged = merge_detections(_yolo_dets + _small_dets + _cascade_dets)
            _diag = (frame.shape[0] ** 2 + frame.shape[1] ** 2) ** 0.5
            self._last_fire_detections = self._fire_confirmer.update(
                _merged, k=fparams["confirm_k"], n=fparams["confirm_n"],
                frame_diag=_diag,
            )
            self._fire_detector_available = bool(_fd is not None and _fd.available)
            if not self._fire_detector_status_emitted:
                self._fire_detector_status_emitted = True
                self.fire_detector_status_signal.emit(
                    self._fire_detector_available, (_fd.load_error if _fd else "") or ""
                )
            now = time.time()
            for box, kind, conf in self._last_fire_detections:
                if now - self._last_fire_alert_ts.get(kind, 0.0) < self._FIRE_ALERT_COOLDOWN:
                    continue  # هنوز داخل بازه‌ی کول‌داون همان نوع رویداد برای این دوربین
                self._last_fire_alert_ts[kind] = now
                self.fire_event_signal.emit(kind, _crop_face(frame, box), conf)

            # --- سیستم پلاک‌خوان: تشخیص ناحیه‌ی پلاک + OCR + ردیابی چندفریمی ---
            # دقیقاً همان الگوی تشخیص شخص/آتش: در همین ترد پس‌زمینه‌ی تشخیص
            # (نه ترد اصلی خواندن فریم) اجرا می‌شود تا پخش زنده هرگز منتظرش
            # نماند. بارگذاری مدل/OCR تنبل است (اولین تیکِ دوربینی که پلاک‌خوانش
            # فعال است). تطبیق با پلاک‌های تعریف‌شده همین‌جا (ارزان، با کش)
            # انجام می‌شود تا رنگ باکس روی تصویر درست باشد؛ ثبت رویداد و
            # به‌روزرسانی گزارش در main.py است.
            if self.plate_detection_enabled and self._plate_tracker is not None:
                try:
                    _pld = _get_plate_detector()
                except Exception:
                    _pld = None
                self._plate_detector_available = bool(
                    _pld is not None and _pld.available)
                if not self._plate_detector_status_emitted:
                    self._plate_detector_status_emitted = True
                    self.plate_detector_status_signal.emit(
                        self._plate_detector_available,
                        (_pld.load_error if _pld else "") or "")
                if self._plate_detector_available:
                    try:
                        _pboxes = _pld.detect(frame)
                    except Exception:
                        _pboxes = []
                    try:
                        if self._plate_ocr is None:
                            self._plate_ocr = _get_plate_ocr()
                        _pevents = self._plate_tracker.update(
                            _pboxes, frame, self._plate_ocr)
                    except Exception:
                        _pevents = []
                    # وضعیت فعلی ترک‌ها برای رسم (با تطبیق تعریف‌شده/نشده)
                    _draw_list = []
                    try:
                        from plate_store import plate_store as _ps2
                        _tracks = self._plate_tracker.current_tracks()
                        for _box, _text, _conf in _tracks:
                            _m, _s, _k = _ps2.find_match(_text) if _text else (None, 0.0, "none")
                            _draw_list.append((_box, _text, bool(_m)))
                    except Exception:
                        _draw_list = []
                    self._last_plate_detections = _draw_list
                    # رویدادهای تازه‌ی تأییدشده -> main.py
                    for _box, _text, _conf in _pevents:
                        try:
                            from plate_store import prettify_plate as _pretty
                            _x1, _y1, _x2, _y2 = (int(v) for v in _box)
                            _crop = frame[max(0, _y1):_y2, max(0, _x1):_x2].copy()
                        except Exception:
                            _crop = None
                        self.plate_event_signal.emit({
                            "box": _box,
                            "plate_text": _text,
                            "plate_display": _pretty(_text),
                            "conf": float(_conf),
                            "crop": _crop,
                        })

            if unknown_event is not None:
                unknown_crop = _crop_face(frame, unknown_event)
                # رفع درخواست: چهره‌ی تعریف‌نشده علاوه بر نمایش در پنل، بر اساس
                # تاریخ و ساعت روی دیسک هم ذخیره می‌شود (رجوع کنید به
                # FaceEngine.save_unknown_face). چون خود recognize() یک
                # کول‌داون برای این رویداد دارد، این ذخیره‌سازی هم به‌طور
                # خودکار محدود می‌شود و باعث انباشت بی‌رویه فایل نمی‌شود.
                self.face_engine.save_unknown_face(unknown_crop)
                self.face_event_signal.emit(None, unknown_crop)
            for person, box in known_events:
                self.face_event_signal.emit(person, _crop_face(frame, box))
        except Exception as e:
            # خطای تشخیص چهره نباید باعث توقف پخش زنده شود.
            print(f"خطا در تشخیص چهره: {e}")
        finally:
            self._recognize_busy.clear()
            # آزاد کردن ظرفیت سراسری تشخیص (رجوع کنید به _submit_recognition).
            try:
                _DETECTION_SEMAPHORE.release()
            except Exception:
                pass

    def run(self):
        cap = open_capture(self.rtsp_url, FFMPEG_LOW_LATENCY_OPTS)
        try:
            # بافر داخلی OpenCV/FFmpeg را به حداقل می‌رسانیم تا همیشه جدیدترین فریم نمایش داده شود.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            self.error_signal.emit("خطا در برقراری ارتباط با استریم RTSP.")
            self._executor.shutdown(wait=False)
            return

        self.connected_signal.emit()
        frame_counter = 0

        while self._run_flag:
            ret, frame = cap.read()
            if not ret or frame is None:
                self.msleep(10)
                continue

            frame_counter += 1

            # تشخیص چهره به‌صورت ناهمزمان (پس‌زمینه) ارسال می‌شود و حلقه‌ی خواندن فریم
            # را هرگز مسدود (block) نمی‌کند؛ در نتیجه تصویر همیشه با کمترین تاخیر ممکن
            # (فقط تاخیر شبکه) نمایش داده می‌شود.
            if frame_counter % self.process_every_n == 0:
                self._submit_recognition(frame)

            # رفع باگ «کسی تو اتاقه ولی عدد صفر قفل شده»: شمارش دیگر
            # پردازش/تِرد جداگانه‌ای ندارد - فقط طول یکی از دو لیست از قبل
            # موجود (self._last_person_boxes یا self._last_results، هر دو
            # با هر بار تشخیص در _run_recognition به‌روزرسانی می‌شوند) خوانده
            # می‌شود. اولویت با PersonDetector (کل بدن، هر حالتی) است چون
            # نیازی به دیدن چهره ندارد؛ فقط اگر آن تشخیص‌دهنده در دسترس
            # نباشد (مثلاً ultralytics نصب نیست)، به شمارش بر پایه‌ی چهره
            # برمی‌گردیم. چون این فقط یک len() است (نه پردازش تصویر)، بدون
            # هیچ هزینه‌ی اضافه‌ای هر فریم قابل به‌روزرسانی است.
            if self.count_people_enabled:
                if self._person_detector_available:
                    current_count = len(self._last_person_boxes)
                else:
                    current_count = len(self._last_results)
                if current_count != self._last_people_count:
                    self._last_people_count = current_count
                    self.people_count_signal.emit(current_count)

            # بهینه‌سازی سرعت: بیشتر فریم‌ها هیچ باکس/پروفایلی برای رسم ندارند؛
            # کپی ۶ مگابایتیِ هر فریم ۱۰۸۰p فقط وقتی لازم است که واقعاً چیزی
            # روی آن رسم یا پردازش شود. در حالت بیکار، همان فریم خام (فقط
            # خواندنی - هیچ‌یک از مراحل بعدی آن را تغییر نمی‌دهند) ارسال
            # می‌شود.
            _img_profile = self._image_profile
            _needs_overlay = (
                len(self._last_results) > 0
                or len(self._last_person_boxes) > 0
                or len(self._last_fire_detections) > 0
                or len(self._last_plate_detections) > 0
                or _img_profile is not None
            )
            if _needs_overlay:
                display_frame = frame.copy()
                self.face_engine.draw_results(display_frame, self._last_results)
                # کادر آبی‌روشن دور کل بدن هر فرد (فارغ از حالت/چهره) - جدا از
                # کادر سبز/قرمز چهره که بالا رسم شد. همیشه رسم می‌شود (نه فقط
                # وقتی شمارش روشن است) تا کاربر بلافاصله ببیند تشخیص شخص در حال
                # کار است، دقیقاً مثل تشخیص چهره که همیشه فعال است.
                if self._person_detector_available:
                    try:
                        _get_person_detector().draw_boxes(display_frame, self._last_person_boxes)
                    except Exception:
                        pass
                # کادر نارنجی/قرمز (آتش) یا خاکستری (دود) - همیشه رسم می‌شود (نه
                # فقط وقتی رویدادی تازه صادر شده) تا کاربر تا وقتی ناحیه در کادر
                # دوربین باقی است، کادر را ببیند - دقیقاً مثل کادر شخص/چهره.
                if self._fire_detector_available:
                    try:
                        _get_fire_detector().draw_boxes(display_frame, self._last_fire_detections)
                    except Exception:
                        pass
                # باکس پلاک: سبز = تعریف‌شده، نارنجی = تعریف‌نشده + برچسب لاتین
                # (متن فارسی با cv2.putText رسم نمی‌شود؛ متن کامل در گزارش عبور است).
                if self._last_plate_detections:
                    try:
                        for _pbox, _ptext, _pdefined in self._last_plate_detections:
                            _px1, _py1, _px2, _py2 = (int(v) for v in _pbox)
                            _pcolor = (0, 200, 0) if _pdefined else (0, 165, 255)
                            cv2.rectangle(display_frame, (_px1, _py1), (_px2, _py2),
                                          _pcolor, 2)
                            _plabel = f"PLATE {'OK' if _pdefined else '??'}"
                            cv2.putText(display_frame, _plabel, (_px1, max(0, _py1 - 6)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, _pcolor, 2)
                    except Exception:
                        pass

                # تنظیمات تصویر این دوربین (روشنایی/WDR/ضد مه/...) فقط روی فریم
                # نمایشی اعمال می‌شود؛ فریم خام (برای تشخیص) دست‌نخورده می‌ماند.
                # خطا هرگز نباید حلقه‌ی پخش را متوقف کند.
                if _img_profile is not None:
                    try:
                        display_frame = _apply_image_profile(display_frame, _img_profile)
                    except Exception:
                        pass
            else:
                display_frame = frame

            # frame خام (بدون باکس) هم ارسال می‌شود تا برای «ثبت چهره از تصویر زنده» استفاده شود.
            self.frame_ready.emit(display_frame, frame)

        cap.release()
        self._executor.shutdown(wait=False)

    def stop(self):
        self._run_flag = False
        self.wait()

import os
import threading

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

# ---------------------------------------------------------------------------
# تشخیص هوشمند «سطح زمین/کف» (AI Floor/Ground Segmentation)
# ---------------------------------------------------------------------------
# رفع درخواست: «یک حالت جدید هم اضافه کنی که خودش سطح زمین رو تشخیص بده و
# کلش رو محدوده محسوب کنه، ولی بازم قابل ادیت باشه - دقیق‌تر، با مدل هوش
# مصنوعی (Segmentation)». قبلاً دکمه‌ی «🌐 تشخیص خودکار محدوده (کل تصویر)»
# (main.py -> CameraSlotWidget.start_auto_full_frame_region) واقعاً هیچ
# تشخیصی انجام نمی‌داد - چون مدلی برای تشخیص دقیقِ کفِ زمین در دسترس نبود،
# فقط کل کادر تصویر را به‌عنوان محدوده می‌گذاشت (رجوع کنید به توضیح همان
# تابع و تولتیپ auto_region_btn). این ماژول همان مدلِ از قبل غایب را اضافه
# می‌کند و main.py یک دکمه‌ی تازه («🧭 تشخیص هوشمند زمین») برای آن دارد.
#
# مدل استفاده‌شده: SegFormer-B0 (نسخه‌ی fine-tune شده روی دیتاست ADE20K)، از
# کتابخانه‌ی transformers - سبک‌ترین عضو خانواده‌ی SegFormer (حدود ۱۴
# مگابایت وزن)، دقیقاً به همان دلیلی که برای تشخیص شخص کوچک‌ترین عضو
# خانواده‌ی YOLOv8 (yolov8n) انتخاب شده بود (person_detector.py): تعادل
# بین سرعت (باید روی CPU سیستم‌های معمولی/بدون کارت گرافیک هم قابل اجرا
# باشد، چون این پروژه صراحتاً برای چنین سیستم‌هایی هم بهینه شده) و دقت
# کافی برای این کاربرد.
#
# چرا ADE20K و نه COCO (که برای تشخیص شخص استفاده شده): ۸۰ کلاس COCO فقط
# «شیء» مجزا دارند (شخص، صندلی، ماشین و ...) و اصلاً کلاسی برای «کف/زمین»
# ندارند - این نوع سطوح پیوسته را در ادبیات segmentation «stuff» می‌گویند،
# نه «thing»، و COCO (نسخه‌ی معمول/detection) اصلاً stuff را پوشش نمی‌دهد.
# ADE20K دقیقاً برعکس: ۱۵۰ کلاس دارد که هم thing و هم stuff (از جمله چند
# کلاس مرتبط با زمین/کف) را پوشش می‌دهد - رجوع کنید به
# _FLOOR_LIKE_CLASS_IDS پایین‌تر.
#
# نکات مهم درباره‌ی وابستگی اختیاری بودن (دقیقاً همان الگوی
# person_detector.py با ultralytics، یا FaceEngine با dlib):
#   - نصب `transformers` اختیاری است. اگر نصب نباشد، یا بارگذاری/دانلود
#     وزن مدل (فقط یک‌بار، در اولین استفاده‌ی واقعی - نه در استارت برنامه -
#     حدود ۱۴ مگابایت، نیازمند اینترنت همان یک‌بار) به هر دلیلی شکست
#     بخورد، برنامه کرش نمی‌کند - فقط دکمه‌ی «🧭 تشخیص هوشمند زمین» با یک
#     پیام روشن غیرفعال می‌ماند و کاربر می‌تواند به‌جایش از «🌐 تشخیص
#     خودکار محدوده (کل تصویر)» یا رسم دستی استفاده کند (رجوع کنید به
#     main.py: MainWindow._on_ai_floor_detect_finished).
#   - torch از قبل به‌خاطر ultralytics روی سیستم/exe نصب/بسته‌بندی است، پس
#     این حالت وابستگی سنگین *تازه‌ای* (غیر از خودِ transformers، که سبک
#     است) اضافه نمی‌کند.
#   - بارگذاری مدل thread-safe نیست (مثل dlib/ultralytics)؛ چون این ماژول
#     هم یک نمونه‌ی مشترک (singleton) است، یک قفل (RLock) بارگذاری و
#     فراخوانی مدل را سریالایز می‌کند. در عمل تشخیص زمین فقط با کلیک صریح
#     کاربر (نه هر فریم، بر خلاف تشخیص چهره/شخص) اجرا می‌شود، پس رقابت
#     همزمان روی این قفل عملاً نادر است.


def _resolve_model_dir(dirname):
    """دقیقاً همان منطق person_detector._resolve_model_path، ولی برای یک
    *پوشه* (نه یک فایل تکی) - چون خروجی transformers.save_pretrained چند
    فایل (config.json، model.safetensors، preprocessor_config.json و ...)
    داخل یک پوشه است، نه یک وزنِ تکی مثل yolov8n.pt. اگر هیچ‌کدام از
    مسیرهای بسته‌بندی‌شده (برای exe پرتابل - رجوع کنید به build.yml) پیدا
    نشد، None برمی‌گرداند تا از شناسه‌ی HuggingFace Hub (دانلود آنلاین،
    فقط حالت اجرا از سورس) استفاده شود."""
    candidates = []
    try:
        candidates.append(os.path.join(__nuitka_binary_dir__, dirname))  # noqa: F821
    except NameError:
        pass
    candidates.append(os.path.join(os.getcwd(), dirname))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), dirname))
    for path in candidates:
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "config.json")):
            return path
    return None


# شناسه‌ی مدل روی HuggingFace Hub - فقط وقتی پوشه‌ی بسته‌بندی‌شده پیدا نشود
# استفاده می‌شود (رجوع کنید به _resolve_model_dir بالا).
_HF_MODEL_ID = "nvidia/segformer-b0-finetuned-ade-512-512"
_LOCAL_MODEL_DIRNAME = "segformer_floor_model"

# کلاس‌های ADE20K (از مجموع ۱۵۰ کلاس، اندیس ۰..۱۴۹ - رجوع کنید به
# https://github.com/CSAILVision/ADE20K) که «سطح قابل‌ایستادن/زمین» حساب
# می‌شوند - ترکیبی از سطوح داخلی (کف اتاق، فرش) و بیرونی (جاده، پیاده‌رو،
# خاک، چمن، شن، مسیر، باند) چون این برنامه هم برای دوربین‌های داخل ساختمان
# و هم بیرون (حیاط و ...) استفاده می‌شود (رجوع کنید به README پروژه).
_FLOOR_LIKE_CLASS_IDS = frozenset({
    3,   # floor, flooring
    6,   # road, route
    9,   # grass
    11,  # sidewalk, pavement
    13,  # earth, ground
    28,  # rug, carpet, carpeting
    29,  # field
    46,  # sand
    52,  # path
    54,  # runway
})


class FloorDetector:
    """تشخیص چندضلعیِ سطح زمین/کف در یک فریم، با مدل Segmentation بالا.
    مثل PersonDetector (person_detector.py)، یک نمونه‌ی مشترک (singleton)
    است تا مدل فقط یک‌بار در حافظه بارگذاری شود - رجوع کنید به نمونه‌ی
    floor_detector در انتهای همین فایل."""

    def __init__(self):
        self._processor = None
        self._model = None
        self._load_attempted = False
        self._load_error = None
        self._lock = threading.RLock()

    @property
    def load_error(self):
        """پیام خطای بارگذاری مدل (اگر بارگذاری تلاش و ناموفق بوده)، یا
        None در غیر این صورت - main.py از این مقدار برای نمایش دلیل واقعیِ
        غیرفعال‌بودنِ «تشخیص هوشمند زمین» به کاربر استفاده می‌کند."""
        return self._load_error

    @property
    def available(self):
        """True فقط اگر مدل واقعاً با موفقیت بارگذاری شده باشد. اولین
        فراخوانی این property (یا detect_polygon) همان لحظه‌ای است که تلاش
        برای بارگذاری/دانلود مدل انجام می‌شود؛ چون این کار می‌تواند چند
        ثانیه طول بکشد، همیشه از یک ترد پس‌زمینه صدا زده شود (رجوع کنید به
        FloorDetectThread پایین‌تر) تا ترد UI قفل نکند."""
        self._ensure_loaded()
        return self._model is not None

    def _ensure_loaded(self):
        if self._load_attempted:
            return
        with self._lock:
            if self._load_attempted:
                return
            self._load_attempted = True
            try:
                from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation
                local_dir = _resolve_model_dir(_LOCAL_MODEL_DIRNAME)
                source = local_dir or _HF_MODEL_ID
                self._processor = SegformerImageProcessor.from_pretrained(source)
                self._model = SegformerForSemanticSegmentation.from_pretrained(source)
                self._model.eval()
            except Exception as e:
                self._model = None
                self._processor = None
                self._load_error = str(e)
                print(
                    "تشخیص هوشمند زمین (SegFormer) در دسترس نیست - این حالت "
                    "غیرفعال می‌ماند؛ به‌جایش می‌توانید از «تشخیص خودکار محدوده "
                    "(کل تصویر)» یا رسم دستی استفاده کنید. اگر از سورس اجرا "
                    "می‌کنید: pip install transformers و اتصال اینترنت برای "
                    f"دانلود یک‌بارِ وزن مدل لازم است. خطا: {e}"
                )

    def detect_polygon(self, frame_bgr, min_area_ratio=0.02, epsilon_ratio=0.004):
        """چندضلعیِ نرمال‌شده‌ی (0..1) بزرگ‌ترین ناحیه‌ی «زمین/کف»‌مانندِ
        شناسایی‌شده در frame_bgr را برمی‌گرداند - با همان قالب نقاطی که
        main.py/camera_stream.py برای points یک region استفاده می‌کنند
        (لیستی از تاپل‌های (x, y) نرمال‌شده)، تا مستقیم قابل استفاده در
        CameraSlotWidget.start_ai_floor_region باشد.

        اگر مدل در دسترس نباشد یا هیچ ناحیه‌ی به‌اندازه‌ی کافی بزرگی پیدا
        نشود، None برمی‌گرداند (بدون پرتاب خطا) - فراخوان (main.py) این
        حالت را با یک پیام روشن به کاربر مدیریت می‌کند؛ رجوع کنید به
        load_error برای تشخیص علت (مدل بارگذاری نشده در برابر مدل بارگذاری
        شده ولی چیزی پیدا نکرده).

        min_area_ratio: کوچک‌ترین نسبت مساحت (از کل فریم) که یک ناحیه باید
        داشته باشد تا معتبر شمرده شود - رد‌کردن نویز/لکه‌های خیلی کوچک.
        epsilon_ratio: دقتِ ساده‌سازیِ چندضلعی (cv2.approxPolyDP) نسبت به
        محیط کانتور - عددی کوچک‌تر یعنی گوشه‌های بیشتر/دقیق‌تر ولی
        احتمالاً دست‌وپاگیرتر برای ویرایش دستی بعدی با ماوس؛ این مقدار
        تعادل معقولی برای تعداد گوشه‌ی قابل‌مدیریت می‌دهد."""
        if not self.available or frame_bgr is None:
            return None
        try:
            import torch
            h, w = frame_bgr.shape[:2]
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            with self._lock:
                inputs = self._processor(images=rgb, return_tensors="pt")
                with torch.no_grad():
                    outputs = self._model(**inputs)
            logits = outputs.logits  # (1, num_classes, h', w')
            # خروجی SegFormer روی اندازه‌ای کوچک‌تر از ورودی است؛ upsample
            # به اندازه‌ی واقعی فریم لازم است تا ماسک روی همان مختصات
            # تصویر اصلی (برای تبدیل بعدی به points نرمال‌شده) قابل استفاده
            # باشد.
            upsampled = torch.nn.functional.interpolate(
                logits, size=(h, w), mode="bilinear", align_corners=False
            )
            pred = upsampled.argmax(dim=1)[0].cpu().numpy()  # (h, w)، مقادیر ۰..۱۴۹

            mask = np.isin(pred, list(_FLOOR_LIKE_CLASS_IDS)).astype(np.uint8) * 255
            # بستن حفره‌های کوچک داخل زمین (مثلاً پای یک صندلی وسط کف) و
            # حذف نویز/لکه‌های ریز پراکنده - تا کانتور نهایی یک شکل نسبتاً
            # یکپارچه باشد، نه ده‌ها تکه‌ی جدا.
            kernel = np.ones((9, 9), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None
            largest = max(contours, key=cv2.contourArea)
            frame_area = float(h * w)
            if cv2.contourArea(largest) / frame_area < min_area_ratio:
                return None

            perimeter = cv2.arcLength(largest, True)
            approx = cv2.approxPolyDP(largest, epsilon_ratio * perimeter, True)
            if len(approx) < 3:
                # ساده‌سازی شکل را به کمتر از ۳ گوشه رسانده (شکل خیلی
                # نامنظم/نویزی)؛ به‌جایش از خودِ hull محدب (بدون ساده‌سازی
                # اضافه) استفاده می‌شود تا حداقل یک چندضلعی معتبر بماند.
                approx = cv2.convexHull(largest)
                if len(approx) < 3:
                    return None

            points_norm = [
                (max(0.0, min(1.0, float(p[0][0]) / w)), max(0.0, min(1.0, float(p[0][1]) / h)))
                for p in approx
            ]
            return points_norm
        except Exception as e:
            print(f"خطا در تشخیص هوشمند زمین: {e}")
            return None


class FloorDetectThread(QThread):
    """اجرای FloorDetector.detect_polygon در یک ترد جدا - دقیقاً همان دلیل
    NetworkScanThread در scanner.py: این پردازش (به‌خصوص اولین بار که مدل
    هنوز بارگذاری/دانلود نشده) می‌تواند چند ثانیه طول بکشد؛ اگر مستقیم روی
    ترد UI اجرا شود، کل برنامه (از جمله پخش زنده‌ی بقیه‌ی دوربین‌های باز)
    برای همان مدت فریز می‌شود."""

    # (points_norm یا None وقتی چیزی پیدا نشد/مدل در دسترس نبود, پیام برای
    # نمایش به کاربر - همیشه یک رشته‌ی خالی وقتی موفق بوده)
    finished_signal = pyqtSignal(object, str)

    def __init__(self, frame_bgr, parent=None):
        super().__init__(parent)
        self._frame = frame_bgr

    def run(self):
        points = floor_detector.detect_polygon(self._frame)
        if points is not None:
            self.finished_signal.emit(points, "")
            return
        err = floor_detector.load_error
        if err:
            message = (
                "مدل تشخیص هوشمند زمین در دسترس نیست.\n\n"
                f"جزئیات فنی: {err}"
            )
        else:
            message = (
                "مدل هیچ ناحیه‌ی زمین/کفِ به‌اندازه‌ی کافی بزرگی در تصویر فعلی "
                "این دوربین پیدا نکرد."
            )
        self.finished_signal.emit(None, message)


# نمونه‌ی مشترک (singleton) بین همه‌ی دوربین‌ها - دقیقاً مثل person_detector
# در person_detector.py؛ چون بارگذاری مدل هم کند است (چند ثانیه، فقط
# یک‌بار) و هم حافظه می‌گیرد، لازم نیست هر دوربین نسخه‌ی جدای خودش را
# نگه دارد.
floor_detector = FloorDetector()

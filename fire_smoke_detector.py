import os
import threading

import cv2

# ---------------------------------------------------------------------------
# تشخیص تصویری «آتش/دود» (Fire & Smoke Detection) روی خودِ تصویر دوربین‌ها
# ---------------------------------------------------------------------------
# رفع درخواست: «سیستم تشخیص دود و اعلام حریق» به برنامه اضافه شود.
#
# این ماژول دقیقاً همان الگوی person_detector.py (YOLOv8n برای تشخیص شخص) را
# دنبال می‌کند: یک مدل YOLO سبک روی هر چند فریم (نه هر فریم) در ترد پس‌زمینه‌ی
# تشخیص هر دوربین (camera_stream.py) اجرا می‌شود، بدون اینکه حلقه‌ی اصلی خواندن
# فریم/نمایش زنده هرگز منتظرش بماند.
#
# تفاوت مهم با person_detector.py: کلاس «شخص» جزو ۸۰ کلاس دیتاست COCO است که
# YOLOv8n به‌صورت پیش‌فرض با آن آموزش دیده، پس همان وزن پیش‌فرض (yolov8n.pt)
# کافی بود. «آتش» و «دود» جزو آن ۸۰ کلاس نیستند؛ بنابراین این ماژول به یک وزن
# مدل جداگانه (پیش‌فرض: fire_smoke.pt) نیاز دارد که از قبل روی یک دیتاست
# fire/smoke (مثلاً یکی از مدل‌های متن‌باز YOLOv8 آموزش‌دیده روی Roboflow
# Universe/Kaggle برای همین منظور) fine-tune شده باشد - نه یک مدل عمومی COCO.
#
# دقیقاً مثل yolov8n.pt/floor segformer:
#   - این قابلیت کاملاً اختیاری است. اگر کتابخانه‌ی ultralytics نصب نباشد، یا
#     فایل وزن (fire_smoke.pt) پیدا نشود/دانلود نشود، برنامه کرش نمی‌کند - فقط
#     همین یک قابلیت (کادر/برچسب آتش-دود و رویدادهای مربوطه) غیرفعال می‌ماند و
#     بقیه‌ی برنامه (پخش زنده، تشخیص چهره، تشخیص شخص و ...) طبق معمول کار
#     می‌کند.
#   - برای بسته‌بندی در exe پرتابل (GitHub Actions/Nuitka)، باید مرحله‌ای مشابه
#     دانلود yolov8n.pt در .github/workflows/build.yml برای fire_smoke.pt هم
#     اضافه شود (رجوع کنید به کامنت‌های همان فایل) - آدرس دقیق وزن مدل باید
#     توسط کاربر پروژه انتخاب/جایگزین شود چون هیچ آدرس ثابت و همیشه-معتبری
#     برای یک مدل شخص‌ثالث نمی‌توان اینجا فرض کرد.
#   - اگر پروژه را از سورس اجرا می‌کنید (``python main.py``) و می‌خواهید این
#     قابلیت را امتحان کنید، کافی است یک فایل وزن YOLOv8 آموزش‌دیده روی
#     fire/smoke (دو کلاس ``fire``/``smoke``، یا حتی فقط یکی از آن‌ها) را با
#     نام ``fire_smoke.pt`` کنار ``main.py`` قرار دهید.


def _resolve_model_path(filename):
    """دقیقاً همان منطق person_detector._resolve_model_path - رجوع کنید به
    آن فایل برای توضیح کامل هر مرحله (exe پرتابل Nuitka / اجرا از سورس)."""
    candidates = []
    try:
        candidates.append(os.path.join(__nuitka_binary_dir__, filename))  # noqa: F821
    except NameError:
        pass
    candidates.append(os.path.join(os.getcwd(), filename))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return filename


# نگاشت نام کلاس خام مدل (بسته به دیتاست آموزشی می‌تواند En/Fa، مفرد/جمع یا
# کمی متفاوت باشد) به یکی از دو کلید داخلی استاندارد این ماژول. هر کلاسی که با
# هیچ‌کدام از این کلیدواژه‌ها مطابقت نداشت، نادیده گرفته می‌شود (نه کرش).
_FIRE_KEYWORDS = ("fire", "flame", "آتش", "شعله")
_SMOKE_KEYWORDS = ("smoke", "دود")

_LABELS_FA = {"fire": "🔥 آتش", "smoke": "💨 دود"}
_BOX_COLORS_BGR = {
    "fire": (0, 80, 255),     # نارنجی/قرمز پررنگ
    "smoke": (180, 180, 180),  # خاکستری
}


def _classify_name(raw_name: str):
    name = (raw_name or "").strip().lower()
    if any(k in name for k in _FIRE_KEYWORDS):
        return "fire"
    if any(k in name for k in _SMOKE_KEYWORDS):
        return "smoke"
    return None


class FireSmokeDetector:
    """تشخیص‌دهنده‌ی آتش/دود روی فریم خام دوربین - رجوع کنید به توضیح بالای
    فایل. نمونه‌ی مشترک (singleton) بین همه‌ی دوربین‌ها، دقیقاً مثل
    person_detector، چون بارگذاری مدل هم کند است و هم حافظه می‌گیرد."""

    _MODEL_FILENAME = "fire_smoke.pt"

    def __init__(self, conf_threshold=0.45, imgsz=480):
        self.conf_threshold = conf_threshold
        self.imgsz = imgsz
        self._model = None
        self._load_attempted = False
        self._load_error = None
        self._lock = threading.RLock()

    @property
    def load_error(self):
        return self._load_error

    @property
    def available(self):
        self._ensure_loaded()
        return self._model is not None

    def _ensure_loaded(self):
        if self._load_attempted:
            return
        with self._lock:
            if self._load_attempted:
                return
            self._load_attempted = True
            model_path = _resolve_model_path(self._MODEL_FILENAME)
            if not os.path.isfile(model_path):
                # رفع درخواست: بر خلاف person_detector (که وزن پیش‌فرض
                # ultralytics را در صورت نبود فایل محلی خودکار دانلود می‌کند
                # چون yolov8n.pt یک وزن رسمی/عمومی ultralytics است)، اینجا از
                # تلاش برای دانلود خودکار خودداری می‌شود - چون fire_smoke.pt
                # یک وزن شخص‌ثالث است و آدرس ثابتی برایش وجود ندارد؛ تلاش
                # نابه‌جا برای دانلود فقط منجر به خطای گمراه‌کننده می‌شود. در
                # عوض یک پیام روشن چاپ می‌شود که کاربر بداند دقیقاً باید چه
                # فایلی را کجا قرار دهد.
                self._model = None
                self._load_error = (
                    f"فایل وزن مدل «{self._MODEL_FILENAME}» پیدا نشد. برای فعال شدن "
                    "تشخیص تصویری آتش/دود، یک فایل وزن YOLOv8 آموزش‌دیده روی "
                    "دیتاست آتش/دود را با همین نام کنار main.py قرار دهید (یا "
                    "مسیر دانلود آن را در .github/workflows/build.yml اضافه کنید تا "
                    "داخل exe پرتابل هم بسته‌بندی شود)."
                )
                print(f"تشخیص تصویری آتش/دود در دسترس نیست: {self._load_error}")
                return
            try:
                from ultralytics import YOLO
                self._model = YOLO(model_path)
            except Exception as e:
                self._model = None
                self._load_error = str(e)
                print(f"خطا در بارگذاری مدل تشخیص آتش/دود: {e}")

    def detect(self, frame):
        """خروجی: لیستی از (box, kind, conf) که box=(top,right,bottom,left)
        (همان قالب FaceEngine/PersonDetector)، kind یکی از 'fire'/'smoke' و
        conf عددی بین ۰ و ۱ است. اگر مدل در دسترس نباشد، لیست خالی (بدون
        خطا) برمی‌گرداند."""
        if not self.available:
            return []
        with self._lock:
            try:
                results = self._model.predict(
                    frame, imgsz=self.imgsz, conf=self.conf_threshold, verbose=False,
                )
            except Exception as e:
                print(f"خطا در تشخیص آتش/دود: {e}")
                return []

        detections = []
        for r in results:
            if r.boxes is None:
                continue
            names = r.names or {}
            for box_data in r.boxes:
                cls_id = int(box_data.cls[0]) if box_data.cls is not None else -1
                kind = _classify_name(names.get(cls_id, ""))
                if kind is None:
                    continue  # کلاس ناشناخته/نامرتبط این وزن؛ نادیده گرفته می‌شود
                conf = float(box_data.conf[0]) if box_data.conf is not None else 0.0
                left, top, right, bottom = (int(v) for v in box_data.xyxy[0].tolist())
                detections.append(((top, right, bottom, left), kind, conf))
        return detections

    def draw_boxes(self, frame, detections):
        """کادر رنگی متمایز دور هر ناحیه‌ی آتش (نارنجی/قرمز) یا دود
        (خاکستری) - جدا از کادر سبز/قرمز چهره و کادر آبی «شخص»."""
        for box, kind, conf in detections:
            top, right, bottom, left = box
            color = _BOX_COLORS_BGR.get(kind, (0, 0, 255))
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            label = f"{_LABELS_FA.get(kind, kind)} {conf * 100:.0f}%"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 0.55, 1)
            label_top = max(0, top - th - 8)
            cv2.rectangle(frame, (left, label_top), (left + tw + 8, label_top + th + 6), color, cv2.FILLED)
            cv2.putText(frame, label, (left + 4, label_top + th + 1), cv2.FONT_HERSHEY_DUPLEX, 0.55, (20, 20, 20), 1)
        return frame


# نمونه‌ی سراسری مشترک - همان الگوی person_detector.py.
fire_smoke_detector = FireSmokeDetector()

import os
import sys
import threading

import cv2

# ---------------------------------------------------------------------------
# تشخیص «شخص» (Person Detection) — مستقل از حالت بدن و بدون نیاز به دیدن چهره
# ---------------------------------------------------------------------------
# رفع درخواست: «شخص رو شناسایی نمی‌کنه؛ همه‌ی حالت‌های اشخاص (ایستاده، نشسته،
# نیم‌خیز و ...) را براش تعریف کن».
#
# چرا قبلاً «۰ نفر» نمایش داده می‌شد: شمارش قبلی (camera_stream.py) کاملاً
# روی FaceEngine.recognize (تشخیص *چهره*، نه بدن) سوار بود. تشخیص چهره فقط
# وقتی جواب می‌دهد که صورت فرد رو به دوربین، نسبتاً واضح و با اندازه‌ی کافی
# باشد. در تصویری که شما فرستادید، فرد پشتش به دوربین است و مشغول قفسه -
# یعنی هیچ چهره‌ای برای تشخیص وجود ندارد، پس عدد همیشه صفر می‌ماند، حتی
# با اینکه کل بدنش کاملاً داخل قاب و حتی داخل کادر سبز (ناحیه‌ی تعریف‌شده)
# است.
#
# راه‌حل درست، تعریف دستیِ قانون به‌ازای «هر حالت بدن» نیست - چون تعداد
# واقعی حالت‌ها عملاً بی‌نهایت است (زاویه‌ی کمر، چرخش سر، خم‌شدن روی میز،
# نشستن پشت مانیتور، پشت به دوربین، نیمه‌پنهان پشت قفسه/میز و ...؛ نوشتن
# قانون جداگانه برای تک‌تک آن‌ها هم عملی نیست و هم هیچ‌وقت کامل نمی‌شود).
# راه‌حل درست، استفاده از یک تشخیص‌دهنده‌ی «شیء - کلاس شخص» است که از قبل
# روی ده‌ها هزار عکس واقعی از افراد در تمام حالت‌های ممکن (ایستاده، نشسته،
# خم‌شده، نیم‌خیز، پشت به دوربین، نیمه‌پیدا پشت اثاثیه و ...) آموزش دیده؛
# یعنی خودِ مدل این تنوع را از داده یاد گرفته، نه اینکه ما قانون‌به‌قانون
# کدنویسی کرده باشیم. این با تشخیص چهره‌ی قبلی کاملاً فرق دارد: اینجا کل
# جعبه‌ی بدن فرد تشخیص داده می‌شود، نه فقط صورتش - پس دیدن چهره اصلاً لازم
# نیست.
#
# مدل استفاده‌شده: YOLOv8n (کوچک‌ترین/سریع‌ترین عضو خانواده‌ی YOLOv8 از
# کتابخانه‌ی ultralytics)، با فیلتر روی فقط کلاس ۰ (person) از ۸۰ کلاسِ
# دیتاست COCO - که دقیقاً همان دیتاستی است که این تنوع حالت‌های بدن را در
# خودش دارد.
#
# نکات مهم درباره‌ی وابستگی اختیاری بودن:
#   - نصب کتابخانه (`pip install ultralytics`) اختیاری است. اگر نصب نباشد،
#     یا بارگذاری/دانلود وزن مدل (فقط یک‌بار، در اولین اجرا، حدود ۶ مگابایت،
#     نیازمند اینترنت همان یک‌بار) به هر دلیلی شکست بخورد، برنامه کرش
#     نمی‌کند - فقط قابلیت «تشخیص شخص» غیرفعال می‌ماند و شمارش به روش قبلی
#     (بر پایه‌ی تعداد چهره‌های شناسایی‌شده) به‌عنوان جایگزین ادامه می‌یابد
#     (رجوع کنید به camera_stream.py: self._person_detector_available).
#   - این کتابخانه (مثل dlib در FaceEngine) برای فراخوانی هم‌زمان از چند
#     ترد مختلف thread-safe نیست؛ چون این ماژول یک نمونه‌ی مشترک (singleton)
#     بین همه‌ی دوربین‌هاست (تا مدل فقط یک‌بار در حافظه بارگذاری شود، نه یک
#     نسخه‌ی جدا به‌ازای هر دوربین)، یک قفل (RLock) تمام فراخوانی‌های
#     model.predict را سریالایز می‌کند - دقیقاً همان الگویی که FaceEngine
#     برای قفل کردن dlib استفاده می‌کند.
#
# رفع درخواست «بدون نصب چیزی روی سیستم من، فقط از طریق GitHub به exe
# پرتابل تبدیل شود»: خودِ وابستگی (ultralytics/torch) و هم فایل وزن مدل
# (yolov8n.pt) در همان workflow گیت‌هاب (.github/workflows/build.yml) نصب/
# دانلود و مستقیماً *داخل* exe پرتابل بسته‌بندی می‌شوند (با فلگ Nuitka
# --include-data-files)؛ یعنی روی سیستم کاربر نهایی هیچ نصب یا دانلودی
# (حتی همان یک‌بار دانلود وزن مدل) لازم نیست - کل کار در سرور گیت‌هاب انجام
# می‌شود. تابع _resolve_model_path پایین‌تر مسیر همین فایل بسته‌بندی‌شده را
# چه در حالت اجرا از سورس و چه در حالت exe کامپایل‌شده (--onefile) پیدا
# می‌کند.


def _resolve_model_path(filename):
    """آدرس فایل وزن مدل را به‌ترتیب اولویت زیر پیدا می‌کند:
      ۱. کنار محل استخراج‌شده‌ی exe پرتابل - وقتی برنامه با Nuitka
         (--onefile) کامپایل شده و این فایل با
         --include-data-files=yolov8n.pt=yolov8n.pt داخلش بسته‌بندی شده
         باشد (رجوع کنید به build.yml). در این حالت، Nuitka متغیر سراسری
         __nuitka_binary_dir__ را خودکار به هر ماژول برنامه‌ی کامپایل‌شده
         تزریق می‌کند که پوشه‌ی محل استخراج فایل‌های همراه را نشان می‌دهد.
      ۲. پوشه‌ی جاری (cwd) - وقتی از سورس اجرا شده (python main.py) و
         مرحله‌ی دانلود در build.yml همین فایل را کنار main.py گذاشته یا
         کاربر دستی کنارش کپی کرده باشد.
      ۳. پوشه‌ی همین فایل پایتون (person_detector.py).
      ۴. فقط نام فایل - در این حالت (فقط وقتی هیچ‌کدام از موارد بالا فایل
         را پیدا نکردند) خودِ ultralytics تلاش می‌کند وزن را دانلود کند که
         نیازمند اینترنت است؛ این یعنی یا حالت توسعه/اجرا از سورس بدون
         دانلود دستی، یا build.yml به هر دلیلی وزن را کنار نگذاشته."""
    candidates = []
    try:
        candidates.append(os.path.join(__nuitka_binary_dir__, filename))  # noqa: F821
    except NameError:
        pass
    # exe ساخته‌شده با PyInstaller: در حالت onedirِ نسخه‌ی ۶ به بعد، همه‌ی
    # فایل‌های باندل (از جمله --add-dataها) داخل زیرپوشه‌ی _internal کنار
    # فایل اجرایی هستند و sys._MEIPASS همان‌جا را نشان می‌دهد؛ پس اول
    # همان‌جا دنبال مدل می‌گردیم.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(meipass):
        candidates.append(os.path.join(meipass, filename))
    candidates.append(os.path.join(os.getcwd(), filename))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return filename


def _box_iou(a, b):
    """IoU برای باکس‌های قالب (top, right, bottom, left) که detect برمی‌گرداند."""
    top = max(a[0], b[0])
    right = min(a[1], b[1])
    bottom = min(a[2], b[2])
    left = max(a[3], b[3])
    iw = max(0, right - left)
    ih = max(0, bottom - top)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(0, a[1] - a[3]) * max(0, a[2] - a[0])
    bb = max(0, b[1] - b[3]) * max(0, b[2] - b[0])
    union = aa + bb - inter
    return (inter / union) if union > 0 else 0.0


def _plausible_person_box(box, face_boxes):
    """فیلتر هندسی «باکس عریض»: باکس آدم واقعی معمولاً از عرضش کشیده‌تر
    است. وقتی خود مدل یک موتور/خودروی پارک‌شده را مستقیماً «شخص» (کلاس ۰)
    تشخیص می‌دهد، هیچ باکس وسیله‌ی نقلیه‌ای برای فیلتر IoU وجود ندارد و
    این تنها راه حذف آن است. باکسی که واضحاً عریض‌تر از قدش باشد فقط وقتی
    نگه داشته می‌شود که مرکز یک چهره داخلش باشد (مثل فرد نشسته‌ی رو به
    دوربین)؛ در غیر این صورت (مثل موتور از بغل که چهره‌ای ندارد) حذف
    می‌شود. قالب باکس: (top, right, bottom, left)."""
    top, right, bottom, left = box
    w = max(0, right - left)
    h = max(0, bottom - top)
    if w <= 1.25 * h:
        return True
    for fb in face_boxes:
        cx = (fb[3] + fb[1]) / 2.0
        cy = (fb[0] + fb[2]) / 2.0
        if left <= cx <= right and top <= cy <= bottom:
            return True
    return False


class PersonDetector:
    _MODEL_FILENAME = "yolov8n.pt"

    def __init__(self, conf_threshold=0.30, imgsz=480):
        self.conf_threshold = conf_threshold
        self.imgsz = imgsz
        self._model = None
        self._load_attempted = False
        self._load_error = None
        self._lock = threading.RLock()

    @property
    def load_error(self):
        """پیام خطای بارگذاری مدل (اگر بارگذاری تلاش و ناموفق بوده)، یا
        None اگر مدل با موفقیت بارگذاری شده یا هنوز اصلاً تلاشی صورت
        نگرفته. camera_stream.py از این مقدار برای نمایش وضعیت واقعی
        (چرا هشدار محدوده کار نمی‌کند) روی UI استفاده می‌کند - قبلاً این
        پیام فقط با print() به کنسول می‌رفت که در خروجی exe نهایی
        (windows-console-mode=disable) اصلاً دیده نمی‌شد."""
        return self._load_error

    @property
    def available(self):
        """True فقط اگر مدل واقعاً با موفقیت بارگذاری شده باشد. اولین
        فراخوانی این property (یا detect) همان لحظه‌ای است که تلاش برای
        بارگذاری/دانلود مدل انجام می‌شود - بهتر است اولین فراخوانی از یک
        ترد پس‌زمینه (نه ترد اصلی UI/خواندن فریم) انجام شود تا برنامه در
        همان لحظه قفل نکند؛ camera_stream.py این کار را از ترد تشخیص
        (executor پس‌زمینه) انجام می‌دهد."""
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
                from ultralytics import YOLO
                model_path = _resolve_model_path(self._MODEL_FILENAME)
                self._model = YOLO(model_path)
            except Exception as e:
                self._model = None
                self._load_error = str(e)
                print(
                    "تشخیص شخص (YOLOv8) در دسترس نیست - شمارش صرفاً بر پایه‌ی "
                    "چهره ادامه می‌یابد. اگر این خروجی exe رسمی (ساخته‌شده با "
                    "GitHub Actions) است، این پیام یعنی چیزی در build.yml درست "
                    "بسته‌بندی نشده؛ اگر از سورس اجرا می‌کنید: "
                    f"pip install ultralytics و اتصال اینترنت برای دانلود یک‌بارِ "
                    f"وزن مدل لازم است. خطا: {e}"
                )

    def detect(self, frame, face_boxes=None):
        """لیستی از باکس‌های افراد در frame را برمی‌گرداند - با همان قالب
        (top, right, bottom, left) که در بقیه‌ی پروژه (FaceEngine) استفاده
        می‌شود، تا camera_stream.py بتواند این نتایج را کنار نتایج چهره،
        بدون تغییر قالب، رسم و شمارش کند. اگر مدل در دسترس نباشد، لیست
        خالی برمی‌گرداند (بدون خطا).

        face_boxes: لیستی از باکس‌های چهره‌ی آخرین دور تشخیص چهره (همان
        قالب)، یا None اگر تشخیص چهره اصلاً اجرا نشده است. برای حذف خطای
        «موتور/خودرو پارک‌شده = شخص» (وقتی مدل خودش وسیله را کلاس ۰
        می‌دهد) لازم است؛ اگر None باشد رفتار قبلی حفظ می‌شود."""
        if not self.available:
            return []
        with self._lock:
            try:
                results = self._model.predict(
                    frame,
                    imgsz=self.imgsz,
                    conf=self.conf_threshold,
                    # شخص + همه‌ی کلاس‌های وسیله‌ی نقلیه‌ی COCO (دوچرخه=۱،
                    # خودرو=۲، موتورسیکلت=۳، اتوبوس=۵، کامیون=۷): مدل گاهی
                    # وسیله‌ی پارک‌شده را «شخص» تشخیص می‌دهد؛ باکس‌های
                    # وسیله‌نقلیه فقط برای حذف این خطا گرفته می‌شوند و در
                    # خروجی نیستند.
                    classes=[0, 1, 2, 3, 5, 7],
                    verbose=False,
                )
            except Exception as e:
                print(f"خطا در تشخیص شخص: {e}")
                return []

        persons = []
        vehicles = []
        for r in results:
            if r.boxes is None:
                continue
            try:
                clses = r.boxes.cls.tolist()
            except Exception:
                clses = [0] * len(r.boxes)
            for b, c in zip(r.boxes.xyxy.tolist(), clses):
                left, top, right, bottom = (int(v) for v in b)
                box = (top, right, bottom, left)
                if int(c) == 0:
                    persons.append(box)
                else:
                    vehicles.append(box)
        # باکس «شخص»‌ی که هم‌پوشانی زیاد با وسیله‌ی نقلیه دارد، خطای مدل
        # است (مثل موتور پارک‌شده) و حذف می‌شود. عابر کنار موتور چون
        # هم‌پوشانی کمی دارد، نگه داشته می‌شود.
        boxes = [p for p in persons
                 if not any(_box_iou(p, v) > 0.45 for v in vehicles)]
        # وقتی خود مدل وسیله را مستقیماً «شخص» تشخیص داده (باکس وسیله‌ای
        # برای فیلتر بالا وجود ندارد)، باکس‌های واضحاً عریض فقط با مدرک
        # چهره نگه داشته می‌شوند.
        if face_boxes is not None:
            boxes = [b for b in boxes
                     if _plausible_person_box(b, face_boxes)]
        return boxes

    def draw_boxes(self, frame, boxes):
        """کادر آبی‌روشن دور *کل بدن* هر فرد شناسایی‌شده (فارغ از حالت بدن یا
        اینکه چهره‌اش دیده می‌شود یا نه) - جدا و قابل‌تشخیص از کادر سبز/قرمز
        چهره که FaceEngine.draw_results رسم می‌کند."""
        for (top, right, bottom, left) in boxes:
            cv2.rectangle(frame, (left, top), (right, bottom), (255, 200, 0), 2)
            label = "شخص"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 0.5, 1)
            label_top = max(0, top - th - 8)
            cv2.rectangle(frame, (left, label_top), (left + tw + 8, label_top + th + 6), (255, 200, 0), cv2.FILLED)
            cv2.putText(frame, label, (left + 4, label_top + th + 1), cv2.FONT_HERSHEY_DUPLEX, 0.5, (30, 30, 30), 1)
        return frame


# نمونه‌ی مشترک (singleton) بین همه‌ی دوربین‌ها/تردها - چون بارگذاری مدل هم
# کند است (چند ثانیه، فقط یک‌بار) و هم حافظه می‌گیرد؛ لازم نیست هر دوربین
# نسخه‌ی جدای خودش را در حافظه نگه دارد. قفل داخل کلاس فراخوانی هم‌زمان از
# چند ترد دوربین مختلف را ایمن می‌کند.
person_detector = PersonDetector()

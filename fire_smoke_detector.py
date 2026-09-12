# -*- coding: utf-8 -*-
"""تشخیص تصویری آتش/دود روی فریم‌های دوربین.

دو روش پشتیبانی می‌شود:

۱) مدل YOLOv8n اختصاصی (ultralytics) - اگر فایل وزن (پیش‌فرض
   fire_smoke.pt) در کنار برنامه موجود باشد و ultralytics نصب باشد، دقیق‌تر
   است و هم آتش هم دود را تشخیص می‌دهد.

۲) رفع درخواست «تشخیص حریق کار نمی‌کند، بشه از طریق خودِ دوربین هم
   تشخیص بده»: چون به‌صورت پیش‌فرض هیچ فایل وزنِ fire_smoke.pt در این
   پروژه وجود ندارد (باید جدا تهیه/آموزش داده شود)، حالت (۱) همیشه غیرفعال
   می‌ماند و در نتیجه از دید کاربر «تشخیص حریق اصلاً کار نمی‌کند». برای
   این‌که تشخیص آتش از روی خودِ تصویر دوربین، بدون نیاز به هیچ فایل وزنِ
   خارجی، از همین الان کار کند، یک تشخیص‌دهنده‌ی کلاسیک بر پایه‌ی رنگ+نور
   (HSV) اضافه شده که هر بار مدل YOLO در دسترس نبود به‌طور خودکار استفاده
   می‌شود: پیکسل‌های نارنجی/قرمز/زرد پرنورِ فریم را پیدا و اگر ناحیه‌ای
   به‌اندازه‌ی کافی بزرگ از این رنگ‌ها پیدا شود، آن را به‌عنوان «fire»
   گزارش می‌کند. این روش دقت یک مدل آموزش‌دیده‌ی واقعی را ندارد (ممکن است
   با نور نارنجی/لامپ‌های زرد هم واکنش نشان دهد) ولی بدون هیچ فایل/نصب
   اضافه‌ای بلافاصله کار می‌کند؛ هر وقت یک وزن fire_smoke.pt واقعی در کنار
   برنامه گذاشته شود، برنامه خودکار به همان مدل دقیق‌تر سوییچ می‌کند.

نام کلاس‌های مدل مورد انتظار (برای حالت ۱): index 0 = 'fire',
index 1 = 'smoke' (قابل تنظیم با پارامتر ``class_names`` هنگام ساخت شیء
اگر وزن دیگری با نگاشت متفاوت استفاده شود).
"""

import os

DEFAULT_WEIGHTS_PATH = "fire_smoke.pt"
DEFAULT_CLASS_NAMES = {0: "fire", 1: "smoke"}
BOX_COLORS = {"fire": (0, 0, 255), "smoke": (128, 128, 128)}  # BGR

# --- تنظیمات تشخیص کلاسیکِ رنگ‌محور (فقط وقتی مدل YOLO در دسترس نباشد) ---
# محدوده‌ی رنگِ شعله در فضای HSV (OpenCV: H در 0..179). دو بازه چون قرمز
# دو سر طیف Hue را می‌پوشاند.
_FIRE_HSV_RANGES = [
    # (H_min, H_max, S_min, V_min)
    (0, 35, 80, 180),     # قرمز تا نارنجی/زرد پرنور
]
# حداقل درصد پیکسل‌های «رنگ آتش» نسبت به کل فریم تا اصلاً یک ناحیه بررسی شود
_MIN_FIRE_PIXEL_RATIO = 0.0015
# حداقل مساحت یک ناحیه (به پیکسل، پس از resize به _WORK_SIZE) تا به‌عنوان
# باکس تشخیص گزارش شود - نویزهای کوچک (مثلاً یک پیکسل قرمز تنها) نادیده گرفته می‌شوند
_MIN_CONTOUR_AREA = 180
_WORK_SIZE = (320, 240)  # برای سرعت، تحلیل رنگ روی یک نسخه‌ی کوچک‌شده انجام می‌شود
_MAX_CLASSICAL_BOXES = 5


class FireSmokeDetector:
    def __init__(self, weights_path: str = DEFAULT_WEIGHTS_PATH,
                 class_names: dict | None = None, conf_threshold: float = 0.45,
                 classical_fallback: bool = True):
        self.weights_path = weights_path
        self.class_names = class_names or DEFAULT_CLASS_NAMES
        self.conf_threshold = conf_threshold
        self.available = False
        self.model = None
        self._load_error = None
        # آیا در صورت نبودِ مدل YOLO، از تشخیص کلاسیک رنگ‌محور استفاده شود؟
        self.classical_fallback_enabled = classical_fallback
        self._load_model()

    def _load_model(self):
        if not os.path.exists(self.weights_path):
            self._load_error = f"فایل وزن '{self.weights_path}' پیدا نشد."
            if self.classical_fallback_enabled:
                print(f"FireSmokeDetector: {self._load_error} "
                      f"(به‌جای مدل YOLO، از تشخیص کلاسیکِ رنگ‌محورِ آتش روی تصویر دوربین استفاده می‌شود)")
            else:
                print(f"FireSmokeDetector: {self._load_error} (تشخیص آتش/دود غیرفعال است)")
            return
        try:
            from ultralytics import YOLO  # وارد کردن دیرهنگام: بدون ultralytics هم برنامه بالا بیاید
            self.model = YOLO(self.weights_path)
            self.available = True
        except Exception as e:
            self._load_error = str(e)
            self.model = None
            self.available = False
            if self.classical_fallback_enabled:
                print(f"FireSmokeDetector: بارگذاری مدل YOLO ناموفق بود ({e}) - "
                      f"به‌جای آن از تشخیص کلاسیکِ رنگ‌محورِ آتش استفاده می‌شود")
            else:
                print(f"FireSmokeDetector: بارگذاری مدل ناموفق بود ({e}) - تشخیص آتش/دود غیرفعال است")

    @property
    def using_ml_model(self) -> bool:
        """آیا هم‌اکنون از مدل واقعیِ YOLO استفاده می‌شود (برخلاف تشخیصِ
        کلاسیکِ رنگ‌محورِ جایگزین)؟"""
        return self.available

    def detect(self, frame):
        """روی یک فریم BGR (numpy array) اجرا می‌شود و لیستی از
        (label, confidence, (x1, y1, x2, y2)) برمی‌گرداند. اگر مدل YOLO در
        دسترس باشد از آن استفاده می‌شود؛ در غیر این صورت (و اگر
        classical_fallback_enabled باشد) از تشخیص کلاسیکِ رنگ‌محور آتش
        روی همان فریم استفاده می‌شود. هرگز استثنا پرتاب نمی‌کند."""
        if frame is None:
            return []
        if self.available:
            return self._detect_ml(frame)
        if self.classical_fallback_enabled:
            return self._detect_classical(frame)
        return []

    def _detect_ml(self, frame):
        try:
            results = self.model.predict(frame, conf=self.conf_threshold, verbose=False)
        except Exception as e:
            print(f"FireSmokeDetector: خطا در حین تشخیص ({e})")
            return []

        detections = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                cls_id = int(box.cls[0])
                label = self.class_names.get(cls_id, str(cls_id))
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                detections.append((label, confidence, (x1, y1, x2, y2)))
        return detections

    def _detect_classical(self, frame):
        """تشخیص آتش بر پایه‌ی رنگ (بدون هیچ مدل/فایل وزنی): پیکسل‌های
        نارنجی/قرمز/زردِ پرنور و پراشباع فریم پیدا می‌شوند (محدوده‌ی HSV
        بالا)، سپس نواحیِ به‌هم‌پیوسته‌ی به‌اندازه‌ی کافی بزرگ از این
        پیکسل‌ها به‌عنوان «fire» با یک اطمینانِ تخمینی (بر اساس نسبت
        پیکسل‌های رنگِ آتش داخل همان کادر) گزارش می‌شوند. برای سرعت، تحلیل
        روی یک نسخه‌ی کوچک‌شده (_WORK_SIZE) از فریم انجام و در پایان
        مختصات به اندازه‌ی فریم اصلی برگردانده می‌شود."""
        try:
            import cv2
            import numpy as np
        except Exception:
            return []

        try:
            h0, w0 = frame.shape[:2]
            small = cv2.resize(frame, _WORK_SIZE, interpolation=cv2.INTER_LINEAR)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

            mask = None
            for h_min, h_max, s_min, v_min in _FIRE_HSV_RANGES:
                lower = np.array([h_min, s_min, v_min], dtype="uint8")
                upper = np.array([h_max, 255, 255], dtype="uint8")
                part = cv2.inRange(hsv, lower, upper)
                mask = part if mask is None else cv2.bitwise_or(mask, part)

            total_pixels = _WORK_SIZE[0] * _WORK_SIZE[1]
            fire_pixel_ratio = float(cv2.countNonZero(mask)) / total_pixels
            if fire_pixel_ratio < _MIN_FIRE_PIXEL_RATIO:
                return []

            # حذف نویزهای ریز/پرکردن حفره‌های کوچک داخل ناحیه‌ی شعله
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return []

            scale_x = w0 / _WORK_SIZE[0]
            scale_y = h0 / _WORK_SIZE[1]
            contours = sorted(contours, key=cv2.contourArea, reverse=True)[:_MAX_CLASSICAL_BOXES]

            detections = []
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < _MIN_CONTOUR_AREA:
                    continue
                x, y, w, h = cv2.boundingRect(cnt)
                # اطمینانِ تخمینی: هرچه ناحیه‌ی رنگِ آتش نسبت به کل فریم
                # بزرگ‌تر باشد، احتمال یک آتشِ واقعی (نه یک لامپ/جسم کوچک
                # نارنجی) بیشتر در نظر گرفته می‌شود.
                area_ratio = area / total_pixels
                confidence = min(0.95, 0.35 + area_ratio * 6.0)
                x1 = int(x * scale_x)
                y1 = int(y * scale_y)
                x2 = int((x + w) * scale_x)
                y2 = int((y + h) * scale_y)
                detections.append(("fire", confidence, (x1, y1, x2, y2)))
            return detections
        except Exception as e:
            print(f"FireSmokeDetector: خطا در تشخیص کلاسیکِ رنگ‌محور ({e})")
            return []

    @staticmethod
    def draw_boxes(frame, detections):
        """باکس‌های رنگی (قرمز=آتش، خاکستری=دود) را روی فریم رسم می‌کند.
        وارد کردن cv2 دیرهنگام است تا این ماژول بدون opencv هم import شود."""
        if not detections:
            return frame
        import cv2
        for label, confidence, (x1, y1, x2, y2) in detections:
            color = BOX_COLORS.get(label, (0, 255, 255))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            text = f"{label} {confidence:.0%}"
            cv2.putText(frame, text, (x1, max(0, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, color, 2, cv2.LINE_AA)
        return frame


# نمونه‌ی سراسری - همان الگوی face_engine/camera_store/report_store در این
# پروژه (تک نمونه‌ی مشترک، تا وزن مدل فقط یک‌بار در کل برنامه بارگذاری شود).
fire_smoke_detector = FireSmokeDetector()

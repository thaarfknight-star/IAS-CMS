# -*- coding: utf-8 -*-
"""تشخیص تصویری آتش/دود روی فریم‌های دوربین با هوش مصنوعی.

دقیقاً همان الگوی floor_detector.py (تشخیص هوشمند زمین): یک مدل آماده از
HuggingFace Hub، از طریق کتابخانه‌ی transformers، اولین‌بار که واقعاً لازم
شود بارگذاری/دانلود می‌شود (نه در استارت برنامه) و از آن پس در حافظه
می‌ماند. اگر transformers نصب نباشد یا دانلود/بارگذاری مدل به هر دلیلی
(نبود اینترنت و ...) شکست بخورد، برنامه کرش نمی‌کند - فقط تشخیص آتش/دود
غیرفعال می‌ماند (رجوع کنید به self.available/self.load_error) و به‌جایش،
برای این‌که کاربر بی‌هیچ توضیحی «تشخیص حریق کار نمی‌کند» را نبیند، از یک
تشخیص کلاسیکِ رنگ‌محور (بدون نیاز به هیچ مدل/اینترنتی) به‌عنوان جایگزین
استفاده می‌شود - رجوع کنید به _detect_classical.

مدل استفاده‌شده: prithivMLmods/Fire-Detection-Siglip2 - یک مدل
طبقه‌بندی‌تصویر (SiglipForImageClassification، fine-tune شده روی
google/siglip2-base-patch16-224) با ۳ کلاس «fire / normal / smoke»، دقتی
حدود ۹۹٪ روی دیتاست ارزیابی‌اش. چون این یک مدل طبقه‌بندیِ کل‌تصویر است (نه
شیءیاب مثل YOLO)، بر خلاف تشخیص شخص/چهره باکسِ دقیقِ دورِ شعله نمی‌دهد -
وقتی «آتش» یا «دود» با اطمینان کافی تشخیص داده شود، کل کادر تصویر همان
دوربین به‌عنوان ناحیه‌ی هشدار علامت‌گذاری می‌شود (دقیقاً کافی برای رفع
درخواست «کادر دوربین قرمز شود/آلارم پخش شود» که camera_stream.py و
main.py از قبل بر همان اساس - رجوع کنید به fire_event_signal - پیاده‌سازی
کرده‌اند).

نکات وابستگی (دقیقاً مثل floor_detector.py):
  - transformers/torch از قبل به‌خاطر تشخیص هوشمند زمین (floor_detector.py)
    و/یا ultralytics روی سیستم/exe نصب/بسته‌بندی هستند - وابستگی سنگین
    تازه‌ای اضافه نمی‌شود.
  - بارگذاری/فراخوانیِ مدل thread-safe نیست؛ چون این ماژول هم یک نمونه‌ی
    مشترک (singleton) است، یک قفل (RLock) بارگذاری و هر بار پیش‌بینی را
    سریالایز می‌کند - حتی اگر چند دوربین هم‌زمان تشخیص را روی ترد
    پس‌زمینه‌ی خودشان صدا بزنند.
"""

import os
import threading

DEFAULT_CLASS_NAMES = {0: "fire", 1: "smoke"}
BOX_COLORS = {"fire": (0, 0, 255), "smoke": (128, 128, 128)}  # BGR

# --- مدل هوش مصنوعیِ طبقه‌بندیِ آتش/دود (اولویت اول) ---
_HF_MODEL_ID = "prithivMLmods/Fire-Detection-Siglip2"
_LOCAL_MODEL_DIRNAME = "fire_smoke_ai_model"
# آستانه‌ی اطمینانِ پیش‌فرض برای این‌که خروجی «fire»/«smoke» مدل به‌عنوان
# یک رویداد واقعی در نظر گرفته شود (نه صرفاً کمی بالاتر از «normal»).
_AI_CONF_THRESHOLD = 0.65

# --- تنظیمات تشخیص کلاسیکِ رنگ‌محور (فقط وقتی مدل AI در دسترس نباشد) ---
_FIRE_HSV_RANGES = [
    # (H_min, H_max, S_min, V_min) - قرمز تا نارنجی/زرد پرنور
    (0, 35, 80, 180),
]
_MIN_FIRE_PIXEL_RATIO = 0.0015
_MIN_CONTOUR_AREA = 180
_WORK_SIZE = (320, 240)
_MAX_CLASSICAL_BOXES = 5


def _resolve_model_dir(dirname):
    """همان منطق floor_detector._resolve_model_dir - جست‌وجوی یک پوشه‌ی
    بسته‌بندی‌شده‌ی مدل (کنار exe یا کنار سورس) پیش از افتادن به دانلود
    آنلاین از HuggingFace Hub."""
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


class FireSmokeDetector:
    """نمونه‌ی مشترک (singleton) - دقیقاً مثل PersonDetector/FloorDetector:
    مدل فقط یک‌بار در حافظه بارگذاری می‌شود."""

    def __init__(self, conf_threshold: float = _AI_CONF_THRESHOLD,
                 class_names: dict | None = None, classical_fallback: bool = True):
        self.conf_threshold = conf_threshold
        self.class_names = class_names or DEFAULT_CLASS_NAMES
        self.classical_fallback_enabled = classical_fallback

        self._processor = None
        self._model = None
        self._id2label = None  # {index: "fire"/"normal"/"smoke"/...} از خودِ کانفیگ مدل
        self._load_attempted = False
        self._load_error = None
        self._lock = threading.RLock()

    @property
    def load_error(self):
        """پیام خطای بارگذاری مدل AI (اگر بارگذاری تلاش و ناموفق بوده)، یا
        None در غیر این صورت."""
        return self._load_error

    @property
    def available(self):
        """True فقط اگر مدل هوش مصنوعی واقعاً با موفقیت بارگذاری شده
        باشد. اولین فراخوانی همین‌جا (نه در استارت برنامه) تلاش برای
        بارگذاری/دانلود مدل را انجام می‌دهد - چون این تشخیص از قبل روی
        یک ترد پس‌زمینه (همان ترد تشخیص چهره/شخص در camera_stream.py)
        صدا زده می‌شود، تاخیر اولین بار باعث فریز شدن پخش زنده نمی‌شود."""
        self._ensure_loaded()
        return self._model is not None

    @property
    def using_ml_model(self) -> bool:
        """آیا هم‌اکنون از مدل واقعیِ هوش مصنوعی استفاده می‌شود (برخلاف
        تشخیصِ کلاسیکِ رنگ‌محورِ جایگزین)؟"""
        return self.available

    def _ensure_loaded(self):
        if self._load_attempted:
            return
        with self._lock:
            if self._load_attempted:
                return
            self._load_attempted = True
            try:
                from transformers import AutoImageProcessor, SiglipForImageClassification
                local_dir = _resolve_model_dir(_LOCAL_MODEL_DIRNAME)
                source = local_dir or _HF_MODEL_ID
                self._processor = AutoImageProcessor.from_pretrained(source)
                self._model = SiglipForImageClassification.from_pretrained(source)
                self._model.eval()
                raw_id2label = getattr(self._model.config, "id2label", {}) or {}
                self._id2label = {int(k): str(v).strip().lower() for k, v in raw_id2label.items()}
            except Exception as e:
                self._model = None
                self._processor = None
                self._id2label = None
                self._load_error = str(e)
                if self.classical_fallback_enabled:
                    print(
                        "تشخیص هوشمند آتش/دود (AI) در دسترس نیست - به‌جای آن از تشخیص "
                        "کلاسیکِ رنگ‌محورِ آتش روی تصویر دوربین استفاده می‌شود. اگر از سورس "
                        "اجرا می‌کنید: pip install transformers و اتصال اینترنت برای دانلود "
                        f"یک‌بارِ وزن مدل لازم است. خطا: {e}"
                    )
                else:
                    print(f"FireSmokeDetector: بارگذاری مدل AI ناموفق بود ({e}) - تشخیص آتش/دود غیرفعال است")

    def detect(self, frame):
        """روی یک فریم BGR (numpy array) اجرا می‌شود و لیستی از
        (label, confidence, (x1, y1, x2, y2)) برمی‌گرداند. اگر مدل AI در
        دسترس باشد از آن استفاده می‌شود (باکس = کل کادر فریم چون این یک
        طبقه‌بند تصویر است، نه شیءیاب)؛ در غیر این صورت (و اگر
        classical_fallback_enabled باشد) از تشخیص کلاسیکِ رنگ‌محورِ آتش
        استفاده می‌شود. هرگز استثنا پرتاب نمی‌کند."""
        if frame is None:
            return []
        if self.available:
            return self._detect_ai(frame)
        if self.classical_fallback_enabled:
            return self._detect_classical(frame)
        return []

    def _detect_ai(self, frame):
        try:
            import cv2
            import torch
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._lock:
                inputs = self._processor(images=rgb, return_tensors="pt")
                with torch.no_grad():
                    logits = self._model(**inputs).logits[0]
            probs = torch.softmax(logits, dim=0).cpu().numpy()

            detections = []
            for idx, prob in enumerate(probs):
                name = (self._id2label or {}).get(idx, "")
                if "fire" in name:
                    label = "fire"
                elif "smoke" in name:
                    label = "smoke"
                else:
                    continue  # کلاس "normal"/بدون‌آتش - نادیده گرفته می‌شود
                confidence = float(prob)
                if confidence >= self.conf_threshold:
                    detections.append((label, confidence, (0, 0, w, h)))
            return detections
        except Exception as e:
            print(f"FireSmokeDetector: خطا در تشخیص هوشمند آتش/دود ({e})")
            return []

    def _detect_classical(self, frame):
        """تشخیص جایگزینِ آتش بر پایه‌ی رنگ (بدون هیچ مدلی) - فقط وقتی
        مدل AI بارگذاری نشده باشد استفاده می‌شود؛ رجوع کنید به توضیح
        بالای فایل."""
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


# نمونه‌ی سراسری - همان الگوی face_engine/camera_store/report_store/
# floor_detector در این پروژه (تک نمونه‌ی مشترک، تا وزن مدل فقط یک‌بار در
# کل برنامه بارگذاری شود).
fire_smoke_detector = FireSmokeDetector()

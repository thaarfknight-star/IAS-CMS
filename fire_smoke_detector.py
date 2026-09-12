# -*- coding: utf-8 -*-
"""تشخیص تصویری آتش/دود روی فریم‌های دوربین، به همان الگوی person_detector.py:
یک مدل YOLOv8n سبک (ultralytics) که وزن‌های اختصاصی fire_smoke.pt را در
صورت وجود بارگذاری می‌کند. اگر ultralytics نصب نباشد یا وزن پیدا نشود،
``self.available`` برابر False می‌ماند و صدا زدن ``detect`` هیچ باکسی
برنمی‌گرداند (نه استثنا) - یعنی بقیه‌ی برنامه (تشخیص چهره/شخص، پخش زنده)
کاملاً بدون تغییر و بدون کرش کار می‌کند.

نام کلاس‌های مدل مورد انتظار: index 0 = 'fire', index 1 = 'smoke' (قابل
تنظیم با پارامتر ``class_names`` هنگام ساخت شیء اگر وزن دیگری با نگاشت
متفاوت استفاده شود).
"""

import os

DEFAULT_WEIGHTS_PATH = "fire_smoke.pt"
DEFAULT_CLASS_NAMES = {0: "fire", 1: "smoke"}
BOX_COLORS = {"fire": (0, 0, 255), "smoke": (128, 128, 128)}  # BGR


class FireSmokeDetector:
    def __init__(self, weights_path: str = DEFAULT_WEIGHTS_PATH,
                 class_names: dict | None = None, conf_threshold: float = 0.45):
        self.weights_path = weights_path
        self.class_names = class_names or DEFAULT_CLASS_NAMES
        self.conf_threshold = conf_threshold
        self.available = False
        self.model = None
        self._load_error = None
        self._load_model()

    def _load_model(self):
        if not os.path.exists(self.weights_path):
            self._load_error = f"فایل وزن '{self.weights_path}' پیدا نشد."
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
            print(f"FireSmokeDetector: بارگذاری مدل ناموفق بود ({e}) - تشخیص آتش/دود غیرفعال است")

    def detect(self, frame):
        """روی یک فریم BGR (numpy array) اجرا می‌شود و لیستی از
        (label, confidence, (x1, y1, x2, y2)) برمی‌گرداند. اگر مدل در
        دسترس نباشد، لیست خالی برمی‌گرداند (هرگز استثنا)."""
        if not self.available or frame is None:
            return []
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

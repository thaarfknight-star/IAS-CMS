import os
import threading

import cv2
import numpy as np
import requests

# ---------------------------------------------------------------------------
# رفع درخواست: «شخص رو شناسایی نمی‌کنه» / تعریف تمام حالت‌های شخص
# ---------------------------------------------------------------------------
# ریشه‌ی مشکل: شمارش/نمایش «افراد» در این برنامه قبلاً کاملاً بر پایه‌ی
# تشخیص چهره (FaceEngine + dlib) بود. تشخیص چهره فقط وقتی کار می‌کند که
# صورت کم‌وبیش رو به دوربین باشد. در تصویر گزارش‌شده توسط کاربر، شخص پشتش
# (یا پهلویش) به دوربین است و گوشی را کنار گوشش گرفته - هیچ چهره‌ای دیده
# نمی‌شود - پس با روش قبلی همیشه «۰ نفر / خالی» نشان داده می‌شد، مستقل از
# اینکه شخص واقعاً ایستاده، نشسته یا نیم‌خیز باشد.
#
# راه‌حل: به‌جای تکیه بر چهره، از یک تشخیص‌دهنده‌ی عمومی «شخص» (نه فقط
# صورت) استفاده می‌کنیم: مدل MobileNet-SSD (آموزش‌دیده روی PASCAL VOC) که
# با OpenCV DNN (همان وابستگی opencv-python که از قبل در پروژه هست - بدون
# نیاز به نصب هیچ کتابخانه‌ی سنگین جدیدی مثل torch/ultralytics) اجرا
# می‌شود. این مدل «شکل کلی بدن» را تشخیص می‌دهد، نه ویژگی‌های صورت؛ در
# نتیجه مستقل از حالت/زاویه‌ی شخص کار می‌کند:
#   - ایستاده (رو‌به‌دوربین یا پشت به دوربین)
#   - نشسته (مثلاً پشت میز، نیمی از بدن پشت میز/قفسه پنهان)
#   - نیم‌خیز / درحال خم‌شدن یا بلندشدن
#   - در حال راه‌رفتن، از پهلو دیده‌شده و ...
# در فایل camera_stream.py، این تشخیص برای «شمارش افراد» و رسم کادر دور
# بدن استفاده می‌شود، در حالی که FaceEngine (face_engine.py) هم‌چنان برای
# «شناسایی هویت» (چه کسی است) وقتی چهره دیده می‌شود به‌کار می‌رود - این دو
# مکمل هم‌اند، نه جایگزین هم.

MODEL_DIR = "models"
PROTO_FILENAME = "MobileNetSSD_deploy.prototxt"
WEIGHTS_FILENAME = "MobileNetSSD_deploy.caffemodel"

# منبع فایل‌های مدل (پروژه‌ی متن‌باز عمومی djmv/MobilNet_SSD_opencv که خودِ
# فایل‌های آموزش‌دیده را - نه فقط لینک دانلود خارجی - داخل مخزن گیت‌هاب
# نگه می‌دارد؛ در نتیجه با یک درخواست ساده‌ی HTTP قابل دریافت است).
PROTO_URL = "https://raw.githubusercontent.com/djmv/MobilNet_SSD_opencv/master/MobileNetSSD_deploy.prototxt"
WEIGHTS_URL = "https://raw.githubusercontent.com/djmv/MobilNet_SSD_opencv/master/MobileNetSSD_deploy.caffemodel"

# ترتیب کلاس‌های PASCAL VOC که این مدل روی آن‌ها آموزش دیده - فقط کلاس
# "person" برای ما اهمیت دارد.
VOC_CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse",
    "motorbike", "person", "pottedplant", "sheep", "sofa", "train",
    "tvmonitor",
]
PERSON_CLASS_ID = VOC_CLASSES.index("person")


def _classify_pose(box):
    """حدس ساده‌ی «حالت» شخص فقط بر اساس نسبت ارتفاع/عرض کادر بدنش - رفع
    درخواست «همه‌ی حالت‌های اشخاص (ایستاده، نشسته، نیم‌خیز و...) را تعریف
    کن». این یک تخمین تقریبی است (نه تشخیص اسکلت/pose-estimation واقعی)،
    ولی برای برچسب‌زدن روی تصویر کافی است؛ تشخیصِ خودِ «وجود شخص» (که مشکل
    اصلی گزارش‌شده بود) کاملاً مستقل از این تخمین و برای هر حالتی کار
    می‌کند."""
    top, right, bottom, left = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    ratio = height / width
    if ratio >= 1.9:
        return "ایستاده"
    if ratio >= 1.15:
        return "نیم‌خیز"
    return "نشسته"


class PersonDetector:
    """تشخیص «شخص» بر اساس کل بدن (مستقل از قابل‌مشاهده‌بودن چهره)."""

    def __init__(self, confidence=0.5, model_dir=MODEL_DIR):
        self.confidence = confidence
        self.model_dir = model_dir
        os.makedirs(self.model_dir, exist_ok=True)

        self._proto_path = os.path.join(self.model_dir, PROTO_FILENAME)
        self._weights_path = os.path.join(self.model_dir, WEIGHTS_FILENAME)
        self._download_if_missing(self._proto_path, PROTO_URL)
        self._download_if_missing(self._weights_path, WEIGHTS_URL)

        self.net = cv2.dnn.readNetFromCaffe(self._proto_path, self._weights_path)

        # مدل‌های cv2.dnn هم مثل dlib برای فراخوانی هم‌زمان از چند ترد
        # thread-safe نیستند؛ چون این کلاس هم (مثل FaceEngine) ممکن است
        # به‌صورت مشترک بین تردهای پخش چند دوربین استفاده شود، یک قفل ساده
        # کافی است.
        self._lock = threading.Lock()

    @staticmethod
    def _download_if_missing(path, url):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        tmp_path = path + ".part"
        with open(tmp_path, "wb") as f:
            f.write(response.content)
        os.replace(tmp_path, path)

    def detect(self, frame):
        """بازگشت: لیستی از {"box": (top, right, bottom, left), "pose": str,
        "confidence": float} برای هر «شخص» شناسایی‌شده در فریم - مستقل از
        حالت بدن و مستقل از دیده‌شدن یا نشدن چهره."""
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5
        )
        with self._lock:
            self.net.setInput(blob)
            detections = self.net.forward()

        results = []
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            class_id = int(detections[0, 0, i, 1])
            if class_id != PERSON_CLASS_ID or confidence < self.confidence:
                continue

            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            left, top, right, bottom = box.astype(int)
            left, top = max(0, left), max(0, top)
            right, bottom = min(w, right), min(h, bottom)
            if right <= left or bottom <= top:
                continue

            box_tuple = (int(top), int(right), int(bottom), int(left))
            results.append({
                "box": box_tuple,
                "pose": _classify_pose(box_tuple),
                "confidence": confidence,
            })
        return results


# ---------------------------------------------------------------------------
# نمونه‌ی مشترک (Singleton) - همه‌ی تردهای پخش دوربین‌ها از همین یک نمونه
# استفاده می‌کنند (دقیقاً مثل الگوی FaceEngine که یک نمونه بین همه‌ی
# دوربین‌ها مشترک است)، تا مدل فقط یک‌بار بارگذاری/دانلود شود.
# ---------------------------------------------------------------------------
_instance = None
_instance_error = None
_instance_lock = threading.Lock()


def get_person_detector():
    """نمونه‌ی مشترک PersonDetector را برمی‌گرداند؛ در صورت شکست در دانلود/
    بارگذاری مدل (مثلاً نبود اینترنت در اولین اجرا)، None برمی‌گرداند تا
    تشخیص شخص به‌آرامی غیرفعال شود، بدون کرش برنامه (تشخیص چهره هم‌چنان
    کار می‌کند)."""
    global _instance, _instance_error
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is None and _instance_error is None:
            try:
                _instance = PersonDetector()
            except Exception as e:  # noqa: BLE001 - می‌خواهیم هر خطایی را نرم مدیریت کنیم
                _instance_error = e
                print(f"خطا در بارگذاری مدل تشخیص شخص (models/): {e}")
    return _instance

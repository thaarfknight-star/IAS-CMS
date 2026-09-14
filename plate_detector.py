# -*- coding: utf-8 -*-
"""تشخیص ناحیه‌ی پلاک خودرو + خوانش متن پلاک (ANPR).

معماری سه‌لایه (مشابه fire_smoke_detector):
  ۱) PlateDetector: یافتن مستطیل پلاک با YOLO (lazy-load، فقط وقتی پلاک‌خوان
     یک دوربین فعال شود؛ مدل plate_detector.pt کنار برنامه یا دانلود خودکار)
  ۲) PlateOCR: خوانش متن از کراپ پلاک با دو موتور (به ترتیب اولویت):
       - easyocr با زبان فارسی ('fa') — مخصوص پلاک‌های ایرانی
       - rapidocr_onnxruntime — سبک و سریع، برای ارقام لاتین/عربی
     هر دو lazy-load می‌شوند و نبودشان باعث کرش نمی‌شود (OCR در دسترس نیست
     ولی تشخیص ناحیه‌ی پلاک و ثبت تصویر همچنان کار می‌کند).
  ۳) PlateTracker: ردیابی چندفریمی هر پلاک (تطبیق با IoU) + تأیید خوانش
     (همان متن باید K بار خوانده شود) + کول‌داون برای هر پلاک — تا یک خودروی
     پارک‌کرده هر چند ثانیه رویداد تکراری تولید نکند.

نکته‌ی مهم درباره‌ی import: هیچ‌کدام از کتابخانه‌های سنگین (ultralytics،
torch، easyocr، rapidocr) در سطح ماژول import نمی‌شوند؛ همه داخل توابع و
فقط در اولین استفاده‌ی واقعی بارگذاری می‌شوند تا بالا آمدن برنامه کند نشود.
"""

import os
import sys
import time
import threading

try:
    import cv2  # فقط برای upscale برش پلاک قبل از OCR؛ نبودش = بدون upscale
except Exception:
    cv2 = None  # type: ignore
import numpy as np

try:
    from plate_store import normalize_plate_text, prettify_plate
except Exception:  # اجرای مستقل برای تست
    from plate_store import normalize_plate_text, prettify_plate


# --------------------------------------------------------------------------
# ۱) تشخیص ناحیه‌ی پلاک
# --------------------------------------------------------------------------

# اگر مدل محلی پیدا نشود، از این آدرس دانلود می‌شود (در ترد پس‌زمینه).
# مدل: joker5914/yolov8n-license-plate از HuggingFace - YOLOv8n فاین‌تیون‌شده
# روی دیتاست تشخیص پلاک خودرو؛ ~۶ مگابایت (سبک، مناسب CPU و ۴GB RAM)، فرمت
# ‎.pt‎ سازگار با ultralytics، دقت mAP50 ≈ ۰٫۹۸۳.
# اعتبارسنجی لینک در 2026-09-13 (HTTP 200، فایل best.pt در صفحه‌ی مدل تأیید شد).
# نکته‌ی فنی: تشخیص «کادر» پلاک مستقل از کشور است و روی پلاک ایرانی هم جواب
# می‌دهد؛ خوانش حروف فارسی با OCR فارسی (EasyOCR fa) در همین ماژول انجام می‌شود.
# با IAS_PLATE_MODEL_URL می‌توان URL دیگری داد.
PLATE_MODEL_URL = os.environ.get(
    "IAS_PLATE_MODEL_URL",
    "https://huggingface.co/joker5914/yolov8n-license-plate/resolve/main/best.pt",
)


def _app_dir():
    try:
        base = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else ""
        if base and os.path.isdir(base):
            return base
    except Exception:
        pass
    return os.getcwd()


def _find_plate_model():
    """مسیر فایل وزن مدل پلاک؛ اولویت: متغیر محیطی، کنار برنامه، پوشه‌ی models."""
    env_path = os.environ.get("IAS_PLATE_MODEL", "").strip()
    candidates = []
    if env_path:
        candidates.append(env_path)
    app = _app_dir()
    candidates.append(os.path.join(app, "plate_detector.pt"))
    candidates.append(os.path.join(app, "models", "plate_detector.pt"))
    candidates.append(os.path.join(app, "plate_data", "models", "plate_detector.pt"))
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def _download_plate_model(dest):
    """دانلود مدل در ترد پس‌زمینه؛ False یعنی ناموفق (آفلاین بودن و...)."""
    url = PLATE_MODEL_URL.strip()
    if not url:
        return False
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".downloading"
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "IAS-CMS"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
        if os.path.getsize(tmp) < 100 * 1024:  # فایل خیلی کوچک = خطا
            os.remove(tmp)
            return False
        os.replace(tmp, dest)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


class PlateDetector:
    """تشخیص مستطیل پلاک با YOLO. available=False یعنی مدل/کتابخانه در دسترس
    نیست و load_error دلیل فارسی آن را توضیح می‌دهد (برای نمایش روی تایل)."""

    def __init__(self, model_path=None, conf=0.40):
        self.available = False
        self.load_error = ""
        self.conf = conf
        self.model = None
        try:
            from ultralytics import YOLO
        except Exception:
            self.load_error = (
                "کتابخانه‌ی ultralytics نصب نیست؛ پلاک‌خوان غیرفعال است. "
                "(راهنما: pip install ultralytics)"
            )
            return
        path = model_path or _find_plate_model()
        if not path:
            # تلاش برای دانلود خودکار (فقط یک‌بار برای هر نمونه)
            dest = os.path.join(_app_dir(), "plate_data", "models", "plate_detector.pt")
            if os.path.isfile(dest):
                path = dest
            elif _download_plate_model(dest):
                path = dest
        if not path:
            self.load_error = (
                "فایل مدل پلاک‌خوان (plate_detector.pt) یافت نشد و دانلود خودکار "
                "هم موفق نبود. فایل را کنار برنامه بگذارید یا متغیر IAS_PLATE_MODEL "
                "را تنظیم کنید."
            )
            return
        try:
            self.model = YOLO(path)
            self.available = True
        except Exception as e:
            self.load_error = f"خطا در بارگذاری مدل پلاک: {e}"

    def detect(self, frame):
        """خروجی: لیست [(x1, y1, x2, y2, conf), ...] به پیکسل."""
        if not self.available:
            return []
        try:
            results = self.model.predict(frame, conf=self.conf, verbose=False)
            boxes = []
            for r in results:
                if r.boxes is None:
                    continue
                for b in r.boxes:
                    x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                    boxes.append((x1, y1, x2, y2, float(b.conf[0])))
            return boxes
        except Exception:
            return []


# نمونه‌ی مشترک بین دوربین‌ها (مثل person_detector) + کش خطا
_PLATE_DETECTOR = None
_PLATE_DETECTOR_TRIED = False
_PLATE_DETECTOR_LOCK = threading.Lock()


def get_shared_plate_detector():
    """نمونه‌ی مشترک؛ اگر مدل در دسترس نباشد None برمی‌گرداند (نه نمونه‌ی خراب)."""
    global _PLATE_DETECTOR, _PLATE_DETECTOR_TRIED
    with _PLATE_DETECTOR_LOCK:
        if _PLATE_DETECTOR_TRIED:
            d = _PLATE_DETECTOR
            return d if (d is not None and d.available) else None
        _PLATE_DETECTOR_TRIED = True
        try:
            d = PlateDetector()
            _PLATE_DETECTOR = d if d.available else None
        except Exception:
            _PLATE_DETECTOR = None
        return _PLATE_DETECTOR


# --------------------------------------------------------------------------
# ۲) OCR چندموتوره
# --------------------------------------------------------------------------

def _preprocess_for_ocr(crop):
    """بزرگ‌نمایی کراپ‌های کوچک + کنتراست؛ ورودی/خروجی BGR."""
    h, w = crop.shape[:2]
    if h <= 0 or w <= 0:
        return crop
    if cv2 is not None and h < 96:
        scale = 96.0 / h
        crop = cv2.resize(crop, (int(w * scale), 96), interpolation=cv2.INTER_CUBIC)
    return crop


class PlateOCR:
    """خوانش متن پلاک از تصویر کراپ‌شده. موتورها به ترتیب اولویت:
    easyocr-fa (پلاک ایرانی) بعد rapidocr (سبک). هر موتور فقط یک‌بار و
    تنبل بارگذاری می‌شود."""

    def __init__(self):
        self._easyocr_reader = None
        self._easyocr_tried = False
        self._rapidocr = None
        self._rapidocr_tried = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ easyocr -
    @property
    def engine_name(self):
        """نام موتور OCR فعالی که واقعاً بارگذاری شده (برای نمایش/دیباگ)."""
        if getattr(self, "_easyocr_reader", None) is not None:
            return "easyocr(fa)"
        if getattr(self, "_rapidocr", None) is not None:
            return "rapidocr"
        return "none"

    def _get_easyocr(self):
        with self._lock:
            if self._easyocr_tried:
                return self._easyocr_reader
            self._easyocr_tried = True
            try:
                import easyocr
                # فقط تشخیص متن (recognizer) روی کراپ کوچک؛ detector روی کراپ
                # لازم نیست ولی easyocr همیشه هر دو را لود می‌کند - برای همین
                # lazy است و فقط وقتی پلاک‌خوان فعال باشد.
                self._easyocr_reader = easyocr.Reader(["fa", "en"], gpu=False,
                                                      verbose=False)
            except Exception as e:
                print(f"[plate_ocr] easyocr در دسترس نیست: {e}")
                self._easyocr_reader = None
            return self._easyocr_reader

    def _read_easyocr(self, crop):
        reader = self._get_easyocr()
        if reader is None:
            return []
        try:
            # detail=1 -> [(box, text, conf)]
            results = reader.readtext(crop, detail=1)
            out = []
            for _box, text, conf in results:
                t = normalize_plate_text(text)
                if len(t) >= 3:
                    out.append((t, float(conf), "easyocr-fa"))
            return out
        except Exception:
            return []

    # ----------------------------------------------------------- rapidocr -
    def _get_rapidocr(self):
        with self._lock:
            if self._rapidocr_tried:
                return self._rapidocr
            self._rapidocr_tried = True
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._rapidocr = RapidOCR()
            except Exception as e:
                print(f"[plate_ocr] rapidocr در دسترس نیست: {e}")
                self._rapidocr = None
            return self._rapidocr

    def _read_rapidocr(self, crop):
        engine = self._get_rapidocr()
        if engine is None:
            return []
        try:
            result, _elapse = engine(crop)
            out = []
            if result:
                for _box, text, conf in result:
                    t = normalize_plate_text(text)
                    if len(t) >= 3:
                        out.append((t, float(conf), "rapidocr"))
            return out
        except Exception:
            return []

    # -------------------------------------------------------------- عمومی -
    @property
    def available(self):
        """True اگر حداقل یک موتور OCR آماده باشد."""
        return (self._get_easyocr() is not None) or (self._get_rapidocr() is not None)

    def engines_status(self):
        return {
            "easyocr_fa": self._get_easyocr() is not None,
            "rapidocr": self._get_rapidocr() is not None,
        }

    def read(self, crop_bgr):
        """خوانش متن از کراپ پلاک. خروجی: لیست [(text, conf, engine)] مرتب
        بر اساس اطمینان (نزولی)، بدون تکراری."""
        if crop_bgr is None or crop_bgr.size == 0:
            return []
        crop = _preprocess_for_ocr(crop_bgr)
        candidates = []
        # اولویت با easyocr فارسی (پلاک ایرانی) است
        candidates.extend(self._read_easyocr(crop))
        candidates.extend(self._read_rapidocr(crop))
        # حذف تکراری‌ها (نگه‌داشتن بالاترین اطمینان برای هر متن)
        best = {}
        for text, conf, engine in candidates:
            if text not in best or conf > best[text][0]:
                best[text] = (conf, engine)
        ranked = sorted(
            [(t, c, e) for t, (c, e) in best.items()],
            key=lambda x: x[1], reverse=True)
        return ranked


_OCR_SINGLETON = None
_OCR_LOCK = threading.Lock()


def get_shared_plate_ocr():
    global _OCR_SINGLETON
    with _OCR_LOCK:
        if _OCR_SINGLETON is None:
            _OCR_SINGLETON = PlateOCR()
        return _OCR_SINGLETON


# --------------------------------------------------------------------------
# ۳) ردیاب چندفریمی + تأیید + کول‌داون
# --------------------------------------------------------------------------

def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def _expand_box(box, frame_w, frame_h, ratio=0.12):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    dx, dy = int(w * ratio), int(h * ratio)
    return (max(0, x1 - dx), max(0, y1 - dy),
            min(frame_w, x2 + dx), min(frame_h, y2 + dy))


class PlateTracker:
    """ردیابی هر پلاک در فریم‌های متوالی (نمونه‌ی جدا برای هر دوربین).

    - هر باکس تازه با IoU به نزدیک‌ترین ترک موجود وصل می‌شود.
    - OCR حداکثر هر ~1.2 ثانیه برای هر ترک اجرا می‌شود (صرفه‌جویی CPU).
    - وقتی یک متن یکسان K بار (پیش‌فرض ۲) خوانده شود، «تأیید» و رویداد صادر
      می‌شود؛ برای همان پلاک تا پایان کول‌داون رویداد تکراری صادر نمی‌شود.
    """

    def __init__(self, confirm_reads=2, ocr_interval_s=1.2,
                 track_ttl_s=3.0, cooldown_s=45.0):
        self.confirm_reads = max(1, int(confirm_reads))
        self.ocr_interval_s = ocr_interval_s
        self.track_ttl_s = track_ttl_s
        self.cooldown_s = cooldown_s
        self._tracks = []  # dict(box, reads, last_seen, last_ocr_ts, last_event_ts, last_text)
        self._lock = threading.Lock()

    def update(self, detections, frame, ocr):
        """detections: [(x1,y1,x2,y2,conf)]. خروجی: لیست رویدادهای تازه‌ی
        تأییدشده‌ی [(box, text, conf)]."""
        now = time.time()
        h, w = frame.shape[:2]
        events = []
        with self._lock:
            # ۱) تطبیق دتکشن‌ها به ترک‌ها
            unmatched = list(detections)
            for tr in self._tracks:
                best, best_iou, best_idx = None, 0.35, -1
                for i, det in enumerate(unmatched):
                    iou = _iou(tr["box"][:4], det[:4])
                    if iou > best_iou:
                        best, best_iou, best_idx = det, iou, i
                if best is not None:
                    tr["box"] = best
                    tr["last_seen"] = now
                    unmatched.pop(best_idx)
            # ۲) ترک تازه برای دتکشن‌های بی‌صاحب
            for det in unmatched:
                self._tracks.append({
                    "box": det, "reads": [], "last_seen": now,
                    "last_ocr_ts": 0.0, "last_event_ts": 0.0,
                    "last_text": "", "last_conf": 0.0,
                })
            # ۳) حذف ترک‌های منقضی
            self._tracks = [t for t in self._tracks
                            if now - t["last_seen"] <= self.track_ttl_s]
            # ۴) OCR تنبل + تأیید
            for tr in self._tracks:
                if now - tr["last_ocr_ts"] < self.ocr_interval_s:
                    continue
                tr["last_ocr_ts"] = now
                x1, y1, x2, y2 = _expand_box(tr["box"][:4], w, h)
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                try:
                    reads = ocr.read(crop)
                except Exception:
                    reads = []
                if not reads:
                    continue
                _r0 = reads[0]
                text, conf = (_r0[0], _r0[1]) if len(_r0) >= 2 else (str(_r0), 0.0)
                tr["reads"].append(text)
                tr["reads"] = tr["reads"][-8:]
                tr["last_text"] = text
                tr["last_conf"] = conf
                # تأیید: همان متن حداقل confirm_reads بار در خوانش‌های اخیر
                count = tr["reads"].count(text)
                if (count >= self.confirm_reads
                        and now - tr["last_event_ts"] >= self.cooldown_s):
                    tr["last_event_ts"] = now
                    events.append((tr["box"][:4], text, conf))
        return events

    def current_tracks(self):
        """وضعیت فعلی ترک‌ها برای رسم روی تصویر: [(box, text, conf)]."""
        now = time.time()
        with self._lock:
            return [(t["box"][:4], t["last_text"], t["last_conf"])
                    for t in self._tracks
                    if now - t["last_seen"] <= self.track_ttl_s]


def reset_shared_plate_detector():
    """برای تست: کش نمونه‌ی مشترک را پاک می‌کند."""
    global _PLATE_DETECTOR, _PLATE_DETECTOR_TRIED
    with _PLATE_DETECTOR_LOCK:
        _PLATE_DETECTOR = None
        _PLATE_DETECTOR_TRIED = False

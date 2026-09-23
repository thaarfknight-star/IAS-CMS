# -*- coding: utf-8 -*-
"""تشخیص ناحیه‌ی پلاک خودرو + خوانش متن پلاک (ANPR) — نسخه‌ی ۲.

خط لوله‌ی استاندارد جهانی (مطابق معماری سیستم‌های ANPR واقعی):
  ۱) PlateDetector: یافتن مستطیل پلاک با YOLO (lazy-load). مدل plate_detector.pt
     در زمان بیلد داخل exe بسته‌بندی می‌شود (رجوع کنید به build.yml)؛ پس در
     سیستم کاربر هیچ دانلودی لازم نیست. اگر مدل پیدا نشود، تلاش برای دانلود
     (اول HuggingFace، بعد آینه‌ی hf-mirror) انجام می‌شود و در صورت شکست،
     available=False با load_error فارسی — بدون کرش.
  ۲) پیش‌پردازش کراپ (قبل از OCR):
     - بزرگ‌نمایی کراپ‌های کوچک
     - گیت تاری (Blur gating با واریانس لاپلاسین، آستانه‌های استاندارد:
       کمتر از ۲۵ = خیلی تار → OCR رد می‌شود؛ ۲۵ تا ۱۰۰ = کمی تار →
       فیلتر bilateral)
     - یکسان‌سازی کنتراست با CLAHE
     - اصلاح پرسپکتیو (rectification): پیدا کردن چهارگوش پلاک با کانتور و
       warp به مستطیل روبه‌رو؛ در صورت شکست، همان کراپ ساده
     - حاشیه‌ی سفید دور کراپ
  ۳) PlateOCR: خوانش متن با دو موتور (به ترتیب اولویت):
       - hezarai/crnn-fa-license-plate-recognition-v2 به‌صورت ONNX
         (موتور اصلی — مخصوص پلاک فارسی آموزش دیده؛ بدون torch، فقط
         onnxruntime؛ فایل hezar_plate_v2.onnx داخل باندل برنامه است و
         روی سیستم کاربر هیچ دانلودی انجام نمی‌شود)
     هر دو lazy-load می‌شوند و نبودشان باعث کرش نمی‌شود.
  ۴) پس‌پردازش متن:
     - نرمال‌سازی ارقام فارسی/عربی/لاتین (plate_store.normalize_plate_text)
     - اصلاح اشتباه‌های رایج OCR (O→0، I→1، S→5، ...)
     - اعتبارسنجی قالب پلاک ایرانی (خودرو: ۲ رقم + حرف + ۵ رقم؛ موتورسیکلت)
       — خوانش‌هایی که حرف نامعتبر دارند وارد رأی‌گیری نمی‌شوند
     - اتصال تکه‌های شکسته‌شده‌ی OCR
  ۵) PlateTracker: ردیابی هر پلاک در فریم‌های متوالی (تطبیق IoU حریصانه —
     ابزار استاندارد دوربین‌های ثابت) + رأی‌گیری اکثریت «به‌ازای هر موقعیت
     کاراکتر» روی خوانش‌های هر ترک (تکنیک واقعی که دقت پلاک‌های کم‌کیفیت را
     به‌طور چشمگیری بالا می‌برد) + کول‌داون برای هر پلاک — تا یک خودروی
     پارک‌کرده هر چند ثانیه رویداد تکراری تولید نکند.
  ۶) شمارنده‌های تشخیصی (diag_snapshot) برای عیب‌یابی روی ویندوز: معلوم
     می‌کند مسیر detection → OCR → vote → event دقیقاً کجا می‌ایستد.

نکته‌ی مهم درباره‌ی import: هیچ‌کدام از کتابخانه‌های سنگین (ultralytics،
onnxruntime) در سطح ماژول import نمی‌شود؛ فقط داخل تابع و
فقط در اولین استفاده‌ی واقعی بارگذاری می‌شوند تا بالا آمدن برنامه کند نشود.
"""

import os
import sys
import time
import threading

try:
    import cv2  # برای پیش‌پردازش کراپ؛ نبودش = پیش‌پردازش حداقلی
except Exception:
    cv2 = None  # type: ignore
import numpy as np

try:
    from plate_store import (normalize_plate_text, prettify_plate,
                             IRANIAN_PLATE_LETTERS)
except Exception:  # اجرای مستقل برای تست
    from plate_store import (normalize_plate_text, prettify_plate,
                             IRANIAN_PLATE_LETTERS)


# --------------------------------------------------------------------------
# ۰) مسیرها و مدل‌های باندل‌شده
# --------------------------------------------------------------------------

def _app_dir():
    try:
        base = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else ""
        if base and os.path.isdir(base):
            return base
    except Exception:
        pass
    return os.getcwd()


def _bundle_dir():
    """پوشه‌ی فایل‌های فقط‌خواندنیِ باندل‌شده (مدل‌ها).
    در exe ساخته‌شده با PyInstaller این sys._MEIPASS است؛ در حالت onedirِ
    نسخه‌ی ۶ به بعد همان زیرپوشه‌ی _internal کنار فایل اجرایی است که
    --add-dataها (مثل plate_detector.pt و plate_ocr_models) داخلش قرار
    می‌گیرند. در اجرای از سورس None برمی‌گرداند."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(meipass):
        return meipass
    return None


def _plate_ocr_models_dir():
    """پوشه‌ی مدل‌های OCR پلاک (plate_ocr_models).
    در exe فریزشده: _internal/plate_ocr_models (با --add-data باندل شده)؛
    در اجرای از سورس: پوشه‌ی plate_ocr_models کنار همین فایل."""
    bundle = _bundle_dir()
    if bundle:
        p = os.path.join(bundle, "plate_ocr_models")
        if os.path.isdir(p):
            return p
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "plate_ocr_models")
    if os.path.isdir(here):
        return here
    return ""


def hezar_model_bundled():
    """True اگر فایل ONNX مدل هزار (hezar_plate_v2.onnx) داخل باندل برنامه
    باشد (بدون نیاز به دانلود روی سیستم کاربر)."""
    base = _plate_ocr_models_dir()
    try:
        return bool(base) and os.path.isfile(
            os.path.join(base, "hezar_plate_v2.onnx"))
    except Exception:
        return False


# --------------------------------------------------------------------------
# ۱) تشخیص ناحیه‌ی پلاک
# --------------------------------------------------------------------------

# مدل: joker5914/yolov8n-license-plate از HuggingFace - YOLOv8n فاین‌تیون‌شده
# روی دیتاست تشخیص پلاک خودرو؛ ~۶ مگابایت (سبک، مناسب CPU و ۴GB RAM)، فرمت
# ‎.pt‎ سازگار با ultralytics، دقت mAP50 ≈ ۰٫۹۸۳.
# نکته‌ی فنی: تشخیص «کادر» پلاک مستقل از کشور است و روی پلاک ایرانی هم جواب
# می‌دهد؛ خوانش حروف فارسی با OCR فارسی (مدل هزار CRNN) در همین ماژول انجام می‌شود.
_PLATE_MODEL_REPO = "joker5914/yolov8n-license-plate"
_PLATE_MODEL_SOURCES = [
    f"https://huggingface.co/{_PLATE_MODEL_REPO}/resolve/main/best.pt",
    # آینه برای شبکه‌هایی که CDN اصلی هایگینگ‌فیس در آن‌ها بسته است
    f"https://hf-mirror.com/{_PLATE_MODEL_REPO}/resolve/main/best.pt",
]
if os.environ.get("IAS_PLATE_MODEL_URL", "").strip():
    _PLATE_MODEL_SOURCES.insert(0, os.environ["IAS_PLATE_MODEL_URL"].strip())


def _find_plate_model():
    """مسیر فایل وزن مدل پلاک؛ اولویت: متغیر محیطی، کنار برنامه، پوشه‌ی models."""
    env_path = os.environ.get("IAS_PLATE_MODEL", "").strip()
    candidates = []
    if env_path:
        candidates.append(env_path)
    bundle = _bundle_dir()
    if bundle:
        # exe فریزشده (PyInstaller onedir v6+): مدل با --add-data داخل
        # _internal باندل شده است
        candidates.append(os.path.join(bundle, "plate_detector.pt"))
    app = _app_dir()
    candidates.append(os.path.join(app, "plate_detector.pt"))
    candidates.append(os.path.join(app, "models", "plate_detector.pt"))
    candidates.append(os.path.join(app, "plate_data", "models", "plate_detector.pt"))
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def _download_plate_model(dest):
    """دانلود مدل از اولین منبعی که جواب بدهد؛ False یعنی ناموفق.

    توجه: این فقط fallback است — در بیلد رسمی مدل از قبل داخل exe است و
    این تابع در سیستم کاربر صدا زده نمی‌شود."""
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except Exception:
        pass
    tmp = dest + ".downloading"
    import urllib.request
    for url in _PLATE_MODEL_SOURCES:
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "IAS-CMS"})
            with urllib.request.urlopen(req, timeout=45) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(tmp) < 100 * 1024:  # فایل خیلی کوچک = خطا
                raise IOError("model file too small")
            os.replace(tmp, dest)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            continue
    return False


class PlateDetector:
    """تشخیص مستطیل پلاک با YOLO. available=False یعنی مدل/کتابخانه در دسترس
    نیست و load_error دلیل فارسی آن را توضیح می‌دهد (برای نمایش روی تایل)."""

    def __init__(self, model_path=None, conf=0.40):
        self.available = False
        self.load_error = ""
        self.conf = conf
        self.model = None
        self.model_source = ""  # از کجا لود شد (برای دیباگ)
        self.diag = {"ticks": 0, "boxes_total": 0, "detect_errors": 0}
        try:
            from ultralytics import YOLO
        except Exception:
            self.load_error = (
                "کتابخانه‌ی ultralytics داخل برنامه نیست؛ پلاک‌خوان غیرفعال است."
            )
            return
        path = model_path or _find_plate_model()
        if path:
            self.model_source = "bundled:" + os.path.basename(path)
        else:
            # تلاش برای دانلود خودکار (فقط یک‌بار برای هر نمونه)
            dest = os.path.join(_app_dir(), "plate_data", "models", "plate_detector.pt")
            if os.path.isfile(dest):
                path = dest
                self.model_source = "downloaded-cache"
            elif _download_plate_model(dest):
                path = dest
                self.model_source = "downloaded-now"
        if not path:
            self.load_error = (
                "فایل مدل پلاک‌خوان (plate_detector.pt) داخل برنامه نیست و "
                "دانلود خودکار هم موفق نبود (احتمالاً اینترنت/فیلترشکن لازم است). "
                "با بیلد جدید برنامه که مدل داخل آن است، درست می‌شود."
            )
            return
        try:
            self.model = YOLO(path)
            self.available = True
        except Exception as e:
            self.load_error = f"خطا در بارگذاری مدل پلاک: {e}"

    def detect(self, frame, conf=None):
        """خروجی: لیست [(x1, y1, x2, y2, conf), ...] به پیکسل (قالب xyxy).
        conf: آستانه‌ی اختیاری برای این فراخوانی (مثلاً کمتر وقتی روی ناحیه‌ی
        زوم کار می‌کنیم تا کاندیدای بیشتری بگیریم)؛ None یعنی self.conf."""
        self.diag["ticks"] += 1
        if not self.available:
            return []
        try:
            results = self.model.predict(
                frame, conf=self.conf if conf is None else conf,
                verbose=False)
            boxes = []
            for r in results:
                if r.boxes is None:
                    continue
                for b in r.boxes:
                    x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                    boxes.append((x1, y1, x2, y2, float(b.conf[0])))
            self.diag["boxes_total"] += len(boxes)
            return boxes
        except Exception:
            self.diag["detect_errors"] += 1
            return []


# نمونه‌ی مشترک بین دوربین‌ها (مثل person_detector) + کش خطا
_PLATE_DETECTOR = None
_PLATE_DETECTOR_NEXT_RETRY = 0.0
# شکست لود مدل دائمی نیست: هر ۱۸۰ ثانیه یک‌بار دوباره تلاش می‌شود تا
# خطای گذرا (مثلاً فشار حافظه موقع استارت) کل سشن را از کار نیندازد.
_PLATE_DETECTOR_RETRY_S = 180.0
_PLATE_DETECTOR_LOCK = threading.Lock()


def get_shared_plate_detector():
    """نمونه‌ی مشترک؛ اگر مدل در دسترس نباشد None برمی‌گرداند (نه نمونه‌ی خراب).
    نمونه‌ی ناموفق هم کش می‌شود تا load_error آن برای دیاگ در دسترس باشد."""
    global _PLATE_DETECTOR, _PLATE_DETECTOR_NEXT_RETRY
    with _PLATE_DETECTOR_LOCK:
        d = _PLATE_DETECTOR
        if d is not None and d.available:
            return d
        now = time.monotonic()
        if now < _PLATE_DETECTOR_NEXT_RETRY:
            return None
        _PLATE_DETECTOR_NEXT_RETRY = now + _PLATE_DETECTOR_RETRY_S
        try:
            d = PlateDetector()
            _PLATE_DETECTOR = d
        except Exception:
            _PLATE_DETECTOR = None
            return None
        return d if d.available else None


def get_plate_detector_load_error():
    """متن خطای آخرین تلاش لود مدل YOLO (برای دیاگ)؛ خالی یعنی خطایی ثبت نشده."""
    try:
        d = _PLATE_DETECTOR
        return (getattr(d, "load_error", "") or "")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# ۲) پیش‌پردازش کراپ + اصلاح پرسپکتیو
# --------------------------------------------------------------------------

# آستانه‌های استاندارد گیت تاری (واریانس لاپلاسین) — مطابق مقالات ANPR:
_BLUR_HEAVY = 25.0    # کمتر از این: خیلی تار → OCR بی‌فایده است، رد می‌شود
_BLUR_MILD = 100.0    # بین این دو: کمی تار → فیلتر bilateral
# ترکِ در حال حرکت: موشن‌بلر ذاتاً بیشتر است؛ گیت تاری بازتر می‌شود تا پلاکِ
# خودروی متحرک شانس OCR بگیرد (خوانش نامعتبر باز هم در رأی‌گیری رد می‌شود).
_BLUR_HEAVY_MOVING = 12.0


def _blur_score(gray):
    """واریانس لاپلاسین؛ هرچه کمتر، تارتر."""
    if cv2 is None:
        return 999.0
    try:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return 999.0


def _order_quad(pts):
    """مرتب‌سازی ۴ نقطه به ترتیب: بالا-چپ، بالا-راست، پایین-راست، پایین-چپ."""
    pts = np.array(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def _rectify_plate(crop):
    """اصلاح پرسپکتیو: بزرگ‌ترین چهارگوش داخل کراپ (بدنه‌ی پلاک) پیدا و به
    مستطیل روبه‌رو warp می‌شود. در صورت شکست، همان کراپ برمی‌گردد.
    خروجی: (image, rectified: bool)."""
    if cv2 is None:
        return crop, False
    try:
        h, w = crop.shape[:2]
        if h < 10 or w < 10:
            return crop, False
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return crop, False
        # بزرگ‌ترین کانتورها را امتحان کن تا یکی چهارگوش معتبر بدهد
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
        crop_area = float(w * h)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < crop_area * 0.30:  # خیلی کوچک = نویز/متن داخلی
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4:
                continue
            quad = _order_quad(approx.reshape(4, 2))
            (tl, tr, br, bl) = quad
            w_top = np.linalg.norm(tr - tl)
            w_bot = np.linalg.norm(br - bl)
            h_l = np.linalg.norm(bl - tl)
            h_r = np.linalg.norm(br - tr)
            dst_w = int(max(w_top, w_bot))
            dst_h = int(max(h_l, h_r))
            if dst_w < 20 or dst_h < 8:
                continue
            # نسبت ابعاد معقول پلاک (مستطیل کشیده یا مربعی موتورسیکلت)
            aspect = dst_w / float(dst_h)
            if not (1.2 <= aspect <= 8.0):
                continue
            dst = np.array([[0, 0], [dst_w - 1, 0],
                            [dst_w - 1, dst_h - 1], [0, dst_h - 1]],
                           dtype=np.float32)
            m = cv2.getPerspectiveTransform(quad, dst)
            warped = cv2.warpPerspective(crop, m, (dst_w, dst_h),
                                        flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_REPLICATE)
            return warped, True
        return crop, False
    except Exception:
        return crop, False


def _preprocess_for_ocr(crop, blur_heavy=None):
    """آماده‌سازی کراپ پلاک برای OCR. خروجی: (image, info) که info شامل
    blur_score و rectified و skipped_reason است.
    blur_heavy: آستانه‌ی گیت تاری (پیش‌فرض _BLUR_HEAVY)؛ برای ترکِ متحرک
    بازتر پاس داده می‌شود."""
    _bh = float(blur_heavy) if blur_heavy else _BLUR_HEAVY
    info = {"blur": -1.0, "rectified": False, "skipped": ""}
    h, w = crop.shape[:2]
    if h <= 0 or w <= 0:
        info["skipped"] = "empty"
        return crop, info
    if cv2 is None:
        return crop, info
    # بزرگ‌نمایی کراپ‌های کوچک (متن ریز بهتر خوانده می‌شود)
    if h < 128:
        scale = 128.0 / h
        crop = cv2.resize(crop, (int(w * scale), 128),
                          interpolation=cv2.INTER_CUBIC)
        h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    info["blur"] = _blur_score(gray)
    if info["blur"] < _bh:
        # خیلی تار: OCR فقط نویز تولید می‌کند
        info["skipped"] = "blur"
        return crop, info
    if info["blur"] < _BLUR_MILD:
        try:
            gray = cv2.bilateralFilter(gray, 9, 75, 75)
        except Exception:
            pass
    # CLAHE: کنتراست بهتر زیر نورهای مختلف
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    except Exception:
        pass
    crop = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    # اصلاح پرسپکتیو (اگر چهارگوش پلاک پیدا شود)
    crop, info["rectified"] = _rectify_plate(crop)
    # حاشیه‌ی روشن دور کراپ (به تشخیص مرز متن کمک می‌کند)
    try:
        crop = cv2.copyMakeBorder(crop, 8, 8, 8, 8,
                                  cv2.BORDER_CONSTANT, value=(255, 255, 255))
    except Exception:
        pass
    return crop, info


# --------------------------------------------------------------------------
# ۳-الف) موتور هزار: hezarai/crnn-fa-license-plate-recognition-v2 (ONNX)
# --------------------------------------------------------------------------
# مدل CRNN مخصوص پلاک فارسی؛ نسخه‌ی ONNX از روی وزن‌های PyTorch با معماری
# دقیقاً یکسان ساخته شده (بدون torch در زمان اجرا — فقط onnxruntime).
# پیش‌پردازش و پس‌پردازش دقیقاً مطابق image processor خود مدل است:
# خاکستری → آینه‌ی افقی → تغییراندازه به ‎(384, 32)‎ → نرمال‌سازی،
# و دیکد CTC حریصانه + برگرداندن سگمنت‌های رقمی (reverse_string_digits).

_HEZAR_ID2LABEL = [
    "", "آ", "ا", "ب", "پ", "ت", "ث", "ج", "چ", "ه", "خ", "د", "ذ",
    "ر", "ز", "ژ", "س", "ش", "ص", "ض", "ط", "ظ", "ع", "غ", "ف",
    "ق", "ک", "گ", "ل", "م", "ن", "و", "ه", "ی", " ",
    "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹", "۰",
]
_HEZAR_BLANK_ID = 0
_HEZAR_IMG_W, _HEZAR_IMG_H = 384, 32
_HEZAR_MEAN, _HEZAR_STD = 0.6595, 0.1501


def _hezar_preprocess(crop_bgr):
    """کراپ BGR → تنسور (1,1,32,384) دقیقاً مطابق پیش‌پردازش مدل هزار."""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.flip(gray, 1)  # mirror افقی (مثل image processor هزار)
    gray = cv2.resize(gray, (_HEZAR_IMG_W, _HEZAR_IMG_H),
                      interpolation=cv2.INTER_LINEAR)
    arr = gray.astype(np.float32) * (1.0 / 255.0)
    arr = (arr - _HEZAR_MEAN) / _HEZAR_STD
    return arr[None, None, :, :].astype(np.float32)


def _hezar_ctc_decode(logits):
    """دیکد حریصانه‌ی CTC روی خروجی (T,B,C)؛ خروجی: لیست لیست شناسه‌ها."""
    labels = np.argmax(logits, axis=-1)  # (T, B)
    out = []
    for b in range(labels.shape[1]):
        col = labels[:, b]
        merged, prev = [], -1
        for v in col:
            v = int(v)
            if v != prev:
                merged.append(v)
                prev = v
        out.append([v for v in merged if v != _HEZAR_BLANK_ID])
    return out


def _reverse_string_digits(text):
    r"""برگرداندن سگمنت‌های رقمی — دقیقاً معادل reverse_string_digits هزار.
    (در re پایتون، ‎\d‎ ارقام یونیکد مثل ۱۲۳ را هم می‌گیرد.)"""
    import re
    return re.sub(r"(\d+(?:\D\d+)*)", lambda m: m.group(1)[::-1], text)


# --------------------------------------------------------------------------
# ۳-ب) پس‌پردازش متن: اصلاح اشتباه‌های OCR + اعتبارسنجی قالب ایرانی
# --------------------------------------------------------------------------

# اشتباه‌های رایج OCR در «جایگاه رقم» (وقتی حرف لاتین به‌جای رقم خوانده شده)
_DIGIT_CONFUSIONS = {
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "I": "1", "i": "1", "l": "1", "|": "1", "L": "1",
    "Z": "2", "z": "2",
    "A": "4",
    "S": "5", "s": "5",
    "G": "6", "b": "6",
    "B": "8",
    "q": "9", "g": "9",
}

_IRAN_LETTERS = set(IRANIAN_PLATE_LETTERS) if IRANIAN_PLATE_LETTERS else set()


def _correct_confusions(text):
    """اصلاح اشتباه‌های رایج OCR در جایگاه ارقام."""
    return "".join(_DIGIT_CONFUSIONS.get(ch, ch) for ch in text)


def _looks_like_plate(text):
    """آیا متن به قالب پلاک ایرانی می‌خورد؟
    خودرو: ۲ رقم + ۱ حرف معتبر + ۵ رقم (کانونیکال ۸ کاراکتری مثل «12ب34567»)
    موتورسیکلت: ۳-۴ رقم + ۱-۲ حرف معتبر."""
    if not text:
        return False
    import re
    m = re.match(r"^([0-9]{2})([^0-9])([0-9]{5})$", text)
    if m and (not _IRAN_LETTERS or m.group(2) in _IRAN_LETTERS):
        return True
    m2 = re.match(r"^([0-9]{3,4})([^0-9]{1,2})$", text)
    if m2 and (not _IRAN_LETTERS
               or all(ch in _IRAN_LETTERS for ch in m2.group(2))):
        return True
    return False


def canonicalize_ocr_text(raw):
    """متن خام OCR → فرم کانونیکال تمیزشده (یا «» اگر نامعتبر).
    ترتیب: نرمال‌سازی ارقام → اصلاح اشتباه‌ها → حذف نویز → اعتبارسنجی."""
    t = normalize_plate_text(raw)
    if not t:
        return ""
    t = _correct_confusions(t)
    # فقط ارقام و حروف فارسی نگه دار (نویز مثل - * . حذف می‌شود)
    t = "".join(ch for ch in t if ch.isdigit() or "\u0600" <= ch <= "\u06FF")
    if not _looks_like_plate(t):
        return ""
    return t


# --------------------------------------------------------------------------
# ۴) OCR چندموتوره
# --------------------------------------------------------------------------

def ocr_install_status():
    """وضعیت نصب موتور OCR *بدون* بارگذاری سنگین: خروجی (hezar_installed,)
    که یعنی onnxruntime نصب است و فایل مدل هزار هم در دسترس است."""
    import importlib.util
    hezar = (importlib.util.find_spec("onnxruntime") is not None
             and hezar_model_bundled())
    return (hezar,)


class PlateOCR:
    """خوانش متن پلاک از تصویر کراپ‌شده با موتور hezar-crnn-fa
    (مخصوص پلاک ایرانی، ONNX). موتور فقط یک‌بار و تنبل بارگذاری می‌شود."""

    def __init__(self):
        self._hezar_session = None
        self._hezar_error = ""
        # شکست لود دائمی نیست: اگر بالا نیامد، هر ۱۲۰ ثانیه یک‌بار دوباره
        # تلاش می‌شود (روی سیستم ضعیف، تلاش اول ممکن است به‌خاطر فشار حافظه
        # موقع استارت برنامه شکست بخورد و نباید کل سشن را از کار بیندازد).
        self._hezar_next_retry = 0.0
        self._lock = threading.Lock()
        self.diag = {"ocr_calls": 0, "ocr_empty": 0, "ocr_skipped_blur": 0,
                     "rectified": 0, "rect_fallback": 0}

    # --------------------------------------------------------------- hezar -
    @property
    def engine_name(self):
        """نام موتور OCR فعالی که واقعاً بارگذاری شده (برای نمایش/دیباگ)."""
        if getattr(self, "_hezar_session", None) is not None:
            return "hezar(crnn-fa)"
        return "none"

    def _get_hezar(self):
        """موتور اصلی پلاک‌خوان: hezarai/crnn-fa-license-plate-recognition-v2
        به‌صورت ONNX (تبدیل‌شده از مدل PyTorch؛ معماری و وزن‌ها دقیقاً همان).
        فقط onnxruntime لازم دارد (بدون torch)؛ فایل مدل از پوشه‌ی
        plate_ocr_models داخل باندل لود می‌شود و هیچ‌وقت چیزی روی سیستم
        کاربر دانلود نمی‌شود."""
        with self._lock:
            if self._hezar_session is not None:
                return self._hezar_session
            now = time.monotonic()
            if now < self._hezar_next_retry:
                return None
            self._hezar_next_retry = now + 120.0
            try:
                import onnxruntime as ort
            except Exception as e:
                self._hezar_error = ("onnxruntime نصب/باندل نیست: %s" % e)[:300]
                self._hezar_session = None
                return None
            try:
                base = _plate_ocr_models_dir()
                p = os.path.join(base, "hezar_plate_v2.onnx") if base else ""
                if not p or not os.path.isfile(p):
                    raise FileNotFoundError(
                        "فایل hezar_plate_v2.onnx در plate_ocr_models باندل نیست.")
                opts = ort.SessionOptions()
                try:
                    opts.intra_op_num_threads = 2
                    opts.inter_op_num_threads = 1
                except Exception:
                    pass
                self._hezar_session = ort.InferenceSession(
                    p, sess_options=opts, providers=["CPUExecutionProvider"])
                self._hezar_error = ""
                print("[plate_ocr] hezar CRNN فارسی (ONNX) لود شد.")
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, e)
                self._hezar_error = err[:300]
                print(f"[plate_ocr] موتور هزار در دسترس نیست: {err}")
                self._hezar_session = None
            return self._hezar_session

    def _read_hezar(self, crop):
        sess = self._get_hezar()
        if sess is None or cv2 is None:
            return []
        try:
            x = _hezar_preprocess(crop)
            logits = sess.run(None, {"pixel_values": x})[0]  # (T,1,45)
            ids = _hezar_ctc_decode(logits)[0]
            text = "".join(_HEZAR_ID2LABEL[i] for i in ids)
            text = _reverse_string_digits(text)
            try:
                conf = float(np.exp(logits).max(axis=-1)[:, 0].mean())
            except Exception:
                conf = 0.5
            c = canonicalize_ocr_text(text)
            if c:
                return [(c, conf, "hezar-crnn-fa")]
            return []
        except Exception:
            return []

    def available(self):
        """True اگر موتور OCR هزار آماده باشد."""
        return self._get_hezar() is not None

    def engines_status(self):
        return {
            "hezar_crnn_fa": self._get_hezar() is not None,
            "model_bundled": hezar_model_bundled(),
            "hezar_error": self._hezar_error,
        }

    def read(self, crop_bgr, blur_heavy=None):
        """خوانش متن از کراپ پلاک. خروجی: لیست [(text, conf, engine)] مرتب
        بر اساس اطمینان (نزولی)، بدون تکراری. فقط متن‌های با قالب معتبر
        پلاک ایرانی برمی‌گرداند.
        blur_heavy: آستانه‌ی گیت تاری؛ برای کراپِ ترکِ متحرک بازتر."""
        if crop_bgr is None or crop_bgr.size == 0:
            return []
        self.diag["ocr_calls"] += 1
        crop, info = _preprocess_for_ocr(crop_bgr, blur_heavy=blur_heavy)
        if info.get("rectified"):
            self.diag["rectified"] += 1
        else:
            self.diag["rect_fallback"] += 1
        if info.get("skipped") == "blur":
            self.diag["ocr_skipped_blur"] += 1
            return []
        candidates = []
        # موتور هزار (CRNN مخصوص پلاک فارسی)؛ کراپ خام (بدون حاشیه/CLAHE)
        # می‌گیرد چون دقیقاً با همان پیش‌پردازش آموزش دیده است
        candidates.extend(self._read_hezar(crop_bgr))
        # حذف تکراری‌ها (نگه‌داشتن بالاترین اطمینان برای هر متن)
        best = {}
        for text, conf, engine in candidates:
            if text not in best or conf > best[text][0]:
                best[text] = (conf, engine)
        ranked = sorted(
            [(t, c, e) for t, (c, e) in best.items()],
            key=lambda x: x[1], reverse=True)
        if not ranked:
            self.diag["ocr_empty"] += 1
        return ranked

    def read_plate_from_view(self, crop_bgr, max_width=640):
        """خوانش فوری پلاک از کل نما (مسیر جایگزین وقتی دتکتور YOLO پلاکی
        پیدا نکرد؛ مثلاً وقتی کاربر روی پلاک زوم کرده و کل نما عملاً خود
        پلاک است). برای سرعت، نما تا max_width کوچک می‌شود (متن پلاک در
        حالت زوم به‌اندازه‌ی کافی بزرگ است)، بعد یک‌جا OCR می‌شود و اولین
        متنی که قالب پلاک ایرانی داشته باشد برگردانده می‌شود.
        خروجی: (text, conf) یا None."""
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        try:
            h, w = crop_bgr.shape[:2]
            if w > max_width and cv2 is not None:
                _s = max_width / float(w)
                crop_bgr = cv2.resize(crop_bgr, (max_width, max(1, int(h * _s))))
        except Exception:
            pass
        try:
            reads = self.read(crop_bgr)
        except Exception:
            return None
        for text, conf, _engine in reads:
            if _looks_like_plate(text):
                self.diag["fallback_hits"] = self.diag.get("fallback_hits", 0) + 1
                return text, float(conf)
        return None


_OCR_SINGLETON = None
_OCR_LOCK = threading.Lock()


def get_shared_plate_ocr():
    global _OCR_SINGLETON
    with _OCR_LOCK:
        if _OCR_SINGLETON is None:
            _OCR_SINGLETON = PlateOCR()
        return _OCR_SINGLETON


# --------------------------------------------------------------------------
# ۵) رأی‌گیری اکثریت به‌ازای هر موقعیت کاراکتر + ردیاب چندفریمی
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


def _shift_box(box, dx, dy):
    """جابه‌جایی باکس با بردار (dx, dy) — برای پیش‌بینی موقعیت ترکِ متحرک."""
    x1, y1, x2, y2 = box
    return [x1 + dx, y1 + dy, x2 + dx, y2 + dy]


def _center_close(a, b, ratio=2.0):
    """آیا مرکز b به‌اندازه‌ی کافی به مرکز a نزدیک است که همان شیءِ
    در حال حرکتِ سریع باشد؟ (وقتی جابه‌جایی بین دو تیکِ کم‌تکرار، IoU را
    صفر می‌کند ولی مرکز هنوز در همسایگی است.)"""
    acx, acy = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    bcx, bcy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    aw, ah = max(1.0, a[2] - a[0]), max(1.0, a[3] - a[1])
    bw, bh = max(1.0, b[2] - b[0]), max(1.0, b[3] - b[1])
    dist = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
    if dist > ratio * (aw + bw) / 2.0:
        return False
    area_ratio = (aw * ah) / max(1.0, bw * bh)
    return 0.4 <= area_ratio <= 2.5


def _expand_box(box, frame_w, frame_h, ratio=0.12):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    dx, dy = int(w * ratio), int(h * ratio)
    return (max(0, x1 - dx), max(0, y1 - dy),
            min(frame_w, x2 + dx), min(frame_h, y2 + dy))


def majority_vote(reads):
    """رأی‌گیری اکثریت به‌ازای هر موقعیت کاراکتر (تکنیک استاندارد ANPR).

    reads: لیست [(text, conf)] — فقط متن‌های کانونیکال معتبر.
    خروجی: (voted_text, confidence, votes) که votes تعداد خوانش‌های هم‌طول
    شرکت‌کننده در رأی است؛ اگر رأی معتبری نباشد (None, 0.0, 0)."""
    if not reads:
        return None, 0.0, 0
    # گروه‌بندی بر اساس طول؛ طولِ دارای بیشترین خوانش مبناست
    by_len = {}
    for text, conf in reads:
        by_len.setdefault(len(text), []).append((text, conf))
    best_len = max(by_len, key=lambda L: len(by_len[L]))
    group = by_len[best_len]
    if len(group) < 2:
        return None, 0.0, 0  # برای رأی‌گیری حداقل ۲ خوانش لازم است
    chars = []
    agree_sum = 0.0
    for i in range(best_len):
        counter = {}
        for text, _conf in group:
            counter[text[i]] = counter.get(text[i], 0) + 1
        ch = max(counter, key=lambda c: counter[c])
        chars.append(ch)
        agree_sum += counter[ch] / len(group)
    voted = "".join(chars)
    if not _looks_like_plate(voted):
        return None, 0.0, 0
    avg_conf = sum(c for _, c in group) / len(group)
    confidence = avg_conf * (agree_sum / best_len)
    return voted, confidence, len(group)


class PlateTracker:
    """ردیابی هر پلاک در فریم‌های متوالی (نمونه‌ی جدا برای هر دوربین).

    - هر باکس تازه با IoU حریصانه به نزدیک‌ترین ترک موجود وصل می‌شود؛
      برای ترکِ متحرک، باکس با سرعتِ برآوردشده به لحظه‌ی فعلی جلو برده
      می‌شود (پیش‌بینی) و اگر IoU ناکافی بود ولی مرکز نزدیک بود، باز هم
      لینک می‌شود تا ترکِ پلاکِ در حال حرکت بین تیک‌های کم‌تکرار نشکند.
    - OCR حداکثر هر ~۱ ثانیه برای هر ترک اجرا می‌شود (صرفه‌جویی CPU)؛
      برای ترکِ متحرک هر ~۰٫۵ ثانیه و با گیت تاریِ بازتر.
    - متن نهایی با رأی‌گیری اکثریت به‌ازای هر موقعیت کاراکتر ساخته می‌شود؛
      وقتی تعداد خوانش‌های شرکت‌کننده به confirm_reads برسد، «تأیید» و رویداد
      صادر می‌شود (برای ترکِ متحرک: ۲ خوانش کافی است)؛ برای همان پلاک تا
      پایان کول‌داون رویداد تکراری صادر نمی‌شود.
    """

    def __init__(self, confirm_reads=3, ocr_interval_s=1.0,
                 track_ttl_s=25.0, cooldown_s=45.0):
        self.confirm_reads = max(2, int(confirm_reads))
        self.ocr_interval_s = ocr_interval_s
        self.track_ttl_s = track_ttl_s
        self.cooldown_s = cooldown_s
        # حالت «زوم‌بوست»: وقتی کاربر روی ناحیه‌ی پلاک زوم کرده، خوانش
        # مشتاق‌تر می‌شود — OCR زودتر تکرار و تأیید با رأی کمتر صادر می‌شود،
        # چون کاربر عمداً همان ناحیه را برای خواندن انتخاب کرده است.
        self.zoom_boost = False
        self._tracks = []  # dict(box, reads, last_seen, last_ocr_ts, last_event_ts, ...)
        self._lock = threading.Lock()
        self.diag = {"ticks": 0, "detections_total": 0, "tracks_created": 0,
                     "ocr_runs": 0, "reads_total": 0, "reads_rejected": 0,
                     "votes_cast": 0, "events": 0, "cooldown_skips": 0}

    def update(self, detections, frame, ocr):
        """detections: [(x1,y1,x2,y2,conf)]. خروجی: لیست رویدادهای تازه‌ی
        تأییدشده‌ی [(box, text, conf)]."""
        now = time.time()
        h, w = frame.shape[:2]
        events = []
        with self._lock:
            self.diag["ticks"] += 1
            self.diag["detections_total"] += len(detections)
            # ۱) تطبیق دتکشن‌ها به ترک‌ها — آگاه از حرکت:
            # باکس هر ترک با سرعتِ برآوردشده به لحظه‌ی فعلی جلو برده می‌شود
            # (پیش‌بینی) و IoU با باکسِ پیش‌بینی‌شده حساب می‌شود؛ اگر IoU
            # ناکافی بود ولی مرکز دتکشن در همسایگی مرکز پیش‌بینی‌شده بود
            # (جابه‌جایی سریع بین دو تیکِ کم‌تکرار)، باز هم لینک می‌شود تا
            # ترکِ پلاکِ متحرک نشکند.
            unmatched = list(detections)
            for tr in self._tracks:
                _dt = min(max(now - tr["last_seen"], 0.0), 5.0)
                _vx, _vy = tr.get("vel", (0.0, 0.0))
                _pb = _shift_box(tr["box"][:4], _vx * _dt, _vy * _dt)
                best, best_score, best_idx = None, -1.0, -1
                for i, det in enumerate(unmatched):
                    _iou_v = _iou(_pb, det[:4])
                    if _iou_v >= 0.30:
                        _score = _iou_v
                    elif _center_close(_pb, det[:4]):
                        _score = 0.15  # لینک اضطراریِ حرکت سریع
                    else:
                        continue
                    if _score > best_score:
                        best, best_score, best_idx = det, _score, i
                if best is not None:
                    # به‌روزرسانی سرعت از جابه‌جایی مرکز (میانگین نمایی)
                    _ocx = (tr["box"][0] + tr["box"][2]) / 2.0
                    _ocy = (tr["box"][1] + tr["box"][3]) / 2.0
                    _ncx = (best[0] + best[2]) / 2.0
                    _ncy = (best[1] + best[3]) / 2.0
                    _bw = max(1.0, tr["box"][2] - tr["box"][0])
                    if _dt > 1e-3:
                        _mvx, _mvy = (_ncx - _ocx) / _dt, (_ncy - _ocy) / _dt
                        tr["vel"] = (0.65 * _vx + 0.35 * _mvx,
                                     0.65 * _vy + 0.35 * _mvy)
                    _disp = ((_ncx - _ocx) ** 2 + (_ncy - _ocy) ** 2) ** 0.5
                    tr["moving"] = _disp > 0.30 * _bw
                    tr["box"] = best
                    tr["last_seen"] = now
                    unmatched.pop(best_idx)
            # ۲) ترک تازه برای دتکشن‌های بی‌صاحب
            for det in unmatched:
                self._tracks.append({
                    "box": det, "reads": [], "last_seen": now,
                    "last_ocr_ts": 0.0, "last_event_ts": 0.0,
                    "last_text": "", "last_conf": 0.0, "voted_text": "",
                    "vel": (0.0, 0.0), "moving": False,
                })
                self.diag["tracks_created"] += 1
            # ۳) حذف ترک‌های منقضی
            self._tracks = [t for t in self._tracks
                            if now - t["last_seen"] <= self.track_ttl_s]
            # ۴) OCR تنبل + رأی‌گیری — آگاه از حرکت:
            # ترکِ متحرک فقط چند تیک دیده می‌شود؛ پس OCR زودتر تکرار، تأیید
            # با رأی کمتر صادر و گیت تاری بازتر می‌شود. (در حالت زوم‌بوست
            # هم همین رفتار مشتاقانه اعمال می‌شود.)
            for tr in self._tracks:
                _moving = bool(tr.get("moving"))
                _is_new = not tr["reads"]
                # ترکِ تازه هم مشتاق است: پلاکی که فقط یک تیک دیده می‌شود
                # (خودروی تندگذر) باید همان‌جا شانس تأیید داشته باشد.
                _eager = self.zoom_boost or _moving or _is_new
                _ocr_gap = 0.4 if _eager else self.ocr_interval_s
                _need_votes = 2 if _eager else self.confirm_reads
                _blur_gate = _BLUR_HEAVY_MOVING if _moving else None
                if now - tr["last_ocr_ts"] < _ocr_gap:
                    continue
                tr["last_ocr_ts"] = now
                x1, y1, x2, y2 = _expand_box(tr["box"][:4], w, h)
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                try:
                    reads = ocr.read(crop, blur_heavy=_blur_gate)
                    if _is_new and reads:
                        # خوانش دوم با کراپِ کمی بازتر: اگر هر دو خوانش
                        # معتبر و هم‌طول باشند، همان تیک اول ۲ رأی دارد و
                        # پلاکِ تک‌تیکیِ متحرک هم تأیید می‌شود.
                        x1b, y1b, x2b, y2b = _expand_box(
                            tr["box"][:4], w, h, ratio=0.22)
                        crop2 = frame[y1b:y2b, x1b:x2b]
                        if crop2.size > 0:
                            try:
                                reads = reads + ocr.read(
                                    crop2, blur_heavy=_blur_gate)
                            except Exception:
                                pass
                except Exception:
                    reads = []
                self.diag["ocr_runs"] += 1
                if not reads:
                    continue
                # همه‌ی خوانش‌های معتبر وارد رأی‌گیری می‌شوند (نه فقط بهترین)
                _added = 0
                for _rt, _rc, _re in reads:
                    if not _looks_like_plate(_rt):
                        self.diag["reads_rejected"] += 1
                        continue
                    tr["reads"].append((_rt, float(_rc)))
                    _added += 1
                tr["reads"] = tr["reads"][-10:]
                if not _added:
                    continue
                self.diag["reads_total"] += _added
                text, conf = tr["reads"][-1]
                tr["last_text"] = text
                tr["last_conf"] = float(conf)
                # ۵) رأی‌گیری اکثریت به‌ازای هر موقعیت کاراکتر
                voted, vconf, votes = majority_vote(tr["reads"])
                if voted is None:
                    continue
                self.diag["votes_cast"] += 1
                tr["voted_text"] = voted
                if votes >= _need_votes:
                    if now - tr["last_event_ts"] >= self.cooldown_s:
                        tr["last_event_ts"] = now
                        tr["reads"] = []  # شروع تازه برای رأی بعدی
                        self.diag["events"] += 1
                        events.append((tr["box"][:4], voted, vconf))
                    else:
                        self.diag["cooldown_skips"] += 1
        return events

    def current_tracks(self):
        """وضعیت فعلی ترک‌ها برای رسم روی تصویر: [(box, text, conf)]."""
        now = time.time()
        with self._lock:
            return [(t["box"][:4], t["voted_text"] or t["last_text"],
                     t["last_conf"])
                    for t in self._tracks
                    if now - t["last_seen"] <= self.track_ttl_s]

    def diag_snapshot(self):
        """شمارنده‌های تشخیصی برای صفحه‌ی پلاک‌خوان و فایل لاگ."""
        with self._lock:
            d = dict(self.diag)
            now = time.time()
            d["tracks_active"] = sum(
                1 for t in self._tracks
                if now - t["last_seen"] <= self.track_ttl_s)
            return d


def reset_shared_plate_detector():
    """برای تست: کش نمونه‌ی مشترک را پاک می‌کند."""
    global _PLATE_DETECTOR, _PLATE_DETECTOR_NEXT_RETRY
    with _PLATE_DETECTOR_LOCK:
        _PLATE_DETECTOR = None
        _PLATE_DETECTOR_NEXT_RETRY = 0.0

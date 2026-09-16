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
       - easyocr با زبان فارسی ('fa') — مخصوص پلاک‌های ایرانی. مدل‌هایش در
         زمان بیلد پیش‌دانلود و داخل exe هستند (EASYOCR_MODULE_PATH)؛ پس
         در سیستم کاربر دانلودی انجام نمی‌شود.
       - rapidocr_onnxruntime — سبک و سریع، fallback.
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
torch، easyocr، rapidocr) در سطح ماژول import نمی‌شوند؛ همه داخل توابع و
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
    --add-dataها (مثل plate_detector.pt و easyocr_models) داخلش قرار
    می‌گیرند. در اجرای از سورس None برمی‌گرداند."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(meipass):
        return meipass
    return None


def _configure_easyocr_bundled_models():
    """اگر مدل‌های EasyOCR داخل باندل برنامه باشند (easyocr_models/model)،
    متغیر EASYOCR_MODULE_PATH را طوری تنظیم می‌کند که Reader همان‌ها را لود
    کند و در سیستم کاربر هیچ دانلودی انجام نشود."""
    if os.environ.get("EASYOCR_MODULE_PATH"):
        return
    app = _app_dir()
    cands = []
    bundle = _bundle_dir()
    if bundle:
        # exe فریزشده: مدل‌ها با --add-data داخل _internal باندل شده‌اند
        cands.append(os.path.join(bundle, "easyocr_models"))
    cands.extend((os.path.join(app, "easyocr_models"),
                  os.path.join(app, "plate_data", "easyocr_models")))
    for cand in cands:
        try:
            if os.path.isdir(os.path.join(cand, "model")):
                os.environ["EASYOCR_MODULE_PATH"] = cand
                break
        except Exception:
            pass


_configure_easyocr_bundled_models()


# --------------------------------------------------------------------------
# ۱) تشخیص ناحیه‌ی پلاک
# --------------------------------------------------------------------------

# مدل: joker5914/yolov8n-license-plate از HuggingFace - YOLOv8n فاین‌تیون‌شده
# روی دیتاست تشخیص پلاک خودرو؛ ~۶ مگابایت (سبک، مناسب CPU و ۴GB RAM)، فرمت
# ‎.pt‎ سازگار با ultralytics، دقت mAP50 ≈ ۰٫۹۸۳.
# نکته‌ی فنی: تشخیص «کادر» پلاک مستقل از کشور است و روی پلاک ایرانی هم جواب
# می‌دهد؛ خوانش حروف فارسی با OCR فارسی (EasyOCR fa) در همین ماژول انجام می‌شود.
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

    def detect(self, frame):
        """خروجی: لیست [(x1, y1, x2, y2, conf), ...] به پیکسل (قالب xyxy)."""
        self.diag["ticks"] += 1
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
            self.diag["boxes_total"] += len(boxes)
            return boxes
        except Exception:
            self.diag["detect_errors"] += 1
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
# ۲) پیش‌پردازش کراپ + اصلاح پرسپکتیو
# --------------------------------------------------------------------------

# آستانه‌های استاندارد گیت تاری (واریانس لاپلاسین) — مطابق مقالات ANPR:
_BLUR_HEAVY = 25.0    # کمتر از این: خیلی تار → OCR بی‌فایده است، رد می‌شود
_BLUR_MILD = 100.0    # بین این دو: کمی تار → فیلتر bilateral


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


def _preprocess_for_ocr(crop):
    """آماده‌سازی کراپ پلاک برای OCR. خروجی: (image, info) که info شامل
    blur_score و rectified و skipped_reason است."""
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
    if info["blur"] < _BLUR_HEAVY:
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
# ۳) پس‌پردازش متن: اصلاح اشتباه‌های OCR + اعتبارسنجی قالب ایرانی
# --------------------------------------------------------------------------

# حروف مجاز پلاک ایرانی + ارقام فارسی/عربی/لاتین؛ برای محدود کردن خروجی
# easyocr و کم شدن خروجی‌های بی‌ربط.
_PLATE_ALLOWLIST = ("ابپتثجچحخدرزژسشصضطظعغفقکگلمنوهی"
                    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩0123456789")

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
    """وضعیت نصب موتورهای OCR *بدون* بارگذاری سنگین:
    خروجی (easyocr_installed, rapidocr_installed)."""
    import importlib.util
    return (importlib.util.find_spec("easyocr") is not None,
            importlib.util.find_spec("rapidocr_onnxruntime") is not None)


def easyocr_models_bundled():
    """True اگر مدل‌های EasyOCR داخل باندل برنامه باشند (بدون نیاز به دانلود)."""
    mp = os.environ.get("EASYOCR_MODULE_PATH", "")
    try:
        return bool(mp) and os.path.isdir(os.path.join(mp, "model"))
    except Exception:
        return False


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
        self.diag = {"ocr_calls": 0, "ocr_empty": 0, "ocr_skipped_blur": 0,
                     "rectified": 0, "rect_fallback": 0}

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
                # مدل‌ها از EASYOCR_MODULE_PATH خوانده می‌شوند؛ در بیلد رسمی
                # این مسیر به easyocr_models داخل exe اشاره می‌کند و هیچ
                # دانلودی در سیستم کاربر انجام نمی‌شود.
                kwargs = {"verbose": False}
                mp = os.environ.get("EASYOCR_MODULE_PATH", "")
                if mp and os.path.isdir(mp):
                    kwargs["model_storage_directory"] = mp
                # فقط تشخیص متن (recognizer) روی کراپ کوچک؛ detector روی کراپ
                # لازم نیست ولی easyocr همیشه هر دو را لود می‌کند - برای همین
                # lazy است و فقط وقتی پلاک‌خوان فعال باشد.
                self._easyocr_reader = easyocr.Reader(["fa", "en"], gpu=False,
                                                      **kwargs)
            except Exception as e:
                print(f"[plate_ocr] easyocr در دسترس نیست: {e}")
                self._easyocr_reader = None
            return self._easyocr_reader

    def _read_easyocr(self, crop):
        reader = self._get_easyocr()
        if reader is None:
            return []
        try:
            # detail=1 -> [(box, text, conf)]؛ allowlist خروجی بی‌ربط را کم می‌کند
            results = reader.readtext(crop, detail=1, allowlist=_PLATE_ALLOWLIST)
            frags = []
            for _box, text, conf in results:
                t = canonicalize_ocr_text(text)
                if t:
                    frags.append((t, float(conf)))
            out = []
            for t, conf in frags:
                out.append((t, conf, "easyocr-fa"))
            # گاهی پلاک به چند تکه شکسته می‌شود؛ ترکیب همه‌ی تکه‌ها هم به‌عنوان
            # یک کاندیدا اضافه می‌شود (رأی‌گیری چندفریمی داوری نهایی را می‌کند)
            if len(frags) > 1:
                joined = canonicalize_ocr_text("".join(t for t, _ in frags))
                if joined:
                    avg = sum(c for _, c in frags) / len(frags)
                    out.append((joined, avg * 0.95, "easyocr-fa"))
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
                    t = canonicalize_ocr_text(text)
                    if t:
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
            "models_bundled": easyocr_models_bundled(),
        }

    def read(self, crop_bgr):
        """خوانش متن از کراپ پلاک. خروجی: لیست [(text, conf, engine)] مرتب
        بر اساس اطمینان (نزولی)، بدون تکراری. فقط متن‌های با قالب معتبر
        پلاک ایرانی برمی‌گرداند."""
        if crop_bgr is None or crop_bgr.size == 0:
            return []
        self.diag["ocr_calls"] += 1
        crop, info = _preprocess_for_ocr(crop_bgr)
        if info.get("rectified"):
            self.diag["rectified"] += 1
        else:
            self.diag["rect_fallback"] += 1
        if info.get("skipped") == "blur":
            self.diag["ocr_skipped_blur"] += 1
            return []
        candidates = []
        # اولویت با easyocr فارسی (پلاک ایرانی) است
        candidates.extend(self._read_easyocr(crop))
        # اگر easyocr چیزی نداد، rapidocr هم امتحان می‌شود
        if not candidates:
            candidates.extend(self._read_rapidocr(crop))
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

    - هر باکس تازه با IoU حریصانه به نزدیک‌ترین ترک موجود وصل می‌شود
      (ابزار استاندارد دوربین‌های ثابت).
    - OCR حداکثر هر ~۱ ثانیه برای هر ترک اجرا می‌شود (صرفه‌جویی CPU).
    - متن نهایی با رأی‌گیری اکثریت به‌ازای هر موقعیت کاراکتر ساخته می‌شود؛
      وقتی تعداد خوانش‌های شرکت‌کننده به confirm_reads برسد، «تأیید» و رویداد
      صادر می‌شود؛ برای همان پلاک تا پایان کول‌داون رویداد تکراری صادر نمی‌شود.
    """

    def __init__(self, confirm_reads=3, ocr_interval_s=1.0,
                 track_ttl_s=4.0, cooldown_s=45.0):
        self.confirm_reads = max(2, int(confirm_reads))
        self.ocr_interval_s = ocr_interval_s
        self.track_ttl_s = track_ttl_s
        self.cooldown_s = cooldown_s
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
            # ۱) تطبیق دتکشن‌ها به ترک‌ها (IoU حریصانه)
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
                    "last_text": "", "last_conf": 0.0, "voted_text": "",
                })
                self.diag["tracks_created"] += 1
            # ۳) حذف ترک‌های منقضی
            self._tracks = [t for t in self._tracks
                            if now - t["last_seen"] <= self.track_ttl_s]
            # ۴) OCR تنبل + رأی‌گیری
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
                self.diag["ocr_runs"] += 1
                if not reads:
                    continue
                text, conf = reads[0][0], reads[0][1]
                # فقط خوانش‌های با قالب معتبر وارد رأی‌گیری می‌شوند
                if not _looks_like_plate(text):
                    self.diag["reads_rejected"] += 1
                    continue
                tr["reads"].append((text, float(conf)))
                tr["reads"] = tr["reads"][-10:]
                self.diag["reads_total"] += 1
                tr["last_text"] = text
                tr["last_conf"] = float(conf)
                # ۵) رأی‌گیری اکثریت به‌ازای هر موقعیت کاراکتر
                voted, vconf, votes = majority_vote(tr["reads"])
                if voted is None:
                    continue
                self.diag["votes_cast"] += 1
                tr["voted_text"] = voted
                if votes >= self.confirm_reads:
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
    global _PLATE_DETECTOR, _PLATE_DETECTOR_TRIED
    with _PLATE_DETECTOR_LOCK:
        _PLATE_DETECTOR = None
        _PLATE_DETECTOR_TRIED = False

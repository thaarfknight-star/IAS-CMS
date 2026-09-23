# -*- coding: utf-8 -*-
"""ردیابی اشخاص — موتور tracking-by-detection به سبک ByteTrack/SORT.

معماری (الگوی استاندارد سیستم‌های ردیابی افراد در جهان):
  ۱) تشخیص (Detection): PersonDetector (YOLOv8n) هر دور تشخیص، باکس افراد را
     می‌دهد. خروجی آن با قالب (top, right, bottom, left) است و این ماژول
     همان‌جا آن را به قالب استاندارد [x1, y1, x2, y2] تبدیل و اعتبارسنجی
     می‌کند (رفع ریشه‌ای باگ «هیچ گزارشی ثبت نمی‌شود»: قبلاً باکس‌ها با
     قالب اشتباه خوانده می‌شدند، کراپ نامعتبر می‌شد و رویداد confirmed
     هرگز صادر نمی‌شد).
  ۲) ردیابی محلی (PersonLocalTracker): هر دوربین یک ردیاب دارد؛
     - برای هر رد یک فیلتر کالمن ساده‌شده (مدل سرعت ثابت، فقط numpy) باکس
       بعدی را «پیش‌بینی» می‌کند و تطبیق با باکس‌های تازه بر اساس IoU با
       «باکس پیش‌بینی‌شده» انجام می‌شود (نه باکس فریم قبل) — این همان ایده‌ی
       اصلی SORT/ByteTrack است و باعث می‌شود ردیابی روی تیک‌های کم‌تکرار
       تشخیص (CPU ضعیف) هم نپرد؛
     - تطبیق دومرحله‌ای حریصانه: اول ردهای فعال با IoU≥0.3، بعد ردهای
       «گمشده» با IoU≥0.25 برای بازپس‌گیری هویت (ایده‌ی مرحله‌ی دوم
       ByteTrack برای باکس‌های کم‌اطمینان/پوشیده‌شده)؛ IoU کمتر از ۰.۲
       همیشه رد می‌شود (عدد مقاله‌ی ByteTrack)؛
     - وضعیت ردها: tentative (تازه) ← confirmed (بعد از min_hits هیت)
       ← lost (چند تیک دیده نشده، تا max_age تیک نگه داشته می‌شود)
       ← removed. بازپس‌گیری رد گمشده، همان شناسه را حفظ می‌کند؛
     - اگر ساخت توصیف‌گر در لحظه‌ی تأیید ناموفق باشد، رد «تأییدشده‌ی
       بی‌صدا» نمی‌شود؛ در تیک‌های بعد دوباره تلاش می‌شود (رفع باگ دوم
       «هیچ گزارشی ثبت نمی‌شود»).
  ۳) توصیف‌گر ظاهری (describe_person): قوی‌تر ولی همچنان فقط numpy/cv2
     (بدون دانلود مدل جدید، CPUمحور برای ۴GB رم):
     - هیستوگرام HSV سه ناحیه‌ی بدن با «وزن مرکزی» (پیکسل‌های میانی بدن
       مهم‌ترند) تا نشت پس‌زمینه — یکی از علت‌های شناخته‌شده‌ی شکست
       هیستوگرام‌های ساده — کم شود؛
     - هیستوگرام جهت گرادیان (Sobel) هر ناحیه: بافتِ نامتغیر نسبت به نور،
       که ضعف دوم هیستوگرام رنگِ خالص در برابر تغییر نور دوربین‌هاست؛
     - بردار نهایی L2-نرمال می‌شود و فاصله‌ی بین دوربینی «کسینوسی» است
       (روش استاندارد ReID در صنعت، مثل آستانه‌ی ۰.۷۵ تشابه کسینوسی در
       سیستم‌های واقعی)؛ نام‌های فارسی رنگ‌ها برای نمایش حفظ شده‌اند.
  ۴) کمک چهره: خروجی آماده‌ی FaceEngine همان دور تشخیص (هزینه‌ی صفر) با
     باکس بدن تطبیق داده می‌شود؛ چهره‌ی شناخته‌شده در بانک چهره‌ها =
     تطبیق قطعی بین دوربین‌ها حتی با لباس عوض‌شده.
  ۵) تطبیق سراسری (GlobalPersonMatcher): امضای اشخاصِ اخیراً دیده‌شده با
     فاصله‌ی کسینوسی + پنجره‌ی زمانی؛ به‌روزرسانی میانگین متحرک امضاها.

نکته‌ی صادقانه: برای چهره‌های ناشناس این «بازشناسی هویتی» قطعی نیست؛ دو
نفر با ظاهر خیلی شبیه ممکن است یکی حساب شوند. چهره‌ی شناخته‌شده در بانک
چهره‌ها = تطبیق قطعی.
"""

import threading
import time

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# ابزار باکس: یکسان‌سازی قالب
# ---------------------------------------------------------------------------

def normalize_box(box, box_format="trbl"):
    """تبدیل باکس به قالب استاندارد [x1, y1, x2, y2] (float) + اعتبارسنجی.
    box_format: "trbl" یعنی (top, right, bottom, left) — خروجی PersonDetector
      و FaceEngine؛ یا "xyxy" یعنی (x1, y1, x2, y2).
    خروجی None اگر باکس نامعتبر/تبه‌گون باشد (مختصات منفی معکوس، مساحت صفر،
    NaN و...). این همان نقطه‌ای است که قبلاً باکس‌ها اشتباه خوانده می‌شدند.
    """
    try:
        vals = [float(v) for v in box]
        if len(vals) != 4 or any(not np.isfinite(v) for v in vals):
            return None
        if box_format == "trbl":
            top, right, bottom, left = vals
            x1, y1, x2, y2 = left, top, right, bottom
        else:
            x1, y1, x2, y2 = vals
        if x2 <= x1 or y2 <= y1:
            return None
        w, h = x2 - x1, y2 - y1
        if w < 4 or h < 8 or w > 100000 or h > 100000:
            return None
        return [x1, y1, x2, y2]
    except Exception:
        return None


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


# ---------------------------------------------------------------------------
# نام فارسی رنگ‌ها از روی HSV
# ---------------------------------------------------------------------------

_HUE_NAMES = [
    (8, "قرمز"), (22, "نارنجی"), (38, "زرد"), (80, "سبز"),
    (95, "فیروزه‌ای"), (125, "آبی"), (150, "بنفش"), (168, "صورتی"),
    (180, "قرمز"),
]


def hsv_to_persian_color(h, s, v):
    """یک پیکسل HSV (مقادیر OpenCV: H 0-179, S/V 0-255) را به نام فارسی رنگ
    تبدیل می‌کند. h/s/v می‌توانند میانگین/میانه‌ی یک ناحیه باشند."""
    h, s, v = float(h), float(s), float(v)
    if v < 60:
        return "مشکی"
    if s < 35:
        if v > 200:
            return "سفید"
        return "خاکستری"
    if 8 <= h < 25 and s < 130:
        return "قهوه‌ای" if v < 150 else "کرم"
    if v < 110:
        for bound, name in _HUE_NAMES:
            if h < bound:
                if name == "قرمز":
                    return "زرشکی"
                if name == "آبی":
                    return "سرمه‌ای"
                return name + " تیره"
    for bound, name in _HUE_NAMES:
        if h < bound:
            return name
    return "نامشخص"


def _region_median_hsv(region_bgr):
    """میانه‌ی HSV پیکسل‌های یک ناحیه (مقاوم در برابر نویز/سایه‌ی لحظه‌ای)."""
    if region_bgr is None or region_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2].ravel()
    lo, hi = np.percentile(v, [20, 90])
    mask = (v >= lo) & (v <= hi)
    if mask.sum() < 10:
        mask = np.ones_like(v, dtype=bool)
    h = np.median(hsv[:, :, 0].ravel()[mask])
    s = np.median(hsv[:, :, 1].ravel()[mask])
    vv = np.median(v[mask])
    return h, s, vv


# ---------------------------------------------------------------------------
# توصیف‌گر ظاهری شخص (نسخه‌ی ۲: وزن مرکزی + گرادیان + نرمال‌سازی L2)
# ---------------------------------------------------------------------------

_HEAD_FRac = (0.00, 0.18)     # سر
_UPPER_FRac = (0.18, 0.55)    # بالاتنه (لباس)
_LOWER_FRac = (0.55, 1.00)    # پایین‌تنه (شلوار)

_HSV_BINS = (16, 4, 4)        # H=16, S=4, V=4 -> ۲۵۶ ویژگی در هر ناحیه
_GRAD_BINS = 8                # هیستوگرام جهت گرادیان (۰ تا ۱۸۰ درجه)


def _center_weight_mask(h, w):
    """ماسک وزن مرکزی (گاوسی بیضوی): پیکسل‌های میانی بدن ~۱، لبه‌ها ~۰.۳۵.
    لبه‌های کراپ معمولاً پس‌زمینه‌اند؛ این وزن، «نشت پس‌زمینه» در هیستوگرام
    — علت اصلی شکست هیستوگرام‌های ساده — را کم می‌کند."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xx / max(1, w) - 0.5) * 2.0
    dy = (yy / max(1, h) - 0.5) * 2.0
    wgt = np.exp(-(dx * dx * 1.6 + dy * dy * 1.1))
    return np.clip(0.35 + 0.65 * wgt, 0.35, 1.0).astype(np.float32)


def _region_features(region_bgr):
    """ویژگی‌های یک ناحیه: هیستوگرام HSV وزن‌دار + هیستوگرام جهت گرادیان."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    wgt = _center_weight_mask(h, w)
    hh = hsv[:, :, 0].astype(np.float32)
    ss = hsv[:, :, 1].astype(np.float32)
    vv = hsv[:, :, 2].astype(np.float32)
    hb = np.clip((hh / 180.0 * _HSV_BINS[0]).astype(np.int32), 0, _HSV_BINS[0] - 1)
    sb = np.clip((ss / 256.0 * _HSV_BINS[1]).astype(np.int32), 0, _HSV_BINS[1] - 1)
    vb = np.clip((vv / 256.0 * _HSV_BINS[2]).astype(np.int32), 0, _HSV_BINS[2] - 1)
    idx = (hb * _HSV_BINS[1] + sb) * _HSV_BINS[2] + vb
    hist = np.bincount(idx.ravel(),
                       weights=wgt.ravel(),
                       minlength=_HSV_BINS[0] * _HSV_BINS[1] * _HSV_BINS[2])
    hist = hist.astype(np.float32)
    s = hist.sum()
    if s > 0:
        hist /= s
    # جهت گرادیان (بافت نامتغیر نسبت به نور) با وزن اندازه‌ی گرادیان
    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = (np.degrees(np.arctan2(gy, gx)) % 180.0)
    ab = np.clip((ang / 180.0 * _GRAD_BINS).astype(np.int32), 0, _GRAD_BINS - 1)
    ghist = np.bincount(ab.ravel(), weights=(mag * wgt).ravel(),
                        minlength=_GRAD_BINS).astype(np.float32)
    gs = ghist.sum()
    if gs > 0:
        ghist /= gs
    return np.concatenate([hist, ghist]).astype(np.float32)


def _split_regions(crop):
    h = crop.shape[0]
    regs = {}
    for name, (a, b) in (("head", _HEAD_FRac), ("upper", _UPPER_FRac),
                         ("lower", _LOWER_FRac)):
        y1, y2 = int(h * a), max(int(h * b), int(h * a) + 1)
        regs[name] = crop[y1:y2]
    return regs


def _estimate_hair(crop, regs):
    """رنگ و بلندی مو — هیوریستیک ساده: نیمه‌ی بالایی ناحیه‌ی سر «مو» فرض
    می‌شود. اگر پیکسل‌های تیره‌ی مومانند تا روی شانه ادامه داشته باشند،
    «بلند» وگرنه «کوتاه»."""
    head = regs["head"]
    hh = head.shape[0]
    if hh < 12:
        return "نامشخص", "نامشخص"
    hair_part = head[: max(1, int(hh * 0.55))]
    med = _region_median_hsv(hair_part)
    hair_color = hsv_to_persian_color(*med) if med else "نامشخص"
    upper = regs["upper"]
    ext_h = int(upper.shape[0] * 0.30)
    extended = np.vstack([head, upper[:ext_h]]) if ext_h > 0 else head
    hsv = cv2.cvtColor(extended, cv2.COLOR_BGR2HSV)
    dark = (hsv[:, :, 2] < 90).astype(np.float32)
    rows = dark.mean(axis=1)
    eh = len(rows)
    top_dark = rows[: int(eh * 0.5)].mean()
    bottom_dark = rows[int(eh * 0.5):].mean()
    if top_dark < 0.15:
        hair_len = "نامشخص"
    elif bottom_dark > 0.30 and bottom_dark > top_dark * 0.6:
        hair_len = "بلند"
    else:
        hair_len = "کوتاه"
    return hair_color, hair_len


def _l2norm(v):
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        return v.astype(np.float32)
    return (v / n).astype(np.float32)


def describe_person(crop_bgr):
    """از روی کراپ BGR بدن، توصیف‌گر ظاهری می‌سازد.
    خروجی: dict با کلیدهای vector (np.float32، L2-نرمال‌شده — برای فاصله‌ی
    کسینوسی بین دوربینی)، face_vector (امضای نرمال‌شده‌ی ناحیه‌ی سر)،
    shirt_color، pants_color، hair_color، hair_length، face_person_id،
    face_name (دو تای آخر هنگام تأیید رد، از تطبیق با نتایج FaceEngine
    پر می‌شوند)."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    crop = cv2.resize(crop_bgr, (64, 128))
    regs = _split_regions(crop)
    feats = []
    for name in ("head", "upper", "lower"):
        feats.append(_region_features(regs[name]))
    vector = _l2norm(np.concatenate(feats))
    # امضای چهره: ویژگی همان ناحیه‌ی سر، جداگانه نرمال‌شده (برای تطبیق
    # چهره‌های ناشناس بین دوربین‌ها در کنار ظاهر کلی).
    face_vector = _l2norm(feats[0].copy())

    med = _region_median_hsv(regs["upper"])
    shirt = hsv_to_persian_color(*med) if med else "نامشخص"
    med = _region_median_hsv(regs["lower"])
    pants = hsv_to_persian_color(*med) if med else "نامشخص"
    hair_color, hair_len = _estimate_hair(crop, regs)
    return {
        "vector": vector,
        "face_vector": face_vector,
        "shirt_color": shirt,
        "pants_color": pants,
        "hair_color": hair_color,
        "hair_length": hair_len,
        "face_person_id": "",
        "face_name": "",
    }


def cosine_distance(a, b):
    """فاصله‌ی کسینوسی ۰ تا ۲ بین دو بردار نرمال‌شده (کمتر = شبیه‌تر)؛
    معیار استاندارد ReID. برای بردارهای نرمال، همان ۱ − تشابه کسینوسی."""
    try:
        va = np.asarray(a, dtype=np.float32).ravel()
        vb = np.asarray(b, dtype=np.float32).ravel()
        if va.shape != vb.shape or va.size == 0:
            return 2.0
        na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
        if na < 1e-9 or nb < 1e-9:
            return 2.0
        sim = float(np.dot(va, vb) / (na * nb))
        sim = min(1.0, max(-1.0, sim))
        return float(1.0 - sim)
    except Exception:
        return 2.0


def appearance_distance(vec_a, vec_b):
    """فاصله‌ی ظاهری بین دو توصیف‌گر (کمتر = شبیه‌تر). نسخه‌ی ۲: فاصله‌ی
    کسینوسی روی بردار نرمال‌شده (سازگار با امضاهای قدیمیِ نرمال‌نشده هم
    هست چون داخل cosine_distance دوباره نرمال می‌شود)."""
    return cosine_distance(vec_a, vec_b)


def face_distance(fa, fb):
    """فاصله‌ی ۰ تا ۲ بین دو امضای چهره (کسینوسی؛ کمتر = شبیه‌تر)."""
    return cosine_distance(fa, fb)


def match_face_to_person(person_box_xyxy, face_results):
    """تطبیق چهره به باکس بدن.

    person_box_xyxy: [x1, y1, x2, y2] (قالب استاندارد).
    face_results: لیست {"box": (top, right, bottom, left), "person": dict|None}
      از خروجی FaceEngine (همان دور تشخیص).
    خروجی: (face_person_dict|None, face_box_xyxy|None) — چهره‌ای که مرکزش
    در نیمه‌ی بالایی باکس بدن باشد؛ در صورت چند کاندیدا، هم‌پوشانی بیشتر.
    """
    if not face_results:
        return None, None
    try:
        px1, py1, px2, py2 = (float(v) for v in person_box_xyxy)
    except Exception:
        return None, None
    pw, ph = px2 - px1, py2 - py1
    if pw <= 0 or ph <= 0:
        return None, None
    best, best_box, best_ov = None, None, 0.0
    for fr in face_results:
        try:
            box = fr.get("box")
            top, right, bottom, left = (float(v) for v in box)
        except Exception:
            continue
        fx1, fy1, fx2, fy2 = left, top, right, bottom
        if fx2 <= fx1 or fy2 <= fy1:
            continue
        fcx, fcy = (fx1 + fx2) / 2.0, (fy1 + fy2) / 2.0
        if not (px1 <= fcx <= px2 and py1 <= fcy <= py2):
            continue
        if fcy > py1 + 0.45 * ph:
            continue
        if (fx2 - fx1) > pw * 1.2:
            continue
        ix1, iy1 = max(px1, fx1), max(py1, fy1)
        ix2, iy2 = min(px2, fx2), min(py2, fy2)
        ov = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        if ov > best_ov:
            best, best_box, best_ov = (
                fr.get("person"), [fx1, fy1, fx2, fy2], ov)
    return best, best_box


# ---------------------------------------------------------------------------
# فیلتر کالمن ساده‌شده (مدل سرعت ثابت) — فقط numpy
# ---------------------------------------------------------------------------

class _KalmanBox:
    """پیش‌بینی موقعیت بعدی باکس با مدل «سرعت ثابت».

    حالت: [cx, cy, w, h, vx, vy, vw, vh]. در هر تیک تشخیص (نه هر فریم —
    چون تشخیص روی CPU کم‌تکرار است) با dt واقعیِ سپری‌شده پیش‌بینی می‌کند
    و با مشاهده‌ی تازه به‌روزرسانی می‌شود (بهره‌ی ثابت؛ نسخه‌ی سبک‌شده‌ی
    کالمن استاندارد SORT که برای چند شیء روی CPU ضعیف کافی و پایدار است).
    """

    _K_POS = 0.55   # بهره‌ی تصحیح مرکز/ابعاد با مشاهده‌ی تازه
    _K_VEL = 0.55   # بهره‌ی تصحیح سرعت (بالا نگه داشته شده تا بین تیک‌های
                    # کم‌تکرارِ CPU، سرعتِ شخصِ در حال حرکت زود برآورد شود)

    def __init__(self, box_xyxy, ts):
        x1, y1, x2, y2 = box_xyxy
        self.x = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0,
                           max(1.0, x2 - x1), max(1.0, y2 - y1),
                           0.0, 0.0, 0.0, 0.0], dtype=np.float64)
        self.last_ts = float(ts)
        # (2.0.34-beta) زمان‌بندی جدا برای پیش‌بینی و به‌روزرسانی: قبلاً
        # predict() همان last_ts را جلو می‌برد و update() با dt=۰ سرعت را
        # هیچ‌وقت یاد نمی‌گرفت (سرعت همیشه صفر می‌ماند و پیش‌بینیِ شخصِ
        # در حال حرکت بی‌فایده بود).
        self._last_pred_ts = float(ts)

    def predict(self, ts):
        """باکس پیش‌بینی‌شده برای لحظه‌ی ts به قالب [x1,y1,x2,y2]."""
        ts = float(ts)
        dt = min(max(ts - self._last_pred_ts, 0.0), 5.0)
        self._last_pred_ts = ts
        if dt > 0:
            self.x[0] += self.x[4] * dt
            self.x[1] += self.x[5] * dt
            self.x[2] = max(1.0, self.x[2] + self.x[6] * dt)
            self.x[3] = max(1.0, self.x[3] + self.x[7] * dt)
        return self._to_box()

    def update(self, box_xyxy, ts):
        """تصحیح حالت با مشاهده‌ی تازه."""
        ts = float(ts)
        dt = min(max(ts - self.last_ts, 0.0), 5.0)
        x1, y1, x2, y2 = box_xyxy
        z = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0,
                      max(1.0, x2 - x1), max(1.0, y2 - y1)], dtype=np.float64)
        pred = self.x[:4].copy()
        self.x[:4] = pred + self._K_POS * (z - pred)
        if dt > 1e-3:
            v_meas = (z - pred) / dt
            self.x[4:] = (self.x[4:] + self._K_VEL * (v_meas - self.x[4:]))
        else:
            self.x[4:] *= 0.9
        self.last_ts = ts
        self._last_pred_ts = ts
        return self._to_box()

    def _to_box(self):
        cx, cy, w, h = self.x[0], self.x[1], self.x[2], self.x[3]
        return [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0]


# ---------------------------------------------------------------------------
# ردیاب محلی هر دوربین — tracking-by-detection به سبک ByteTrack
# ---------------------------------------------------------------------------

# آستانه‌های تطبیق (مبنا: مقاله‌ی ByteTrack و تجربه‌ی SORT):
# - IoU کمتر از ۰.۲ همیشه رد می‌شود (عدد مقاله)؛
# - مرحله‌ی اول (ردهای فعال) IoU ≥ ۰.۳؛
# - مرحله‌ی دوم (بازپس‌گیری ردهای گمشده) IoU ≥ ۰.۲۵.
_IOU_MATCH = 0.30
_IOU_REJECT = 0.20
_IOU_REACQUIRE = 0.25


def _centers_near(a, b, ratio=1.6):
    """آیا مرکز b در همسایگی مرکز a است (متناسب با ابعاد)؟ برای لینکِ
    شخصِ در حال حرکت، وقتی جابه‌جایی سریع بین دو تیکِ کم‌تکرار IoU را
    صفر کرده ولی همان شخص است."""
    acx, acy = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    bcx, bcy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    aw, ah = max(1.0, a[2] - a[0]), max(1.0, a[3] - a[1])
    bw, bh = max(1.0, b[2] - b[0]), max(1.0, b[3] - b[1])
    dist = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
    if dist > ratio * (aw + bw + ah + bh) / 4.0:
        return False
    ar = (aw * ah) / max(1.0, bw * bh)
    return 0.35 <= ar <= 2.8


class PersonLocalTracker:
    """ردیابی محلی باکس‌های شخص داخل یک دوربین (tracking-by-detection).

    قرارداد ورودی/خروجی با نسخه‌ی قبلی سازگار است تا camera_stream.py و
    main.py بدون تغییر کار کنند:
      update(boxes, frame, ts=None, face_results=None, box_format="trbl")
        boxes: خروجی PersonDetector.detect — قالب (top, right, bottom, left).
      رویدادها: ("candidate", ...)، ("confirmed", track)،
        ("candidate_ended", ...)، ("ended", track).

    تفاوت‌های نسخه‌ی ۲ نسبت به قبل:
      - باکس‌ها اول نرمال و اعتبارسنجی می‌شوند (رفع باگ قالب)؛
      - تطبیق با «باکس پیش‌بینی‌شده‌ی کالمن» انجام می‌شود نه باکس فریم قبل؛
      - دومرحله‌ای: اول ردهای فعال، بعد بازپس‌گیری ردهای گمشده؛
      - رد گمشده تا max_age تیک نگه داشته می‌شود و با بازگشت شخص، همان
        شناسه ادامه پیدا می‌کند (بدون ثبت تکراری در دیتابیس)؛
      - اگر توصیف‌گر لحظه‌ی تأیید ساخته نشد، رد بی‌صدا «تأیید» نمی‌شود؛
        در تیک بعد دوباره تلاش می‌شود و شمارنده‌ی diag بالا می‌رود؛
      - شمارنده‌های تشخیصی (diag_snapshot) برای عیب‌یابی روی ویندوز.
    """

    # بعد از چند «هیت»، کاندیدا اعلام شود (زودتر از تأیید نهایی — نشان زرد)
    CANDIDATE_HITS = 2

    def __init__(self, confirm_frames=2, max_miss=20, iou_thresh=0.30):
        self.confirm_frames = max(1, int(confirm_frames))
        # max_miss در نسخه‌ی قبل «تیکِ بدون باکس تا پایان رد» بود؛ حالا همان
        # نقش max_age در ByteTrack را دارد: رد گمشده چند تیک نگه داشته شود.
        self.max_age = max(1, int(max_miss))
        self.iou_thresh = float(iou_thresh) if iou_thresh else _IOU_MATCH
        self._lock = threading.Lock()
        self._tracks = []
        self._next_id = 1
        self._diag = {"ticks": 0, "boxes_total": 0, "boxes_invalid": 0,
                      "tracks_created": 0, "candidates": 0, "confirmed": 0,
                      "ended": 0, "reactivated": 0, "descriptor_failures": 0,
                      "last_tick_ts": 0.0}

    # -- API عمومی --
    def update(self, boxes, frame, ts=None, face_results=None,
               box_format="trbl"):
        ts = time.time() if ts is None else ts
        events = []
        with self._lock:
            d = self._diag
            d["ticks"] += 1
            d["last_tick_ts"] = ts
            # ۱) نرمال‌سازی و اعتبارسنجی باکس‌ها (رفع باگ قالب)
            dets = []
            for b in (boxes or []):
                nb = normalize_box(b, box_format=box_format)
                if nb is None:
                    d["boxes_invalid"] += 1
                else:
                    dets.append(nb)
            d["boxes_total"] += len(dets)
            # ۲) پیش‌بینی همه‌ی ردها با کالمن برای همین لحظه
            for tr in self._tracks:
                tr["pred_box"] = tr["kf"].predict(ts)
            # ۳) مرحله‌ی اول: ردهای فعال ← باکس‌ها
            # (2.0.29-beta) تطبیق دومرحله‌ای برای گرفتن «شخصِ در حال حرکت
            # از اولین لحظه»: اول ردهای تأییدشده با آستانه‌ی معمول، بعد
            # ردهای تازه (tentative) با آستانه‌ی بازتر (_IOU_REJECT)؛ وگرنه
            # جابه‌جایی سریع شخص بین دو تشخیصِ پیاپی رد را می‌شکست، هیچ
            # ردی به حد تأیید نمی‌رسید و شخصِ در حال حرکت هرگز ثبت نمی‌شد.
            # (2.0.34-beta) برای ردهای تازه/گمشده، اگر IoU صفر شد ولی مرکز
            # دتکشن در همسایگی مرکز پیش‌بینی‌شده بود (حرکت سریع بین دو تیکِ
            # کم‌تکرار روی CPU)، لینک اضطراری صادر می‌شود.
            confirmed_tr = [tr for tr in self._tracks
                            if tr["state"] == "confirmed"]
            tentative_tr = [tr for tr in self._tracks
                            if tr["state"] == "tentative"]
            matches_c, unmatched_c, unmatched_det_c = self._associate(
                confirmed_tr, dets, self.iou_thresh)
            rem_dets = [dets[i] for i in unmatched_det_c]
            matches_t, unmatched_t, unmatched_det_t = self._associate(
                tentative_tr, rem_dets, _IOU_REJECT, dist_fallback=True)
            _nc = len(confirmed_tr)
            active = confirmed_tr + tentative_tr
            matches = ([(ti, unmatched_det_c[di]) for ti, di in matches_c]
                       + [(_nc + ti, unmatched_det_c[di])
                          for ti, di in matches_t])
            unmatched_tr = (list(unmatched_c)
                            + [_nc + i for i in unmatched_t])
            unmatched_det = [unmatched_det_c[i] for i in unmatched_det_t]
            # ۴) مرحله‌ی دوم (ایده‌ی ByteTrack): ردهای گمشده ← باکس‌های مانده
            # un_lost: ایندکس‌های ردهای گمشده‌ای که در مرحله‌ی دوم هم
            # بی‌باکس ماندند (ایندکس داخل لیست lost — جدا از unmatched_tr).
            lost = [tr for tr in self._tracks if tr["state"] == "lost"]
            un_lost = list(range(len(lost)))
            if lost and unmatched_det:
                rem_dets = [dets[i] for i in unmatched_det]
                m2, un_lost, un_det2 = self._associate(
                    lost, rem_dets, _IOU_REACQUIRE, dist_fallback=True)
                for ti, di in m2:
                    tr = lost[ti]
                    det = rem_dets[di]
                    self._on_match(tr, det, ts, frame, face_results,
                                   events, reactivated=True)
                    d["reactivated"] += 1
                unmatched_det = [unmatched_det[i] for i in un_det2]
            # ۵) اعمال تطبیق‌های مرحله‌ی اول
            for ti, di in matches:
                self._on_match(active[ti], dets[di], ts, frame,
                               face_results, events)
            # ۶) ردهای فعالِ بی‌باکس (unmatched_tr: ایندکس داخل active)
            for ti in unmatched_tr:
                tr = active[ti]
                tr["miss"] += 1
                if tr["state"] == "tentative":
                    # رد تازه‌ی تأییدنشده که زود محو شد: بی‌سر و صدا حذف
                    if tr["miss"] >= 2:
                        if tr["candidate_notified"]:
                            events.append(("candidate_ended", {
                                "local_id": tr["local_id"]}))
                        self._tracks.remove(tr)
                elif tr["state"] == "confirmed":
                    tr["state"] = "lost"
                    tr["lost_since"] = ts
            # ردهای گمشده‌ی بی‌باکس فقط پیر می‌شوند (حذف در مرحله‌ی ۸)
            for li in un_lost:
                lost[li]["miss"] += 1
            # ۷) ردهای تازه برای باکس‌های بی‌صاحب
            for di in unmatched_det:
                det = dets[di]
                self._tracks.append({
                    "local_id": self._next_id, "kf": _KalmanBox(det, ts),
                    "box": list(det), "pred_box": list(det),
                    "hits": 1, "miss": 0, "state": "tentative",
                    "candidate_notified": False,
                    "descriptor": None, "crop": None, "face_box": None,
                    "label": f"#{self._next_id}",
                    "first_seen": ts, "last_seen": ts,
                    "lost_since": 0.0,
                })
                self._next_id += 1
                d["tracks_created"] += 1
            # ۸) حذف ردهای منقضی‌شده (lost قدیمی‌تر از max_age)
            for tr in list(self._tracks):
                if tr["state"] == "lost" and tr["miss"] >= self.max_age:
                    self._tracks.remove(tr)
                    if tr.get("was_confirmed"):
                        d["ended"] += 1
                        events.append(("ended", self._public(tr)))
                    elif tr["candidate_notified"]:
                        events.append(("candidate_ended", {
                            "local_id": tr["local_id"]}))
            draw = [(tr["box"], tr["label"],
                     tr["descriptor"]["shirt_color"]
                     if tr["descriptor"] else "")
                    for tr in self._tracks if tr["state"] == "confirmed"]
        return events, draw

    @staticmethod
    def _associate(tracks, dets, iou_thresh, dist_fallback=False):
        """تطبیق حریصانه‌ی IoU بین باکس پیش‌بینی‌شده‌ی ردها و باکس‌ها.
        خروجی: (matches [(ti, di)], unmatched_track_idx, unmatched_det_idx).
        IoU < ‎_IOU_REJECT‎ همیشه رد می‌شود (عدد مقاله‌ی ByteTrack)؛ اگر
        dist_fallback=True باشد، برای ردهایی که IoU صفر گرفته‌اند ولی مرکز
        دتکشن در همسایگی مرکز پیش‌بینی‌شده است (حرکت سریع بین دو تیک)،
        لینک اضطراری با امتیاز پایین صادر می‌شود."""
        pairs = []
        for ti, tr in enumerate(tracks):
            pb = tr.get("pred_box") or tr["box"]
            for di, det in enumerate(dets):
                v = _iou(pb, det)
                if v >= max(iou_thresh, _IOU_REJECT):
                    pairs.append((v, ti, di))
                elif dist_fallback and _centers_near(pb, det):
                    pairs.append((_IOU_REJECT * 0.75, ti, di))
        pairs.sort(key=lambda p: p[0], reverse=True)
        used_t, used_d, matches = set(), set(), []
        for v, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            matches.append((ti, di))
        unmatched_tr = [i for i in range(len(tracks)) if i not in used_t]
        unmatched_det = [i for i in range(len(dets)) if i not in used_d]
        return matches, unmatched_tr, unmatched_det

    def _on_match(self, tr, det, ts, frame, face_results, events,
                  reactivated=False):
        d = self._diag
        tr["box"] = list(det)
        tr["kf"].update(det, ts)
        tr["miss"] = 0
        tr["hits"] += 1
        tr["last_seen"] = ts
        if reactivated or tr["state"] == "lost":
            # بازگشت شخص گمشده: همان شناسه ادامه پیدا می‌کند؛ اگر قبلاً
            # تأیید شده بود، نیازی به رویداد confirmed تازه نیست.
            tr["state"] = "confirmed" if tr.get("was_confirmed") else "tentative"
        # به‌روزرسانی تدریجی توصیف‌گر با نمای تازه (میانگین متحرک)
        if tr["state"] == "confirmed" and tr["descriptor"] is not None \
                and tr["hits"] % 6 == 0:
            desc = self._describe_box(frame, tr["box"])
            if desc is not None:
                tr["descriptor"]["vector"] = _l2norm(
                    0.8 * tr["descriptor"]["vector"] + 0.2 * desc["vector"])
        # کاندیدای «در لحظه»
        if (tr["state"] == "tentative" and not tr["candidate_notified"]
                and tr["hits"] >= self.CANDIDATE_HITS):
            tr["candidate_notified"] = True
            d["candidates"] += 1
            events.append(("candidate", {"local_id": tr["local_id"],
                                         "box": list(tr["box"])}))
        # تأیید نهایی
        if tr["state"] == "tentative" and tr["hits"] >= self.confirm_frames:
            desc = self._describe_box(frame, tr["box"])
            if desc is None:
                # رفع باگ «تأیید بی‌صدا»: توصیف‌گر ساخته نشد -> تأیید عقب
                # می‌افتد و در تیک بعد دوباره تلاش می‌شود؛ شمارنده بالا
                # می‌رود تا در لاگ تشخیصی دیده شود.
                d["descriptor_failures"] += 1
            else:
                fp, fbox = match_face_to_person(tr["box"], face_results)
                if fp is not None:
                    desc["face_person_id"] = str(fp.get("id") or "")
                    desc["face_name"] = str(fp.get("name") or "")
                tr["face_box"] = fbox
                tr["descriptor"] = desc
                tr["crop"] = self._crop(frame, tr["box"])
                tr["state"] = "confirmed"
                tr["was_confirmed"] = True
                d["confirmed"] += 1
                events.append(("confirmed", self._public(tr)))

    def set_track_label(self, local_id, text):
        with self._lock:
            for tr in self._tracks:
                if tr["local_id"] == local_id:
                    tr["label"] = str(text)
                    break

    def diag_snapshot(self):
        """شمارنده‌های تشخیصی برای عیب‌یابی (روی ویندوز طه معلوم می‌کند
        دقیقاً کجای مسیر candidate→confirmed→store می‌ایستد)."""
        with self._lock:
            d = dict(self._diag)
            d["active_now"] = sum(1 for tr in self._tracks
                                  if tr["state"] in ("tentative", "confirmed"))
            d["confirmed_now"] = sum(1 for tr in self._tracks
                                     if tr["state"] == "confirmed")
            d["lost_now"] = sum(1 for tr in self._tracks
                                if tr["state"] == "lost")
            return d

    def flush(self):
        """پایان‌دادن اجباری همه‌ی ردهای فعال (مثلاً هنگام توقف دوربین) تا
        رویداد ended صادر و «حضور»‌ها در دیتابیس بسته شوند."""
        events = []
        with self._lock:
            for tr in self._tracks:
                if tr.get("was_confirmed"):
                    events.append(("ended", self._public(tr)))
                elif tr.get("candidate_notified"):
                    events.append(("candidate_ended",
                                   {"local_id": tr["local_id"]}))
            self._tracks = []
        return events

    # -- داخلی --
    @staticmethod
    def _crop(frame, box):
        try:
            if frame is None:
                return None
            x1, y1, x2, y2 = (int(v) for v in box)
            h, w = frame.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                return None
            return frame[y1:y2, x1:x2].copy()
        except Exception:
            return None

    def _describe_box(self, frame, box):
        crop = self._crop(frame, box)
        if crop is None:
            return None
        # کراپ‌های کوچک (شخصِ دور) را تا حداقلِ لازم برای توصیف‌گر بزرگ
        # می‌کنیم تا شخصِ دورِ در حال حرکت هم شانس تأیید داشته باشد؛
        # بزرگ‌نمایی حداکثر ۴ برابر تا نویز غالب نشود.
        try:
            ch, cw = crop.shape[:2]
            if (ch < 40 or cw < 20) and cv2 is not None:
                _sc = min(4.0, max(40.0 / max(1, ch), 20.0 / max(1, cw)))
                crop = cv2.resize(crop, (max(1, int(cw * _sc)),
                                        max(1, int(ch * _sc))),
                                  interpolation=cv2.INTER_CUBIC)
        except Exception:
            pass
        if crop.shape[0] < 40 or crop.shape[1] < 20:
            return None
        try:
            return describe_person(crop)
        except Exception:
            return None

    @staticmethod
    def _public(tr):
        return {
            "local_id": tr["local_id"], "box": list(tr["box"]),
            "descriptor": tr["descriptor"], "crop": tr["crop"],
            "first_seen": tr["first_seen"], "last_seen": tr["last_seen"],
        }


# ---------------------------------------------------------------------------
# تطبیق سراسری بین دوربین‌ها
# ---------------------------------------------------------------------------

class GlobalPersonMatcher:
    """امضای ظاهری اشخاصِ اخیراً دیده‌شده را نگه می‌دارد.

    find_match(vector, camera_name, ts, face_vector=None, face_person_id=None):
      ۱) اگر face_person_id (چهره‌ی شناخته‌شده در بانک چهره‌ها) داده شده و
         امضایی با همان شناسه‌ی چهره وجود دارد -> تطبیق قطعی (فاصله ۰).
      ۲) وگرنه نزدیک‌ترین امضای زنده (داخل پنجره‌ی زمانی) با فاصله‌ی
         کسینوسی ترکیب‌شده‌ی «ظاهر + امضای چهره»؛ اگر از آستانه کمتر بود،
         همان شخص.
    register(...): ثبت امضای یک شخص تازه (همراه امضای چهره و شناسه‌ی چهره).
    """

    # وزن امضای چهره در فاصله‌ی ترکیبی (وقتی هر دو طرف امضا دارند)
    FACE_WEIGHT = 0.35

    def __init__(self, threshold=0.45, window_s=600.0, max_signatures=500):
        self.threshold = float(threshold)
        self.window_s = float(window_s)
        self.max_signatures = int(max_signatures)
        self._sigs = []  # {person_id, vector, face_vector, face_person_id,
                         #  camera, last_ts}

    def configure(self, threshold=None, window_s=None):
        if threshold is not None:
            self.threshold = float(threshold)
        if window_s is not None:
            self.window_s = float(window_s)

    def _prune(self, ts):
        self._sigs = [s for s in self._sigs
                      if ts - s["last_ts"] <= self.window_s]
        if len(self._sigs) > self.max_signatures:
            self._sigs = sorted(self._sigs, key=lambda s: s["last_ts"])[
                -self.max_signatures:]

    def find_match(self, vector, camera_name, ts=None,
                   face_vector=None, face_person_id=None):
        ts = time.time() if ts is None else ts
        self._prune(ts)
        # ۱) چهره‌ی شناخته‌شده: تطبیق قطعی، حتی با لباس متفاوت
        if face_person_id:
            for s in self._sigs:
                if str(s.get("face_person_id") or "") == str(face_person_id):
                    s["last_ts"] = ts
                    s["camera"] = camera_name
                    return s["person_id"], 0.0
        # ۲) فاصله‌ی کسینوسی ترکیب‌شده‌ی ظاهر + امضای چهره
        best, best_d = None, None
        for s in self._sigs:
            d = cosine_distance(vector, s["vector"])
            sfv = s.get("face_vector")
            if face_vector is not None and sfv is not None:
                try:
                    d = ((1.0 - self.FACE_WEIGHT) * d
                         + self.FACE_WEIGHT * cosine_distance(face_vector, sfv))
                except Exception:
                    pass
            if best_d is None or d < best_d:
                best, best_d = s, d
        if best is not None and best_d <= self.threshold:
            # امضا را با نمای تازه تقویت کن (میانگین متحرک + نرمال‌سازی)
            try:
                best["vector"] = _l2norm(
                    0.7 * np.asarray(best["vector"], dtype=np.float32)
                    + 0.3 * np.asarray(vector, dtype=np.float32))
            except Exception:
                pass
            if face_vector is not None and best.get("face_vector") is not None:
                try:
                    best["face_vector"] = _l2norm(
                        0.7 * np.asarray(best["face_vector"], dtype=np.float32)
                        + 0.3 * np.asarray(face_vector, dtype=np.float32))
                except Exception:
                    pass
            best["last_ts"] = ts
            best["camera"] = camera_name
            return best["person_id"], float(best_d)
        return None, None

    def register(self, person_id, vector, camera_name, ts=None,
                 face_vector=None, face_person_id=None):
        ts = time.time() if ts is None else ts
        self._prune(ts)
        fv = None
        try:
            if face_vector is not None:
                fv = _l2norm(np.asarray(face_vector, dtype=np.float32))
        except Exception:
            fv = None
        try:
            vec = _l2norm(np.asarray(vector, dtype=np.float32))
        except Exception:
            vec = np.asarray(vector, dtype=np.float32)
        self._sigs.append({"person_id": person_id, "vector": vec,
                           "face_vector": fv,
                           "face_person_id": str(face_person_id or ""),
                           "camera": camera_name, "last_ts": ts})

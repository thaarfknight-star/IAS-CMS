# -*- coding: utf-8 -*-
"""ردیابی ظاهری اشخاص بین دوربین‌ها — با کمک چهره.

ایده‌ی کلی (الگو دقیقاً مثل PlateTracker در plate_detector.py):
  ۱) هر دوربین یک PersonLocalTracker دارد که باکس‌های PersonDetector را
     فریم‌به‌فریم با IoU به هم می‌چسباند (ردیابی «محلی» داخل همان دوربین).
  ۲) وقتی یک رد محلی «تأیید» شد (چند فریم پیاپی دیده شد)، یک توصیف‌گر
     ظاهری از روی کراپ بدن ساخته می‌شود: هیستوگرام رنگ HSV سه ناحیه‌ی
     سر / بالاتنه / پایین‌تنه + ویژگی‌های قابل‌خواندن برای انسان
     (رنگ لباس، رنگ شلوار، رنگ مو، بلندی مو).
  ۳) کمک چهره (جدید): نتایج تشخیص چهره‌ی همان دور تشخیص (FaceEngine که
     در ترد تشخیص دوربین از قبل اجرا می‌شود) با باکس بدن تطبیق داده
     می‌شود؛ اگر چهره در «بانک چهره‌ها» شناخته‌شده باشد، شناسه‌ی آن چهره
     به‌عنوان قوی‌ترین نشانه‌ی هویتی به رد الصاق می‌شود (حتی اگر لباس
     عوض شده باشد، همان شخص شناخته می‌شود). برای چهره‌های ناشناس هم یک
     امضای سبک از ناحیه‌ی سر نگه داشته می‌شود تا تطبیق دقیق‌تر شود.
  ۴) GlobalPersonMatcher (یک نمونه‌ی سراسری در ترد اصلی) توصیف‌گر رد تازه
     را با «امضاهای» اشخاصی که در چند دقیقه‌ی اخیر در دوربین‌های دیگر
     دیده شده‌اند مقایسه می‌کند؛ اگر به‌اندازه‌ی کافی شبیه بود، همان شخص
     قبلی است (مسیرش ادامه پیدا می‌کند)، وگرنه یک «شخص» جدید ثبت می‌شود.

نکته‌ی صادقانه‌ی مهم: برای چهره‌های «ناشناس» (تعریف‌نشده در بانک چهره)
این هنوز «بازشناسی هویتی» قطعی نیست؛ امضای چهره فقط یک هیستوگرام رنگ
سبک از ناحیه‌ی سر است (بدون dlib، بدون embedding عمیق) و در کنار رنگ
لباس به تطبیق کمک می‌کند. پس:
  - دو نفر با لباس و ظاهر خیلی شبیه ممکن است یکی حساب شوند؛
  - چهره‌ی شناخته‌شده در بانک چهره‌ها = تطبیق قطعی بین دوربین‌ها.
آستانه‌ی تطبیق و پنجره‌ی زمانی اتصال از صفحه‌ی «ردیابی اشخاص» قابل
تنظیم است (رجوع کنید به person_store.py).

وابستگی سنگین ندارد (فقط numpy و cv2) تا روی همان سخت‌افزار ۴GB بدون GPU
در ترد پس‌زمینه‌ی تشخیص دوربین‌ها اجرا شود. استخراج embedding عمیق چهره
عمداً انجام نمی‌شود تا CPU ذوب نشود؛ از خروجی آماده‌ی FaceEngine که همان
دور تشخیص از قبل حساب شده استفاده می‌شود (هزینه‌ی اضافه ≈ صفر).
"""

import threading
import time

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# نام فارسی رنگ‌ها از روی HSV
# ---------------------------------------------------------------------------

# نگاشت بازه‌ی Hue (۰ تا ۱۷۹ در OpenCV) به نام رنگ — برای پیکسل‌های «رنگی»
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
    # قهوه‌ای/کرم: اشباع متوسط و روشنایی متوسط، ته‌مایه‌ی نارنجی
    if 8 <= h < 25 and s < 130:
        return "قهوه‌ای" if v < 150 else "کرم"
    if v < 110:
        # تیره‌های رنگی: زرشکی، سرمه‌ای، سبز تیره...
        for bound, name in _HUE_NAMES:
            if h < bound:
                if name in ("قرمز",):
                    return "زرشکی"
                if name in ("آبی",):
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
    # برای حذف اثر لبه‌های تیره‌ی کراپ، فقط چارک میانی روشنایی را می‌گیریم
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
# توصیف‌گر ظاهری شخص
# ---------------------------------------------------------------------------

# ناحیه‌ها به‌صورت کسری از ارتفاع کراپ بدن
_HEAD_FRac = (0.00, 0.16)    # سر
_UPPER_FRac = (0.16, 0.52)   # بالاتنه (لباس)
_LOWER_FRac = (0.52, 1.00)   # پایین‌تنه (شلوار)

# وزن ناحیه‌ها در فاصله‌ی ظاهری: لباس مهم‌ترین نشانه است
_REGION_WEIGHTS = (0.15, 0.50, 0.35)

# ابعاد هیستوگرام هر ناحیه: H=12 ، S=3 ، V=3
_HIST_BINS = (12, 3, 3)
_HIST_RANGES = [(0, 180), (0, 256), (0, 256)]


def _region_hist(region_bgr):
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    hist, _ = np.histogramdd(
        hsv.reshape(-1, 3), bins=_HIST_BINS, range=_HIST_RANGES)
    hist = hist.ravel().astype(np.float32)
    s = hist.sum()
    if s > 0:
        hist /= s
    return hist


def _split_regions(crop):
    h = crop.shape[0]
    regs = {}
    for name, (a, b) in (("head", _HEAD_FRac), ("upper", _UPPER_FRac),
                         ("lower", _LOWER_FRac)):
        y1, y2 = int(h * a), max(int(h * b), int(h * a) + 1)
        regs[name] = crop[y1:y2]
    return regs


def _estimate_hair(crop, regs):
    """رنگ و بلندی مو — یک هیوریستیک ساده و صادقانه:
    نیمه‌ی بالایی ناحیه‌ی سر را «مو» فرض می‌کنیم (نه پیشانی/صورت).
    بلندی: اگر پیکسل‌های تیره‌ی «مومانند» تا پایین ناحیه‌ی سر و کمی
    پایین‌تر (روی شانه) ادامه داشته باشند -> «بلند»، وگرنه «کوتاه».
    اگر سر خیلی کوچک باشد -> «نامشخص»."""
    head = regs["head"]
    hh = head.shape[0]
    if hh < 12:
        return "نامشخص", "نامشخص"
    hair_part = head[: max(1, int(hh * 0.55))]
    med = _region_median_hsv(hair_part)
    hair_color = hsv_to_persian_color(*med) if med else "نامشخص"
    # بلندی مو: پروفایل عمودی پیکسل‌های تیره در (سر + ۲۵٪ بالایی بالاتنه)
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
        hair_len = "نامشخص"  # مو دیده نمی‌شود (کلاه/تاس/نور زیاد)
    elif bottom_dark > 0.30 and bottom_dark > top_dark * 0.6:
        hair_len = "بلند"
    else:
        hair_len = "کوتاه"
    return hair_color, hair_len


def describe_person(crop_bgr):
    """از روی کراپ BGR بدن، توصیف‌گر ظاهری می‌سازد.
    خروجی: dict با کلیدهای vector (np.float32 نرمال‌شده)، face_vector
    (امضای سبک ناحیه‌ی سر برای کمک چهره)، shirt_color، pants_color،
    hair_color، hair_length، face_person_id، face_name (دو تای آخر هنگام
    تأیید رد، از تطبیق با نتایج FaceEngine پر می‌شوند)."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    # یکدست‌سازی ابعاد تا هیستوگرام‌ها قابل‌مقایسه باشند
    crop = cv2.resize(crop_bgr, (64, 128))
    regs = _split_regions(crop)
    vecs = []
    for name in ("head", "upper", "lower"):
        vecs.append(_region_hist(regs[name]))
    vector = np.concatenate(vecs).astype(np.float32)
    # امضای چهره: هیستوگرام همان ناحیه‌ی سر (بدون هزینه‌ی اضافه — از قبل
    # حساب شده). برای چهره‌های ناشناس، همین در تطبیق بین دوربینی وزن می‌گیرد.
    face_vector = vecs[0].astype(np.float32)

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


def face_distance(fa, fb):
    """فاصله‌ی ۰ تا ۱ بین دو امضای چهره (کمتر = شبیه‌تر)؛ Bhattacharyya
    روی هیستوگرام ناحیه‌ی سر."""
    try:
        a = np.asarray(fa, dtype=np.float32).ravel()
        b = np.asarray(fb, dtype=np.float32).ravel()
        if a.shape != b.shape or a.size == 0:
            return 1.0
        bc = float(np.sum(np.sqrt(np.clip(a, 0, None) * np.clip(b, 0, None))))
        bc = min(1.0, max(0.0, bc))
        return float(np.sqrt(max(0.0, 1.0 - bc)))
    except Exception:
        return 1.0


def match_face_to_person(person_box, face_results):
    """تطبیق چهره به باکس بدن.

    person_box: [x1, y1, x2, y2] (مختصات فریم).
    face_results: لیست {"box": (top, right, bottom, left), "person": dict|None}
      از خروجی FaceEngine (همان دور تشخیص).
    خروجی: (face_person_dict|None, face_box|None) — چهره‌ای که مرکزش در
      نیمه‌ی بالایی باکس بدن باشد؛ در صورت چند کاندیدا، هم‌پوشانی بیشتر.
    """
    if not face_results:
        return None, None
    try:
        px1, py1, px2, py2 = (float(v) for v in person_box)
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
        # مرکز چهره باید داخل باکس بدن و در نیمه‌ی بالایی آن باشد
        if not (px1 <= fcx <= px2 and py1 <= fcy <= py2):
            continue
        if fcy > py1 + 0.45 * ph:
            continue
        # چهره نباید از خود بدن بزرگ‌تر باشد (فیلتر خطای آشکار)
        if (fx2 - fx1) > pw * 1.2:
            continue
        ix1, iy1 = max(px1, fx1), max(py1, fy1)
        ix2, iy2 = min(px2, fx2), min(py2, fy2)
        ov = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        if ov > best_ov:
            best, best_box, best_ov = fr.get("person"), (fx1, fy1, fx2, fy2), ov
    return best, best_box


def appearance_distance(vec_a, vec_b):
    """فاصله‌ی ظاهری ۰ تا ۱ بین دو توصیف‌گر (کمتر = شبیه‌تر).
    برای هر ناحیه از فاصله‌ی Bhattacharyya هیستوگرام‌ها استفاده می‌شود
    و با وزن ناحیه‌ها ترکیب می‌شود."""
    n = _HIST_BINS[0] * _HIST_BINS[1] * _HIST_BINS[2]
    dist = 0.0
    for i, w in enumerate(_REGION_WEIGHTS):
        a = vec_a[i * n:(i + 1) * n]
        b = vec_b[i * n:(i + 1) * n]
        bc = float(np.sum(np.sqrt(a * b)))  # ضریب Bhattacharyya
        bc = min(1.0, max(0.0, bc))
        dist += w * float(np.sqrt(max(0.0, 1.0 - bc)))
    return dist


# ---------------------------------------------------------------------------
# ردیاب محلی هر دوربین (IoU چندفریمی — مثل PlateTracker)
# ---------------------------------------------------------------------------

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


class PersonLocalTracker:
    """ردیابی محلی باکس‌های شخص داخل یک دوربین.

    رویدادهای خروجی update():
      ("candidate", {"local_id", "box"}) — رد بعد از ۲ دور پیاپی دیده شد
          ولی هنوز به آستانه‌ی تأیید نرسیده؛ برای نشان «در حال شناسایی»
          (حس در لحظه بودن) استفاده می‌شود — بدون ثبت در دیتابیس.
      ("confirmed", track) — رد بعد از confirm_frames فریم پیاپی تأیید شد؛
          track دیکشنری {local_id, box, descriptor, attrs, crop, first_seen}.
          اگر چهره‌ی رد در بانک چهره‌ها شناخته‌شده باشد، descriptor شامل
          face_person_id و face_name هم هست.
      ("candidate_ended", {"local_id"}) — ردِ کاندیدا بدون رسیدن به تأیید
          تمام شد (نشان موقتش باید برداشته شود).
      ("ended", track) — رد بعد از max_miss فریمِ بدون باکس تمام شد.
    برچسب نمایشی هر رد (مثلاً «P-0003») را ترد اصلی با set_track_label
    به‌روز می‌کند تا روی تصویر، کد یکتای شخص دیده شود.

    face_results (اختیاری): خروجی FaceEngine همان دور تشخیص —
      [{"box": (top,right,bottom,left), "person": dict|None}] — برای الصاق
      هویت چهره به رد تأییدشده. چون FaceEngine در ترد تشخیص دوربین از قبل
      اجرا شده، هزینه‌ی اضافه‌ای ندارد.
    """

    # بعد از چند «هیت» پیاپی، کاندیدا اعلام شود (زودتر از تأیید نهایی)
    CANDIDATE_HITS = 2

    def __init__(self, confirm_frames=3, max_miss=10, iou_thresh=0.30):
        self.confirm_frames = max(1, int(confirm_frames))
        self.max_miss = max(1, int(max_miss))
        self.iou_thresh = float(iou_thresh)
        self._lock = threading.Lock()
        self._tracks = []  # هر رد: dict
        self._next_id = 1

    def update(self, boxes, frame, ts=None, face_results=None):
        """boxes: لیست [x1,y1,x2,y2] خروجی PersonDetector؛ frame: فریم BGR.
        face_results: خروجی FaceEngine همان دور تشخیص (اختیاری)."""
        ts = time.time() if ts is None else ts
        events = []
        with self._lock:
            # تطبیق حریصانه‌ی IoU
            unmatched = list(range(len(boxes)))
            for tr in self._tracks:
                best, best_iou = -1, self.iou_thresh
                for i in unmatched:
                    v = _iou(tr["box"], boxes[i])
                    if v > best_iou:
                        best, best_iou = i, v
                if best >= 0:
                    tr["box"] = [float(v) for v in boxes[best]]
                    tr["miss"] = 0
                    tr["hits"] += 1
                    tr["last_seen"] = ts
                    unmatched.remove(best)
                    # هر چند فریم یک‌بار، توصیف‌گر را با نمای تازه به‌روز کن
                    # (میانگین متحرک — مقاوم در برابر چرخش بدن/نور لحظه‌ای)
                    if tr["hits"] % 4 == 0:
                        desc = self._describe_box(frame, tr["box"])
                        if desc is not None:
                            tr["descriptor"]["vector"] = (
                                0.75 * tr["descriptor"]["vector"]
                                + 0.25 * desc["vector"]
                            ).astype(np.float32)
                    # کاندیدای «در لحظه»: زودتر از تأیید نهایی، برای نشان زرد
                    if (not tr["confirmed"]
                            and not tr["candidate_notified"]
                            and tr["hits"] >= self.CANDIDATE_HITS):
                        tr["candidate_notified"] = True
                        events.append(("candidate", {
                            "local_id": tr["local_id"],
                            "box": list(tr["box"]),
                        }))
                    if not tr["confirmed"] and tr["hits"] >= self.confirm_frames:
                        tr["confirmed"] = True
                        desc = self._describe_box(frame, tr["box"])
                        if desc is not None:
                            # کمک چهره: اگر چهره‌ی این رد در بانک چهره‌ها
                            # شناخته‌شده است، هویتش الصاق می‌شود
                            fp, fbox = match_face_to_person(
                                tr["box"], face_results)
                            if fp is not None:
                                desc["face_person_id"] = str(fp.get("id") or "")
                                desc["face_name"] = str(fp.get("name") or "")
                            tr["face_box"] = fbox
                            tr["descriptor"] = desc
                            tr["crop"] = self._crop(frame, tr["box"])
                            events.append(("confirmed", self._public(tr)))
                else:
                    tr["miss"] += 1
            # ردهای تازه برای باکس‌های بی‌صاحب
            for i in unmatched:
                box = [float(v) for v in boxes[i]]
                self._tracks.append({
                    "local_id": self._next_id, "box": box,
                    "hits": 1, "miss": 0, "confirmed": False,
                    "candidate_notified": False,
                    "descriptor": None, "crop": None,
                    "face_box": None,
                    "label": f"#{self._next_id}",
                    "first_seen": ts, "last_seen": ts,
                })
                self._next_id += 1
            # ردهای منقضی‌شده
            alive = []
            for tr in self._tracks:
                if tr["miss"] >= self.max_miss:
                    if tr["confirmed"]:
                        events.append(("ended", self._public(tr)))
                    elif tr["candidate_notified"]:
                        events.append(("candidate_ended", {
                            "local_id": tr["local_id"]}))
                else:
                    alive.append(tr)
            self._tracks = alive
            draw = [(tr["box"], tr["label"],
                     tr["descriptor"]["shirt_color"]
                     if tr["descriptor"] else "")
                    for tr in self._tracks if tr["confirmed"]]
        return events, draw

    def set_track_label(self, local_id, text):
        with self._lock:
            for tr in self._tracks:
                if tr["local_id"] == local_id:
                    tr["label"] = str(text)
                    break

    def flush(self):
        """پایان‌دادن اجباری همه‌ی ردهای فعال (مثلاً هنگام توقف دوربین) تا
        رویداد ended صادر و «حضور»‌ها در دیتابیس بسته شوند."""
        events = []
        with self._lock:
            for tr in self._tracks:
                if tr["confirmed"]:
                    events.append(("ended", self._public(tr)))
                elif tr.get("candidate_notified"):
                    events.append(("candidate_ended",
                                   {"local_id": tr["local_id"]}))
            self._tracks = []
        return events

    # -- داخلی --
    @staticmethod
    def _crop(frame, box):
        x1, y1, x2, y2 = (int(v) for v in box)
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2].copy()

    def _describe_box(self, frame, box):
        crop = self._crop(frame, box)
        if crop is None or crop.shape[0] < 40 or crop.shape[1] < 20:
            return None  # کراپ خیلی کوچک؛ توصیف‌گر قابل‌اعتماد نیست
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
      ۲) وگرنه نزدیک‌ترین امضای زنده (داخل پنجره‌ی زمانی) با فاصله‌ی ترکیبی
         «ظاهر + امضای چهره»؛ اگر به‌اندازه‌ی آستانه نزدیک بود، همان شخص.
    register(...): ثبت امضای یک شخص تازه (همراه امضای چهره و شناسه‌ی چهره).
    """

    # وزن امضای چهره در فاصله‌ی ترکیبی (وقتی هر دو طرف امضا دارند)
    FACE_WEIGHT = 0.35

    def __init__(self, threshold=0.50, window_s=600.0, max_signatures=500):
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
        # ۲) فاصله‌ی ترکیبی ظاهر + امضای چهره
        best, best_d = None, None
        for s in self._sigs:
            d = appearance_distance(vector, s["vector"])
            sfv = s.get("face_vector")
            if face_vector is not None and sfv is not None:
                try:
                    d = ((1.0 - self.FACE_WEIGHT) * d
                         + self.FACE_WEIGHT * face_distance(face_vector, sfv))
                except Exception:
                    pass
            if best_d is None or d < best_d:
                best, best_d = s, d
        if best is not None and best_d <= self.threshold:
            # امضا را با نمای تازه تقویت کن (میانگین متحرک)
            best["vector"] = (0.7 * best["vector"]
                              + 0.3 * vector).astype(np.float32)
            if face_vector is not None and best.get("face_vector") is not None:
                try:
                    best["face_vector"] = (
                        0.7 * best["face_vector"]
                        + 0.3 * np.asarray(face_vector,
                                           dtype=np.float32)).astype(np.float32)
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
                fv = np.asarray(face_vector, dtype=np.float32)
        except Exception:
            fv = None
        self._sigs.append({"person_id": person_id,
                           "vector": vector.astype(np.float32),
                           "face_vector": fv,
                           "face_person_id": str(face_person_id or ""),
                           "camera": camera_name, "last_ts": ts})

# -*- coding: utf-8 -*-
"""آشکارساز شعله‌ی کوچک + تأیید چندفریمی (small flame detection):

مشکل: مدل YOLO تشخیص آتش/دود (fire_smoke_detector.py) روی تصویر ۴۸۰ پیکسلی
اجرا می‌شود؛ شعله‌ی کوچکی مثل فندک/شمع/کبریت در آن رزولوشن فقط چند پیکسل است
و YOLO «شیء خیلی کوچک» را ذاتاً خوب نمی‌بیند (small-object problem). بالا بردن
کورکورانه‌ی حساسیت هم فقط آلارم اشتباه می‌سازد.

راه‌حل این ماژول (مکمل مدل، نه جایگزین آن):
  ۱) آشکارساز کلاسیک سبک (بدون مدل، فقط OpenCV): شعله دو ویژگی فیزیکی دارد -
     «روشنایی و اشباع خیلی بالا در ناحیه‌ی زرد/نارنجی/قرمز» و «سوسو زدن»
     (flicker) با فرکانس چند هرتز. لامپ/بازتاب/پوست (که رنگشان شبیه شعله است)
     سوسو نمی‌زنند، پس با تحلیل نوسان روشنایی در چند فریم متوالی از کاندیداهای
     ثابت تفکیک می‌شوند. آن‌قدر سبک است که هر دور تشخیص اجرا شود.
  ۲) آبشار (cascade): وقتی آشکارساز کلاسیک ناحیه‌ی مشکوکی یافت، همان ناحیه از
     فریم اصلی کراپ و بزرگ‌نمایی می‌شود و به YOLO داده می‌شود؛ شعله‌ی کوچک برای
     مدل «بزرگ» می‌شود و اگر مدل آن را تأیید کرد، اطمینان بالا می‌رود.
  ۳) تأیید چندفریمی (DetectionConfirmer): هیچ تشخیصی با یک فریم به خروجی
     نمی‌رسد؛ یک شیء باید در حداقل k فریم از n فریم آخر دیده شده باشد. این اجازه
     می‌دهد آستانه‌ها پایین (حساس) باشند بدون اینکه نویز لحظه‌ای آلارم بسازد.

قالب خروجی همه‌جا مثل FireSmokeDetector است: لیستی از (box, kind, conf) با
box=(top, right, bottom, left) و kind='fire'. این ماژول عمداً به Qt وابسته نیست
تا headless هم تست شود.
"""

import threading
from collections import deque

import cv2
import numpy as np

from fire_config import get_params

# عرض کاری تشخیص کلاسیک؛ شعله‌ی نزدیک دوربین حتی در این ابعاد هم به‌اندازه‌ی
# کافی بزرگ است و سرعت چند برابر می‌شود.
_WORK_WIDTH = 320
_HISTORY_LEN = 8
_WARMUP_FRAMES = 4  # حداقل فریم لازم برای تحلیل سوسو


def _iou(box_a, box_b):
    at, ar, ab, al = box_a
    bt, br, bb, bl = box_b
    it = max(at, bt)
    ir = min(ar, br)
    ib = min(ab, bb)
    il = max(al, bl)
    if ir <= il or ib <= it:
        return 0.0
    inter = (ir - il) * (ib - it)
    area_a = max(0, ar - al) * max(0, ab - at)
    area_b = max(0, br - bl) * max(0, bb - bt)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _center_dist(box_a, box_b):
    at, ar, ab, al = box_a
    bt, br, bb, bl = box_b
    return abs((al + ar) / 2 - (bl + br) / 2) + abs((at + ab) / 2 - (bt + bb) / 2)


def merge_detections(detections, iou_thresh=0.3):
    """حذف تکراری‌ها بین منابع مختلف (YOLO تمام‌فریم، کلاسیک، آبشار):
    از هر خوشه‌ی هم‌پوشانِ هم‌نوع، بالاترین اطمینان نگه داشته می‌شود."""
    merged = []
    for box, kind, conf in detections:
        placed = False
        for i, (mbox, mkind, mconf) in enumerate(merged):
            if mkind == kind and _iou(box, mbox) > iou_thresh:
                if conf > mconf:
                    merged[i] = (box, kind, conf)
                placed = True
                break
        if not placed:
            merged.append((box, kind, conf))
    return merged


class SmallFlameDetector:
    """آشکارساز کلاسیک شعله‌ی کوچک - نمونه‌ی جدا برای هر دوربین (حالت زمانی
    داخل خودش نگه می‌دارد). فقط در ترد تشخیص همان دوربین صدا زده شود."""

    def __init__(self):
        self._v_history = deque(maxlen=_HISTORY_LEN)     # کانال V (روشنایی)
        self._mask_history = deque(maxlen=_HISTORY_LEN)  # ماسک باینری شعله
        self._lock = threading.RLock()

    def reset(self):
        with self._lock:
            self._v_history.clear()
            self._mask_history.clear()

    def _flame_mask(self, small_bgr):
        """ماسک پیکسل‌های «شعله‌مانند»: روشن + اشباع + ته‌مایه‌ی زرد/نارنجی/قرمز،
        به‌علاوه‌ی هسته‌ی سفیدداغِ چسبیده به آن."""
        hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)
        h = hsv[:, :, 0].astype(np.int16)
        s = hsv[:, :, 1]
        v = hsv[:, :, 2]
        hue_ok = (h <= 30) | (h >= 150)          # قرمز تا زرد (با چرخش قرمز)
        body = (hue_ok & (s > 90) & (v > 190)).astype(np.uint8)
        # هسته‌ی سفیدداغ: خیلی روشن ولی کم‌اشباع، فقط اگر به بدنه‌ی رنگی چسبیده باشد
        dilated = cv2.dilate(body, np.ones((7, 7), np.uint8))
        core = ((v > 225) & (dilated > 0)).astype(np.uint8)
        mask = np.maximum(body, core) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        return mask, v.astype(np.float32)

    def detect(self, frame):
        """ورودی: فریم BGR تمام‌رزولوشن. خروجی: [(box, 'fire', conf), ...] با
        box=(top, right, bottom, left) در مختصات فریم اصلی."""
        params = get_params()
        h0, w0 = frame.shape[:2]
        if w0 <= 0 or h0 <= 0:
            return []
        scale = _WORK_WIDTH / float(w0)
        small = cv2.resize(frame, (_WORK_WIDTH, max(1, int(h0 * scale))),
                           interpolation=cv2.INTER_AREA)
        mask, v = self._flame_mask(small)

        with self._lock:
            self._v_history.append(v)
            self._mask_history.append((mask > 0).astype(np.float32))
            if len(self._v_history) < _WARMUP_FRAMES:
                return []  # هنوز تاریخچه‌ی کافی برای سوسو نداریم
            v_stack = np.stack(self._v_history, axis=0)
            m_stack = np.stack(self._mask_history, axis=0)

        # نقشه‌ی سوسو: پیکسل‌هایی که روشناییشان در چند فریم اخیر نوسان زیاد داشته
        tstd = v_stack.std(axis=0)
        flicker_map = tstd > params["flicker_std"]
        # پایداری: ناحیه باید در اکثر فریم‌های اخیر واقعاً «شعله‌مانند» بوده باشد
        persist_map = m_stack.mean(axis=0) > 0.4

        num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        small_area = small.shape[0] * small.shape[1]
        min_area = params["min_area"]
        max_area = small_area * 0.5
        inv_scale = 1.0 / scale
        detections = []
        for i in range(1, num):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < min_area or area > max_area:
                continue
            comp = (labels == i)
            flicker_ratio = float((flicker_map & comp).sum()) / float(area)
            if flicker_ratio < params["flicker_ratio"]:
                continue  # روشن ولی ثابت: لامپ، بازتاب، نور ثابت...
            persist_ratio = float((persist_map & comp).sum()) / float(area)
            if persist_ratio < 0.5:
                continue  # نویز تک‌فریمی
            x = stats[i, cv2.CC_STAT_LEFT]
            y = stats[i, cv2.CC_STAT_TOP]
            w = stats[i, cv2.CC_STAT_WIDTH]
            hh = stats[i, cv2.CC_STAT_HEIGHT]
            left = max(0, int(x * inv_scale))
            top = max(0, int(y * inv_scale))
            right = min(w0, int((x + w) * inv_scale))
            bottom = min(h0, int((y + hh) * inv_scale))
            conf = min(0.92, 0.55 + 0.30 * min(1.0, flicker_ratio * 2.0))
            detections.append(((top, right, bottom, left), "fire", conf))
        return detections


class DetectionConfirmer:
    """تأیید چندفریمی عمومی: یک تشخیص فقط وقتی به خروجی می‌رسد که شیء هم‌نوع در
    حداقل k فریم از n فریم آخر (با تطبیق مکانی) دیده شده باشد. نمونه‌ی جدا برای
    هر دوربین؛ فقط در ترد تشخیص همان دوربین صدا زده شود."""

    def __init__(self):
        self._tracks = []  # [{"box","kind","conf","hits":deque}]
        self._lock = threading.RLock()

    def reset(self):
        with self._lock:
            self._tracks = []

    def update(self, detections, k=3, n=6, frame_diag=1000.0):
        with self._lock:
            # تطبیق تشخیص‌های این دور با ترک‌های موجود
            unmatched = list(detections)
            for tr in self._tracks:
                best, best_idx = None, -1
                for idx, (box, kind, conf) in enumerate(unmatched):
                    if kind != tr["kind"]:
                        continue
                    if _iou(box, tr["box"]) > 0.15 or \
                       _center_dist(box, tr["box"]) < 0.05 * frame_diag:
                        best, best_idx = (box, kind, conf), idx
                        break
                if best is not None:
                    box, kind, conf = best
                    tr["box"] = box
                    tr["conf"] = max(tr["conf"] * 0.95, conf)
                    tr["hits"].append(True)
                    unmatched.pop(best_idx)
                else:
                    tr["hits"].append(False)
            for box, kind, conf in unmatched:
                self._tracks.append({
                    "box": box, "kind": kind, "conf": conf,
                    "hits": deque([True], maxlen=n),
                })
            # حذف ترک‌های کاملاً مرده (در n فریم اخیر هیچ هیت نداشته‌اند)
            self._tracks = [tr for tr in self._tracks
                            if not (len(tr["hits"]) >= n and not any(tr["hits"]))]
            confirmed = [(tr["box"], tr["kind"], tr["conf"])
                         for tr in self._tracks if sum(tr["hits"]) >= k]
            return confirmed


def cascade_yolo_confirm(frame, candidates, conf=None):
    """آبشار: برای هر کاندیدای آشکارساز کلاسیک، ناحیه را از فریم اصلی کراپ و
    بزرگ‌نمایی می‌کند و YOLO را فقط روی همان ناحیه اجرا می‌کند (شعله‌ی کوچک
    برای مدل بزرگ می‌شود). اگر مدل در دسترس نباشد، بی‌صدا [] برمی‌گرداند تا
    تشخیص کلاسیک به‌تنهایی کار کند."""
    if not candidates:
        return []
    try:
        from fire_smoke_detector import fire_smoke_detector
    except Exception:
        return []
    if not fire_smoke_detector.available:
        return []
    h, w = frame.shape[:2]
    out = []
    for (box, kind, c0) in candidates:
        top, right, bottom, left = box
        bw, bh = max(1, right - left), max(1, bottom - top)
        x1 = max(0, left - bw // 2)
        y1 = max(0, top - bh // 2)
        x2 = min(w, right + bw // 2)
        y2 = min(h, bottom + bh // 2)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        rs = 640.0 / max(crop.shape[1], crop.shape[0])
        crop_rs = cv2.resize(crop, (max(1, int(crop.shape[1] * rs)),
                                    max(1, int(crop.shape[0] * rs))),
                             interpolation=cv2.INTER_CUBIC)
        try:
            yolo_dets = fire_smoke_detector.detect(crop_rs, conf=conf)
        except TypeError:
            # سازگاری با نسخه‌ی قدیمی fire_smoke_detector بدون پارامتر conf
            yolo_dets = fire_smoke_detector.detect(crop_rs)
        for (cbox, ckind, cconf) in yolo_dets:
            ct, cr, cb, cl = cbox
            mapped = (int(y1 + ct / rs), int(x1 + cr / rs),
                      int(y1 + cb / rs), int(x1 + cl / rs))
            out.append((mapped, ckind, max(cconf, c0)))
    return out

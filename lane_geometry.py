# -*- coding: utf-8 -*-
"""lane_geometry.py — هندسه‌ی مسیرهای پلاک‌خوان روی نقشه (2.0.17-beta).

ماژول خالص (بدون Qt) برای رسم و اتصال مسیر روی نقشه‌ی ساختمان:

- مسیر = یک چندخطی (polyline) از نقاطی که کاربر روی نقشه کلیک می‌کند.
- «جهت رسم» (نقطه‌ی اول -> آخر) = جهت مثبت مسیر = جهت «رفت».
- دوربین‌ها بر اساس «فاصله‌ی طولی پروجکشن» روی مسیر مرتب می‌شوند
  (نزدیک‌ترین به ابتدای مسیر = ترتیب ۰) و این ترتیب در موتور جهت
  (plate_direction.py — قانون د) برای تشخیص «حرکت معکوس» استفاده می‌شود.

قوانین اتصال دوربین به مسیر (تحقیق و تدوین 2.0.17):
  ۱) حداقل ۲ نقطه برای رسم مسیر لازم است.
  ۲) فقط دوربین‌هایی که فاصله‌شان از خط مسیر حداکثر «تلرانس» (پیش‌فرض
     ۵ متر) باشد قابل اتصال‌اند؛ بقیه با اخطار رد می‌شوند.
  ۳) دوربین باید نقش پلاکی (ورود/خروج) داشته باشد؛ بدون نقش، موتور جهت
     نمی‌تواند ورود/خروج را تشخیص دهد پس اتصال ممنوع است.
  ۴) هر دوربین فقط عضو «یک» مسیر است؛ اتصال به مسیر جدید، اتصال قبلی را
     قطع می‌کند.
  ۵) حداقل یک دوربین برای ذخیره‌ی مسیر لازم است.
  ۶) ترتیب دوربین‌ها = فاصله‌ی طولی پروجکشن از ابتدای مسیر (صعودی).
"""

import math

#: تلرانس پیش‌فرش اتصال دوربین به مسیر (متر)
DEFAULT_ATTACH_TOLERANCE_M = 5.0


def dist_point_to_segment(px, py, ax, ay, bx, by):
    """فاصله‌ی نقطه‌ی P از پاره‌خط AB + فاصله‌ی طولی نقطه‌ی پروجکشن از A.

    خروجی: (dist, s) که dist فاصله‌ی اقلیدسی و s فاصله‌ی نقطه‌ی تصویرشده
    از ابتدای پاره‌خط (A) در همان واحد ورودی است.
    """
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 1e-12:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    qx = ax + t * dx
    qy = ay + t * dy
    seg_len = math.sqrt(seg_len_sq)
    return math.hypot(px - qx, py - qy), t * seg_len


def project_point_on_polyline(px, py, points):
    """نزدیک‌ترین فاصله‌ی نقطه به چندخطی + فاصله‌ی طولی از ابتدای مسیر.

    points: لیست [(x, y), ...] با حداقل ۲ نقطه.
    خروجی: (min_dist, s_total)؛ اگر مسیر نامعتبر باشد (None, None).
    """
    if not points or len(points) < 2:
        return None, None
    best_dist = None
    best_s = 0.0
    acc = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        seg_len = math.hypot(bx - ax, by - ay)
        dist, s = dist_point_to_segment(px, py, ax, ay, bx, by)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_s = acc + s
        acc += seg_len
    return best_dist, best_s


def camera_on_lane(cam_x, cam_y, points, to_meter=1.0,
                   tolerance_m=DEFAULT_ATTACH_TOLERANCE_M):
    """آیا دوربین به مسیر نزدیک‌تر از تلرانس است؟

    خروجی: (ok, dist_m, s_scene) — فاصله به متر و فاصله‌ی طولی به واحد صحنه.
    """
    dist, s = project_point_on_polyline(cam_x, cam_y, points)
    if dist is None:
        return False, None, None
    dist_m = dist * (to_meter or 1.0)
    return dist_m <= tolerance_m, dist_m, s


def order_cameras_on_lane(cameras, points):
    """مرتب‌سازی دوربین‌ها بر اساس فاصله‌ی طولی از ابتدای مسیر.

    cameras: لیست دیکشنری‌های {"camera_id", "x", "y", ...}.
    خروجی: لیست [(camera_dict, s_scene)] مرتب‌شده‌ی صعودی بر اساس s.
    """
    scored = []
    for cam in cameras:
        _d, s = project_point_on_polyline(cam.get("x", 0), cam.get("y", 0),
                                          points)
        scored.append((cam, s if s is not None else 0.0))
    scored.sort(key=lambda t: t[1])
    return scored


def build_lane_record(name, allowed, floor_id, points, to_meter, cameras):
    """ساخت رکورد مسیر برای ذخیره در plate_store.

    cameras: لیست {"camera_id", "x", "y"} — فقط دوربین‌های داخل تلرانس
    باید پاس داده شوند (فیلتر تلرانس با camera_on_lane قبلاً اعمال شود).
    خروجی: دیکشنری lane با کلیدهای name/allowed/floor_id/points/
    to_meter/cameras (مرتب‌شده با order).
    """
    ordered = order_cameras_on_lane(cameras, points)
    cam_list = [{"camera_id": str(c.get("camera_id")), "order": i}
                for i, (c, _s) in enumerate(ordered)]
    return {
        "name": name,
        "allowed": allowed,  # "going" | "return"
        "floor_id": floor_id or "",
        "points": [[float(x), float(y)] for x, y in points],
        "to_meter": float(to_meter or 1.0),
        "cameras": cam_list,
    }


def validate_lane_points(points):
    """اعتبارسنجی نقاط رسم‌شده: (ok, پیام خطا)."""
    if not points or len(points) < 2:
        return False, "برای رسم مسیر حداقل ۲ نقطه لازم است."
    return True, ""


def polyline_length(points):
    """طول کل چندخطی (به همان واحد ورودی)."""
    if not points or len(points) < 2:
        return 0.0
    total = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        total += math.hypot(bx - ax, by - ay)
    return total


def point_at_distance(points, dist):
    """موقعیت نقطه‌ای که در فاصله‌ی طولی dist از ابتدای مسیر قرار دارد.

    points: لیست [(x, y), ...]؛ dist بریده می‌شود به [۰، طول کل].
    خروجی: (x, y). اگر مسیر نامعتبر باشد (۰.۰، ۰.۰).
    """
    if not points:
        return 0.0, 0.0
    if len(points) == 1:
        return float(points[0][0]), float(points[0][1])
    total = polyline_length(points)
    d = max(0.0, min(float(dist), total))
    acc = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len <= 1e-12:
            continue
        if acc + seg_len >= d:
            t = (d - acc) / seg_len
            return ax + t * (bx - ax), ay + t * (by - ay)
        acc += seg_len
    return float(points[-1][0]), float(points[-1][1])


def camera_s_at_order(lane_points, cam_x, cam_y):
    """فاصله‌ی طولی پروجکشن یک دوربین روی مسیر (برای شبیه‌ساز).

    خروجی: s (واحد صحنه) یا None اگر مسیر نامعتبر باشد.
    """
    _dist, s = project_point_on_polyline(cam_x, cam_y, lane_points)
    return s

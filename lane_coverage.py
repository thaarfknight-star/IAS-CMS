# -*- coding: utf-8 -*-
"""lane_coverage.py — تحلیل زنده‌ی پوشش دوربین روی نقاط مسیرهای پلاک‌خوان.

ماژول خالص (بدون Qt) — نسخه‌ی 2.0.21-beta:

- نمونه‌برداری از نقاط مسیر: هر ``step_m`` متر یک نقطه روی چندخطی مسیر
  (شامل نقطه‌ی شروع و پایان)؛ هر نقطه: مختصات صحنه + ``s_m`` (متراژ از
  ابتدای مسیر).
- آزمون قرارگیری نقطه در قطاع دید دوربین: دقیقاً همان قرارداد
  ``_sector_path`` در building_map_dialog.py — زاویه‌ی ۰ = شرق، مثبت خلاف
  جهت عقربه‌های ساعت، با وارونگی محور y صحنه:
  نقطه‌ی روی لبه‌ی قطاع = (cx + r·cos a, cy − r·sin a).
- تحلیل پوشش: برای هر نقطه‌ی هر مسیر، فهرست دوربین‌هایی که آن را می‌بینند؛
  خروجی هم «نقاط بدون پوشش» را می‌دهد و هم «هر دوربین کدام نقاط را
  پوشش می‌دهد».
"""

import math

from lane_geometry import polyline_length, point_at_distance

#: فاصله‌ی نمونه‌برداری پیش‌فرض روی مسیر (متر)
DEFAULT_SAMPLE_STEP_M = 2.0


def sample_lane_points(points, to_meter=1.0, step_m=DEFAULT_SAMPLE_STEP_M):
    """نمونه‌برداری هر step_m متر روی چندخطی مسیر.

    points: لیست [(x, y), ...] به واحد صحنه.
    خروجی: لیست [{"x", "y", "s_m"}] که s_m متراژ نقطه از ابتدای مسیر است؛
    همیشه شامل نقطه‌ی شروع (۰ متر) و نقطه‌ی پایان است.
    """
    pts = [(float(x), float(y)) for x, y in (points or [])]
    if len(pts) < 2:
        return []
    tm = to_meter or 1.0
    step = max(float(step_m or DEFAULT_SAMPLE_STEP_M), 0.1)
    total_scene = polyline_length(pts)
    total_m = total_scene * tm
    if total_m <= 0:
        return []
    out = []
    s = 0.0
    # نقطه‌ی شروع + میانی‌ها
    while s < total_m - 1e-9:
        x, y = point_at_distance(pts, s / tm)
        out.append({"x": float(x), "y": float(y), "s_m": round(s, 1)})
        s += step
    # نقطه‌ی پایان (اگر با آخرین نمونه یکی نیست)
    x, y = point_at_distance(pts, total_scene)
    if not out or abs(out[-1]["s_m"] - round(total_m, 1)) > 1e-6:
        out.append({"x": float(x), "y": float(y), "s_m": round(total_m, 1)})
    return out


def point_in_sector(px, py, cx, cy, angle_deg, fov_deg, range_scene):
    """آیا نقطه‌ی (px, py) داخل قطاع دید دوربین است؟

    قرارداد زاویه دقیقاً مطابق _sector_path: ۰ = شرق، مثبت خلاف عقربه‌ی
    ساعت، با وارونگی y صحنه (نقطه‌ی زاویه‌ی a روی لبه = (cx + r·cos a,
    cy − r·sin a)).
    range_scene: برد دوربین به واحد صحنه.
    """
    dx = px - cx
    dy = py - cy
    dist = math.hypot(dx, dy)
    if dist > range_scene + 1e-9:
        return False
    if dist <= 1e-9:
        return True  # نقطه‌ی روی خودِ دوربین
    # زاویه‌ی ریاضی نقطه با وارونگی محور y صحنه
    ang = math.degrees(math.atan2(-dy, dx))
    diff = (ang - float(angle_deg) + 180.0) % 360.0 - 180.0
    return abs(diff) <= float(fov_deg) / 2.0 + 1e-9


def analyze_lane_coverage(lanes, cameras, to_meter=1.0,
                          step_m=DEFAULT_SAMPLE_STEP_M):
    """تحلیل پوشش دوربین‌ها روی نقاط نمونه‌برداری‌شده‌ی مسیرها.

    lanes: لیست {"name", "points", "to_meter"}.
    cameras: لیست {"name"/"id", "x", "y", "angle", "fov", "view_distance"}
      که view_distance به «متر» است.
    to_meter: ضریب تبدیل واحد صحنه به متر (برای برد دوربین).

    خروجی:
      {"lanes": [{"name", "points": [{"x","y","s_m","covered_by":[نام‌ها]}],
                  "uncovered": [...]}, ...],
       "cameras": [{"name", "covered": [{"lane", "s_m", "x", "y"}]}, ...]}
    """
    tm = to_meter or 1.0
    cam_secs = []
    for cam in cameras or []:
        cam_secs.append({
            "name": cam.get("name") or cam.get("id") or "؟",
            "x": float(cam.get("x", 0.0)),
            "y": float(cam.get("y", 0.0)),
            "angle": float(cam.get("angle", 0.0)),
            "fov": float(cam.get("fov", 90.0)),
            "range_scene": float(cam.get("view_distance", 8.0)) / tm,
            "covered": [],
        })
    lane_results = []
    for lane in lanes or []:
        lane_tm = lane.get("to_meter", tm) or tm
        pts = sample_lane_points(lane.get("points"), lane_tm, step_m)
        uncovered = []
        for p in pts:
            covering = [c for c in cam_secs
                        if point_in_sector(p["x"], p["y"], c["x"], c["y"],
                                           c["angle"], c["fov"],
                                           c["range_scene"])]
            p["covered_by"] = [c["name"] for c in covering]
            for c in covering:
                c["covered"].append({"lane": lane.get("name", "؟"),
                                    "s_m": p["s_m"],
                                    "x": p["x"], "y": p["y"]})
            if not covering:
                uncovered.append(p)
        lane_results.append({
            "name": lane.get("name", "؟"),
            "points": pts,
            "uncovered": uncovered,
        })
    return {
        "lanes": lane_results,
        "cameras": [{"name": c["name"], "covered": c["covered"]}
                    for c in cam_secs],
    }

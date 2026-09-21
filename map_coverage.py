# -*- coding: utf-8 -*-
"""map_coverage.py — تحلیل پوشش دوربین روی نقشه‌ی ساختمان (2.0.18-beta).

ماژول خالص (بدون Qt):
- استخراج موقعیت درها از فایل DXF (قوس‌های ARC = چرخش لنگه‌ی در، رایج‌ترین
  قرارداد ترسیم در در پلان‌های معماری) + بلاک‌هایی که نامشان درب/door است.
- تحلیل پوشش: هر دری که هیچ دوربینی در شعاع مشخص (پیش‌فرض ۵ متر) نداشته
  باشد، «بدون پوشش» گزارش می‌شود.

مختصات خروجی درها با قرارداد DxfMapLoader یکسان است: (x, -y).
"""

import math

#: شعاع پیش‌فرض پوشش دوربین (متر)
DEFAULT_COVERAGE_RADIUS_M = 5.0

#: حداقل/حداکثر شعاع قوسِ قابل‌قبول به‌عنوان لنگه‌ی در (متر)
DOOR_ARC_RADIUS_MIN_M = 0.4
DOOR_ARC_RADIUS_MAX_M = 1.6

_DOOR_NAME_HINTS = ("door", "درب", "در_")


def ezdxf_available():
    try:
        import ezdxf  # noqa: F401
        return True
    except Exception:
        return False


def _insunits_to_meter(doc):
    try:
        insunits = int(doc.header.get("$INSUNITS", 0))
    except Exception:
        insunits = 0
    return {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01,
            6: 1.0, 8: 0.001, 13: 1e-9}.get(insunits, 1.0)


def extract_doors_from_dxf(dxf_path):
    """استخراج موقعیت درها از DXF.

    خروجی: (doors, to_meter) که doors لیستی از (x, y) به واحد صحنه است.
    اگر ezdxf نصب نباشد یا فایل خوانده نشود، ([], 1.0) برمی‌گردد.
    """
    if not ezdxf_available():
        return [], 1.0
    import ezdxf
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        return [], 1.0
    to_meter = _insunits_to_meter(doc)
    doors = []

    def _iter_entities(layout):
        for e in layout:
            if e.dxftype() == "INSERT":
                try:
                    for ve in e.virtual_entities():
                        if ve.dxftype() == "INSERT":
                            continue
                        try:
                            ve.dxf.layer = str(e.dxf.get("layer", "0"))
                        except Exception:
                            pass
                        yield ve
                except Exception:
                    continue
            else:
                yield e

    try:
        msp = doc.modelspace()
    except Exception:
        return [], to_meter

    for entity in _iter_entities(msp):
        try:
            etype = entity.dxftype()
        except Exception:
            continue
        if etype == "ARC":
            try:
                r = float(entity.dxf.radius)
            except Exception:
                continue
            r_m = r * to_meter
            if not (DOOR_ARC_RADIUS_MIN_M <= r_m <= DOOR_ARC_RADIUS_MAX_M):
                continue
            try:
                cx, cy = entity.dxf.center.x, entity.dxf.center.y
            except Exception:
                continue
            doors.append((cx, -cy))
        elif etype == "INSERT":
            try:
                name = str(entity.dxf.name or "").lower()
            except Exception:
                name = ""
            if any(h in name for h in _DOOR_NAME_HINTS):
                try:
                    ix, iy = entity.dxf.insert.x, entity.dxf.insert.y
                except Exception:
                    continue
                doors.append((ix, -iy))

    # ادغام درهای خیلی نزدیک به هم (تکراری‌های ناشی از باز شدن بلاک)
    merged = []
    for x, y in doors:
        dup = False
        for i, (mx, my) in enumerate(merged):
            if math.hypot(x - mx, y - my) * to_meter < 0.3:
                dup = True
                break
        if not dup:
            merged.append((x, y))
    return merged, to_meter


def analyze_coverage(doors, cameras, radius_m=DEFAULT_COVERAGE_RADIUS_M,
                     to_meter=1.0):
    """تحلیل پوشش دوربین برای لیست درها.

    doors: لیست [(x, y), ...] به واحد صحنه.
    cameras: لیست دیکشنری‌های {\"x\", \"y\", ...} به واحد صحنه.
    خروجی: لیست دیکشنری‌های {\"x\", \"y\", \"covered\": bool,
             \"nearest_m\": float|None}.
    """
    results = []
    for dx, dy in doors:
        nearest = None
        for cam in cameras or []:
            try:
                cx = float(cam.get("x", 0))
                cy = float(cam.get("y", 0))
            except (TypeError, ValueError):
                continue
            d = math.hypot(dx - cx, dy - cy) * (to_meter or 1.0)
            if nearest is None or d < nearest:
                nearest = d
        covered = nearest is not None and nearest <= radius_m
        results.append({"x": dx, "y": dy, "covered": covered,
                        "nearest_m": nearest})
    return results

# -*- coding: utf-8 -*-
"""نقشه‌ی تعاملی ساختمان (Building Map)

این ماژول دو بخش دارد:

۱) ``MapStore``: مدل داده‌ی نقشه‌ها؛ هر ساختمان چند «طبقه» دارد و هر طبقه
   یک فایل نقشه (DXF خروجی اتوکد یا تصویر) به‌علاوه‌ی لیستی از تجهیزات
   جای‌گذاری‌شده (دوربین، NVR، رک، سوئیچ، ...) با مختصات دقیق روی نقشه.
   همه‌چیز در ``maps_data/building_maps.json`` ذخیره می‌شود.

۲) ``DxfMapLoader``: رندر فایل DXF (فرمت تبادل اتوکد) روی صحنه‌ی گرافیکی
   Qt. اتوکد با Save As به‌راحتی خروجی DXF می‌دهد و این لودر آن را با همان
   دقت مختصات اصلی، لایه‌به‌لایه و با رنگ‌های واقعی روی نقشه می‌کشد.
   برای این کار از کتابخانه‌ی ``ezdxf`` (خالص پایتون، بدون نیاز به اتوکد)
   استفاده می‌شود؛ اگر نصب نباشد، نقشه‌ی تصویری (PNG/JPG) همچنان کار می‌کند.

واحدها: مختصات صحنه دقیقاً همان واحد فایل DXF است (معمولاً میلی‌متر) و
محور Y معکوس می‌شود تا با جهت صفحه‌نمایش یکی شود.
"""

import json
import math
import os
import shutil
import uuid

# ---------------------------------------------------------------------------
# انواع تجهیزاتی که می‌توان روی نقشه گذاشت
# ---------------------------------------------------------------------------
DEVICE_KINDS = {
    "camera": {"fa": "دوربین", "icon": "🎥"},
    "nvr":    {"fa": "NVR",   "icon": "🖥"},
    "rack":   {"fa": "رک",    "icon": "🗄"},
    "switch": {"fa": "سوئیچ شبکه", "icon": "🔌"},
    "siren":  {"fa": "آژیر",  "icon": "🔔"},
    "panel":  {"fa": "پنل اعلام حریق", "icon": "🎛"},
    "other":  {"fa": "سایر",  "icon": "📍"},
}

# واحدهای اتوکد ($INSUNITS) -> ضریب تبدیل به متر
INSUNITS_TO_METER = {
    0: 1.0,    # بدون واحد: فرض بر متر
    1: 0.0254,  # اینچ
    2: 0.3048,  # فوت
    3: 1609.344,  # مایل
    4: 0.001,   # میلی‌متر
    5: 0.01,    # سانتی‌متر
    6: 1.0,     # متر
    7: 1000.0,  # کیلومتر
    8: 0.0000254,  # میکرواینچ
    9: 0.001,   # میل (هزارم اینچ)
    10: 0.9144,  # یارد
    11: 1e-10,  # آنگستروم
    12: 1e-9,   # نانومتر
    13: 1e-6,   # میکرون
    14: 0.01,   # دسی‌متر
    15: 0.1,    # دکامتر
    16: 100.0,  # هکتومتر
    17: 1e9,    # گیگامتر
    18: 149597870700.0,  # واحد نجومی
    19: 9.4607304725808e15,  # سال نوری
    20: 3.08567758149137e16,  # پارسک
}
INSUNITS_FA = {
    0: "بدون واحد", 1: "اینچ", 2: "فوت", 3: "مایل", 4: "میلی‌متر",
    5: "سانتی‌متر", 6: "متر", 7: "کیلومتر", 8: "میکرواینچ", 9: "میل",
    10: "یارد", 11: "آنگستروم", 12: "نانومتر", 13: "میکرون",
    14: "دسی‌متر", 15: "دکامتر", 16: "هکتومتر", 17: "گیگامتر",
    18: "واحد نجومی", 19: "سال نوری", 20: "پارسک",
}


def _base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def maps_data_dir():
    d = os.path.join(_base_dir(), "maps_data")
    os.makedirs(d, exist_ok=True)
    return d


class MapStore:
    """نگهداری طبقات و تجهیزات روی دیسک (JSON)."""

    def __init__(self, json_path=None):
        self.json_path = json_path or os.path.join(maps_data_dir(), "building_maps.json")
        self.data = {"floors": []}
        self.load()

    # -- خواندن/نوشتن --
    def load(self):
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("floors"), list):
                self.data = data
        except (OSError, ValueError):
            self.data = {"floors": []}

    def save(self):
        tmp = self.json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.json_path)

    # -- طبقات --
    def floors(self):
        return self.data["floors"]

    def get_floor(self, floor_id):
        for fl in self.data["floors"]:
            if fl.get("id") == floor_id:
                return fl
        return None

    def add_floor(self, name):
        fl = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "dxf": "",      # مسیر نسبی فایل DXF داخل maps_data
            "image": "",    # مسیر نسبی تصویر جایگزین
            "devices": [],
        }
        self.data["floors"].append(fl)
        self.save()
        return fl

    def rename_floor(self, floor_id, name):
        fl = self.get_floor(floor_id)
        if fl:
            fl["name"] = name
            self.save()

    def remove_floor(self, floor_id):
        self.data["floors"] = [fl for fl in self.data["floors"] if fl.get("id") != floor_id]
        self.save()

    def set_floor_map(self, floor_id, dxf_rel="", image_rel=""):
        fl = self.get_floor(floor_id)
        if fl:
            fl["dxf"] = dxf_rel
            fl["image"] = image_rel
            self.save()

    def import_map_file(self, floor_id, src_path):
        """کپی فایل نقشه (DXF یا تصویر) به داخل maps_data و ثبت روی طبقه."""
        fl = self.get_floor(floor_id)
        if not fl:
            return None
        ext = os.path.splitext(src_path)[1].lower()
        fname = f"{fl['id']}{ext}"
        dst = os.path.join(maps_data_dir(), fname)
        shutil.copyfile(src_path, dst)
        if ext == ".dxf":
            fl["dxf"] = fname
            fl["image"] = ""
        else:
            fl["image"] = fname
            fl["dxf"] = ""
        self.save()
        return fname

    def floor_map_abs(self, fl):
        """مسیر مطلق فایل نقشه‌ی طبقه (DXF ترجیح دارد، وگرنه تصویر)."""
        for key in ("dxf", "image"):
            rel = (fl.get(key) or "").strip()
            if rel:
                p = os.path.join(maps_data_dir(), os.path.basename(rel))
                if os.path.isfile(p):
                    return p, key
        return None, None

    # -- تجهیزات --
    def add_device(self, floor_id, kind, name, x, y, ref_id="",
                   angle=0.0, fov=90.0):
        fl = self.get_floor(floor_id)
        if not fl:
            return None
        dev = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind if kind in DEVICE_KINDS else "other",
            "name": name,
            "ref_id": ref_id,   # id دوربین یا NVR در camera_store (اختیاری)
            "x": float(x), "y": float(y),
            "angle": float(angle),  # درجه؛ ۰ = سمت راست (شرق)، خلاف عقربه ساعت
            "fov": float(fov),      # زاویه‌ی دید دوربین (فقط برای camera)
        }
        fl["devices"].append(dev)
        self.save()
        return dev

    def update_device(self, floor_id, device_id, **fields):
        fl = self.get_floor(floor_id)
        if not fl:
            return False
        for dev in fl["devices"]:
            if dev.get("id") == device_id:
                dev.update(fields)
                self.save()
                return True
        return False

    def remove_device(self, floor_id, device_id):
        fl = self.get_floor(floor_id)
        if not fl:
            return False
        n = len(fl["devices"])
        fl["devices"] = [d for d in fl["devices"] if d.get("id") != device_id]
        if len(fl["devices"]) != n:
            self.save()
            return True
        return False

    def devices_by_camera(self, camera_id):
        """همه‌ی جای‌گذاری‌های یک دوربین روی همه‌ی طبقات: [(floor, device)]."""
        out = []
        for fl in self.data["floors"]:
            for dev in fl["devices"]:
                if dev.get("kind") == "camera" and str(dev.get("ref_id") or "") == str(camera_id):
                    out.append((fl, dev))
        return out


# ---------------------------------------------------------------------------
# رندر DXF
# ---------------------------------------------------------------------------
class DxfError(Exception):
    pass


def ezdxf_available():
    try:
        import ezdxf  # noqa: F401
        return True
    except ImportError:
        return False


class DxfMapLoader:
    """تبدیل فضای مدل یک فایل DXF به آیتم‌های QGraphicsScene.

    خروجی ``load()`` دیکشنری است با کلیدهای:
      - layers: {نام لایه: {"color": QColor, "item": QGraphicsPathItem, "count": int}}
      - texts: [QGraphicsSimpleTextItem, ...]
      - bounds: QRectF محدوده‌ی نقشه (واحد فایل، با Y معکوس‌شده)
      - units: کد $INSUNITS، units_fa: نام فارسی، to_meter: ضریب تبدیل به متر
    """

    def __init__(self):
        # ایمپورت دیرهنگام تا بدون ezdxf هم ماژول بالا بیاید
        try:
            import ezdxf
            from ezdxf import path as _epath
            from ezdxf.colors import aci2rgb
        except ImportError:
            raise DxfError(
                "کتابخانه‌ی ezdxf نصب نیست. برای نمایش نقشه‌های اتوکد:\n"
                "pip install ezdxf"
            )
        self._ezdxf = ezdxf
        self._epath = _epath
        self._aci2rgb = aci2rgb

    # -- رنگ --
    def _entity_color(self, entity, doc):
        try:
            rgb = entity.rgb  # رنگ TrueColor
            if rgb:
                return rgb
        except Exception:
            pass
        aci = 256
        try:
            aci = int(entity.dxf.get("color", 256))
        except Exception:
            pass
        if aci in (0, 256):
            try:
                layer = doc.layers.get(entity.dxf.layer)
                aci = int(layer.color)
            except Exception:
                aci = 7
        if aci in (0, 256):
            aci = 7
        try:
            return self._aci2rgb(aci)
        except Exception:
            return (255, 255, 255)

    def _iter_flattened(self, entity, flatten_dist):
        """هر Entity را به چندخطی‌های تخت (لیست نقاط Vec3) تبدیل می‌کند.
        دایره/کمان/بیضی به‌صورت پارامتریک با گام ۵ درجه تولید می‌شوند تا در
        هر زومی کاملاً صاف دیده شوند."""
        import math
        etype = entity.dxftype()
        try:
            if etype == "CIRCLE":
                c = entity.dxf.center
                r = float(entity.dxf.radius)
                n = 72
                yield [self._v(c.x + r * math.cos(2 * math.pi * i / n),
                               c.y + r * math.sin(2 * math.pi * i / n))
                       for i in range(n + 1)]
                return
            if etype == "ARC":
                c = entity.dxf.center
                r = float(entity.dxf.radius)
                a0 = math.radians(float(entity.dxf.start_angle))
                a1 = math.radians(float(entity.dxf.end_angle))
                if a1 <= a0:
                    a1 += 2 * math.pi
                n = max(8, int(math.degrees(a1 - a0) / 5) + 1)
                yield [self._v(c.x + r * math.cos(a0 + (a1 - a0) * i / n),
                               c.y + r * math.sin(a0 + (a1 - a0) * i / n))
                       for i in range(n + 1)]
                return
            if etype == "ELLIPSE":
                c = entity.dxf.center
                major = entity.dxf.major_axis
                ratio = float(entity.dxf.ratio or 0)
                p0 = float(entity.dxf.get("start_param", 0.0) or 0.0)
                p1 = float(entity.dxf.get("end_param", 2 * math.pi) or 2 * math.pi)
                if p1 <= p0:
                    p1 += 2 * math.pi
                n = max(16, int(math.degrees(p1 - p0) / 5) + 1)
                mx, my = major.x, major.y
                px, py = -my * ratio, mx * ratio
                yield [self._v(c.x + mx * math.cos(p0 + (p1 - p0) * i / n)
                               + px * math.sin(p0 + (p1 - p0) * i / n),
                               c.y + my * math.cos(p0 + (p1 - p0) * i / n)
                               + py * math.sin(p0 + (p1 - p0) * i / n))
                       for i in range(n + 1)]
                return
            if etype in ("LINE", "LWPOLYLINE", "POLYLINE", "SPLINE"):
                p = self._epath.make_path(entity)
                pts = list(p.flattening(flatten_dist))
                if len(pts) >= 2:
                    yield pts
            elif etype == "POINT":
                v = entity.dxf.location
                yield [v]
        except Exception:
            return

    @staticmethod
    def _v(x, y):
        from ezdxf.math import Vec3
        return Vec3(x, y, 0)

    def _iter_entities(self, layout):
        for e in layout:
            etype = e.dxftype()
            if etype == "INSERT":
                # باز کردن یک سطح بلاک (با احتیاط)؛ موجودیت‌های داخل بلاک
                # لایه‌ی خود INSERT را می‌گیرند تا با لایه‌اش یکپارچه شوند
                try:
                    insert_layer = str(e.dxf.get("layer", "0"))
                    for ve in e.virtual_entities():
                        if ve.dxftype() == "INSERT":
                            continue
                        try:
                            ve.dxf.layer = insert_layer
                        except Exception:
                            pass
                        yield ve
                except Exception:
                    continue
            else:
                yield e

    def load(self, dxf_path):
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QPainterPath, QPen
        from PyQt6.QtWidgets import QGraphicsPathItem, QGraphicsSimpleTextItem

        ezdxf = self._ezdxf
        try:
            doc = ezdxf.readfile(dxf_path)
        except Exception as ex:
            raise DxfError(f"خواندن فایل DXF ناموفق بود:\n{ex}")
        msp = doc.modelspace()

        try:
            insunits = int(doc.header.get("$INSUNITS", 0))
        except Exception:
            insunits = 0
        to_meter = INSUNITS_TO_METER.get(insunits, 1.0)
        # دقت تخت‌سازی قوس‌ها: حدود ۱۰ سانتی‌متر به واحد نقشه
        flatten_dist = max(0.1 / to_meter, 1e-6)

        layer_paths = {}   # layer -> QPainterPath
        layer_colors = {}
        texts = []
        minx = miny = float("inf")
        maxx = maxy = float("-inf")

        def feed(x, y):
            nonlocal minx, miny, maxx, maxy
            if x < minx: minx = x
            if x > maxx: maxx = x
            if y < miny: miny = y
            if y > maxy: maxy = y

        for entity in self._iter_entities(msp):
            etype = entity.dxftype()
            if etype in ("DIMENSION", "HATCH", "LEADER", "WIPEOUT",
                         "MULTILEADER", "IMAGE", "UNDERLAY"):
                continue
            layer = str(entity.dxf.get("layer", "0"))
            color = self._entity_color(entity, doc)
            if layer not in layer_paths:
                layer_paths[layer] = QPainterPath()
                layer_colors[layer] = color

            if etype in ("TEXT", "MTEXT"):
                try:
                    if etype == "TEXT":
                        ins = entity.dxf.insert
                        txt = entity.dxf.text or ""
                        h = float(entity.dxf.get("height", 2.5) or 2.5)
                        rot = float(entity.dxf.get("rotation", 0.0) or 0.0)
                    else:
                        ins = entity.dxf.insert
                        txt = (entity.text or "").replace("\\P", " ")
                        h = float(entity.dxf.get("char_height", 2.5) or 2.5)
                        rot = float(entity.dxf.get("rotation", 0.0) or 0.0)
                    if not txt.strip():
                        continue
                    item = QGraphicsSimpleTextItem(txt.strip().split("\n")[0][:80])
                    from PyQt6.QtGui import QFont
                    f = QFont()
                    f.setPixelSize(max(1, int(h)))
                    item.setFont(f)
                    item.setBrush(QColor(*color))
                    item.setPos(ins.x, -ins.y)
                    item.setRotation(-rot)
                    item.setData(0, f"dxf-text:{layer}")
                    texts.append(item)
                    feed(ins.x, -ins.y)
                except Exception:
                    continue
                continue

            for pts in self._iter_flattened(entity, flatten_dist):
                if len(pts) == 1:
                    # نقطه: یک ضربدر کوچک
                    v = pts[0]
                    s = 1.5
                    pp = layer_paths[layer]
                    x, y = v.x, -v.y
                    pp.moveTo(x - s, y - s); pp.lineTo(x + s, y + s)
                    pp.moveTo(x - s, y + s); pp.lineTo(x + s, y - s)
                    feed(x - s, y - s); feed(x + s, y + s)
                    continue
                pp = layer_paths[layer]
                first = True
                for v in pts:
                    x, y = v.x, -v.y
                    if first:
                        pp.moveTo(x, y)
                        first = False
                    else:
                        pp.lineTo(x, y)
                    feed(x, y)
                # بستن چندخطی‌های بسته
                try:
                    if etype in ("LWPOLYLINE", "POLYLINE") and bool(entity.closed):
                        v0 = pts[0]
                        pp.lineTo(v0.x, -v0.y)
                except Exception:
                    pass

        layers = {}
        for layer, qpath in layer_paths.items():
            if qpath.isEmpty():
                continue
            item = QGraphicsPathItem(qpath)
            r, g, b = layer_colors[layer]
            pen = QPen(QColor(r, g, b))
            pen.setWidth(0)  # cosmetic: همیشه ۱ پیکسل، تیز مثل اتوکد
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setData(0, f"dxf-layer:{layer}")
            layers[layer] = {"color": QColor(r, g, b), "item": item,
                             "count": 0}

        if minx == float("inf"):
            bounds = QRectF(0, 0, 100, 100)
        else:
            bounds = QRectF(minx, miny, maxx - minx, maxy - miny)

        return {
            "layers": layers,
            "texts": texts,
            "bounds": bounds,
            "units": insunits,
            "units_fa": INSUNITS_FA.get(insunits, "بدون واحد"),
            "to_meter": to_meter,
        }

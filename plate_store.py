# -*- coding: utf-8 -*-
"""ذخیره‌سازی پلاک‌های تعریف‌شده و گزارش عبور پلاک‌ها (SQLite).

این ماژول قلب سیستم پلاک‌خوان است و هیچ وابستگی سنگینی ندارد (فقط
sqlite3 استاندارد + cv2 برای ذخیره‌ی تصویر) تا هم در ترد اصلی و هم در
تردهای پس‌زمینه‌ی تشخیص دوربین‌ها امن استفاده شود (thread-safe با قفل).

دو جدول:
  plates: پلاک‌های تعریف‌شده توسط کاربر (با مشخصات کامل مالک/خودرو)
  plate_events: هر عبور ثبت‌شده (تعریف‌شده یا تعریف‌نشده) با تصویر برش‌خورده

نرمال‌سازی پلاک: ارقام فارسی/عربی به لاتین تبدیل و جداکننده‌ها حذف
می‌شوند؛ «ی/ك» عربی یکدست می‌شود. فرم کانونیکال مثلاً «12ب34567» است و
تطبیق (دقیق + فازی) روی همین فرم انجام می‌شود تا خطای OCR (مثلاً ۵ به‌جای ۶)
باعث از دست رفتن تطبیق نشود.
"""

import csv
import difflib
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime


# --------------------------------------------------------------------------
# نرمال‌سازی متن پلاک
# --------------------------------------------------------------------------

_FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_EN_DIGITS = "0123456789"
_SEPARATORS = set(" \t\n\r-–—_|/\\.,،؛:()[]{}'\"«»")


def normalize_plate_text(text):
    """متن خام (خروجی OCR یا ورودی کاربر) را به فرم کانونیکال تبدیل می‌کند:
    ارقام فارسی/عربی -> لاتین، حذف فاصله و جداکننده‌ها، یکدست‌سازی ی/ک."""
    if text is None:
        return ""
    out = []
    for ch in str(text):
        if ch in _FA_DIGITS:
            out.append(_EN_DIGITS[_FA_DIGITS.index(ch)])
        elif ch in _AR_DIGITS:
            out.append(_EN_DIGITS[_AR_DIGITS.index(ch)])
        elif ch == "ي":
            out.append("ی")
        elif ch == "ك":
            out.append("ک")
        elif ch in _SEPARATORS:
            continue
        else:
            out.append(ch)
    return "".join(out).strip()


def prettify_plate(canonical):
    """فرم کانونیکال را برای نمایش قشنگ می‌کند — دقیقاً به ترتیب دیداری
    پلاک فیزیکی (از چپ‌به‌راست: ۲ رقم، حرف، ۳ رقم، کد ایران).
    - پلاک خودروی ایرانی: «۴۰ ۶۲۹ ن ۴۳» (logical؛ در UI راست‌به‌چپ به‌صورت
      «۴۳ ن ۶۲۹ ۴۰» دیده می‌شود، دقیقاً مثل پلاک واقعی)
    - پلاک موتورسیکلت ایرانی (۳ رقم بالا + ۱ رقم و حرف پایین): «۱۲۳ ۴ب»
    در غیر این صورت همان متن را برمی‌گرداند."""
    if not canonical:
        return ""
    import re
    m = re.match(r"^([0-9]{2})([^0-9]{1,2})([0-9]{3})([0-9]{2})$", canonical)
    fa = str.maketrans(_EN_DIGITS, _FA_DIGITS)
    if m:
        d1, letter, d2, code = m.groups()
        # ترتیب نمایشی معکوس کانونیکال است تا در UI راست‌به‌چپ،
        # دقیقاً مثل پلاک فیزیکی دیده شود (کد ایران سمت راست).
        return (f"{code.translate(fa)} {d2.translate(fa)} "
                f"{letter} {d1.translate(fa)}")
    m2 = re.match(r"^([0-9]{3})([0-9])([^0-9]{1,2})$", canonical)
    if m2:
        top, bottom_digit, letter = m2.groups()
        return (f"{top.translate(fa)} "
                f"{bottom_digit.translate(fa)}{letter}")
    return canonical


def prettify_plate_html(canonical):
    """نسخه‌ی HTML پلاک خودرو برای نمایش در جدول/لیبل (راست‌به‌چپ)؛
    کد ایران داخل یک کادر مربعی می‌آید، دقیقاً مثل پلاک فیزیکی.
    برای موتورسیکلت همان prettify_plate برمی‌گردد."""
    if not canonical:
        return ""
    import re
    m = re.match(r"^([0-9]{2})([^0-9]{1,2})([0-9]{3})([0-9]{2})$", canonical)
    fa = str.maketrans(_EN_DIGITS, _FA_DIGITS)
    if m:
        d1, letter, d2, code = m.groups()
        code_fa = code.translate(fa)
        d2_fa = d2.translate(fa)
        d1_fa = d1.translate(fa)
        return (
            '<span style="border:1.5px solid #7aa2ff; border-radius:4px; '
            'padding:0px 6px; background:#16233a; font-weight:bold;">'
            f'{code_fa}</span>'
            f' {d2_fa} {letter} {d1_fa}'
        )
    return prettify_plate(canonical)


# حروف مجاز پلاک‌های ایرانی (برای کمبوباکس فرم تعریف)
IRANIAN_PLATE_LETTERS = [
    "الف", "ب", "پ", "ت", "ث", "ج", "چ", "ح", "خ", "د", "ذ", "ر", "ز", "ژ",
    "س", "ش", "ص", "ط", "ظ", "ع", "غ", "ف", "ق", "ک", "گ", "ل", "م", "ن",
    "و", "ه", "ی",
]

VEHICLE_TYPES = [
    "سواری", "وانت", "کامیونت", "کامیون", "تریلی", "اتوبوس", "مینی‌بوس",
    "موتورسیکلت", "تشریفاتی", "سایر",
]

VEHICLE_COLORS = [
    "سفید", "مشکی", "نقره‌ای", "خاکستری", "قرمز", "آبی", "سرمه‌ای", "سبز",
    "زرد", "نارنجی", "قهوه‌ای", "بژ", "سایر",
]


def validate_iranian_plate(d1, letter, d2, code):
    """اعتبارسنجی بخش‌های پلاک ایرانی. خروجی: (معتبر؟, پیام خطا, کانونیکال)."""
    import re
    if not re.match(r"^[0-9۰-۹٠-٩]{2}$", d1 or ""):
        return False, "دو رقم اول پلاک باید دقیقاً ۲ رقم باشد.", ""
    if not letter or letter not in IRANIAN_PLATE_LETTERS:
        return False, "حرف پلاک معتبر نیست.", ""
    if not re.match(r"^[0-9۰-۹٠-٩]{3}$", d2 or ""):
        return False, "سه رقم میانی پلاک باید دقیقاً ۳ رقم باشد.", ""
    if not re.match(r"^[0-9۰-۹٠-٩]{2}$", code or ""):
        return False, "کد ایران (دو رقم آخر) باید دقیقاً ۲ رقم باشد.", ""
    canonical = normalize_plate_text(d1 + letter + d2 + code)
    return True, "", canonical


def validate_phone(phone):
    """شماره موبایل ایرانی (اختیاری): خالی یا 09xxxxxxxxx."""
    import re
    phone = (phone or "").strip()
    if not phone:
        return True, ""
    phone = normalize_plate_text(phone)
    if re.match(r"^09[0-9]{9}$", phone):
        return True, phone
    return False, phone


# --------------------------------------------------------------------------
# نوع پلاک: خودرو / موتورسیکلت / سایر
# --------------------------------------------------------------------------

# پلاک موتورسیکلت ایرانی: ۳ رقم در ردیف بالا + ۱ رقم و ۱ حرف در ردیف پایین
# فرم کانونیکال: «1234ب» (سه رقم بالا، یک رقم پایین، حرف)
PLATE_KIND_LABELS = {
    "car": "خودرو",
    "motorcycle": "موتورسیکلت",
    "other": "سایر",
}


def detect_plate_kind(canonical):
    """تشخیص نوع پلاک از روی شکل متن کانونیکال.
    خروجی: 'car' | 'motorcycle' | 'other'"""
    import re
    c = (canonical or "").strip()
    if re.match(r"^[0-9]{2}[^0-9]{1,2}[0-9]{5}$", c):
        return "car"
    if re.match(r"^[0-9]{4}[^0-9]{1,2}$", c):
        return "motorcycle"
    return "other"


def plate_kind_label(kind):
    """برچسب فارسی نوع پلاک."""
    return PLATE_KIND_LABELS.get(kind or "other", "سایر")


def validate_motorcycle_plate(d_top, d_bottom, letter):
    """اعتبارسنجی بخش‌های پلاک موتورسیکلت ایرانی
    (۳ رقم بالا + ۱ رقم پایین + حرف). خروجی: (معتبر؟, پیام خطا, کانونیکال)."""
    import re
    if not re.match(r"^[0-9۰-۹٠-٩]{3}$", d_top or ""):
        return False, "سه رقم بالای پلاک موتور باید دقیقاً ۳ رقم باشد.", ""
    if not re.match(r"^[0-9۰-۹٠-٩]{1}$", d_bottom or ""):
        return False, "رقم پایین پلاک موتور باید دقیقاً ۱ رقم باشد.", ""
    if not letter or letter not in IRANIAN_PLATE_LETTERS:
        return False, "حرف پلاک معتبر نیست.", ""
    canonical = normalize_plate_text(d_top + d_bottom + letter)
    return True, "", canonical


# --------------------------------------------------------------------------
# تبدیل تاریخ میلادی به شمسی (بدون وابستگی خارجی - الگوریتم jalaali)
# --------------------------------------------------------------------------

def _div(a, b):
    return int(a / b)


def _mod(a, b):
    return a - _div(a, b) * b


_JALALI_BREAKS = [
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
]


def _jal_cal(jy):
    bl = len(_JALALI_BREAKS)
    gy = jy + 621
    leap_j = -14
    jp = _JALALI_BREAKS[0]
    jump = 0
    for i in range(1, bl):
        jm = _JALALI_BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return leap, gy, march


def _g2d(gy, gm, gd):
    d = (_div((gy + _div(gm - 8, 6) + 100100) * 1461, 4)
         + _div(153 * _mod(gm + 9, 12) + 2, 5)
         + gd - 34840408)
    d = d - _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4) + 752
    return d


def _d2g(jdn):
    j = 4 * jdn + 139361631
    j = j + _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _div(_mod(j, 1461), 4) * 5 + 308
    gd = _div(_mod(i, 153), 5) + 1
    gm = _mod(_div(i, 153), 12) + 1
    gy = _div(j, 1461) - 100100 + _div(8 - gm, 6)
    return gy, gm, gd


def _d2j(jdn):
    gy, gm, gd = _d2g(jdn)
    jy = gy - 621
    leap, _, march = _jal_cal(jy)
    jdn1f = _g2d(gy, 3, march)
    k = jdn - jdn1f
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


def gregorian_to_jalali(gy, gm, gd):
    """(سال، ماه، روز) میلادی -> (سال، ماه، روز) شمسی."""
    return _d2j(_g2d(gy, gm, gd))


def jalali_now_str(ts=None):
    """رشته‌ی تاریخ/ساعت شمسی «YYYY/MM/DD HH:MM:SS» برای نمایش در گزارش‌ها."""
    dt = datetime.fromtimestamp(ts) if ts else datetime.now()
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def jalali_date_str(ts=None):
    dt = datetime.fromtimestamp(ts) if ts else datetime.now()
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


# --------------------------------------------------------------------------
# دیتابیس
# --------------------------------------------------------------------------

def _default_base_dir():
    """پوشه‌ی داده‌ی پلاک‌خوان: کنار فایل اجرایی (حالت portable) یا پوشه‌ی جاری."""
    for base in (
        os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else "",
        os.getcwd(),
    ):
        if not base:
            continue
        try:
            d = os.path.join(base, "plate_data")
            os.makedirs(d, exist_ok=True)
            # تست نوشتن واقعی (ممکن است پوشه فقط‌خواندنی باشد)
            probe = os.path.join(d, ".write_probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except Exception:
            continue
    d = os.path.join(os.path.expanduser("~"), "plate_data")
    os.makedirs(d, exist_ok=True)
    return d


class PlateStore:
    """مدیریت پلاک‌های تعریف‌شده و گزارش عبور. Thread-safe."""

    def __init__(self, db_path=None):
        self._lock = threading.RLock()
        base = os.path.dirname(db_path) if db_path else _default_base_dir()
        os.makedirs(base, exist_ok=True)
        self.base_dir = base
        self.snapshot_dir = os.path.join(base, "snapshots")
        os.makedirs(self.snapshot_dir, exist_ok=True)
        self.db_path = db_path or os.path.join(base, "plates.db")
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        self._plates_cache = None  # کش لیست پلاک‌های فعال برای تطبیق سریع در ترد تشخیص

    # ------------------------------------------------------------- ساختار -

    def _create_tables(self):
        with self._lock:
            c = self._conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS plates (
                    id TEXT PRIMARY KEY,
                    plate_text TEXT UNIQUE NOT NULL,
                    plate_display TEXT,
                    owner_name TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    vehicle_type TEXT DEFAULT '',
                    vehicle_model TEXT DEFAULT '',
                    vehicle_color TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    sample_image TEXT DEFAULT '',
                    created_at REAL,
                    updated_at REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS plate_events (
                    id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    date_g TEXT NOT NULL,
                    time_g TEXT NOT NULL,
                    date_j TEXT NOT NULL,
                    camera_name TEXT DEFAULT '',
                    nvr_id TEXT DEFAULT '',
                    channel INTEGER,
                    plate_text TEXT DEFAULT '',
                    plate_display TEXT DEFAULT '',
                    plate_id TEXT,
                    owner_name TEXT DEFAULT '',
                    is_defined INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0,
                    snapshot_path TEXT DEFAULT '',
                    reviewed INTEGER DEFAULT 0
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON plate_events(ts)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_cam ON plate_events(camera_name)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS plate_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self._conn.commit()
            # مهاجرت: ستون نوع پلاک (برای دیتابیس‌های قدیمی که این ستون را ندارند)
            cols = [r["name"] for r in
                    c.execute("PRAGMA table_info(plates)").fetchall()]
            if "plate_type" not in cols:
                c.execute("ALTER TABLE plates ADD COLUMN plate_type TEXT DEFAULT ''")
                for r in c.execute("SELECT id, plate_text FROM plates").fetchall():
                    kind = detect_plate_kind(r["plate_text"] or "")
                    c.execute("UPDATE plates SET plate_type=? WHERE id=?",
                              (kind, r["id"]))
                self._conn.commit()

    # ------------------------------------------------------------- تنظیمات -

    def get_setting(self, key, default=""):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM plate_settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO plate_settings(key, value) VALUES(?, ?)",
                (key, str(value)))
            self._conn.commit()

    @property
    def match_threshold(self):
        try:
            return float(self.get_setting("match_threshold", "0.82"))
        except ValueError:
            return 0.82

    @match_threshold.setter
    def match_threshold(self, v):
        self.set_setting("match_threshold", str(float(v)))

    @property
    def cooldown_seconds(self):
        try:
            return int(float(self.get_setting("cooldown_seconds", "45")))
        except ValueError:
            return 45

    # ------------------------------------------------- پلاک‌های تعریف‌شده -

    def _invalidate_cache(self):
        self._plates_cache = None

    def _row_to_plate(self, row):
        keys = row.keys() if hasattr(row, "keys") else []
        plate_type = row["plate_type"] if "plate_type" in keys else ""
        return {
            "id": row["id"],
            "plate_text": row["plate_text"],
            "plate_display": row["plate_display"] or prettify_plate(row["plate_text"]),
            "plate_type": plate_type or detect_plate_kind(row["plate_text"]),
            "owner_name": row["owner_name"] or "",
            "phone": row["phone"] or "",
            "vehicle_type": row["vehicle_type"] or "",
            "vehicle_model": row["vehicle_model"] or "",
            "vehicle_color": row["vehicle_color"] or "",
            "description": row["description"] or "",
            "active": bool(row["active"]),
            "sample_image": row["sample_image"] or "",
            "created_at": row["created_at"],
            "created_jalali": jalali_now_str(row["created_at"]) if row["created_at"] else "",
        }

    def add_plate(self, plate_text, owner_name="", phone="", vehicle_type="",
                  vehicle_model="", vehicle_color="", description="",
                  active=True, sample_image="", plate_display="", plate_type=""):
        """افزودن پلاک جدید. خروجی: (True, id) یا (False, پیام خطا فارسی)."""
        canonical = normalize_plate_text(plate_text)
        if len(canonical) < 3:
            return False, "متن پلاک معتبر نیست (خیلی کوتاه است)."
        ok_phone, phone_norm = validate_phone(phone)
        if not ok_phone:
            return False, "شماره تلفن باید به شکل 09xxxxxxxxx باشد (یا خالی بماند)."
        kind = plate_type or detect_plate_kind(canonical)
        if kind not in PLATE_KIND_LABELS:
            kind = "other"
        with self._lock:
            exists = self._conn.execute(
                "SELECT id FROM plates WHERE plate_text=?", (canonical,)).fetchone()
            if exists:
                return False, "این پلاک قبلاً در لیست تعریف شده است."
            pid = uuid.uuid4().hex
            now = time.time()
            self._conn.execute(
                """INSERT INTO plates(id, plate_text, plate_display, plate_type,
                                      owner_name, phone,
                                      vehicle_type, vehicle_model, vehicle_color,
                                      description, active, sample_image,
                                      created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, canonical, plate_display or prettify_plate(canonical), kind,
                 owner_name.strip(), phone_norm, vehicle_type, vehicle_model,
                 vehicle_color, description.strip(), 1 if active else 0,
                 sample_image, now, now))
            self._conn.commit()
            self._invalidate_cache()
            return True, pid

    def update_plate(self, pid, **fields):
        allowed = {"owner_name", "phone", "vehicle_type", "vehicle_model",
                   "vehicle_color", "description", "active", "sample_image",
                   "plate_display", "plate_type"}
        if "phone" in fields:
            ok_phone, phone_norm = validate_phone(fields["phone"])
            if not ok_phone:
                return False, "شماره تلفن باید به شکل 09xxxxxxxxx باشد (یا خالی بماند)."
            fields["phone"] = phone_norm
        if "plate_type" in fields and fields["plate_type"] not in PLATE_KIND_LABELS:
            fields["plate_type"] = "other"
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "active":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return False, "فیلدی برای به‌روزرسانی نیست."
        sets.append("updated_at=?")
        vals.append(time.time())
        vals.append(pid)
        with self._lock:
            self._conn.execute(
                f"UPDATE plates SET {', '.join(sets)} WHERE id=?", vals)
            self._conn.commit()
            self._invalidate_cache()
        return True, ""

    def delete_plate(self, pid):
        with self._lock:
            self._conn.execute("DELETE FROM plates WHERE id=?", (pid,))
            self._conn.commit()
            self._invalidate_cache()

    def set_plate_active(self, pid, active):
        return self.update_plate(pid, active=bool(active))

    def get_plate(self, pid):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM plates WHERE id=?", (pid,)).fetchone()
            return self._row_to_plate(row) if row else None

    def list_plates(self, active_only=False, search=""):
        with self._lock:
            q = "SELECT * FROM plates"
            conds, vals = [], []
            if active_only:
                conds.append("active=1")
            if search:
                s = f"%{search}%"
                conds.append("(plate_text LIKE ? OR owner_name LIKE ? OR phone LIKE ? OR vehicle_model LIKE ?)")
                vals += [s, s, s, s]
            if conds:
                q += " WHERE " + " AND ".join(conds)
            q += " ORDER BY created_at DESC"
            rows = self._conn.execute(q, vals).fetchall()
            return [self._row_to_plate(r) for r in rows]

    def count_plates(self, active_only=False):
        with self._lock:
            q = "SELECT COUNT(*) c FROM plates"
            if active_only:
                q += " WHERE active=1"
            return self._conn.execute(q).fetchone()["c"]

    # ------------------------------------------------------------- تطبیق -

    def _active_plates_cached(self):
        # کش کوتاه‌مدت برای تطبیق سریع در ترد تشخیص (بدون کوئری مداوم).
        if self._plates_cache is None:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM plates WHERE active=1").fetchall()
                self._plates_cache = [self._row_to_plate(r) for r in rows]
        return self._plates_cache

    def find_match(self, plate_text, threshold=None):
        """تطبیق متن خوانده‌شده با پلاک‌های تعریف‌شده‌ی فعال.
        تطبیق دقیق روی همه‌ی پلاک‌ها انجام می‌شود؛ تطبیق فازی فقط بین
        پلاک‌های هم‌نوع (خودرو/موتورسیکلت) به‌علاوه‌ی پلاک‌های «سایر»، تا
        خوانش یک موتور اشتباهی به پلاک یک خودرو نسبت داده نشود.
        خروجی: (plate_dict یا None, امتیاز 0..1, نوع تطبیق: exact/fuzzy/none)."""
        canonical = normalize_plate_text(plate_text)
        if not canonical:
            return None, 0.0, "none"
        if threshold is None:
            threshold = self.match_threshold
        plates = self._active_plates_cached()
        for p in plates:
            if p["plate_text"] == canonical:
                return p, 1.0, "exact"
        kind = detect_plate_kind(canonical)
        if kind == "other":
            pool = plates
        else:
            pool = [p for p in plates
                    if (p.get("plate_type") or "other") in ("other", kind)]
        best, best_score = None, 0.0
        for p in pool:
            s = difflib.SequenceMatcher(None, canonical, p["plate_text"]).ratio()
            if s > best_score:
                best, best_score = p, s
        if best is not None and best_score >= threshold:
            return best, best_score, "fuzzy"
        return None, best_score, "none"

    # ------------------------------------------------------------- رویدادها -

    def _save_snapshot(self, crop_bgr, ts):
        """ذخیره‌ی تصویر برش‌خورده‌ی پلاک؛ خروجی مسیر فایل یا رشته‌ی خالی."""
        if crop_bgr is None:
            return ""
        try:
            import cv2
            dt = datetime.fromtimestamp(ts)
            day_dir = os.path.join(self.snapshot_dir, dt.strftime("%Y-%m-%d"))
            os.makedirs(day_dir, exist_ok=True)
            name = dt.strftime("%H%M%S") + f"_{uuid.uuid4().hex[:6]}.jpg"
            path = os.path.join(day_dir, name)
            ok, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                with open(path, "wb") as f:
                    f.write(buf.tobytes())
                return path
        except Exception:
            pass
        return ""

    def log_event(self, camera_name, plate_text, confidence=0.0, crop_bgr=None,
                  nvr_id="", channel=None, plate_display=""):
        """ثبت یک عبور. تطبیق با پلاک‌های تعریف‌شده همین‌جا انجام می‌شود.
        خروجی: دیکشنری رویداد ثبت‌شده."""
        canonical = normalize_plate_text(plate_text)
        ts = time.time()
        dt = datetime.fromtimestamp(ts)
        match, score, kind = self.find_match(canonical)
        snapshot = self._save_snapshot(crop_bgr, ts)
        eid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """INSERT INTO plate_events(id, ts, date_g, time_g, date_j,
                                            camera_name, nvr_id, channel,
                                            plate_text, plate_display, plate_id,
                                            owner_name, is_defined, confidence,
                                            snapshot_path, reviewed)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (eid, ts, dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"),
                 jalali_date_str(ts), camera_name or "", nvr_id or "",
                 channel, canonical,
                 plate_display or prettify_plate(canonical),
                 match["id"] if match else None,
                 match["owner_name"] if match else "",
                 1 if match else 0, float(confidence), snapshot))
            self._conn.commit()
        return {
            "id": eid, "ts": ts, "camera_name": camera_name or "",
            "plate_text": canonical,
            "plate_display": plate_display or prettify_plate(canonical),
            "is_defined": bool(match), "match_kind": kind, "match_score": score,
            "owner_name": match["owner_name"] if match else "",
            "plate": match, "confidence": float(confidence),
            "snapshot_path": snapshot,
            "date_j": jalali_date_str(ts), "time_g": dt.strftime("%H:%M:%S"),
        }

    def query_events(self, date_from=None, date_to=None, camera_name=None,
                     defined=None, search="", limit=5000, kind=None):
        """گزارش عبور با فیلتر. date_from/date_to رشته‌ی 'YYYY-MM-DD' میلادی.
        defined: True/False/None (همه). kind: 'car'/'motorcycle'/'other'/None."""
        with self._lock:
            conds, vals = [], []
            if date_from:
                conds.append("date_g>=?")
                vals.append(date_from)
            if date_to:
                conds.append("date_g<=?")
                vals.append(date_to)
            if camera_name:
                conds.append("camera_name=?")
                vals.append(camera_name)
            if defined is True:
                conds.append("is_defined=1")
            elif defined is False:
                conds.append("is_defined=0")
            if search:
                s = f"%{search}%"
                conds.append("(plate_text LIKE ? OR plate_display LIKE ? OR owner_name LIKE ?)")
                vals += [s, s, s]
            q = "SELECT * FROM plate_events"
            if conds:
                q += " WHERE " + " AND ".join(conds)
            q += " ORDER BY ts DESC LIMIT ?"
            vals.append(int(limit))
            rows = self._conn.execute(q, vals).fetchall()
            rows = [dict(r) for r in rows]
        if kind in ("car", "motorcycle", "other"):
            rows = [r for r in rows
                    if detect_plate_kind(r.get("plate_text", "")) == kind]
        return rows

    def distinct_event_cameras(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT camera_name FROM plate_events ORDER BY camera_name").fetchall()
            return [r["camera_name"] for r in rows if r["camera_name"]]

    def delete_event(self, eid):
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot_path FROM plate_events WHERE id=?", (eid,)).fetchone()
            self._conn.execute("DELETE FROM plate_events WHERE id=?", (eid,))
            self._conn.commit()
        if row and row["snapshot_path"]:
            try:
                os.remove(row["snapshot_path"])
            except Exception:
                pass

    def get_event(self, eid):
        """خواندن یک رویداد با شناسه (dict یا None)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM plate_events WHERE id=?", (eid,)).fetchone()
            return dict(row) if row else None

    def mark_reviewed(self, eid, reviewed=True):
        with self._lock:
            self._conn.execute("UPDATE plate_events SET reviewed=? WHERE id=?",
                               (1 if reviewed else 0, eid))
            self._conn.commit()

    def attach_event_to_plate(self, event_id, plate):
        """اتصال یک رویداد (پلاک ناشناس) به پلاک تازه‌تعریف‌شده: وضعیت رویداد
        به «تعریف‌شده» تغییر می‌کند و مالک هم ثبت می‌شود."""
        with self._lock:
            self._conn.execute(
                """UPDATE plate_events
                   SET plate_id=?, is_defined=1, owner_name=?, reviewed=1
                   WHERE id=?""",
                (plate["id"], plate.get("owner_name", ""), event_id))
            self._conn.commit()

    def stats(self):
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) c FROM plate_events").fetchone()["c"]
            defined = self._conn.execute(
                "SELECT COUNT(*) c FROM plate_events WHERE is_defined=1").fetchone()["c"]
            today = datetime.now().strftime("%Y-%m-%d")
            today_n = self._conn.execute(
                "SELECT COUNT(*) c FROM plate_events WHERE date_g=?", (today,)).fetchone()["c"]
            return {"total": total, "defined": defined, "undefined": total - defined,
                    "today": today_n, "plates": self.count_plates(),
                    "plates_active": self.count_plates(active_only=True)}

    def export_events_csv(self, path, date_from=None, date_to=None,
                          camera_name=None, defined=None, search="", kind=None):
        rows = self.query_events(date_from=date_from, date_to=date_to,
                                 camera_name=camera_name, defined=defined,
                                 search=search, kind=kind, limit=100000)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["تاریخ شمسی", "ساعت", "دوربین", "پلاک", "نوع پلاک", "مالک",
                        "وضعیت", "اطمینان", "تصویر"])
            for r in rows:
                w.writerow([
                    r.get("date_j", ""), r.get("time_g", ""),
                    r.get("camera_name", ""), r.get("plate_display", ""),
                    plate_kind_label(detect_plate_kind(r.get("plate_text", ""))),
                    r.get("owner_name", ""),
                    "تعریف‌شده" if r.get("is_defined") else "تعریف‌نشده",
                    f"{(r.get('confidence') or 0):.0%}",
                    r.get("snapshot_path", ""),
                ])
        return len(rows)

    def cleanup_old_snapshots(self, days=30, max_files=3000):
        """حذف تصاویر قدیمی‌تر از days روز (جلوگیری از پر شدن دیسک)."""
        import glob
        cutoff = time.time() - days * 86400
        removed = 0
        try:
            files = sorted(glob.glob(os.path.join(self.snapshot_dir, "*", "*.jpg")))
            for p in files:
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        removed += 1
                except Exception:
                    pass
            # اگر باز هم زیاد بود، قدیمی‌ترین‌ها حذف شوند
            files = sorted(glob.glob(os.path.join(self.snapshot_dir, "*", "*.jpg")),
                           key=os.path.getmtime)
            while len(files) - removed > max_files and files:
                try:
                    os.remove(files.pop(0))
                    removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        return removed


# نمونه‌ی سراسری (مثل report_store): همه‌ی بخش‌های برنامه از همین استفاده می‌کنند.
plate_store = PlateStore()

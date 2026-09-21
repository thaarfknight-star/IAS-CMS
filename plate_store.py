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
import json
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


# تبدیل تاریخ میلادی به شمسی از ماژول مشترک jalali.py می‌آید
# (تک‌منبع حقیقت؛ قبلاً همین‌جا کپیِ محلیِ الگوریتم jalaali بود).
from jalali import gregorian_to_jalali, jalali_now_str, jalali_date_str


# --------------------------------------------------------------------------
# دیتابیس
# --------------------------------------------------------------------------

def _default_base_dir():
    """پوشه‌ی داده‌ی پلاک‌خوان: پوشه‌ی یکتای دیتای کاربر (app_paths) تا نسخه‌ی
    نصب‌شده و portable دیتابیس جدا نسازند؛ در حالت توسعه مثل قبل."""
    try:
        from app_paths import get_data_dir
        d = os.path.join(get_data_dir(), "plate_data")
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return d
    except Exception:
        pass
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
            # (2.0.15-beta) موتور قوانین جهت تردد پلاک‌خوان: وضعیت لحظه‌ای
            # هر پلاک + لاگ عبورها با جهت/مسیر + تخلفات.
            c.execute("""
                CREATE TABLE IF NOT EXISTS plate_states (
                    plate_text TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    last_event_id TEXT DEFAULT '',
                    last_camera_id TEXT DEFAULT '',
                    last_ts REAL,
                    updated_at REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS plate_crossings (
                    id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    date_g TEXT NOT NULL,
                    time_g TEXT NOT NULL,
                    date_j TEXT NOT NULL,
                    plate_text TEXT NOT NULL,
                    plate_display TEXT DEFAULT '',
                    plate_id TEXT,
                    owner_name TEXT DEFAULT '',
                    camera_id TEXT DEFAULT '',
                    camera_name TEXT DEFAULT '',
                    lane_id TEXT DEFAULT '',
                    crossing_type TEXT NOT NULL,
                    travel TEXT DEFAULT '',
                    event_id TEXT DEFAULT '',
                    snapshot_path TEXT DEFAULT ''
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_cross_plate ON plate_crossings(plate_text, ts)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_cross_ts ON plate_crossings(ts)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS plate_violations (
                    id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    date_g TEXT NOT NULL,
                    time_g TEXT NOT NULL,
                    date_j TEXT NOT NULL,
                    violation_type TEXT NOT NULL,
                    plate_text TEXT NOT NULL,
                    plate_display TEXT DEFAULT '',
                    plate_id TEXT,
                    owner_name TEXT DEFAULT '',
                    camera_id TEXT DEFAULT '',
                    camera_name TEXT DEFAULT '',
                    lane_id TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    crossing_id TEXT DEFAULT '',
                    snapshot_path TEXT DEFAULT '',
                    acknowledged INTEGER DEFAULT 0
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_pviol_plate ON plate_violations(plate_text, ts)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pviol_ts ON plate_violations(ts)")
            # جدول لیست تحت‌نظر پلاک‌ها (2.0.18-beta): لیست سیاه/سفید
            c.execute("""
                CREATE TABLE IF NOT EXISTS plate_watchlist (
                    id TEXT PRIMARY KEY,
                    plate_text TEXT NOT NULL,
                    plate_display TEXT DEFAULT '',
                    kind TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    created_ts REAL NOT NULL,
                    created_date_j TEXT DEFAULT ''
                )
            """)
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_unique ON plate_watchlist(plate_text, kind)")
            # مهاجرت: لینک دوربین/مسیر روی رویدادهای عبور قدیمی
            ev_cols = [r["name"] for r in
                       c.execute("PRAGMA table_info(plate_events)").fetchall()]
            if "camera_id" not in ev_cols:
                c.execute("ALTER TABLE plate_events ADD COLUMN camera_id TEXT DEFAULT ''")
            if "lane_id" not in ev_cols:
                c.execute("ALTER TABLE plate_events ADD COLUMN lane_id TEXT DEFAULT ''")
            # مهاجرت 2.0.17-beta: آخرین مسیر/ترتیب دوربین برای قانون د
            st_cols = [r["name"] for r in
                       c.execute("PRAGMA table_info(plate_states)").fetchall()]
            if "last_lane_id" not in st_cols:
                c.execute("ALTER TABLE plate_states ADD COLUMN last_lane_id TEXT DEFAULT ''")
            if "last_lane_order" not in st_cols:
                c.execute("ALTER TABLE plate_states ADD COLUMN last_lane_order INTEGER DEFAULT -1")
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

    # --------------------------------------- موتور جهت تردد (2.0.15-beta) --

    VIOLATION_LABELS = {
        "exit_without_entry": "خروج بدون ورود ثبت‌شده",
        "reentry_without_exit": "ورود مجدد بدون خروج قبلی",
        "wrong_way": "تردد خلاف جهت مجاز مسیر",
        "watchlist_black": "⛔ پلاک در لیست سیاه",
        "watchlist_white": "⭐ پلاک در لیست سفید",
    }

    WATCHLIST_LABELS = {"black": "⛔ لیست سیاه", "white": "⭐ لیست سفید"}

    CROSSING_LABELS = {"entry": "ورود", "exit": "خروج"}
    TRAVEL_LABELS = {"going": "رفت", "return": "برگشت"}

    def get_lanes(self):
        """تعریف مسیرها: {lane_id: {"name", "allowed", ...}}.

        (2.0.17-beta) اگر دیتابیس خالی باشد، دو مسیر پیش‌فرض ساخته و ذخیره
        می‌شود: «مسیر ۱» فقط رفت و «مسیر ۲» فقط برگشت.
        رکورد مسیر رسم‌شده روی نقشه این کلیدها را هم دارد:
        floor_id, points ([[x,y],...] به واحد صحنه), to_meter,
        cameras ([{"camera_id", "order"}] مرتب‌شده از ابتدای مسیر).
        """
        try:
            raw = self.get_setting("lanes", "")
            lanes = json.loads(raw) if raw else {}
            if not isinstance(lanes, dict):
                lanes = {}
        except Exception:
            lanes = {}
        if not lanes:
            lanes = {
                "lane1": {"name": "مسیر ۱", "allowed": "going"},
                "lane2": {"name": "مسیر ۲", "allowed": "return"},
            }
            try:
                self.set_lanes(lanes)
            except Exception:
                pass
        return lanes

    def set_lanes(self, lanes):
        self.set_setting("lanes", json.dumps(lanes or {}, ensure_ascii=False))

    @staticmethod
    def lane_camera_order(lane, camera_id):
        """ترتیب دوربین در مسیر رسم‌شده؛ None اگر دوربین عضو مسیر نیست."""
        cams = (lane or {}).get("cameras") or []
        for c in cams:
            if str(c.get("camera_id")) == str(camera_id):
                try:
                    return int(c.get("order", 0))
                except (TypeError, ValueError):
                    return 0
        return None

    def set_plate_lane(self, plate_text, lane_id="", lane_order=None):
        """به‌روزرسانی آخرین مسیر/ترتیب دوربین پلاک (برای قانون د)."""
        canonical = normalize_plate_text(plate_text)
        order = -1 if lane_order is None else int(lane_order)
        with self._lock:
            self._conn.execute(
                """INSERT INTO plate_states(plate_text, state, last_lane_id,
                                            last_lane_order, last_ts, updated_at)
                   VALUES(?, 'outside', ?, ?, ?, ?)
                   ON CONFLICT(plate_text) DO UPDATE SET
                     last_lane_id=excluded.last_lane_id,
                     last_lane_order=excluded.last_lane_order""",
                (canonical, lane_id or "", order, time.time(), time.time()))
            self._conn.commit()

    def get_plate_state(self, plate_text):
        canonical = normalize_plate_text(plate_text)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM plate_states WHERE plate_text=?",
                (canonical,)).fetchone()
            return dict(row) if row else None

    # --------------------------------- لیست تحت‌نظر پلاک‌ها (2.0.18-beta) --

    def add_watchlist_entry(self, plate_text, kind, note=""):
        """افزودن پلاک به لیست سیاه/سفید. kind: \"black\" | \"white\"."""
        kind = (kind or "").strip().lower()
        if kind not in ("black", "white"):
            raise ValueError("kind باید black یا white باشد")
        canonical = normalize_plate_text(plate_text)
        if not canonical:
            raise ValueError("متن پلاک خالی است")
        wid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO plate_watchlist(id, plate_text, plate_display,
                       kind, note, created_ts, created_date_j)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(plate_text, kind) DO UPDATE SET
                     plate_display=excluded.plate_display,
                     note=excluded.note""",
                (wid, canonical, prettify_plate(canonical), kind,
                 note or "", now, jalali_date_str(now)))
            self._conn.commit()
        return wid

    def remove_watchlist_entry(self, plate_text, kind):
        canonical = normalize_plate_text(plate_text)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM plate_watchlist WHERE plate_text=? AND kind=?",
                (canonical, (kind or "").strip().lower()))
            self._conn.commit()
            return cur.rowcount > 0

    def list_watchlist(self, kind=None, search=""):
        with self._lock:
            conds, vals = [], []
            if kind:
                conds.append("kind=?")
                vals.append(kind)
            if search:
                s = f"%{normalize_plate_text(search)}%"
                conds.append("(plate_text LIKE ? OR note LIKE ?)")
                vals += [s, s]
            q = "SELECT * FROM plate_watchlist"
            if conds:
                q += " WHERE " + " AND ".join(conds)
            q += " ORDER BY created_ts DESC"
            return [dict(r) for r in
                    self._conn.execute(q, vals).fetchall()]

    def find_watchlist(self, plate_text):
        """لیست رکوردهای تحت‌نظرِ یک پلاک (ممکن است هم سیاه هم سفید باشد)."""
        canonical = normalize_plate_text(plate_text)
        if not canonical:
            return []
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM plate_watchlist WHERE plate_text=?",
                (canonical,)).fetchall()]

    def count_watchlist(self, kind=None):
        with self._lock:
            if kind:
                row = self._conn.execute(
                    "SELECT COUNT(*) c FROM plate_watchlist WHERE kind=?",
                    (kind,)).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) c FROM plate_watchlist").fetchone()
            return int(row["c"]) if row else 0

    # ------------------------------------- آمار تردد (2.0.18-beta) --

    def lane_traffic_counts(self, date_from=None, date_to=None):
        """تعداد عبور ثبت‌شده‌ی هر مسیر در بازه: {lane_id: count}."""
        with self._lock:
            conds, vals = ["lane_id<>''"], []
            if date_from:
                conds.append("date_g>=?")
                vals.append(date_from)
            if date_to:
                conds.append("date_g<=?")
                vals.append(date_to)
            q = ("SELECT lane_id, COUNT(*) c FROM plate_crossings WHERE "
                 + " AND ".join(conds) + " GROUP BY lane_id")
            return {r["lane_id"]: int(r["c"])
                    for r in self._conn.execute(q, vals).fetchall()}

    def crossing_stats(self, date_from=None, date_to=None, lane_id="",
                       crossing_type=""):
        """آمار عبورها: لیست (date_g, hour, count) برای نمودار ساعتی/روزانه."""
        with self._lock:
            conds, vals = [], []
            if date_from:
                conds.append("date_g>=?")
                vals.append(date_from)
            if date_to:
                conds.append("date_g<=?")
                vals.append(date_to)
            if lane_id:
                conds.append("lane_id=?")
                vals.append(lane_id)
            if crossing_type:
                conds.append("crossing_type=?")
                vals.append(crossing_type)
            q = ("SELECT date_g, SUBSTR(time_g, 1, 2) AS hour, COUNT(*) AS c "
                 "FROM plate_crossings")
            if conds:
                q += " WHERE " + " AND ".join(conds)
            q += " GROUP BY date_g, hour ORDER BY date_g, hour"
            return [(r["date_g"], r["hour"], int(r["c"]))
                    for r in self._conn.execute(q, vals).fetchall()]

    def set_plate_state(self, plate_text, state, last_event_id="",
                        last_camera_id="", last_ts=None):
        canonical = normalize_plate_text(plate_text)
        ts = last_ts if last_ts is not None else time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO plate_states(plate_text, state, last_event_id,
                                            last_camera_id, last_ts, updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(plate_text) DO UPDATE SET
                     state=excluded.state, last_event_id=excluded.last_event_id,
                     last_camera_id=excluded.last_camera_id,
                     last_ts=excluded.last_ts, updated_at=excluded.updated_at""",
                (canonical, state, last_event_id or "", last_camera_id or "",
                 ts, ts))
            self._conn.commit()

    def log_crossing(self, plate_text, camera_id="", camera_name="", lane_id="",
                     crossing_type="entry", travel="", event_id="",
                     snapshot_path="", plate_display="", plate_id=None,
                     owner_name=""):
        ts = time.time()
        dt = datetime.fromtimestamp(ts)
        cid = uuid.uuid4().hex
        canonical = normalize_plate_text(plate_text)
        with self._lock:
            self._conn.execute(
                """INSERT INTO plate_crossings(id, ts, date_g, time_g, date_j,
                       plate_text, plate_display, plate_id, owner_name,
                       camera_id, camera_name, lane_id, crossing_type, travel,
                       event_id, snapshot_path)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, ts, dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"),
                 jalali_date_str(ts), canonical,
                 plate_display or prettify_plate(canonical), plate_id,
                 owner_name or "", camera_id or "", camera_name or "",
                 lane_id or "", crossing_type, travel or "", event_id or "",
                 snapshot_path or ""))
            self._conn.commit()
        return cid

    def log_violation(self, violation_type, plate_text, camera_id="",
                      camera_name="", lane_id="", detail="", crossing_id="",
                      snapshot_path="", plate_display="", plate_id=None,
                      owner_name=""):
        """ثبت تخلف؛ خروجی id تخلف. ضدتکرار: اگر همین پلاک با همین نوع تخلف
        در ۶۰ ثانیه‌ی اخیر ثبت شده باشد، همان id قبلی برمی‌گردد."""
        canonical = normalize_plate_text(plate_text)
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                """SELECT id FROM plate_violations
                   WHERE plate_text=? AND violation_type=? AND ts>?
                   ORDER BY ts DESC LIMIT 1""",
                (canonical, violation_type, now - 60)).fetchone()
            if row:
                return row["id"]
        ts = now
        dt = datetime.fromtimestamp(ts)
        vid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """INSERT INTO plate_violations(id, ts, date_g, time_g, date_j,
                       violation_type, plate_text, plate_display, plate_id,
                       owner_name, camera_id, camera_name, lane_id, detail,
                       crossing_id, snapshot_path, acknowledged)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? ,0)""",
                (vid, ts, dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"),
                 jalali_date_str(ts), violation_type, canonical,
                 plate_display or prettify_plate(canonical), plate_id,
                 owner_name or "", camera_id or "", camera_name or "",
                 lane_id or "", detail or "", crossing_id or "",
                 snapshot_path or ""))
            self._conn.commit()
        return vid

    def list_violations(self, date_from=None, date_to=None,
                        violation_type=None, search="", acknowledged=None,
                        limit=5000):
        with self._lock:
            conds, vals = [], []
            if date_from:
                conds.append("date_g>=?")
                vals.append(date_from)
            if date_to:
                conds.append("date_g<=?")
                vals.append(date_to)
            if violation_type:
                conds.append("violation_type=?")
                vals.append(violation_type)
            if acknowledged is True:
                conds.append("acknowledged=1")
            elif acknowledged is False:
                conds.append("acknowledged=0")
            if search:
                s = f"%{search}%"
                conds.append("(plate_text LIKE ? OR plate_display LIKE ? OR owner_name LIKE ? OR camera_name LIKE ?)")
                vals += [s, s, s, s]
            q = "SELECT * FROM plate_violations"
            if conds:
                q += " WHERE " + " AND ".join(conds)
            q += " ORDER BY ts DESC LIMIT ?"
            vals.append(int(limit))
            return [dict(r) for r in self._conn.execute(q, vals).fetchall()]

    def acknowledge_violation(self, vid, acknowledged=True):
        with self._lock:
            self._conn.execute(
                "UPDATE plate_violations SET acknowledged=? WHERE id=?",
                (1 if acknowledged else 0, vid))
            self._conn.commit()

    def count_unacked_violations(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM plate_violations WHERE acknowledged=0").fetchone()
            return int(row["c"]) if row else 0

    def query_crossings(self, date_from=None, date_to=None, plate_text="",
                        lane_id="", crossing_type="", limit=5000):
        with self._lock:
            conds, vals = [], []
            if date_from:
                conds.append("date_g>=?")
                vals.append(date_from)
            if date_to:
                conds.append("date_g<=?")
                vals.append(date_to)
            if plate_text:
                conds.append("plate_text=?")
                vals.append(normalize_plate_text(plate_text))
            if lane_id:
                conds.append("lane_id=?")
                vals.append(lane_id)
            if crossing_type:
                conds.append("crossing_type=?")
                vals.append(crossing_type)
            q = "SELECT * FROM plate_crossings"
            if conds:
                q += " WHERE " + " AND ".join(conds)
            q += " ORDER BY ts DESC LIMIT ?"
            vals.append(int(limit))
            return [dict(r) for r in self._conn.execute(q, vals).fetchall()]

    def export_violations_csv(self, path, date_from=None, date_to=None,
                              violation_type=None, search=""):
        rows = self.list_violations(date_from=date_from, date_to=date_to,
                                    violation_type=violation_type,
                                    search=search, limit=100000)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["تاریخ شمسی", "ساعت", "نوع تخلف", "پلاک", "مالک",
                        "دوربین", "مسیر", "جزئیات", "وضعیت بررسی"])
            for r in rows:
                w.writerow([
                    r.get("date_j", ""), r.get("time_g", ""),
                    self.VIOLATION_LABELS.get(r.get("violation_type"), ""),
                    r.get("plate_display", ""), r.get("owner_name", ""),
                    r.get("camera_name", ""), r.get("lane_id", ""),
                    r.get("detail", ""),
                    "بررسی‌شده" if r.get("acknowledged") else "بررسی‌نشده",
                ])
        return len(rows)


# نمونه‌ی سراسری (مثل report_store): همه‌ی بخش‌های برنامه از همین استفاده می‌کنند.
plate_store = PlateStore()

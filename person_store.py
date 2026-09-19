# -*- coding: utf-8 -*-
"""ذخیره‌سازی اشخاص ردیابی‌شده و مسیر حرکت آن‌ها بین دوربین‌ها (SQLite).

دو جدول:
  persons: هر «شخص» ظاهریِ یکتا (کد P-0001 و...) با ویژگی‌های ظاهری
      (رنگ لباس/شلوار/مو) که از person_reid.describe_person می‌آید.
  person_sightings: هر «حضور» یک شخص در محدوده‌ی یک دوربین (اتاق) با
      ساعت دقیق ورود و خروج.

مثل plate_store: thread-safe با قفل، بدون وابستگی سنگین (فقط sqlite3 و
cv2 برای ذخیره‌ی تصویر)، تاریخ شمسی توکار.
"""

import csv
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime

import cv2


# تاریخ شمسی از ماژول مشترک jalali.py می‌آید (تک‌منبع حقیقت؛ الگوریتم
# دقیق jalaali). نسخه‌ی قبلیِ توکارِ همین فایل باگ داشت و بعضی
# تاریخ‌ها را چند روز جابه‌جا نشان می‌داد.
from jalali import gregorian_to_jalali, jalali_now_str, jalali_date_str


def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# PersonStore
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS = {
    # آستانه‌ی فاصله‌ی کسینوسی بردار ظاهری نرمال‌شده (نسخه‌ی ۲ person_reid:
    # ۰ = یکسان، ~۰.۴۵ به‌هم‌ریختگی معمول یک شخص در دو نما، ۱ به بالا =
    # کاملاً متفاوت). کمتر = سخت‌گیرانه‌تر.
    "match_threshold": "0.45",
    "link_window_min": "10",     # پنجره‌ی زمانی اتصال ردها بین دوربین‌ها (دقیقه)
    "confirm_frames": "3",       # تیک پیاپی لازم برای تأیید یک رد محلی
    # کنترل تردد طبقاتی — طبقات مجاز افراد تعریف‌نشده (JSON لیست floor_id؛
    # "*": همه‌ی طبقات مجاز = بدون محدودیت)
    "undefined_allowed_floors": "\"*\"",
    # آژیر تخلف تردد غیرمجاز (۱=روشن)
    "floor_violation_sound": "1",
    # حداقل فاصله‌ی بین دو تخلف ثبت‌شده برای یک شخص در یک طبقه (دقیقه)
    "floor_violation_cooldown_min": "15",
}


class PersonStore:
    def __init__(self, db_path=None):
        base = _base_dir()
        self.db_path = db_path or os.path.join(base, "person_data", "persons.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.snap_dir = os.path.join(os.path.dirname(self.db_path), "snapshots")
        os.makedirs(self.snap_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        # بستن حضورهای بازمانده از اجرای قبلی (مثلاً با کرش/بستن ناگهانی):
        # خروج = ورود (مدت صفر) تا گزارش مسیر، ردیفِ «باز»یِ ابدی نداشته باشد.
        self._close_stale_sightings()

    # -- اسکیما --
    def _ensure_schema(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS persons (
                    id TEXT PRIMARY KEY,
                    created_ts REAL NOT NULL,
                    last_seen_ts REAL NOT NULL,
                    shirt_color TEXT DEFAULT '',
                    pants_color TEXT DEFAULT '',
                    hair_color TEXT DEFAULT '',
                    hair_length TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    thumb_path TEXT DEFAULT '',
                    sightings_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS person_sightings (
                    id TEXT PRIMARY KEY,
                    person_id TEXT NOT NULL,
                    camera_name TEXT DEFAULT '',
                    nvr_id TEXT DEFAULT '',
                    channel INTEGER,
                    enter_ts REAL NOT NULL,
                    exit_ts REAL,
                    duration_s REAL DEFAULT 0,
                    snapshot_path TEXT DEFAULT '',
                    date_g TEXT DEFAULT '',
                    date_j TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_sight_person
                    ON person_sightings(person_id, enter_ts);
                CREATE INDEX IF NOT EXISTS idx_sight_cam
                    ON person_sightings(camera_name, enter_ts);
                CREATE TABLE IF NOT EXISTS person_settings (
                    key TEXT PRIMARY KEY, value TEXT
                );
                -- کنترل تردد طبقاتی: طبقات مجاز هر شخص (JSON لیست floor_id)
                CREATE TABLE IF NOT EXISTS person_floor_access (
                    person_id TEXT PRIMARY KEY,
                    allowed_floors TEXT DEFAULT ''
                );
                -- تخلفات تردد غیرمجاز در طبقات
                CREATE TABLE IF NOT EXISTS floor_violations (
                    id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    person_id TEXT DEFAULT '',
                    face_person_id TEXT DEFAULT '',
                    face_name TEXT DEFAULT '',
                    camera_id TEXT DEFAULT '',
                    camera_name TEXT DEFAULT '',
                    floor_id TEXT DEFAULT '',
                    floor_name TEXT DEFAULT '',
                    snapshot_path TEXT DEFAULT '',
                    acknowledged INTEGER DEFAULT 0,
                    date_j TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_viol_person
                    ON floor_violations(person_id, ts);
                CREATE INDEX IF NOT EXISTS idx_viol_floor
                    ON floor_violations(floor_id, ts);
            """)
            for k, v in _DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO person_settings(key, value) VALUES(?, ?)",
                    (k, v))
            # مهاجرت سبک: ستون‌های چهره و رنگ (اگر دیتابیس قدیمی باشد)
            for _col, _typ in (("face_person_id", "TEXT DEFAULT ''"),
                               ("face_name", "TEXT DEFAULT ''"),
                               ("color", "TEXT DEFAULT ''")):
                try:
                    self._conn.execute(
                        f"ALTER TABLE persons ADD COLUMN {_col} {_typ}")
                except Exception:
                    pass  # ستون از قبل وجود دارد
            self._conn.commit()

    def _close_stale_sightings(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, enter_ts FROM person_sightings WHERE exit_ts IS NULL"
            ).fetchall()
            for r in rows:
                self._conn.execute(
                    "UPDATE person_sightings SET exit_ts=?, duration_s=0 WHERE id=?",
                    (r["enter_ts"], r["id"]))
            self._conn.commit()

    # -- تنظیمات --
    def get_setting(self, key, default=None):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM person_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return _DEFAULT_SETTINGS.get(key, default)
        return row["value"]

    def set_setting(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO person_settings(key, value) VALUES(?, ?)",
                (key, str(value)))
            self._conn.commit()

    # -- کنترل تردد طبقاتی --
    @staticmethod
    def _parse_floor_list(raw):
        """رشته‌ی JSON تنظیمات → لیست floor_id؛ «"*"» یعنی همه."""
        import json as _json
        if raw is None:
            return None
        try:
            val = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return None
        if val == "*":
            return "*"
        if isinstance(val, list):
            return [str(x) for x in val]
        return None

    @staticmethod
    def _dump_floor_list(val):
        import json as _json
        return _json.dumps(val, ensure_ascii=False)

    def get_undefined_allowed_floors(self):
        """طبقات مجاز افراد تعریف‌نشده: لیست floor_id یا «*» (همه)."""
        parsed = self._parse_floor_list(self.get_setting("undefined_allowed_floors"))
        return "*" if parsed is None else parsed

    def set_undefined_allowed_floors(self, floors):
        """floors: لیست floor_id یا «*» برای همه."""
        self.set_setting("undefined_allowed_floors",
                         self._dump_floor_list(floors))

    def get_person_allowed_floors(self, person_id):
        """طبقات مجاز یک شخص تعریف‌شده؛ None یعنی قانونی تعریف نشده (آزاد)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT allowed_floors FROM person_floor_access WHERE person_id=?",
                (person_id,)).fetchone()
        if row is None:
            return None
        return self._parse_floor_list(row["allowed_floors"])

    def set_person_allowed_floors(self, person_id, floors):
        """floors: لیست floor_id یا «*»؛ None یعنی حذف قانون (آزاد)."""
        with self._lock:
            if floors is None:
                self._conn.execute(
                    "DELETE FROM person_floor_access WHERE person_id=?",
                    (person_id,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO person_floor_access(person_id, allowed_floors)"
                    " VALUES(?, ?)",
                    (person_id, self._dump_floor_list(floors)))
            self._conn.commit()

    def get_person(self, person_id):
        """یک رکورد شخص (برای خواندن face_person_id و ...)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM persons WHERE id=?", (person_id,)).fetchone()
        return dict(row) if row else None

    def record_floor_violation(self, person_id, face_person_id, face_name,
                               camera_id, camera_name, floor_id, floor_name,
                               snapshot_bgr=None, snapshot_path=""):
        """ثبت تخلف تردد غیرمجاز (با cooldown). خروجی: dict تخلف یا None
        اگر داخل پنجره‌ی cooldown تخلف مشابهی ثبت شده باشد."""
        import time as _time, uuid as _uuid, os as _os
        now = _time.time()
        try:
            cooldown_min = float(self.get_setting("floor_violation_cooldown_min", "15"))
        except Exception:
            cooldown_min = 15.0
        with self._lock:
            dup = self._conn.execute(
                """SELECT id FROM floor_violations
                   WHERE person_id=? AND floor_id=? AND ts > ?
                   ORDER BY ts DESC LIMIT 1""",
                (person_id, floor_id, now - cooldown_min * 60)).fetchone()
            if dup:
                return None
            vid = _uuid.uuid4().hex[:12]
            snap = snapshot_path or ""
            if snapshot_bgr is not None and not snap:
                try:
                    snap = self._save_image(snapshot_bgr, f"viol_{vid}", now)
                except Exception:
                    snap = ""
            date_j = jalali_now_str(now)
            self._conn.execute(
                """INSERT INTO floor_violations(id, ts, person_id, face_person_id,
                   face_name, camera_id, camera_name, floor_id, floor_name,
                   snapshot_path, acknowledged, date_j)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (vid, now, person_id, face_person_id or "", face_name or "",
                 camera_id or "", camera_name or "", floor_id or "",
                 floor_name or "", snap, date_j))
            self._conn.commit()
            return {"id": vid, "ts": now, "person_id": person_id,
                    "face_person_id": face_person_id or "",
                    "face_name": face_name or "", "camera_id": camera_id or "",
                    "camera_name": camera_name or "", "floor_id": floor_id or "",
                    "floor_name": floor_name or "", "snapshot_path": snap,
                    "acknowledged": 0, "date_j": date_j}

    def list_floor_violations(self, limit=200, only_unacked=False):
        with self._lock:
            q = "SELECT * FROM floor_violations"
            if only_unacked:
                q += " WHERE acknowledged=0"
            q += " ORDER BY ts DESC LIMIT ?"
            rows = self._conn.execute(q, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def acknowledge_violation(self, viol_id):
        with self._lock:
            self._conn.execute(
                "UPDATE floor_violations SET acknowledged=1 WHERE id=?",
                (viol_id,))
            self._conn.commit()

    def count_unacked_violations(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM floor_violations WHERE acknowledged=0"
            ).fetchone()
        return int(row["c"]) if row else 0


    @property
    def match_threshold(self):
        try:
            return float(self.get_setting("match_threshold", 0.50))
        except Exception:
            return 0.50

    @property
    def link_window_min(self):
        try:
            return float(self.get_setting("link_window_min", 10))
        except Exception:
            return 10.0

    @property
    def confirm_frames(self):
        try:
            return int(float(self.get_setting("confirm_frames", 3)))
        except Exception:
            return 3

    # -- اشخاص --
    def _next_person_code(self):
        row = self._conn.execute(
            "SELECT id FROM persons ORDER BY id DESC LIMIT 1").fetchone()
        n = 0
        if row:
            try:
                n = int(str(row["id"]).lstrip("P-") or 0)
            except Exception:
                n = 0
        return f"P-{n + 1:04d}"

    def _save_image(self, img_bgr, prefix, ts):
        if img_bgr is None:
            return ""
        try:
            h, w = img_bgr.shape[:2]
            scale = min(1.0, 320.0 / max(1, w))
            if scale < 1.0:
                img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)))
            name = f"{prefix}_{int(ts * 1000)}.jpg"
            path = os.path.join(self.snap_dir, name)
            cv2.imwrite(path, img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return path
        except Exception:
            return ""

    def add_person(self, attrs, thumb_bgr=None):
        """ثبت یک شخص تازه. attrs: dict از describe_person. خروجی: کد شخص."""
        ts = time.time()
        with self._lock:
            pid = self._next_person_code()
            thumb = self._save_image(thumb_bgr, f"thumb_{pid}", ts)
            self._conn.execute(
                """INSERT INTO persons(id, created_ts, last_seen_ts, shirt_color,
                                      pants_color, hair_color, hair_length,
                                      thumb_path, sightings_count,
                                      face_person_id, face_name)
                   VALUES(?,?,?,?,?,?,?,?,0,?,?)""",
                (pid, ts, ts,
                 attrs.get("shirt_color", ""), attrs.get("pants_color", ""),
                 attrs.get("hair_color", ""), attrs.get("hair_length", ""),
                 thumb,
                 str(attrs.get("face_person_id") or ""),
                 str(attrs.get("face_name") or "")))
            self._conn.commit()
        return pid

    def find_by_face(self, face_person_id):
        """یافتن کد شخص از روی شناسه‌ی چهره‌ی بانک چهره‌ها (یا None)."""
        if not face_person_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM persons WHERE face_person_id=? LIMIT 1",
                (str(face_person_id),)).fetchone()
        return row["id"] if row else None

    def set_person_face(self, person_id, face_person_id, face_name):
        """الصاق/به‌روزرسانی هویت چهره‌ی شناخته‌شده به یک شخص."""
        with self._lock:
            self._conn.execute(
                "UPDATE persons SET face_person_id=?, face_name=? WHERE id=?",
                (str(face_person_id or ""), str(face_name or ""), person_id))
            self._conn.commit()

    def touch_person(self, person_id, ts=None):
        ts = time.time() if ts is None else ts
        with self._lock:
            self._conn.execute(
                "UPDATE persons SET last_seen_ts=?, "
                "sightings_count=sightings_count+1 WHERE id=?",
                (ts, person_id))
            self._conn.commit()

    def set_person_notes(self, person_id, notes):
        with self._lock:
            self._conn.execute("UPDATE persons SET notes=? WHERE id=?",
                               (notes or "", person_id))
            self._conn.commit()

    @staticmethod
    def _valid_hex_color(hexcol):
        h = str(hexcol or "").strip()
        if len(h) != 7 or not h.startswith("#"):
            return False
        try:
            int(h[1:], 16)
        except ValueError:
            return False
        return True

    def set_person_color(self, person_id, color_hex):
        """تعیین رنگ اختصاصی شخص (hex مثل #ff5500) — برای نمایش مسیر
        حرکتش روی نقشه با خط‌چینِ رنگ خودش. رشته‌ی خالی یعنی «پاک کردن
        رنگ دستی» (برگشت به رنگ خودکار)."""
        hexcol = str(color_hex or "").strip()
        if hexcol and not self._valid_hex_color(hexcol):
            raise ValueError(f"قالب رنگ نامعتبر است: {hexcol!r}")
        with self._lock:
            self._conn.execute("UPDATE persons SET color=? WHERE id=?",
                               (hexcol, person_id))
            self._conn.commit()

    def get_persons(self):
        """لیست اشخاص به‌همراه دوربین‌هایی که در آن‌ها دیده شده‌اند."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM persons ORDER BY last_seen_ts DESC").fetchall()
            cams = self._conn.execute(
                """SELECT person_id, GROUP_CONCAT(DISTINCT camera_name) AS cams
                   FROM person_sightings GROUP BY person_id""").fetchall()
        cam_map = {r["person_id"]: (r["cams"] or "") for r in cams}
        out = []
        for r in rows:
            d = dict(r)
            d["cameras"] = cam_map.get(r["id"], "")
            d["created_j"] = jalali_now_str(r["created_ts"])
            d["last_seen_j"] = jalali_now_str(r["last_seen_ts"])
            out.append(d)
        return out

    # -- حضورها (مسیر حرکت) --
    def start_sighting(self, person_id, camera_name, nvr_id="",
                       channel=None, snapshot_bgr=None):
        ts = time.time()
        dt = datetime.fromtimestamp(ts)
        import uuid as _uuid
        sid = _uuid.uuid4().hex
        snap = self._save_image(snapshot_bgr, f"sight_{sid[:8]}", ts)
        with self._lock:
            self._conn.execute(
                """INSERT INTO person_sightings(id, person_id, camera_name,
                       nvr_id, channel, enter_ts, snapshot_path, date_g, date_j)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (sid, person_id, camera_name or "", nvr_id or "", channel,
                 ts, snap, dt.strftime("%Y-%m-%d"), jalali_date_str(ts)))
            self._conn.commit()
        self.touch_person(person_id, ts)
        return sid

    def end_sighting(self, sighting_id, exit_ts=None):
        exit_ts = time.time() if exit_ts is None else exit_ts
        with self._lock:
            row = self._conn.execute(
                "SELECT enter_ts, exit_ts FROM person_sightings WHERE id=?",
                (sighting_id,)).fetchone()
            if row is None or row["exit_ts"] is not None:
                return
            dur = max(0.0, exit_ts - row["enter_ts"])
            self._conn.execute(
                "UPDATE person_sightings SET exit_ts=?, duration_s=? WHERE id=?",
                (exit_ts, dur, sighting_id))
            self._conn.commit()

    def get_path(self, person_id):
        """مسیر کامل حرکت یک شخص: حضورها به ترتیب زمان ورود."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM person_sightings WHERE person_id=? "
                "ORDER BY enter_ts ASC", (person_id,)).fetchall()
        return [self._fmt_sighting(r) for r in rows]

    def _fmt_sighting(self, r):
        d = dict(r)
        d["enter_j"] = jalali_now_str(r["enter_ts"])
        d["exit_j"] = jalali_now_str(r["exit_ts"]) if r["exit_ts"] else "—"
        d["enter_time"] = datetime.fromtimestamp(
            r["enter_ts"]).strftime("%H:%M:%S")
        d["exit_time"] = (datetime.fromtimestamp(r["exit_ts"]).strftime("%H:%M:%S")
                          if r["exit_ts"] else "—")
        dur = r["duration_s"] or 0
        if dur < 60:
            d["duration_str"] = f"{int(dur)} ثانیه"
        else:
            d["duration_str"] = f"{int(dur // 60)} دقیقه و {int(dur % 60)} ثانیه"
        return d

    def query_sightings(self, date_from=None, date_to=None, camera_name=None,
                        person_id=None, limit=5000):
        """گزارش حضورها با فیلتر. date_from/date_to رشته‌ی 'YYYY-MM-DD' میلادی."""
        with self._lock:
            conds, vals = [], []
            if date_from:
                conds.append("s.date_g>=?")
                vals.append(date_from)
            if date_to:
                conds.append("s.date_g<=?")
                vals.append(date_to)
            if camera_name:
                conds.append("s.camera_name=?")
                vals.append(camera_name)
            if person_id:
                conds.append("s.person_id=?")
                vals.append(person_id)
            where = ("WHERE " + " AND ".join(conds)) if conds else ""
            rows = self._conn.execute(
                f"""SELECT s.*, p.shirt_color, p.pants_color, p.hair_color
                    FROM person_sightings s
                    LEFT JOIN persons p ON p.id=s.person_id
                    {where} ORDER BY s.enter_ts DESC LIMIT ?""",
                (*vals, int(limit))).fetchall()
        return [self._fmt_sighting(r) for r in rows]

    def export_sightings_csv(self, path, **filters):
        rows = self.query_sightings(limit=100000, **filters)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["کد شخص", "دوربین", "تاریخ ورود (شمسی)",
                        "ساعت ورود", "ساعت خروج", "مدت حضور",
                        "رنگ لباس", "رنگ شلوار", "رنگ مو"])
            for r in rows:
                w.writerow([r["person_id"], r["camera_name"], r["date_j"],
                            r["enter_time"], r["exit_time"], r["duration_str"],
                            r.get("shirt_color", ""), r.get("pants_color", ""),
                            r.get("hair_color", "")])
        return path


person_store = PersonStore()

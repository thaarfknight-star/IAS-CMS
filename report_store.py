# -*- coding: utf-8 -*-
"""ثبت دائمی گزارش‌ها (شمارش نفرات، ورود به محدوده‌ی هشدار، چهره‌ی
شناخته‌شده/تعریف‌نشده) روی همان سیستم/لپ‌تاپی که برنامه رویش اجرا می‌شود.

رفع درخواست: قبل از این ماژول، تمام این رویدادها فقط در یک QListWidget
موقت روی صفحه نمایش داده می‌شدند و با بستن برنامه کاملاً از بین می‌رفتند؛
هیچ سابقه‌ای برای جست‌وجوی بعدی («دیروز ساعت ۳ چند نفر تو اتاق بودن؟»،
«چه کسی وارد محدوده‌ی سرور شد؟») وجود نداشت.

معماری: یک پایگاه‌داده‌ی SQLite (کتابخانه‌ی استاندارد پایتون - sqlite3 -
است، هیچ وابستگی جدیدی به requirements.txt/exe نهایی اضافه نمی‌کند) در
همان پوشه‌ای که برنامه اجرا می‌شود (هم‌سطح با cameras.json/face_db.json)
ساخته می‌شود. هر ردیف یک رویداد با ساعت و تاریخ کامل است. تصویر برش‌خورده‌ی
هر رویداد چهره هم به‌صورت فایل jpg در پوشه‌ی report_images/<تاریخ>/ ذخیره
می‌شود (نه داخل خودِ دیتابیس، تا حجم دیتابیس کوچک بماند).

به‌روزرسانی (رفع درخواست «گزارش‌ها روی NVR ضبط بشه»): متن/تصویر گزارش‌ها
همچنان فقط همین‌جا (روی سیستمی که CMS رویش اجراست) نگه داشته می‌شود - چون
NVRهای رایج (Hikvision/Dahua) API عمومی برای نوشتن/آپلود فایل دلخواه روی
هاردشان ندارند. اما هر رویداد حالا nvr_id و channel دوربینِ منبع را هم (در
صورتی که آن دوربین زیرمجموعه‌ی یک NVR باشد) ذخیره می‌کند تا از دیالوگ
گزارش‌ها بشود دکمه‌ی «پخش ویدیوی NVR در همین لحظه» را زد و ویدیوی واقعیِ
همان بازه را مستقیماً از روی خودِ NVR (نه از این دیتابیس) پخش کرد - رجوع
کنید به nvr_playback.py و nvr_playback_dialog.py. برای دوربین‌های مستقل
(بدون NVR)، این دو فیلد خالی می‌مانند و آن دکمه غیرفعال است.
"""

import csv
import os
import queue
import sqlite3
import threading
import time
from contextlib import closing

def _default_data_dir():
    try:
        from app_paths import get_data_dir
        return get_data_dir()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


DB_PATH = os.path.join(_default_data_dir(), "reports.db")
IMAGES_DIR = os.path.join(_default_data_dir(), "report_images")

EVENT_TYPE_LABELS_FA = {
    "face_known": "چهره شناخته‌شده",
    "face_unknown": "چهره تعریف‌نشده",
    "zone_entry": "ورود به محدوده",
    "person_count": "شمارش نفرات",
    # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: دو نوع رویداد جدید -
    # یکی برای تشخیص تصویری روی خودِ دوربین (fire_smoke_detector.py) و
    # دیگری برای هشدار دریافتی از یک پنل/سنسور فیزیکی (fire_alarm_io.py).
    "fire_smoke_visual": "تشخیص تصویری آتش/دود",
    "fire_alarm_panel": "هشدار پنل اعلام حریق",
}


class ReportStore:
    def __init__(self, db_path: str = DB_PATH, images_dir: str = IMAGES_DIR):
        self.db_path = db_path
        self.images_dir = images_dir
        try:
            os.makedirs(self.images_dir, exist_ok=True)
        except Exception as e:
            print(f"خطا در ساخت پوشه‌ی تصاویر گزارش‌ها: {e}")
        self._init_db()
        # (2.0.16-beta - پایداری) همه‌ی نوشتن‌ها (INSERT + ذخیره‌ی تصویر)
        # در یک ترد writer اختصاصی انجام می‌شود؛ ترد GUI (و تردهای استریم)
        # فقط در صف می‌گذارند و هرگز روی قفل دیتابیس بلاک نمی‌شوند —
        # ریشه‌ی «Not Responding»های مقطعیِ ثبت گزارش.
        self._write_queue = queue.Queue()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True, name="report-writer")
        self._writer_thread.start()

    def _writer_loop(self):
        while True:
            try:
                job = self._write_queue.get()
            except Exception:
                continue
            try:
                if job is None:  # سیگنال پایان (close)
                    return
                self._do_insert(job)
            except Exception as e:
                print(f"خطا در ترد نویسنده‌ی گزارش‌ها: {e}")
            finally:
                try:
                    self._write_queue.task_done()
                except Exception:
                    pass

    def flush(self, timeout=10):
        """منتظر می‌ماند تا همه‌ی رویدادهای صف‌شده واقعاً نوشته شوند
        (برای تست‌ها و خروج تمیز از برنامه)."""
        deadline = time.monotonic() + max(0, timeout)
        try:
            while (self._write_queue.unfinished_tasks > 0
                   and time.monotonic() < deadline):
                time.sleep(0.02)
        except Exception:
            pass

    def close(self):
        """تخلیه‌ی صف و پایان تمیز ترد نویسنده (هنگام خروج از برنامه)."""
        try:
            self.flush(5)
            self._write_queue.put_nowait(None)
            self._writer_thread.join(3)
        except Exception:
            pass

    # ------------------------------------------------------------- setup -

    def _connect(self):
        # check_same_thread=False چون رویدادها از تردهای پخش زنده‌ی مختلف
        # (هر دوربین ترد خودش را دارد) می‌رسند، نه فقط از ترد UI؛ هر
        # فراخوانی یک اتصال کوتاه‌عمر جدید باز/بسته می‌کند (سربار SQLite
        # برای این حجم رویداد ناچیز است) تا نیازی به قفل سراسری بین
        # تردهای مختلف نباشد.
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        camera_name TEXT,
                        person_name TEXT,
                        phone TEXT,
                        employee_id TEXT,
                        region_number TEXT,
                        region_name TEXT,
                        person_count INTEGER,
                        image_path TEXT,
                        nvr_id TEXT,
                        channel TEXT,
                        detail TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
                conn.commit()
                self._migrate_add_nvr_columns(conn)
                self._migrate_add_detail_column(conn)
        except Exception as e:
            print(f"خطا در ساخت پایگاه‌داده‌ی گزارش‌ها: {e}")

    def _migrate_add_nvr_columns(self, conn):
        """رفع مهاجرت: پایگاه‌داده‌های reports.db ساخته‌شده با نسخه‌های قبلی
        این ماژول ستون‌های nvr_id/channel را ندارند؛ CREATE TABLE IF NOT
        EXISTS بالا روی جدول از قبل موجود اثری ندارد، پس این دو ستون در صورت
        نبود، جداگانه با ALTER TABLE اضافه می‌شوند (بدون از دست رفتن هیچ
        رویداد قبلاً ثبت‌شده‌ای)."""
        try:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "nvr_id" not in existing:
                conn.execute("ALTER TABLE events ADD COLUMN nvr_id TEXT")
            if "channel" not in existing:
                conn.execute("ALTER TABLE events ADD COLUMN channel TEXT")
            conn.commit()
        except Exception as e:
            print(f"خطا در به‌روزرسانی ساختار پایگاه‌داده‌ی گزارش‌ها: {e}")

    def _migrate_add_detail_column(self, conn):
        """رفع مهاجرت: پایگاه‌داده‌های ساخته‌شده قبل از اضافه‌شدن رویدادهای
        آتش/دود ستون ``detail`` (برای نگه‌داشتن نوع 'fire'/'smoke' یا نام
        پنل فیزیکی) را ندارند."""
        try:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "detail" not in existing:
                conn.execute("ALTER TABLE events ADD COLUMN detail TEXT")
                conn.commit()
        except Exception as e:
            print(f"خطا در به‌روزرسانی ساختار پایگاه‌داده‌ی گزارش‌ها (detail): {e}")

    # ------------------------------------------------------------- ذخیره -

    def _save_image(self, crop_frame, prefix, ts=None):
        if crop_frame is None:
            return None
        try:
            import cv2  # وارد کردن دیرهنگام: این ماژول را بدون opencv هم می‌توان تست کرد

            # (2.0.16-beta) پوشه‌ی روز از روی ساعت خودِ رویداد ساخته می‌شود،
            # چون ذخیره‌ی تصویر ممکن است کمی بعد از ثبت در ترد writer انجام شود.
            day = (ts or "")[:10] or time.strftime("%Y-%m-%d")
            clock = "".join(ch for ch in (ts or "")[11:19] if ch.isdigit()) or \
                time.strftime("%H%M%S")
            day_dir = os.path.join(self.images_dir, day)
            os.makedirs(day_dir, exist_ok=True)
            fname = f"{prefix}_{clock}_{int(time.time() * 1000) % 1000}.jpg"
            path = os.path.join(day_dir, fname)
            cv2.imwrite(path, crop_frame)
            return path
        except Exception as e:
            print(f"خطا در ذخیره‌ی تصویر گزارش: {e}")
            return None

    def _insert(self, ts, event_type, camera_name=None, person_name=None, phone=None,
                employee_id=None, region_number=None, region_name=None,
                person_count=None, image_path=None, nvr_id=None, channel=None, detail=None,
                crop_frame=None, crop_prefix=None):
        # (2.0.16-beta) فقط صف می‌کند و بلافاصله برمی‌گردد؛ نوشتن واقعی
        # (دیسک + SQLite) در ترد writer انجام می‌شود تا ترد صداکننده
        # (GUI یا استریم) هرگز بلاک نشود.
        job = {
            "ts": ts, "event_type": event_type, "camera_name": camera_name,
            "person_name": person_name, "phone": phone, "employee_id": employee_id,
            "region_number": region_number, "region_name": region_name,
            "person_count": person_count, "image_path": image_path,
            "nvr_id": str(nvr_id) if nvr_id else None,
            "channel": str(channel) if channel not in (None, "") else None,
            "detail": detail,
            "crop_frame": crop_frame, "crop_prefix": crop_prefix,
        }
        try:
            self._write_queue.put_nowait(job)
        except Exception as e:
            print(f"خطا در صف‌کردن رویداد گزارش: {e}")

    def _do_insert(self, job):
        """نوشتن واقعی یک رویداد — فقط از ترد writer صدا زده می‌شود."""
        image_path = job.get("image_path")
        if image_path is None and job.get("crop_frame") is not None:
            image_path = self._save_image(
                job["crop_frame"], job.get("crop_prefix") or "event",
                ts=job.get("ts"))
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO events
                        (ts, event_type, camera_name, person_name, phone, employee_id,
                         region_number, region_name, person_count, image_path, nvr_id, channel, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (job.get("ts"), job.get("event_type"), job.get("camera_name"),
                     job.get("person_name"), job.get("phone"), job.get("employee_id"),
                     job.get("region_number"), job.get("region_name"),
                     job.get("person_count"), image_path,
                     job.get("nvr_id"), job.get("channel"), job.get("detail")),
                )
                conn.commit()
        except Exception as e:
            print(f"خطا در ثبت رویداد گزارش: {e}")

    def log_face_event(self, camera_name, person, crop_frame=None, nvr_id=None, channel=None):
        """هر تشخیص چهره (چه شناخته‌شده چه تعریف‌نشده) را با ساعت/تاریخ
        کامل و تصویر برش‌خورده ثبت می‌کند. ``nvr_id``/``channel``: در صورتی
        که این دوربین زیرمجموعه‌ی یک NVR باشد، برای دکمه‌ی «پخش ویدیوی NVR»
        در دیالوگ گزارش‌ها ذخیره می‌شود (رجوع کنید به main.py:on_face_event)."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if person:
            event_type = "face_known"
            person_name = person.get("name", "")
            phone = person.get("phone", "")
            employee_id = person.get("employee_id", "")
        else:
            event_type = "face_unknown"
            person_name = phone = employee_id = None
        # (2.0.16-beta) ذخیره‌ی تصویر هم در ترد writer انجام می‌شود
        self._insert(now, event_type, camera_name=camera_name, person_name=person_name,
                     phone=phone, employee_id=employee_id,
                     crop_frame=crop_frame, crop_prefix=event_type,
                     nvr_id=nvr_id, channel=channel)

    def log_region_alert(self, camera_name, number, name, nvr_id=None, channel=None):
        """با ورود شخصی به یک محدوده‌ی هشدار، ثبت می‌شود."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self._insert(now, "zone_entry", camera_name=camera_name,
                     region_number=str(number), region_name=name,
                     nvr_id=nvr_id, channel=channel)

    def log_person_count(self, camera_name, count, nvr_id=None, channel=None):
        """تغییر تعداد نفراتِ یک دوربین را ثبت می‌کند (فراخوان مسئول
        فراخوانی فقط هنگام *تغییر* عدد است، نه هر فریم - رجوع کنید به
        CameraSlotWidget.on_people_count در main.py)."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self._insert(now, "person_count", camera_name=camera_name, person_count=count,
                     nvr_id=nvr_id, channel=channel)

    def log_fire_smoke_visual(self, camera_name, kind, crop_frame=None, confidence=None,
                               nvr_id=None, channel=None):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: هر تشخیص تصویری آتش
        یا دود (kind: 'fire'/'smoke') را با ساعت/تاریخ کامل و تصویر
        برش‌خورده‌ی همان ناحیه ثبت می‌کند."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        detail = kind if confidence is None else f"{kind} ({confidence * 100:.0f}%)"
        # (2.0.16-beta) ذخیره‌ی تصویر هم در ترد writer انجام می‌شود
        self._insert(now, "fire_smoke_visual", camera_name=camera_name,
                     crop_frame=crop_frame, crop_prefix=f"fire_{kind}",
                     nvr_id=nvr_id, channel=channel, detail=detail)

    def log_fire_alarm_panel(self, panel_name, active=True):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: فعال/غیرفعال‌شدن یک
        پنل/سنسور فیزیکی اعلام حریق را ثبت می‌کند (رجوع کنید به
        fire_alarm_io.FireAlarmMonitorThread)."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        detail = "فعال" if active else "برطرف شد"
        self._insert(now, "fire_alarm_panel", camera_name=panel_name, detail=detail)

    # -------------------------------------------------------------- خواندن -

    def query(self, start=None, end=None, event_type=None, camera_name=None, limit=5000):
        """start/end: رشته‌ی 'YYYY-MM-DD HH:MM:SS' یا 'YYYY-MM-DD'."""
        q = ("SELECT ts, event_type, camera_name, person_name, phone, employee_id, "
             "region_number, region_name, person_count, image_path, nvr_id, channel, detail "
             "FROM events WHERE 1=1")
        params = []
        if start:
            q += " AND ts >= ?"
            params.append(start)
        if end:
            q += " AND ts <= ?"
            params.append(end)
        if event_type:
            q += " AND event_type = ?"
            params.append(event_type)
        if camera_name:
            q += " AND camera_name = ?"
            params.append(camera_name)
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        try:
            with closing(self._connect()) as conn:
                cur = conn.execute(q, params)
                return cur.fetchall()
        except Exception as e:
            print(f"خطا در خواندن گزارش‌ها: {e}")
            return []

    def distinct_cameras(self):
        try:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    "SELECT DISTINCT camera_name FROM events WHERE camera_name IS NOT NULL "
                    "ORDER BY camera_name"
                )
                return [r[0] for r in cur.fetchall()]
        except Exception as e:
            print(f"خطا در خواندن لیست دوربین‌های گزارش‌شده: {e}")
            return []

    # -------------------------------------------------------------- خروجی -

    def export_csv(self, path, start=None, end=None, event_type=None, camera_name=None):
        """خروجی CSV با UTF-8 BOM (utf-8-sig) تا اکسل فارسی را درست نشان
        دهد - قابل باز کردن مستقیم با Excel."""
        rows = self.query(start=start, end=end, event_type=event_type,
                           camera_name=camera_name, limit=1_000_000)
        headers_fa = [
            "تاریخ و ساعت", "نوع رویداد", "دوربین", "نام فرد", "تلفن",
            "شماره کارمندی", "شماره محدوده", "نام محدوده", "تعداد نفرات",
            "مسیر تصویر",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers_fa)
            for r in rows:
                row = list(r[:10])  # دو ستون آخر (nvr_id/channel) فقط برای دکمه‌ی
                # «پخش ویدیوی NVR» داخل خودِ برنامه لازم‌اند، نه خروجی اکسل کاربر.
                row[1] = EVENT_TYPE_LABELS_FA.get(row[1], row[1])
                writer.writerow(["" if v is None else v for v in row])
        return path


# نمونه‌ی سراسری - همان الگوی face_engine/camera_store در این پروژه (تک
# نمونه‌ی مشترک بین همه‌ی تردها/دیالوگ‌ها).
report_store = ReportStore()

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

این ماژول عمداً NVR را درگیر نمی‌کند: NVR همچنان فقط ویدیوی خام دوربین‌ها
را طبق تنظیمات خودش ضبط می‌کند (کاری که مستقل از این برنامه انجام می‌دهد)؛
این گزارش‌های متنی/تصویری صرفاً روی سیستمی که CMS رویش اجراست نگه داشته
می‌شوند - دقیقاً همان چیزی که درخواست شد.
"""

import csv
import os
import sqlite3
import time
from contextlib import closing

DB_PATH = "reports.db"
IMAGES_DIR = "report_images"

EVENT_TYPE_LABELS_FA = {
    "face_known": "چهره شناخته‌شده",
    "face_unknown": "چهره تعریف‌نشده",
    "zone_entry": "ورود به محدوده",
    "person_count": "شمارش نفرات",
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
                        image_path TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
                conn.commit()
        except Exception as e:
            print(f"خطا در ساخت پایگاه‌داده‌ی گزارش‌ها: {e}")

    # ------------------------------------------------------------- ذخیره -

    def _save_image(self, crop_frame, prefix):
        if crop_frame is None:
            return None
        try:
            import cv2  # وارد کردن دیرهنگام: این ماژول را بدون opencv هم می‌توان تست کرد

            day_dir = os.path.join(self.images_dir, time.strftime("%Y-%m-%d"))
            os.makedirs(day_dir, exist_ok=True)
            fname = f"{prefix}_{time.strftime('%H%M%S')}_{int(time.time() * 1000) % 1000}.jpg"
            path = os.path.join(day_dir, fname)
            cv2.imwrite(path, crop_frame)
            return path
        except Exception as e:
            print(f"خطا در ذخیره‌ی تصویر گزارش: {e}")
            return None

    def _insert(self, ts, event_type, camera_name=None, person_name=None, phone=None,
                employee_id=None, region_number=None, region_name=None,
                person_count=None, image_path=None):
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO events
                        (ts, event_type, camera_name, person_name, phone, employee_id,
                         region_number, region_name, person_count, image_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, event_type, camera_name, person_name, phone, employee_id,
                     region_number, region_name, person_count, image_path),
                )
                conn.commit()
        except Exception as e:
            print(f"خطا در ثبت رویداد گزارش: {e}")

    def log_face_event(self, camera_name, person, crop_frame=None):
        """هر تشخیص چهره (چه شناخته‌شده چه تعریف‌نشده) را با ساعت/تاریخ
        کامل و تصویر برش‌خورده ثبت می‌کند."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if person:
            event_type = "face_known"
            person_name = person.get("name", "")
            phone = person.get("phone", "")
            employee_id = person.get("employee_id", "")
        else:
            event_type = "face_unknown"
            person_name = phone = employee_id = None
        image_path = self._save_image(crop_frame, event_type)
        self._insert(now, event_type, camera_name=camera_name, person_name=person_name,
                     phone=phone, employee_id=employee_id, image_path=image_path)

    def log_region_alert(self, camera_name, number, name):
        """با ورود شخصی به یک محدوده‌ی هشدار، ثبت می‌شود."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self._insert(now, "zone_entry", camera_name=camera_name,
                     region_number=str(number), region_name=name)

    def log_person_count(self, camera_name, count):
        """تغییر تعداد نفراتِ یک دوربین را ثبت می‌کند (فراخوان مسئول
        فراخوانی فقط هنگام *تغییر* عدد است، نه هر فریم - رجوع کنید به
        CameraSlotWidget.on_people_count در main.py)."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self._insert(now, "person_count", camera_name=camera_name, person_count=count)

    # -------------------------------------------------------------- خواندن -

    def query(self, start=None, end=None, event_type=None, camera_name=None, limit=5000):
        """start/end: رشته‌ی 'YYYY-MM-DD HH:MM:SS' یا 'YYYY-MM-DD'."""
        q = ("SELECT ts, event_type, camera_name, person_name, phone, employee_id, "
             "region_number, region_name, person_count, image_path FROM events WHERE 1=1")
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
                row = list(r)
                row[1] = EVENT_TYPE_LABELS_FA.get(row[1], row[1])
                writer.writerow(["" if v is None else v for v in row])
        return path


# نمونه‌ی سراسری - همان الگوی face_engine/camera_store در این پروژه (تک
# نمونه‌ی مشترک بین همه‌ی تردها/دیالوگ‌ها).
report_store = ReportStore()

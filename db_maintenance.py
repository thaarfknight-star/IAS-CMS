# -*- coding: utf-8 -*-
"""نگهداری دیتابیس و حافظه — 2.0.35-beta (پایداری نسخه‌ی تحویل).

- tune_connection: فعال‌سازی WAL + timeout روی هر اتصال SQLite تا
  خواندن/نوشتن هم‌زمان تردها دیتابیس را قفل نکند و خرابی فایل کمتر شود.
- backup_databases: بکاپ روزانه‌ی خودکار همه‌ی ‎.db‎های پوشه‌ی دیتا؛
  فقط ۷ بکاپ آخر نگه داشته می‌شود.
- maybe_collect: نگهبان سبک حافظه برای ترد دوربین؛ هر ۶۰۰ فریم یک‌بار
  فقط نسل ۱ gc را جمع می‌کند (مکث ناچیز، مناسب سیستم ۴GB).
"""
from __future__ import annotations

import gc
import os
import shutil
import sqlite3
import time

_KEEP_BACKUPS = 7


def tune_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """اعمال پراگماهای پایداری روی یک اتصال SQLite باز."""
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.execute("PRAGMA temp_store=MEMORY;")
        cur.close()
    except Exception:
        pass
    return conn


def open_tuned(db_path: str, **kwargs) -> sqlite3.Connection:
    """ساخت اتصال SQLite با تنظیمات پایداری."""
    conn = sqlite3.connect(db_path, **kwargs)
    return tune_connection(conn)


def backup_databases(data_dir: str, keep: int = _KEEP_BACKUPS) -> list:
    """بکاپ همه‌ی فایل‌های ‎.db‎ داخل data_dir به زیرپوشه‌ی backups.

    خروجی: لیست مسیر فایل‌های بکاپ ساخته‌شده در این فراخوانی.
    """
    made = []
    try:
        backup_dir = os.path.join(data_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        for name in sorted(os.listdir(data_dir)):
            if not name.lower().endswith(".db"):
                continue
            src = os.path.join(data_dir, name)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(backup_dir, f"{stamp}_{name}")
            try:
                shutil.copy2(src, dst)
                made.append(dst)
            except Exception:
                continue
        # هرس بکاپ‌های قدیمی: فقط keep تای جدید نگه داشته می‌شود
        try:
            files = sorted(
                os.path.join(backup_dir, f)
                for f in os.listdir(backup_dir)
                if f.lower().endswith(".db")
            )
            for old in files[:-keep] if len(files) > keep else []:
                try:
                    os.remove(old)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass
    return made


def run_startup_maintenance(data_dir: str) -> list:
    """نگهداری شروع برنامه: بکاپ روزانه فقط یک‌بار در روز."""
    try:
        marker = os.path.join(data_dir, ".last_db_backup")
        today = time.strftime("%Y-%m-%d")
        last = ""
        try:
            with open(marker, "r", encoding="utf-8") as fh:
                last = fh.read().strip()
        except Exception:
            pass
        if last == today:
            return []
        made = backup_databases(data_dir)
        try:
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(today)
        except Exception:
            pass
        return made
    except Exception:
        return []


class MemoryGuard:
    """نگهبان سبک حافظه: هر N فریم، gc نسل ۱ (سریع و کم‌مکث)."""

    def __init__(self, every_frames: int = 600):
        self.every = max(100, int(every_frames))
        self._count = 0

    def note_frame(self) -> bool:
        """بعد از هر فریم صدا زده می‌شود؛ True یعنی gc انجام شد."""
        self._count += 1
        if self._count >= self.every:
            self._count = 0
            try:
                gc.collect(1)
            except Exception:
                pass
            return True
        return False


def maybe_collect(counter: int, every: int = 600) -> bool:
    """نسخه‌ی تابعی ساده برای تست: اگر counter به مضرب every رسید gc می‌کند."""
    if counter > 0 and counter % every == 0:
        try:
            gc.collect(1)
        except Exception:
            pass
        return True
    return False

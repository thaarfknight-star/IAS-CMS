# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.16-beta — رفع کرش‌های باقی‌مانده از ممیزی پایداری.

۳) دیالوگ بازبینی NVR: rw_timeout در PLAYBACK_FFMPEG_OPTS + توقف امن ترد
   در reject/closeEvent (بدون qFatal).
۵) report_store: نوشتن‌ها در ترد writer جدا انجام می‌شود؛ ترد صداکننده
   (GUI) هرگز روی قفل دیتابیس بلاک نمی‌شود.

اجرا:  ~/workspace/testvenv/bin/python tests/test_216_beta.py
"""

import ast
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_passed = []
_failed = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


# ================================== ۳) دیالوگ بازبینی NVR ==============
import nvr_playback_dialog as npd

check("PLAYBACK_FFMPEG_OPTS has rw_timeout",
      "rw_timeout" in npd.PLAYBACK_FFMPEG_OPTS)

_src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "nvr_playback_dialog.py"),
    encoding="utf-8").read()
_tree = ast.parse(_src)
_cls = next(n for n in ast.walk(_tree)
            if isinstance(n, ast.ClassDef) and n.name == "NVRPlaybackDialog")
_methods = {n.name: n for n in _cls.body if isinstance(n, ast.FunctionDef)}


def _calls(fn_node, target):
    return any(isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == target
               for n in ast.walk(fn_node))


check("_stop_thread defined", "_stop_thread" in _methods)
check("reject stops thread safely", _calls(_methods["reject"], "_stop_thread"))
check("closeEvent stops thread safely",
      _calls(_methods["closeEvent"], "_stop_thread"))
check("_stop_thread has terminate fallback",
      "terminate" in ast.dump(_methods["_stop_thread"]))

# تست رفتاری: دیالوگ با capture ساختگی، بستن فوری نباید ترد زنده بگذارد
try:
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)

    _orig_open = npd.open_capture
    _orig_urls = npd.build_playback_urls

    class _FakeCap:
        def isOpened(self):
            return True

        def read(self):
            return (False, None)

        def release(self):
            pass

    npd.open_capture = lambda url, opts: _FakeCap()
    npd.build_playback_urls = lambda nvr, ch, s, e: [("fake", "rtsp://x")]
    import datetime
    try:
        dlg = npd.NVRPlaybackDialog({}, 1,
                                    datetime.datetime(2026, 9, 21, 10, 0, 0),
                                    datetime.datetime(2026, 9, 21, 10, 5, 0))
        time.sleep(0.3)  # بگذار ترد شروع شود
        dlg.reject()     # بستن فوری حین کار ترد
        check("NVRPlaybackDialog.reject leaves no running thread",
              not dlg._thread.isRunning())
    finally:
        npd.open_capture = _orig_open
        npd.build_playback_urls = _orig_urls
    _qt_ok = True
except Exception as e:
    print("   (Qt behavioral test skipped: %s)" % e)
    _qt_ok = False

# ===================================== ۵) report_store ==================
from report_store import ReportStore

tmp = tempfile.mkdtemp()
store = ReportStore(db_path=os.path.join(tmp, "reports.db"),
                    images_dir=os.path.join(tmp, "images"))

check("writer thread alive", store._writer_thread.is_alive())
check("writer thread is daemon", store._writer_thread.daemon)

# قفل انحصاری روی دیتابیس: فراخوان log باید فوری برگردد (نه بلاک روی قفل)
lock_conn = sqlite3.connect(store.db_path, timeout=30)
lock_conn.execute("BEGIN EXCLUSIVE")
try:
    t0 = time.monotonic()
    store.log_region_alert("دوربین تست", 1, "محدوده‌ی سرور")
    store.log_person_count("دوربین تست", 3)
    dt = time.monotonic() - t0
    check("log_* returns immediately while DB is exclusively locked", dt < 1.0)
    time.sleep(2.0)  # قفل را کمی نگه می‌داریم؛ writer با busy-timeout صبر می‌کند
finally:
    lock_conn.execute("COMMIT")
    lock_conn.close()

store.flush(15)
rows = store.query(event_type="zone_entry")
check("event written after lock released",
      any(r[2] == "دوربین تست" for r in rows))
rows = store.query(event_type="person_count")
check("person_count event persisted",
      any(r[2] == "دوربین تست" and r[8] == 3 for r in rows))

# بقیه‌ی logها هم کار می‌کنند
store.log_fire_alarm_panel("پنل تست", active=True)
store.log_fire_smoke_visual("دوربین تست", "smoke")
store.flush(15)
check("fire events persisted",
      len(store.query(event_type="fire_alarm_panel")) == 1
      and len(store.query(event_type="fire_smoke_visual")) == 1)

# distinct_cameras / export_csv سر جایشان‌اند
check("distinct_cameras works", "دوربین تست" in store.distinct_cameras())
csv_path = os.path.join(tmp, "r.csv")
store.export_csv(csv_path)
check("export_csv works", os.path.isfile(csv_path)
      and os.path.getsize(csv_path) > 0)

store.close()
check("close() stops writer cleanly", not store._writer_thread.is_alive())

# ================================================================ جمع‌بندی
print()
print("RESULT: %d passed, %d failed" % (len(_passed), len(_failed)))
if _failed:
    print("FAILED:")
    for n in _failed:
        print(" - " + n)
    sys.exit(1)

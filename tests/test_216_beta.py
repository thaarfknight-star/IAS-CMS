# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.16-beta — رفع کرش‌های باقی‌مانده از ممیزی پایداری.

۱) _run_guarded در onvif_imaging: تایم‌اوت *واقعی* (برخلاف نسخه‌ی قبلی که
   در shutdown(wait=True) گیر می‌کرد).
۲) _try_ports: با تردهای daemon و مهلت کلی واقعی برمی‌گردد.
۳) دیالوگ بازبینی NVR: rw_timeout در PLAYBACK_FFMPEG_OPTS + توقف امن ترد
   در reject/closeEvent (بدون qFatal).
۴) دیالوگ تنظیمات تصویر: accept/reject/closeEvent ترد ONVIF را متوقف
   می‌کنند.
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


# ================================================= ۱) _run_guarded ======
import onvif_imaging as oi


def _block_forever():
    time.sleep(60)
    return {"ok": True}


t0 = time.monotonic()
res = oi._run_guarded(_block_forever, timeout=2)
dt = time.monotonic() - t0
check("run_guarded returns on hard-block (real timeout)", dt < 8)
check("run_guarded timeout error is Persian",
      isinstance(res, dict) and res.get("ok") is False
      and "مهلت" in str(res.get("error", "")))

res = oi._run_guarded(lambda: {"ok": True, "v": 42}, timeout=5)
check("run_guarded success passthrough",
      res == {"ok": True, "v": 42})


def _boom():
    raise RuntimeError("connection refused")


res = oi._run_guarded(_boom, timeout=5)
check("run_guarded exception -> friendly Persian",
      res.get("ok") is False and "اتصال رد شد" in str(res.get("error", "")))

# ================================================= ۲) _try_ports ========
_orig_new_camera = oi._new_camera


def _hanging_camera(host, port, user, pwd):
    time.sleep(60)
    raise AssertionError("should never return")


oi._new_camera = _hanging_camera
try:
    t0 = time.monotonic()
    cam, imaging, media, port, err = oi._try_ports(
        {"host": "192.0.2.1", "port": None, "user": "a", "pwd": "b"})
    dt = time.monotonic() - t0
finally:
    oi._new_camera = _orig_new_camera
check("try_ports returns within overall timeout on total hang",
      dt < oi._OVERALL_TIMEOUT + 10)
check("try_ports hang -> no crash, error message",
      cam is None and isinstance(err, str) and len(err) > 0)


class _FakeCam:
    def create_imaging_service(self):
        return object()

    def create_media_service(self):
        return object()


def _fast_camera(host, port, user, pwd):
    if port == 80:
        raise ConnectionError("refused")
    return _FakeCam()


oi._new_camera = _fast_camera
try:
    cam, imaging, media, port, err = oi._try_ports(
        {"host": "192.0.2.1", "port": None, "user": "a", "pwd": "b"})
finally:
    oi._new_camera = _orig_new_camera
check("try_ports success returns first answering port",
      err is None and port == 8000 and cam is not None)

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

# ============================== ۴) دیالوگ تنظیمات تصویر =================
_src2 = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "image_settings_dialog.py"),
    encoding="utf-8").read()
_tree2 = ast.parse(_src2)
_cls2 = next(n for n in ast.walk(_tree2)
             if isinstance(n, ast.ClassDef) and n.name == "ImageSettingsDialog")
_methods2 = {n.name: n for n in _cls2.body
             if isinstance(n, ast.FunctionDef)}

check("_stop_hw_worker defined", "_stop_hw_worker" in _methods2)
check("accept stops hw worker", _calls(_methods2["accept"], "_stop_hw_worker"))
check("reject stops hw worker", _calls(_methods2["reject"], "_stop_hw_worker"))
check("closeEvent stops hw worker",
      "closeEvent" in _methods2
      and _calls(_methods2["closeEvent"], "_stop_hw_worker"))
check("_stop_hw_worker has terminate fallback",
      "terminate" in ast.dump(_methods2["_stop_hw_worker"]))

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

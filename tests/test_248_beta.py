"""تست 2.0.61-beta: لیست تحت‌نظر و ثبت عبور مستقل از نقش ورود/خروج دوربین.

رگرسیون گزارش طه (2026-09-27):
- پلاک لیست سیاه دیده می‌شد ولی تخلف «ورود غیرمجاز» ثبت نمی‌شد
  (نه در «تخلفات تردد»، نه در «پنل رویدادها»)
- «آمار تردد» خالی بود با اینکه «گزارش عبور» رکورد داشت
علت: موتور جهت در _process_inner اگر دوربین plate_role نداشت، قبل از
بررسی لیست تحت‌نظر و ثبت عبور، با return None خارج می‌شد.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plate_store import PlateStore, normalize_plate_text
from plate_direction import PlateDirectionEngine


def _make_engine(tmp_path):
    store = PlateStore(db_path=str(tmp_path / "plate_test.db"))
    return PlateDirectionEngine(store), store


def test_watchlist_violation_without_plate_role(tmp_path):
    eng, store = _make_engine(tmp_path)
    store.add_watchlist_entry("12ع34567", "black", note="تست")
    cam = {"id": "cam1", "name": "دوربین تست"}  # بدون plate_role
    data = {"plate_text": "12ع34567", "plate_display": "۱۲ ع ۳۴۵ ۶۷"}
    event = {"id": "ev1", "plate_display": "۱۲ ع ۳۴۵ ۶۷", "plate": None,
             "owner_name": "", "snapshot_path": ""}
    result = eng.process(cam, data, event)
    assert result is not None, "process نباید None برگرداند"
    vids = result.get("violations") or []
    assert len(vids) == 1, f"انتظار ۱ تخلف، گرفته شد: {vids}"
    v = store.get_violation(vids[0])
    assert v["violation_type"] == "watchlist_black", v["violation_type"]


def test_crossing_logged_without_plate_role(tmp_path):
    eng, store = _make_engine(tmp_path)
    cam = {"id": "cam1", "name": "دوربین تست"}  # بدون plate_role
    data = {"plate_text": "99ب88888", "plate_display": "۹۹ ب ۸۸۸ ۸۸"}
    event = {"id": "ev2", "plate_display": "۹۹ ب ۸۸۸ ۸۸", "plate": None,
             "owner_name": "", "snapshot_path": ""}
    result = eng.process(cam, data, event)
    assert result is not None
    assert result.get("crossing_id"), "عبور باید ثبت شود"
    stats = store.crossing_stats()
    assert len(stats) >= 1, "آمار تردد نباید خالی باشد"


def test_entry_role_still_logs_direction_violations(tmp_path):
    # رگرسیون: دوربین با نقش ورود باید مثل قبل قوانین جهت را اجرا کند
    eng, store = _make_engine(tmp_path)
    cam = {"id": "cam1", "name": "دوربین ورود", "plate_role": "entry"}
    data = {"plate_text": "11الف22222", "plate_display": "۱۱ الف ۲۲۲ ۲۲"}
    event = {"id": "ev3", "plate_display": "۱۱ الف ۲۲۲ ۲۲", "plate": None,
             "owner_name": "", "snapshot_path": ""}
    r1 = eng.process(cam, data, event)   # ورود اول: سالم
    assert r1 is not None and r1.get("crossing_id")
    r2 = eng.process(cam, data, event)   # ورود مجدد بدون خروج: تخلف
    vtypes = [store.get_violation(v)["violation_type"]
              for v in (r2.get("violations") or [])]
    assert "reentry_without_exit" in vtypes, f"انواع تخلف: {vtypes}"


def test_no_role_no_direction_violations(tmp_path):
    # بدون نقش: نباید تخلف جهت (مثل ورود مجدد) ثبت شود
    eng, store = _make_engine(tmp_path)
    cam = {"id": "cam1", "name": "دوربین تست"}
    data = {"plate_text": "44ج55555", "plate_display": "۴۴ ج ۵۵۵ ۵۵"}
    event = {"id": "ev4", "plate_display": "۴۴ ج ۵۵۵ ۵۵", "plate": None,
             "owner_name": "", "snapshot_path": ""}
    r1 = eng.process(cam, data, event)
    r2 = eng.process(cam, data, event)
    assert (r1.get("violations") or []) == []
    assert (r2.get("violations") or []) == []

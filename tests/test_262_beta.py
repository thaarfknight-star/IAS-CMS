# -*- coding: utf-8 -*-
"""تست 2.0.75-beta:
به دستور طه، هر بار دیده شدن پلاک «لیست تحت‌نظر» (سیاه/سفید) باید یک تخلف
تازه ثبت کند — ضدتکرار ۶۰ثانیه‌ای log_violation برای watchlist غیرفعال است.
رگرسیون رفتار قبلی: همان پلاکِ لیست سیاه که هر ۲۰ ثانیه دیده می‌شد، فقط
هر ۶۰+ ثانیه یک «⛔ ورود غیرمجاز» می‌گرفت و بینشان «چیزی نوشته نمی‌شد».
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plate_store import PlateStore
from plate_direction import PlateDirectionEngine


def _make_engine(tmp_path):
    store = PlateStore(db_path=str(tmp_path / "plate_test.db"))
    return PlateDirectionEngine(store), store


def _sighting(eng, plate="12ع34567", ev="ev"):
    cam = {"id": "cam1", "name": "دوربین تست"}  # بدون plate_role
    data = {"plate_text": plate, "plate_display": "۱۲ ع ۳۴۵ ۶۷"}
    event = {"id": ev, "plate_display": "۱۲ ع ۳۴۵ ۶۷", "plate": None,
             "owner_name": "", "snapshot_path": ""}
    return eng.process(cam, data, event)


def test_watchlist_violation_logged_on_every_sighting(tmp_path):
    """دو دیده شدن پشت سر همِ پلاک لیست سیاه = دو تخلف جدا."""
    eng, store = _make_engine(tmp_path)
    store.add_watchlist_entry("12ع34567", "black", note="تست")
    r1 = _sighting(eng, ev="ev1")
    r2 = _sighting(eng, ev="ev2")
    v1 = (r1.get("violations") or [])
    v2 = (r2.get("violations") or [])
    assert len(v1) == 1 and len(v2) == 1, (v1, v2)
    assert v1[0] != v2[0], "ضدتکرار نباید id قبلی را برگرداند"
    rows = store.list_violations(violation_type="watchlist_black")
    assert len(rows) == 2, f"انتظار ۲ ردیف تخلف، گرفته شد: {len(rows)}"


def test_watchlist_white_also_no_dedup(tmp_path):
    eng, store = _make_engine(tmp_path)
    store.add_watchlist_entry("12ع34567", "white", note="تست")
    r1 = _sighting(eng, ev="ev1")
    r2 = _sighting(eng, ev="ev2")
    v1 = (r1.get("violations") or [])
    v2 = (r2.get("violations") or [])
    assert len(v1) == 1 and len(v2) == 1
    assert v1[0] != v2[0]


def test_other_violation_types_still_dedup(tmp_path):
    """ضدتکرار ۶۰ثانیه‌ای برای بقیه‌ی انواع تخلف سر جایش است."""
    _, store = _make_engine(tmp_path)
    vid1 = store.log_violation("wrong_way", "12ع34567")
    vid2 = store.log_violation("wrong_way", "12ع34567")
    assert vid1 == vid2, "تخلف غیرwatchlist باید ضدتکرار داشته باشد"


def test_log_violation_dedup_seconds_zero(tmp_path):
    """dedup_seconds=0 یعنی ثبت تازه در هر فراخوانی."""
    _, store = _make_engine(tmp_path)
    vid1 = store.log_violation("watchlist_black", "12ع34567",
                               dedup_seconds=0)
    vid2 = store.log_violation("watchlist_black", "12ع34567",
                               dedup_seconds=0)
    assert vid1 != vid2

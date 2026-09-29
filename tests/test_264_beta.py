# -*- coding: utf-8 -*-
"""تست 2.0.77-beta — رگرسیون گزارش طه (2026-09-29):
۱) پلاک لیست سیاه در جدول «گزارش عبور» ستون وضعیت «تعریف‌نشده» می‌زد
   در حالی که باید «⛔ ورود غیرمجاز» بزند.
۲) در «پنل رویدادها» یک‌بار فقط «پلاک: ...» می‌زد و یک‌بار «⛔ ورود غیرمجاز»
   (ردیف خنثی + ردیف قرمز برای یک دیدن). حالا:
   - لیست سیاه → فقط ردیف قرمز «⛔ ورود غیرمجاز» (ردیف خنثی تکراری نه)
   - تعریف‌نشده → «تعریف‌نشده | پلاک: ...»
   - تعریف‌شده → «نام مالک | پلاک: ...»
"""
import os
import sys
import tempfile
import types
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- stub وابستگی‌های سنگین اختیاری قبل از import main ---
fr = types.ModuleType("face_recognition")
fr.face_locations = lambda *a, **k: []
fr.face_encodings = lambda *a, **k: []
fr.face_distance = lambda *a, **k: []
fr.compare_faces = lambda *a, **k: []
sys.modules.setdefault("face_recognition", fr)

import main
from plate_store import PlateStore, plate_status_label


def _tmp_store():
    tmp = tempfile.mkdtemp(prefix="plate264_")
    return PlateStore(db_path=os.path.join(tmp, "plates.db"))


# ---------- plate_status_label ----------

def test_status_label_black_beats_defined():
    assert plate_status_label("X", True, {"black"}) == "⛔ ورود غیرمجاز"


def test_status_label_black_beats_white():
    assert plate_status_label("X", False, {"black", "white"}) == "⛔ ورود غیرمجاز"


def test_status_label_white():
    assert plate_status_label("X", False, {"white"}) == "⭐ لیست سفید"


def test_status_label_defined():
    assert plate_status_label("X", True, set()) == "✅ تعریف‌شده"


def test_status_label_undefined():
    assert plate_status_label("X", False, set()) == "⚠️ تعریف‌نشده"


def test_watchlist_map_bulk():
    s = _tmp_store()
    s.add_watchlist_entry("12ب34567", "black", note="t")
    s.add_watchlist_entry("99ج99999", "white", note="t")
    m = s.watchlist_map(["12ب34567", "99ج99999", "00ع00000"])
    assert m.get("12ب34567") == {"black"}, m
    assert m.get("99ج99999") == {"white"}, m
    assert "00ع00000" not in m


# ---------- ردیف پنل رویدادها ----------

class _FakePanelList:
    def __init__(self):
        self.texts = []

    def insertItem(self, idx, item):
        self.texts.append(item.text())

    def count(self):
        return len(self.texts)

    def takeItem(self, idx):
        pass


def _sighting(camera="cam1", plate_text="12ب34567",
              plate_display="۱۲ ب ۳۴۵ ۶۷", is_defined=False,
              owner_name="", wl_kinds=(), engine_active=True):
    fake = types.SimpleNamespace(
        plate_direction=object() if engine_active else None,
        events_panel_list=_FakePanelList(),
    )
    wl = [{"kind": k} for k in wl_kinds]
    data = {"plate_text": plate_text, "plate_display": plate_display}
    event = {"is_defined": is_defined, "owner_name": owner_name}
    with mock.patch.object(main.plate_store, "find_watchlist",
                           return_value=wl):
        main.MainWindow._push_plate_sighting_to_events(
            fake, camera, data, event)
    return fake.events_panel_list.texts


def test_blacklisted_sighting_suppressed_when_engine_active():
    """سناریوی طه: پلاک لیست سیاه نباید ردیف خنثی «پلاک: ...» بگیرد؛
    فقط ردیف قرمز «⛔ ورود غیرمجاز» (که موتور جدا ثبت می‌کند)."""
    texts = _sighting(wl_kinds=("black",), engine_active=True)
    assert texts == [], texts


def test_whitelisted_sighting_suppressed_when_engine_active():
    texts = _sighting(wl_kinds=("white",), engine_active=True)
    assert texts == [], texts


def test_blacklisted_sighting_shows_label_when_no_engine():
    texts = _sighting(wl_kinds=("black",), engine_active=False)
    assert len(texts) == 1
    assert "⛔ ورود غیرمجاز" in texts[0], texts[0]


def test_undefined_sighting_shows_undefined():
    texts = _sighting(is_defined=False, wl_kinds=())
    assert len(texts) == 1
    assert "تعریف‌نشده" in texts[0], texts[0]
    assert "۱۲ ب ۳۴۵ ۶۷" in texts[0], texts[0]


def test_defined_sighting_shows_owner_and_plate():
    texts = _sighting(is_defined=True, owner_name="علی رضایی", wl_kinds=())
    assert len(texts) == 1
    assert "علی رضایی" in texts[0], texts[0]
    assert "۱۲ ب ۳۴۵ ۶۷" in texts[0], texts[0]
    assert "تعریف‌نشده" not in texts[0], texts[0]

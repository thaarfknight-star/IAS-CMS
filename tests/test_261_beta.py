# -*- coding: utf-8 -*-
"""تست 2.0.74-beta:
۱) راهنمای فیلترشده: کاربر محدود فقط بخش‌های صفحه‌های مجازش (+ عمومی) را
   در راهنما می‌بیند، نه کل PDF.
۲) «دیدن پلاک» در پنل رویدادها ثبت می‌شود (نه فقط تخلف پلاک).
۳) «تخلف تردد طبقاتی» در پنل رویدادها ثبت می‌شود.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _qt_available():
    try:
        import PyQt6  # noqa: F401
        return True
    except ImportError:
        return False


def _stub_heavy():
    """استاب وابستگی‌های سنگین برای ایمپورت main."""
    import types
    from unittest.mock import MagicMock
    if "cv2" not in sys.modules:
        sys.modules["cv2"] = MagicMock(name="cv2")
    if "face_recognition" not in sys.modules:
        fr = types.ModuleType("face_recognition")
        fr.face_locations = lambda *a, **k: []
        fr.face_encodings = lambda *a, **k: []
        sys.modules["face_recognition"] = fr


# ---------------------------------------------------------------- راهنما ---

def test_section_range_from_next_section_start():
    import app_help
    # پایان هر بخش از روی شروع بخش بعدی حساب می‌شود (مقاوم در برابر رشد PDF)
    assert app_help._section_range("home", 51) == (6, 13)
    assert app_help._section_range("plate", 51) == (25, 31)
    assert app_help._section_range("settings", 51) == (42, 47)
    assert app_help._section_range("faq", 51) == (48, 49)


def test_filtered_manual_only_allowed_sections():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        pytest.skip("pypdf نصب نیست")
    import app_help
    src = app_help.manual_path()
    if not os.path.exists(src):
        pytest.skip("PDF راهنما در ریپو نیست")

    path, remap = app_help._filtered_manual_pdf({"home"})
    assert os.path.exists(path)
    reader = pypdf.PdfReader(path)
    # عمومی (۱،۳،۵) + خانه (۶..۱۳) + PTZ (۴۱) + عیب‌یابی (۴۸،۴۹) = ۱۴ صفحه؛
    # بخش‌های پلاک (۲۵..۳۱) و مدیریتی (۵۰،۵۱) نباید باشند.
    assert len(reader.pages) == 14, len(reader.pages)
    assert remap["home"] == 4  # ترتیب: ۱،۳،۵،۶ → صفحه‌ی ۶ چهارمین است
    assert "plate" not in remap
    assert "users" not in remap and "autoupdate" not in remap


def test_filtered_manual_plate_section_when_allowed():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        pytest.skip("pypdf نصب نیست")
    import app_help
    if not os.path.exists(app_help.manual_path()):
        pytest.skip("PDF راهنما در ریپو نیست")

    path, remap = app_help._filtered_manual_pdf({"home", "plate"})
    reader = pypdf.PdfReader(path)
    # ۱۴ صفحه‌ی قبلی + ۷ صفحه‌ی پلاک (۲۵..۳۱) = ۲۱
    assert len(reader.pages) == 21, len(reader.pages)
    # ترتیب: ۱،۳،۵،۶..۱۳ (۱۱ صفحه) بعد ۲۵ → دوازدهمین
    assert remap["plate"] == 12
    assert remap["home"] == 4


def test_filtered_manual_cached_by_permission_set():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        pytest.skip("pypdf نصب نیست")
    import app_help
    if not os.path.exists(app_help.manual_path()):
        pytest.skip("PDF راهنما در ریپو نیست")
    p1, _ = app_help._filtered_manual_pdf({"home"})
    p2, _ = app_help._filtered_manual_pdf({"home"})
    assert p1 == p2  # کش: همان فایل
    p3, _ = app_help._filtered_manual_pdf({"home", "face"})
    assert p3 != p1  # دسترسی متفاوت → فایل متفاوت


# ------------------------------------------------------- پنل رویدادها ---

def _make_stub_window():
    """پنجره‌ی بدلی با فقط events_panel_list برای صدا زدن متدهای MainWindow
    بدون ساخت کل MainWindow."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _stub_heavy()
    from PyQt6.QtWidgets import QApplication, QListWidget
    _app = QApplication.instance() or QApplication(["test"])
    _stub = types.SimpleNamespace(events_panel_list=QListWidget())
    import main as main_mod
    return _stub, main_mod, _app


def test_plate_sighting_pushed_to_events_panel():
    if not _qt_available():
        return
    stub, main_mod, _app = _make_stub_window()
    data = {"plate_text": "12ب34567",
            "plate_display": "۱۲ ب ۳۴۵ ۶۷",
            "conf": 0.93, "crop": None}
    main_mod.MainWindow._push_plate_sighting_to_events(
        stub, "دوربین ورودی", data)
    assert stub.events_panel_list.count() == 1
    text = stub.events_panel_list.item(0).text()
    assert "۱۲ ب ۳۴۵ ۶۷" in text
    assert "دوربین ورودی" in text
    assert "🚗" in text


def test_floor_violation_pushed_to_events_panel():
    if not _qt_available():
        return
    stub, main_mod, _app = _make_stub_window()
    main_mod.MainWindow._push_floor_violation_to_events(
        stub, "دوربین راهرو", "", "", "طبقه ۲", None)
    assert stub.events_panel_list.count() == 1
    text = stub.events_panel_list.item(0).text()
    assert "تخلف تردد طبقاتی" in text
    assert "طبقه ۲" in text
    assert "شخص ناشناس" in text  # بدون هویت


def test_floor_violation_with_known_name():
    if not _qt_available():
        return
    stub, main_mod, _app = _make_stub_window()
    main_mod.MainWindow._push_floor_violation_to_events(
        stub, "دوربین راهرو", "علی رضایی", "P-0007", "طبقه ۱", None)
    text = stub.events_panel_list.item(0).text()
    assert "علی رضایی" in text
    assert "شخص ناشناس" not in text

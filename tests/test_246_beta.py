# -*- coding: utf-8 -*-
"""تست‌های 2.0.53-beta و 2.0.55-beta:
  ۱) انتقال دکمه‌ی «دیدن تصاویر» از صفحه‌ی چهره‌ها به پنل «تشخیص چهره»
     در صفحه‌ی اصلی (به دستور کاربر)
  ۲) کاتالوگ PDF راهنما + دکمه‌ی «راهنما» در هدر و نگاشت صفحه‌ها
  ۳) (2.0.55) آموزش کامل ۳۲ صفحه‌ای + نگاشت جدید صفحه‌های راهنما
"""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------- نگاشت راهنمای PDF

def test_help_pages_mapping():
    from app_help import HELP_PAGES
    assert set(HELP_PAGES) == {"home", "fire", "face", "reports", "plate",
                               "person", "map", "settings"}
    # نگاشت 2.0.57: شروع هر بخش در آموزش ۴۴ صفحه‌ای دکمه‌به‌دکمه، بدون تکرار
    assert HELP_PAGES == {"home": 6, "fire": 13, "face": 16, "reports": 20,
                          "plate": 24, "person": 31, "map": 35, "settings": 38}


def test_manual_pdf_exists_and_has_44_pages():
    from pypdf import PdfReader
    from app_help import manual_path
    path = manual_path()
    assert os.path.exists(path), f"PDF راهنما پیدا نشد: {path}"
    assert len(PdfReader(path).pages) == 44


def test_manual_pdf_bundled_in_assets():
    # باید داخل assets/help باشد تا build.yml آن را باندل کند
    rel = os.path.join(REPO, "assets", "help", "user-manual.pdf")
    assert os.path.exists(rel)


# --------------------------------------- دکمه‌ی گالری در پنل تشخیص چهره

def _main_source():
    with open(os.path.join(REPO, "main.py"), encoding="utf-8") as f:
        return f.read()


def test_gallery_button_in_face_panel_not_face_page():
    src = _main_source()
    # دکمه در پنل تشخیص چهره‌ی صفحه‌ی اصلی ساخته شده
    assert "دیدن تصاویر" in src
    assert "def open_face_gallery" in src
    assert "DetectedFacesDialog" in src
    assert "FaceGalleryDialog" not in src
    # و دکمه از صفحه‌ی «چهره‌ها» برداشته شده
    with open(os.path.join(REPO, "face_library_dialog.py"),
              encoding="utf-8") as f:
        face_src = f.read()
    assert "gallery_btn" not in face_src
    assert "def open_gallery" not in face_src
    assert "class FaceGalleryDialog" not in face_src
    assert "class DetectedFacesDialog" in face_src


def test_on_face_event_attaches_structured_data():
    """on_face_event باید داده‌ی ساخت‌یافته (camera/time/name/known) را در
    حافظه‌ی _recent_face_events بگذارد تا گالری «دیدن تصاویر» از آن بخواند
    (از 2.0.56: چهره‌ها دیگر در پنل رویدادها نیستند)."""
    src = _main_source()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "on_face_event":
            # کلیدهای دیکشنری داده‌ی ساخت‌یافته
            dicts = [n for n in ast.walk(node) if isinstance(n, ast.Dict)]
            keys = set()
            for d in dicts:
                for k in d.keys:
                    if isinstance(k, ast.Constant):
                        keys.add(k.value)
            assert {"camera", "time", "name", "known"} <= keys
            assert "_recent_face_events" in src
            return
    pytest.fail("on_face_event پیدا نشد")


def test_detected_faces_dialog_renders():
    """رندر آفسکرین گالری چهره‌های تشخیص‌داده‌شده با ایونت فیک."""
    import sys as _sys
    from unittest.mock import MagicMock as _MM
    _sys.modules.setdefault("cv2", _MM(name="cv2"))
    _sys.modules.setdefault("numpy", _MM(name="numpy"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, REPO)
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QPixmap, QColor
    app = QApplication.instance() or QApplication([])
    from face_library_dialog import DetectedFacesDialog

    pix = QPixmap(80, 80)
    pix.fill(QColor("#336699"))
    events = [
        {"pixmap": pix, "camera": "دوربین ۱", "time": "15:40:01",
         "name": "علی", "known": True},
        {"pixmap": pix, "camera": "دوربین ۲", "time": "15:41:22",
         "name": "", "known": False},
        {"pixmap": None, "camera": "", "time": "", "name": "", "known": False},
    ]
    dlg = DetectedFacesDialog(events)
    assert dlg.windowTitle().startswith("🖼")
    # حالت خالی نباید کرش بدهد
    DetectedFacesDialog([])


def test_open_help_wired():
    src = _main_source()
    assert "def open_help" in src
    assert "_current_page_key" in src
    assert "راهنما" in src
    # AST: متد open_help باید open_manual را صدا بزند
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "open_help":
            names = [n.func.id for n in ast.walk(node)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)]
            attrs = [n.func.attr for n in ast.walk(node)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)]
            found = "open_manual" in names + attrs
    assert found

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
                               "person", "map", "ptz", "settings",
                               "faq", "users", "autoupdate"}
    # نگاشت 2.0.74: شروع هر بخش در آموزش ۵۱ صفحه‌ای قدم‌به‌قدم، بدون تکرار
    # (faq=48 قبلاً در PDF بود ولی در نگاشت جا افتاده بود)
    assert HELP_PAGES == {"home": 6, "fire": 14, "face": 17, "reports": 21,
                          "plate": 25, "person": 32, "map": 37, "ptz": 41,
                          "settings": 42, "faq": 48,
                          "users": 50, "autoupdate": 51}


def test_manual_pdf_exists_and_has_51_pages():
    from pypdf import PdfReader
    from app_help import manual_path
    path = manual_path()
    assert os.path.exists(path), f"PDF راهنما پیدا نشد: {path}"
    assert len(PdfReader(path).pages) == 51


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


def test_add_buttons_row_and_net_probe_below_panel():
    # (2.0.58 به دستور کاربر) دو دکمه‌ی افزودن دوربین/NVR کنار هم در یک ردیف،
    # و «تست پایداری شبکه» زیر پنل «دوربین‌ها و NVRهای من» (نه داخل ردیف).
    src = _main_source()
    assert "add_btn_row.addWidget(self.add_camera_btn, 1)" in src
    assert "add_btn_row.addWidget(self.add_nvr_btn, 1)" in src
    assert "add_btn_row.addWidget(self.net_probe_btn)" not in src
    cam = src.index('left_panel.addWidget(cam_group)')
    probe = src.index('left_panel.addWidget(self.net_probe_btn)')
    assert probe > cam, "net_probe_btn باید بعد از cam_group به left_panel اضافه شود"


def test_camera_list_and_connect_hint_created_in_init_ui():
    # رگرسیون 2.0.58: جابه‌جایی دکمه‌ها نباید خطوط ساخت camera_list و
    # connect_hint را پاک کند (باعث NameError موقع اجرای برنامه می‌شد).
    src = _main_source()
    assert "self.camera_list = CameraTreeWidget()" in src
    assert "connect_hint = QLabel(" in src
    # و هر دو قبل از استفاده در cam_layout ساخته شده باشند
    assert src.index("self.camera_list = CameraTreeWidget()") < \
        src.index("cam_layout.addWidget(self.camera_list)")
    assert src.index("connect_hint = QLabel(") < \
        src.index("cam_layout.addWidget(connect_hint)")


def test_add_buttons_have_clean_rtl_labels():
    # رگرسیون 2.0.60: برچسب دکمه‌های افزودن نباید با "+" شروع شود؛ کاراکتر
    # خنثی ابتدای متن راست‌به‌چپ در دکمه‌ی باریک، نوشتار را به‌هم می‌ریزد.
    src = _main_source()
    assert 'QPushButton("افزودن دوربین")' in src
    assert 'QPushButton("افزودن NVR")' in src
    assert '"+ افزودن دوربین"' not in src
    assert '"+ افزودن NVR"' not in src

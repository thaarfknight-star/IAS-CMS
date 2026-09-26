# -*- coding: utf-8 -*-
"""تست‌های 2.0.53-beta:
  ۱) انتقال دکمه‌ی «دیدن تصاویر» از صفحه‌ی چهره‌ها به پنل «تشخیص چهره»
     در صفحه‌ی اصلی (به دستور کاربر)
  ۲) کاتالوگ PDF راهنما + دکمه‌ی «راهنما» در هدر و نگاشت صفحه‌ها
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
    # صفحه‌های ۲ تا ۹، بدون تکرار
    assert sorted(HELP_PAGES.values()) == [2, 3, 4, 5, 6, 7, 8, 9]


def test_manual_pdf_exists_and_has_10_pages():
    from pypdf import PdfReader
    from app_help import manual_path
    path = manual_path()
    assert os.path.exists(path), f"PDF راهنما پیدا نشد: {path}"
    assert len(PdfReader(path).pages) == 10


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
    assert "FaceGalleryDialog" in src
    # و دکمه از صفحه‌ی «چهره‌ها» برداشته شده (کلاس FaceGalleryDialog سر جایش است)
    with open(os.path.join(REPO, "face_library_dialog.py"),
              encoding="utf-8") as f:
        face_src = f.read()
    assert "gallery_btn" not in face_src
    assert "def open_gallery" not in face_src


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

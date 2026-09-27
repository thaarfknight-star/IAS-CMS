# -*- coding: utf-8 -*-
"""تست 2.0.67-beta: برند IAS Viewer در لوگو و گرافیک‌های نصب‌کننده."""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def test_no_sorena_in_installer_graphics_script():
    src = _read("installer/make_graphics.py")
    assert "سورنا" not in src, "make_graphics.py هنوز «سورنا» دارد"
    assert "SORENA" not in src.upper().replace("IAS VIEWER", ""), \
        "make_graphics.py هنوز SORENA دارد"


def test_installer_graphics_say_ias_viewer():
    src = _read("installer/make_graphics.py")
    assert src.count("IAS Viewer") >= 5, "رشته‌های IAS Viewer در make_graphics.py کم است"


def test_version_pill_bidi_isolated():
    src = _read("installer/make_graphics.py")
    # LRE/PDF کلاسیک — python-bidi 0.6.11 روی CI کاراکترهای isolate جدید (U+2066) را نمی‌شناسد
    assert "\\u202a" in src and "\\u202c" in src, "ایزوله‌سازی bidi نسخه در make_graphics.py نیست"
    assert "\\u2066" not in src and "\\u2069" not in src, \
        "کاراکتر isolate جدید (U+2066/U+2069) باعث کرش python-bidi روی CI می‌شود"


def test_logo_full_exists_and_valid():
    from PIL import Image
    p = os.path.join(REPO, "assets", "logo_full.png")
    assert os.path.isfile(p), "assets/logo_full.png نیست"
    img = Image.open(p)
    assert img.width > img.height, "logo_full باید افقی (سپر + متن) باشد"


def test_installer_graphics_regenerated():
    from PIL import Image
    for name in ("bg_welcome", "bg_dir", "bg_install", "bg_finish",
                 "bg_uninstall", "bg_uninstall_progress", "bg_uninstall_finish"):
        for ext in ("png", "bmp"):
            p = os.path.join(REPO, "installer", "graphics", name + "." + ext)
            assert os.path.isfile(p), p + " نیست"
    img = Image.open(os.path.join(REPO, "installer", "graphics", "bg_welcome.png"))
    assert img.size == (960, 600), img.size


def test_nsi_display_name_uses_app_name():
    nsi = _read("installer/installer.nsi")
    assert 'DisplayName" "${APP_NAME}' in nsi
    assert '!define APP_NAME "IAS Viewer"' in nsi

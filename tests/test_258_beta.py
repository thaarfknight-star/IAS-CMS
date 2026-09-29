# -*- coding: utf-8 -*-
"""تست 2.0.72-beta: PDF راهنمای باندل‌شده — اسکرین‌شات واقعی + نسخه + نقشه صفحات.

سناریو: PDF راهنما با ۱۳ اسکرین‌شات واقعی رابط (و دیالوگ‌ها) بازسازی شد و
متن صفحه‌های کلیدی کامل‌تر شد. این تست تضمین می‌کند PDF باندل‌شده:
نسخه‌ی درست را دارد، دقیقاً ۵۱ صفحه است (نقشه‌ی app_help.py به‌هم نریخته)،
هر ۱۳ اسکرین‌شات داخلش embed شده و نقشه‌ی صفحات کمک دست‌نخورده مانده.
"""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(REPO, "assets", "help", "user-manual.pdf")
VERSION = "2.0.77-beta"


def _pdf_bytes():
    assert os.path.isfile(PDF), "assets/help/user-manual.pdf نیست"
    with open(PDF, "rb") as f:
        return f.read()


def test_version_bumped():
    with open(os.path.join(REPO, "version.txt"), encoding="utf-8") as f:
        assert f.read().strip() == VERSION, "version.txt باید 2.0.75-beta باشد"


def test_pdf_version_in_metadata():
    # نسخه در Subject متادیتا (بدون فشرده‌سازی) نوشته می‌شود
    raw = _pdf_bytes()
    assert VERSION.encode("ascii") in raw, \
        "نسخه‌ی PDF باندل‌شده با version.txt هم‌خوان نیست"


def test_pdf_page_count_51():
    raw = _pdf_bytes()
    pages = len(re.findall(rb"/Type\s*/Page[^s]", raw))
    assert pages == 51, f"PDF باید دقیقاً ۵۱ صفحه باشد، هست: {pages}"


def test_pdf_has_all_screenshots():
    raw = _pdf_bytes()
    imgs = len(re.findall(rb"/Subtype\s*/Image", raw))
    assert imgs >= 13, f"هر ۱۱ اسکرین‌شات باید embed شده باشد، هست: {imgs}"


def test_pdf_not_empty_shell():
    size = os.path.getsize(PDF)
    assert size > 500_000, f"حجم PDF مشکوک است: {size} بایت"


def test_help_page_map_unchanged():
    src = open(os.path.join(REPO, "app_help.py"), encoding="utf-8").read()
    assert '"ptz": 41' in src, "نقشه‌ی صفحه‌ی PTZ در app_help.py به‌هم ریخته"
    assert '"settings": 42' in src, "نقشه‌ی صفحه‌ی تنظیمات در app_help.py به‌هم ریخته"

# -*- coding: utf-8 -*-
"""(2.0.55-beta) راهنمای کاربری PDF داخل برنامه.

کاتالوگ «راهنمای کاربری» در assets/help/user-manual.pdf باندل می‌شود
(build.yml کل پوشه‌ی assets را اضافه می‌کند) و دکمه‌ی «❓ راهنما» در هدر
برنامه، PDF را روی صفحه‌ی مربوط به صفحه‌ی فعلی باز می‌کند.

نقشه‌ی صفحه‌های PDF (آموزش کامل ۳۲ صفحه‌ای):
  1=جلد، 2=فهرست، 3=آشنایی، 4=نصب، 5=شروع سریع، 6=صفحه اصلی،
  10=اعلام حریق، 12=چهره‌ها، 15=گزارش‌ها، 18=پلاک‌خوان،
  22=ردیابی اشخاص، 25=نقشه ساختمان، 27=تنظیمات، 31=عیب‌یابی
"""
import os
import sys

HELP_PAGES = {
    "home": 6,
    "fire": 10,
    "face": 12,
    "reports": 15,
    "plate": 18,
    "person": 22,
    "map": 25,
    "settings": 27,
}

_MANUAL_REL = os.path.join("assets", "help", "user-manual.pdf")


def manual_path():
    """مسیر فایل PDF راهنما؛ هم در حالت سورس و هم داخل exe باندل‌شده."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, _MANUAL_REL)
        if os.path.exists(p):
            return p
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        _MANUAL_REL)
    if os.path.exists(here):
        return here
    # حالت اجرای dev از داخل زیرپوشه‌ها
    alt = os.path.join(os.getcwd(), _MANUAL_REL)
    return alt if os.path.exists(alt) else here


def open_manual(page_key=None, parent=None):
    """باز کردن PDF راهنما روی صفحه‌ی مربوط به page_key.

    بیشتر PDFخوان‌ها (Edge/Chrome/Adobe/Sumatra) قطعه‌ی ‎#page=N‎ را
    می‌فهمند و مستقیم به همان صفحه می‌روند؛ اگر PDFخوان آن را نفهمید،
    همان اول PDF باز می‌شود (بدون خطا).
    """
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import QMessageBox
    path = manual_path()
    if not os.path.exists(path):
        QMessageBox.warning(
            parent, "راهنما پیدا نشد",
            "فایل راهنمای کاربری پیدا نشد.\n"
            "اگر از روی سورس اجرا می‌کنید، مطمئن شوید پوشه‌ی assets کنار "
            "main.py است.")
        return False
    page = HELP_PAGES.get(page_key or "home", 2)
    url = QUrl.fromLocalFile(os.path.abspath(path))
    url.setFragment(f"page={page}")
    return QDesktopServices.openUrl(url)

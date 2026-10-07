# -*- coding: utf-8 -*-
"""(2.0.55-beta) راهنمای کاربری PDF داخل برنامه.

کاتالوگ «راهنمای کاربری» در assets/help/user-manual.pdf باندل می‌شود
(build.yml کل پوشه‌ی assets را اضافه می‌کند) و دکمه‌ی «❓ راهنما» در هدر
برنامه، PDF را روی صفحه‌ی مربوط به صفحه‌ی فعلی باز می‌کند.

نقشه‌ی صفحه‌های PDF (آموزش کامل ۵۵ صفحه‌ای، نسخه‌ی 1.0.0):
  1=جلد، 2=فهرست، 6=صفحه اصلی، 14=اعلام حریق، 17=چهره‌ها،
  22=محدوده‌ی هشدار، 23=شمارش افراد، 24=گزارش‌ها، 28=پلاک‌خوان،
  35=ردیابی اشخاص، 40=نقشه ساختمان، 44=کنترل PTZ، 45=شنیدن صدا،
  46=تنظیمات، 52=عیب‌یابی، 54=مدیریت کاربران، 55=آپدیت خودکار
"""
import hashlib
import os
import sys
import tempfile

# (1.0.0) خروجی HELP-PAGE-MAP در build_manual.py — نسخه‌ی 1.0.0 (۵۵ صفحه).
HELP_PAGES = {
    "home": 6,
    "fire": 14,
    "face": 17,
    "region_alert": 22,
    "people_counting": 23,
    "reports": 24,
    "plate": 28,
    "person": 35,
    "map": 40,
    "ptz": 44,
    "listen": 45,
    "settings": 46,
    "faq": 52,
    "users": 54,
    "autoupdate": 55,
}

# (2.0.74-beta) کلیدهایی که در راهنمای فیلترشده‌ی کاربرِ محدود، بخش
# دارند. «users» و «autoupdate» فقط برای ادمین‌اند و هیچ‌وقت در نسخه‌ی
# فیلترشده نمی‌آیند (مسیر ادمین allowed_pages=None می‌گیرد و کل PDF را
# باز می‌کند). «ptz» و «listen» زیرمجموعه‌ی «home» حساب می‌شوند.
# (1.0.0) «region_alert» و «people_counting» عملاً فقط برای ادمین‌اند
# (در ACCESS_PAGES نیستند و به کاربر عادی داده نمی‌شوند).
FILTERABLE_KEYS = ("home", "fire", "face", "region_alert", "people_counting",
                   "reports", "plate", "person", "map", "ptz", "listen",
                   "settings", "faq")

# صفحه‌های عمومی که در راهنمای فیلترشده برای همه می‌آید:
# 1=جلد، 3=آشنایی، 5=شروع سریع (نصب/فهرست/مدیریت کاربران/آپدیت خودکار
# فقط برای ادمین است).
_GENERIC_FILTER_PAGES = (1, 3, 5)


def _section_range(page_key, total_pages):
    """بازه‌ی صفحه‌های PDF (1-based، شامل هر دو سر) برای یک بخش راهنما؛
    پایان بخش از روی شروع بخش بعدی حساب می‌شود تا با رشد راهنما نشکند."""
    starts = sorted(set(HELP_PAGES.values()) | {total_pages + 1})
    s = HELP_PAGES[page_key]
    e = min(x for x in starts if x > s) - 1
    return (s, min(e, total_pages))

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


def _filtered_manual_pdf(allowed_pages):
    """(2.0.74-beta) ساخت نسخه‌ی فیلترشده‌ی «راهنمای کاربری» فقط با
    بخش‌هایی که کاربر به آن‌ها دسترسی دارد (allowed_pages: مجموعه‌ای از
    کلیدهای ACCESS_PAGES).

    خروجی: (مسیر فایل PDF فیلترشده، نگاشت کلید بخش -> شماره‌ی صفحه در
    PDF فیلترشده). فایل در پوشه‌ی موقت سیستم با هشِ مجموعه‌ی دسترسی‌ها
    کش می‌شود تا در هر نشست فقط یک‌بار ساخته شود.
    """
    from pypdf import PdfReader, PdfWriter
    src = manual_path()
    reader = PdfReader(src)
    total = len(reader.pages)

    wanted = []  # [(شماره‌ی صفحه‌ی اصلی, کلید بخش یا None)]
    for p in _GENERIC_FILTER_PAGES:
        if p <= total:
            wanted.append((p, None))
    for key in FILTERABLE_KEYS:
        if key == "home":
            ok = "home" in allowed_pages
        elif key in ("ptz", "listen"):
            ok = "home" in allowed_pages  # PTZ و شنیدن صدا بخشی از صفحه‌ی اصلی‌اند
        elif key == "faq":
            ok = True  # عیب‌یابی عمومی برای همه
        else:
            ok = key in allowed_pages
        if not ok:
            continue
        s, e = _section_range(key, total)
        owner = "home" if key == "ptz" else key
        wanted.extend((p, owner) for p in range(s, e + 1))

    seen = set()
    ordered = []
    for p, owner in sorted(wanted):
        if p not in seen:
            seen.add(p)
            ordered.append((p, owner))

    writer = PdfWriter()
    remap = {}
    for new_idx, (p, owner) in enumerate(ordered, start=1):
        writer.add_page(reader.pages[p - 1])
        if owner and owner not in remap:
            remap[owner] = new_idx

    fingerprint = hashlib.sha256(
        (",".join(sorted(allowed_pages)) + "|" +
         str(os.path.getmtime(src))).encode("utf-8")).hexdigest()[:12]
    out = os.path.join(tempfile.gettempdir(),
                       f"IAS-manual-{fingerprint}.pdf")
    if not os.path.exists(out):
        with open(out, "wb") as f:
            writer.write(f)
    return out, remap


def open_manual(page_key=None, parent=None, allowed_pages=None):
    """باز کردن PDF راهنما روی صفحه‌ی مربوط به page_key.

    بیشتر PDFخوان‌ها (Edge/Chrome/Adobe/Sumatra) قطعه‌ی ‎#page=N‎ را
    می‌فهمند و مستقیم به همان صفحه می‌روند؛ اگر PDFخوان آن را نفهمید،
    همان اول PDF باز می‌شود (بدون خطا).

    (2.0.74-beta) allowed_pages: مجموعه‌ی کلیدهای صفحه‌های مجاز کاربر
    (از user_manager.ACCESS_PAGES). اگر None باشد یعنی دسترسی کامل
    (ادمین/حالت توسعه) و کل PDF مثل قبل باز می‌شود؛ وگرنه نسخه‌ی
    فیلترشده‌ی راهنما (فقط بخش‌های مجاز + صفحه‌های عمومی) ساخته و باز
    می‌شود تا کاربر محدود به «کل راهنما» دسترسی نداشته باشد.
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
    key = page_key or "home"
    if allowed_pages is None:
        page = HELP_PAGES.get(key, 2)
    else:
        # Fail-closed: اگر pypdf در دسترس نباشد، به‌جای لو رفتن کل
        # راهنما، خطا می‌دهیم و چیزی باز نمی‌کنیم.
        try:
            path, remap = _filtered_manual_pdf(set(allowed_pages))
        except Exception as e:
            print(f"خطا در ساخت راهنمای فیلترشده: {e}")
            QMessageBox.warning(
                parent, "خطا در راهنما",
                "ساخت نسخه‌ی راهنمای متناسب با دسترسی شما ممکن نشد.")
            return False
        page = remap.get(key, 1)
    url = QUrl.fromLocalFile(os.path.abspath(path))
    url.setFragment(f"page={page}")
    return QDesktopServices.openUrl(url)

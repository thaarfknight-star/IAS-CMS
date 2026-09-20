# -*- coding: utf-8 -*-
"""app_paths.py — تک‌منبع حقیقت برای مسیرهای برنامه.

مشکل (گزارش طه، ۲۰۲۶-۰۹-۲۰): دیتای کاربر (دوربین‌ها، چهره‌ها، گزارش‌ها…)
پراکنده بود — بعضی کنار exe، بعضی نسبت به پوشه‌ی جاری (CWD). وقتی دو نسخه
از برنامه (نصب‌شده با setup + نسخه‌ی portable) اجرا می‌شد، عملاً «دو برنامه‌ی
جدا» با دیتابیس‌های جدا ساخته می‌شد.

راه‌حل: همه‌ی دیتای قابل‌نوشت کاربر در نسخه‌ی نصب‌شده‌ی ویندوز می‌رود به یک
پوشه‌ی یکتا:
    %APPDATA%\\ImenaraSorena\\IAS-CMS
فرقی نمی‌کند برنامه از شورت‌کات اجرا شود یا exe مستقیم؛ دیتا همیشه یکی است.
آپدیت هم فقط فایل‌های برنامه (پوشه‌ی نصب) را جایگزین می‌کند و به دیتا دست
نمی‌زند. آن‌اینستالر هم طبق قانون «حذف کامل» همین درخت را پاک می‌کند
($APPDATA\\ImenaraSorena در installer.nsi).

در حالت توسعه (غیر frozen) رفتار قبلی حفظ می‌شود: دیتا کنار سورس.
"""

import os
import sys


def is_frozen():
    """آیا به‌صورت exe بیلدشده اجرا می‌شویم؟"""
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", False))


def get_install_dir():
    """پوشه‌ی نصب برنامه (کنار فایل اجرایی) یا ریشه‌ی مخزن در حالت توسعه."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_data_dir():
    """پوشه‌ی یکتای دیتای کاربر. همیشه ساخته می‌شود."""
    if is_frozen() and os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "ImenaraSorena", "IAS-CMS")
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def get_registered_install_dir():
    """محل نصب ثبت‌شده در رجیستری (فقط ویندوز)؛ None اگر پیدا نشود."""
    if os.name != "nt":
        return None
    try:
        import winreg
        sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\IAS-CMS"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "InstallLocation")
                    if val:
                        return str(val)
            except OSError:
                continue
    except Exception:
        pass
    return None


# فایل‌های دیتای تکی که ممکن است از نسخه‌های قبلی کنار exe مانده باشند
_LEGACY_FILES = [
    "cameras.json",
    "nvrs.json",
    "face_db.json",
    "reports.db",
    "app_settings.json",
    "alarm_sound_config.json",
    "fire_settings.json",
    "building_fire_config.json",
]

# پوشه‌های دیتا که ممکن است از نسخه‌های قبلی کنار exe مانده باشند
_LEGACY_DIRS = [
    "person_data",
    "faces",
    "unknown_faces",
    "report_images",
    "plate_data",
]


def migrate_legacy_data():
    """مهاجرت یک‌باره‌ی دیتای نسخه‌های قبلی (کنار exe) به پوشه‌ی یکتای دیتا.

    فقط در نسخه‌ی نصب‌شده‌ی ویندوز اجرا می‌شود و فقط وقتی این exe همان نصب
    ثبت‌شده در رجیستری باشد — تا اگر کاربر نسخه‌ی portable دیگری هم دارد،
    دیتای آن نتواند جای دیتای نصب اصلی را بگیرد. فایل‌هایی که در مقصد از قبل
    هستند، بازنویسی نمی‌شوند. فایل‌های مبدأ حذف نمی‌شوند (بکاپ دستی می‌مانند).
    """
    if not (is_frozen() and os.name == "nt"):
        return
    data_dir = get_data_dir()
    exe_dir = get_install_dir()
    try:
        if os.path.normcase(os.path.abspath(data_dir)) == \
                os.path.normcase(os.path.abspath(exe_dir)):
            return
    except Exception:
        return
    reg_dir = get_registered_install_dir()
    if reg_dir:
        try:
            if os.path.normcase(os.path.normpath(reg_dir)) != \
                    os.path.normcase(os.path.normpath(exe_dir)):
                return  # این کپی، نصب رسمی نیست؛ فقط از دیتای مشترک استفاده کن
        except Exception:
            return
    import shutil
    for name in _LEGACY_FILES:
        src = os.path.join(exe_dir, name)
        dst = os.path.join(data_dir, name)
        try:
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
        except Exception:
            pass
    for dname in _LEGACY_DIRS:
        src = os.path.join(exe_dir, dname)
        dst = os.path.join(data_dir, dname)
        try:
            if os.path.isdir(src) and not os.path.exists(dst):
                shutil.copytree(src, dst)
        except Exception:
            pass

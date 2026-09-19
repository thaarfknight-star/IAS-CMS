# -*- coding: utf-8 -*-
"""اعمال «فایل آپدیت» ایمن آرا سورنا از داخل برنامه.

گردش کار:
  ۱) کاربر از هدر برنامه «⬆️ اعمال فایل آپدیت» را می‌زند و فایل
     IAS-CMS-Update-vX.Y.Z.zip را انتخاب می‌کند.
  ۲) فایل اعتبارسنجی می‌شود (ساختار zip، update_info.json، نسخه).
  ۳) محتوا در <install>/pending_update استخراج می‌شود.
  ۴) updater.ps1 (داخل پوشه‌ی نصب) به‌صورت جداگانه اجرا می‌شود و
     برنامه بسته می‌شود؛ اسکریپت منتظر خروج کامل برنامه می‌ماند،
     فایل‌ها را با بکاپ جایگزین می‌کند و برنامه را دوباره اجرا می‌کند.

نکته: این قابلیت فقط در نسخه‌ی نصب‌شده (frozen) کار می‌کند؛ در حالت
توسعه پیام راهنما نمایش داده می‌شود.
"""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def is_frozen():
    return getattr(sys, "frozen", False)


def get_install_dir():
    """پوشه‌ی نصب (کنار فایل اجرایی) یا ریشه‌ی مخزن در حالت توسعه."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_app_version():
    """خواندن نسخه از version.txt کنار برنامه (پیش‌فرض 2.0.0)."""
    try:
        v = (get_install_dir() / "version.txt").read_text(encoding="utf-8").strip()
        return v.split()[0] if v else "2.0.0"
    except Exception:
        return "2.0.0"


def _ver_tuple(v):
    parts = []
    for p in str(v).strip().split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def validate_update_zip(zip_path):
    """اعتبارسنجی فایل آپدیت. خروجی: (ok, info_or_error_message)."""
    zp = Path(zip_path)
    if not zp.is_file():
        return False, "فایل انتخاب‌شده وجود ندارد."
    if not zipfile.is_zipfile(zp):
        return False, "فایل انتخاب‌شده یک «فایل آپدیت» معتبر نیست."
    try:
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            if "update_info.json" not in names:
                return False, "ساختار فایل آپدیت ناقص است (update_info.json پیدا نشد)."
            info = json.loads(z.read("update_info.json").decode("utf-8"))
    except Exception as e:
        return False, f"خواندن فایل آپدیت ممکن نشد: {e}"
    if info.get("app") != "IAS-CMS":
        return False, "این فایل آپدیت متعلق به ایمن آرا سورنا نیست."
    if not info.get("version"):
        return False, "نسخه‌ی فایل آپدیت مشخص نیست."
    if not info.get("files"):
        return False, "فایل آپدیت خالی است."
    cur = _ver_tuple(get_app_version())
    new = _ver_tuple(info["version"])
    if new < cur:
        return False, (f"نسخه‌ی فایل آپدیت ({info['version']}) از نسخه‌ی فعلی "
                       f"({get_app_version()}) قدیمی‌تر است.")
    return True, info


def apply_update_zip(zip_path, parent=None):
    """مرحله‌بندی آپدیت، اجرای updater.ps1 و بستن برنامه.

    خروجی: (ok, message) — اگر ok باشد برنامه باید بسته شود.
    """
    from PyQt6.QtWidgets import QMessageBox, QApplication

    if not is_frozen():
        QMessageBox.information(
            parent, "اعمال آپدیت",
            "اعمال «فایل آپدیت» فقط در نسخه‌ی نصب‌شده کار می‌کند.\n"
            "این حالتِ توسعه (اجرای از روی سورس) است.")
        return False, "dev-mode"

    ok, info = validate_update_zip(zip_path)
    if not ok:
        QMessageBox.warning(parent, "فایل آپدیت نامعتبر", info)
        return False, info

    cur_ver = get_app_version()
    new_ver = info["version"]
    n_files = len(info["files"])
    total_mb = info.get("total_size", 0) / 1e6
    if _ver_tuple(new_ver) == _ver_tuple(cur_ver):
        extra = "\n(نسخه برابر است؛ فایل‌ها بازنویسی می‌شوند.)"
    else:
        extra = ""
    ans = QMessageBox.question(
        parent, "تأیید آپدیت",
        f"آپدیت به نسخه‌ی {new_ver} اعمال شود؟\n\n"
        f"• نسخه‌ی فعلی: {cur_ver}\n"
        f"• تعداد فایل‌ها: {n_files} ({total_mb:.0f} مگابایت)\n"
        f"• از نسخه‌ی قبلی بکاپ گرفته می‌شود.{extra}\n\n"
        "برنامه بسته می‌شود، آپدیت اعمال و برنامه دوباره اجرا می‌شود.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    if ans != QMessageBox.Yes:
        return False, "cancelled"

    install_dir = get_install_dir()
    ps1 = install_dir / "updater.ps1"
    if not ps1.is_file():
        QMessageBox.warning(
            parent, "خطا",
            "فایل updater.ps1 در پوشه‌ی نصب پیدا نشد؛\nآپدیت ممکن نیست.")
        return False, "no-updater"

    pending = install_dir / "pending_update"
    try:
        if pending.exists():
            shutil.rmtree(pending)
        pending.mkdir(parents=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(pending)
    except Exception as e:
        QMessageBox.warning(parent, "خطا", f"استخراج فایل آپدیت ممکن نشد:\n{e}")
        return False, str(e)

    # اجرای جداگانه‌ی updater.ps1 و بستن برنامه
    try:
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", str(ps1),
             "-InstallDir", str(install_dir),
             "-PendingDir", str(pending),
             "-ExeName", "CCTV_CMS"],
            creationflags=creationflags,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        shutil.rmtree(pending, ignore_errors=True)
        QMessageBox.warning(parent, "خطا", f"اجرای موتور آپدیت ممکن نشد:\n{e}")
        return False, str(e)

    QMessageBox.information(
        parent, "آپدیت شروع شد",
        "برنامه بسته می‌شود و آپدیت اعمال می‌گردد.\n"
        "پس از اتمام، برنامه به‌صورت خودکار دوباره اجرا می‌شود.")
    QApplication.instance().quit()
    # اگر به هر دلیلی quit عمل نکرد، خروج سخت
    os._exit(0)
    return True, "started"


def show_apply_update_dialog(parent=None):
    """دیالوگ انتخاب فایل آپدیت (از هدر برنامه صدا زده می‌شود)."""
    from PyQt6.QtWidgets import QFileDialog
    path, _ = QFileDialog.getOpenFileName(
        parent, "انتخاب فایل آپدیت",
        str(Path.home()),
        "فایل آپدیت ایمن آرا سورنا (*.zip)")
    if path:
        apply_update_zip(path, parent=parent)

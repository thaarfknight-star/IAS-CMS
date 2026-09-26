# -*- coding: utf-8 -*-
"""مرحله‌ی دوم آپدیت «ایمن آرا سورنا» — موتور آپدیت خالص پایتون.

چرا بدون PowerShell؟ موتور قبلی (updater.ps1) روی بعضی سیستم‌ها اصلاً بالا
نمی‌آمد (ExecutionPolicy، آنتی‌ویروس، PowerShell قفل‌شده) و هیچ لاگی هم از
علت واقعی نمی‌داد. این موتور همان فایل اجرایی برنامه است که با پرچم
``--apply-update`` اجرا می‌شود؛ هیچ وابستگی خارجی ندارد و هر قدم را در
``update.log`` ثبت می‌کند.

گردش کار:
  updater.py (داخل برنامه):
    ۱) اعتبارسنجی و استخراج زیپ در <install>/pending_update
    ۲) کپی فایل اجرایی به CCTV_CMS_upd.exe (خواندن فایل در حال اجرا آزاد است)
    ۳) اجرای جداگانه‌ی CCTV_CMS_upd.exe با پرچم --apply-update
    ۴) انتظار برای «updater started» در update.log، بعد بستن برنامه
  این ماژول (در فرایند CCTV_CMS_upd.exe):
    ۱) انتظار برای خروج کامل فرایند والد (حداکثر ۱۲۰ ثانیه)
    ۲) بکاپ فایل‌های قدیمی در backup\\v<prev_version>
    ۳) کپی فایل‌های جدید از pending_update/files/...
    ۴) حذف فایل‌های منسوخ‌شده (removed)
    ۵) راستی‌آزمایی sha256
    ۶) ثبت version.txt و manifest.json جدید + پاک‌سازی pending_update
    ۷) اجرای مجدد برنامه و خروج
  برنامه‌ی اصلی در استارتاپ بعدی CCTV_CMS_upd.exe را پاک می‌کند
  (cleanup_updater_copy).

این ماژول عمداً هیچ وابستگی به Qt یا هیچ پکیج خارجی ندارد تا در
مرحله‌ی دوم آپدیت سبک و قابل‌اتکا باشد.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APPLY_FLAG = "--apply-update"
UPDATER_EXE_NAME = "CCTV_CMS_upd.exe"
HANDSHAKE_LINE = "updater started"


def _log(log_file, msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _msgbox(text, title="خطای آپدیت ایمن آرا سورنا"):
    """پیام خطای قابل‌مشاهده (نه سکوت). فقط روی ویندوز."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x10)
    except Exception:
        pass


def _wait_pid_exit(pid, timeout_s=120):
    """True اگر فرایند تا پایان timeout خارج شد (یا از اول وجود نداشت)."""
    try:
        pid = int(pid)
    except Exception:
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not h:
                return True  # چنین فرایندی نیست
            try:
                res = kernel32.WaitForSingleObject(h, int(timeout_s * 1000))
                return res == 0  # WAIT_OBJECT_0
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            pass
    # fallback همه‌جا: نظرسنجی دوره‌ای
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            time.sleep(0.5)  # فرایند هست ولی دسترسی نداریم → هنوز زنده است
            continue
        except Exception:
            return True
        time.sleep(0.5)
    try:
        os.kill(pid, 0)
        return False
    except Exception:
        return True


def _safe_rel(p):
    """نرمال‌سازی مسیر نسبی داخل بسته‌ی آپدیت (جلوگیری از traversal)."""
    rel = str(p or "").replace("\\", "/").lstrip("/")
    if not rel or rel.startswith(".."):
        return ""
    return rel.replace("/", os.sep)


def apply_update(install_dir, pending_dir, parent_pid, exe_name,
                 log_file=None, relaunch=True):
    """اجرای کامل مرحله‌ی دوم آپدیت. خروجی: کد خروج (۰ یعنی موفق)."""
    install_dir = Path(install_dir)
    pending_dir = Path(pending_dir)
    log_file = Path(log_file) if log_file else install_dir / "update.log"

    _log(log_file, "%s (python engine) install=%s pending=%s exe=%s parent=%s"
         % (HANDSHAKE_LINE, install_dir, pending_dir, exe_name, parent_pid))

    # ۱) انتظار برای خروج کامل برنامه (فایل‌ها قفل‌اند تا برنامه باز است)
    if not _wait_pid_exit(parent_pid, 120):
        msg = ("برنامه‌ی ایمن آرا سورنا بعد از ۱۲۰ ثانیه هنوز باز است؛ "
               "آپدیت لغو شد. لطفاً برنامه را دستی ببندید و دوباره تلاش کنید.")
        _log(log_file, "ERROR: " + msg)
        _msgbox(msg + "\n\nجزئیات در فایل update.log (پوشه‌ی نصب) ثبت شد.")
        return 2

    # ۲) خواندن مشخصات آپدیت
    info_path = pending_dir / "update_info.json"
    if not info_path.is_file():
        msg = "فایل update_info.json در پوشه‌ی pending_update پیدا نشد؛ آپدیت لغو شد."
        _log(log_file, "ERROR: " + msg)
        _msgbox(msg)
        return 3
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as e:
        msg = "خواندن update_info.json ممکن نشد: %s" % e
        _log(log_file, "ERROR: " + msg)
        _msgbox(msg)
        return 4
    files = info.get("files") or []
    removed = info.get("removed") or []
    _log(log_file, "update v%s (prev v%s): %d files, %d removed"
         % (info.get("version"), info.get("prev_version"),
            len(files), len(removed)))

    # ۳) بکاپ فایل‌های قدیمی
    backup_root = install_dir / "backup" / ("v" + str(info.get("prev_version", "unknown")))
    backup_count = 0
    for f in files:
        rel = _safe_rel(f.get("path"))
        if not rel:
            continue
        dst = install_dir / rel
        if dst.is_file():
            b = backup_root / rel
            try:
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, b)
                backup_count += 1
            except Exception as e:
                _log(log_file, "WARN backup failed: %s : %s" % (rel, e))
    _log(log_file, "backed up %d files to %s" % (backup_count, backup_root))

    # ۴) کپی فایل‌های جدید
    copy_fail = 0
    for f in files:
        rel = _safe_rel(f.get("path"))
        if not rel:
            continue
        src = pending_dir / "files" / rel
        dst = install_dir / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except Exception as e:
            _log(log_file, "ERROR copy failed: %s : %s" % (rel, e))
            copy_fail += 1

    # ۵) حذف فایل‌های منسوخ‌شده
    for r in removed:
        rel = _safe_rel(r)
        if not rel:
            continue
        t = install_dir / rel
        if t.is_file():
            try:
                t.unlink()
            except Exception as e:
                _log(log_file, "WARN remove failed: %s : %s" % (rel, e))

    # ۶) راستی‌آزمایی sha256
    bad_hash = 0
    for f in files:
        rel = _safe_rel(f.get("path"))
        if not rel:
            continue
        dst = install_dir / rel
        try:
            h = hashlib.sha256(dst.read_bytes()).hexdigest()
            if h != str(f.get("sha256", "")).lower():
                _log(log_file, "HASH MISMATCH: %s" % rel)
                bad_hash += 1
        except Exception:
            _log(log_file, "HASH CHECK FAILED (missing?): %s" % rel)
            bad_hash += 1

    # ۷) ثبت نسخه و مانیفست جدید + پاک‌سازی pending
    try:
        (install_dir / "version.txt").write_text(
            str(info.get("version", "")), encoding="ascii")
        mp = pending_dir / "manifest.json"
        if mp.is_file():
            shutil.copy2(mp, install_dir / "manifest.json")
    except Exception as e:
        _log(log_file, "ERROR writing version/manifest: %s" % e)
    shutil.rmtree(pending_dir, ignore_errors=True)

    if copy_fail > 0 or bad_hash > 0:
        msg = ("آپدیت با خطا تمام شد (copyFail=%d badHash=%d)؛ "
               "برنامه دوباره اجرا نشد تا وضعیت ناقص نماند."
               % (copy_fail, bad_hash))
        _log(log_file, "ERROR: " + msg)
        _msgbox(msg + "\n\nجزئیات در فایل update.log (پوشه‌ی نصب) ثبت شد.")
        return 5

    _log(log_file, "update to v%s OK" % info.get("version"))

    # ۷-ب) نشانگر «آپدیت تازه اعمال شد» برای پنجره‌ی اطلاع‌رسانی تغییرات:
    # در استارت بعدی برنامه خوانده و بعد از نمایش، پاک می‌شود.
    try:
        (install_dir / "update_applied.json").write_text(
            json.dumps({"prev_version": str(info.get("prev_version", "")),
                        "new_version": str(info.get("version", ""))},
                       ensure_ascii=False),
            encoding="utf-8")
    except Exception as e:
        _log(log_file, "WARN could not write update_applied.json: %s" % e)

    # ۸) اجرای مجدد برنامه + راستی‌آزمایی اینکه واقعاً بالا آمد
    if relaunch:
        exe_path = install_dir / exe_name
        try:
            if os.name == "nt":
                creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x8)
                creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                p = subprocess.Popen(
                    [str(exe_path)], cwd=str(install_dir),
                    creationflags=creationflags, close_fds=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                p = subprocess.Popen(
                    [str(exe_path)], cwd=str(install_dir),
                    start_new_session=True, close_fds=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
            if p.poll() is None:
                _log(log_file, "app relaunched (pid %s)" % p.pid)
            else:
                msg = ("فایل‌ها آپدیت شدند ولی اجرای مجدد برنامه ممکن نشد؛ "
                       "لطفاً برنامه را دستی اجرا کنید.")
                _log(log_file, "ERROR: " + msg)
                _msgbox(msg)
        except Exception as e:
            msg = "اجرای مجدد برنامه ممکن نشد: %s" % e
            _log(log_file, "ERROR: " + msg)
            _msgbox(msg)
    return 0


def cleanup_updater_copy():
    """پاک‌سازی فایل موتور آپدیت در استارتاپ برنامه‌ی اصلی.

    فقط در حالت frozen اجرا شود؛ در حالت توسعه هیچ‌کاری نمی‌کند.
    خروجی: True اگر فایلی پاک شد.
    """
    try:
        if not getattr(sys, "frozen", False):
            return False
        p = Path(sys.executable).resolve().parent / UPDATER_EXE_NAME
        if p.is_file():
            p.unlink()
            return True
    except Exception:
        pass
    return False


def main(argv=None):
    """نقطه‌ی ورود مرحله‌ی دوم:
    <exe> --apply-update <install_dir> <pending_dir> <parent_pid> [exe_name]
    """
    argv = list(argv or sys.argv)
    try:
        i = argv.index(APPLY_FLAG)
        rest = argv[i + 1:]
        install_dir, pending_dir = rest[0], rest[1]
        parent_pid = int(rest[2]) if len(rest) > 2 else 0
        exe_name = rest[3] if len(rest) > 3 else "CCTV_CMS.exe"
    except Exception:
        print("usage: --apply-update <install_dir> <pending_dir> "
              "<parent_pid> [exe_name]")
        return 2
    return apply_update(install_dir, pending_dir, parent_pid, exe_name)


if __name__ == "__main__":
    sys.exit(main())

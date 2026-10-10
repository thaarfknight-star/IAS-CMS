# -*- coding: utf-8 -*-
"""موتور آپدیت IAS Viewer — بازنویسی کامل (2.2.0).

معماری جدید — ساده‌تر و قابل‌اتکاتر از نسخه‌ی قبلی:
----------------------------------------------------------------
۱) بدون کپی فایل اجرایی: برنامه خودش را با پرچم --apply-update دوباره
   اجرا می‌کند (فرایند جدا). نیازی به کپی ۱۰۰+ مگابایتی exe نیست؛
   آنتی‌ویروس هم به فایل جدید گیر نمی‌دهد.

۲) جایگزینی فایل در حال اجرا با ترفند rename: در ویندوز نمی‌شود فایل
   اجراییِ در حال اجرا را بازنویسی/حذف کرد، ولی rename مجاز است.
   پس: app.exe → app.exe.old (rename)، سپس کپی نسخه‌ی جدید به app.exe.
   فایل‌های .old در استارتاپ بعدی پاک می‌شوند (cleanup_old_files).

۳) rollback خودکار: اگر هر مرحله‌ای شکست بخورد، فایل‌ها از بکاپ
   برگردانده می‌شوند و برنامه با نسخه‌ی قبلی بالا می‌آید — نه خراب.

۴) لاگ یکپارچه: همه‌چیز در update.log. بدون فایل err جداگانه.

۵) فایل وضعیت update_state.json: نتیجه‌ی آخرین آپدیت (موفق/ناموفق +
   دلیل) تا برنامه در استارتاپ بعدی بتواند به کاربر اطلاع دهد.

گردش کار:
  updater.py (داخل برنامه):
    ۱) اعتبارسنجی و استخراج زیپ در <install>/pending_update
    ۲) اجرای جداگانه‌ی همین exe با پرچم --apply-update
    ۳) انتظار کوتاه برای زنده ماندن فرایند، بعد بستن برنامه
  این ماژول (در فرایند جدا با پرچم --apply-update):
    ۱) انتظار برای خروج کامل فرایند والد (حداکثر ۱۲۰ ثانیه)
    ۲) بکاپ فایل‌های قدیمی در backup\\v<prev_version>
    ۳) جایگزینی فایل‌ها (با ترفند rename برای فایل‌های قفل)
    ۴) حذف فایل‌های منسوخ‌شده
    ۵) راستی‌آزمایی sha256
    ۶) ثبت version.txt (با تأیید خواندن مجدد) + manifest.json
    ۷) در صورت شکست هر مرحله → rollback خودکار از بکاپ
    ۸) ثبت update_state.json + اجرای مجدد برنامه

این ماژول عمداً هیچ وابستگی به Qt یا هیچ پکیج خارجی ندارد.
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
HANDSHAKE_LINE = "updater started"
STATE_FILE = "update_state.json"
OLD_SUFFIX = ".old"


def _log(log_file, msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _msgbox(text, title="خطای آپدیت IAS Viewer"):
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
    # fallback: نظرسنجی دوره‌ای
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            time.sleep(0.5)
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


def _replace_file(src, dst, log_file):
    """جایگزینی امن فایل — با ترفند rename برای فایل‌های قفل‌شده (ویندوز).

    در ویندوز فایل در حال اجرا را نمی‌شود بازنویسی کرد ولی rename مجاز است.
    خروجی: (True, "") یا (False, دلیل).
    """
    dst = Path(dst)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return True, ""
    except PermissionError as e:
        # احتمالاً فایل قفل است (در حال اجرا) — ترفند rename
        if os.name != "nt":
            return False, str(e)
        try:
            old_path = dst.with_name(dst.name + OLD_SUFFIX)
            # اگر .old قبلی مانده، اول پاکش کن
            try:
                if old_path.is_file():
                    old_path.unlink()
            except Exception:
                pass
            os.rename(str(dst), str(old_path))
            shutil.copy2(str(src), str(dst))
            _log(log_file, "replaced locked file via rename: %s" % dst.name)
            return True, ""
        except Exception as e2:
            return False, "rename trick failed: %s" % e2
    except Exception as e:
        return False, str(e)


def _write_state(install_dir, status, detail=""):
    """ثبت نتیجه‌ی آپدیت برای نمایش در استارتاپ بعدی برنامه."""
    try:
        data = {
            "status": status,  # "ok" | "failed" | "rolled_back"
            "detail": str(detail),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (Path(install_dir) / STATE_FILE).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def cleanup_old_files(install_dir=None):
    """پاک‌سازی فایل‌های .old باقی‌مانده از آپدیت قبلی.

    در استارتاپ برنامه‌ی اصلی صدا زده می‌شود.
    خروجی: تعداد فایل‌های پاک‌شده.
    """
    count = 0
    try:
        base = Path(install_dir) if install_dir else None
        if base is None:
            try:
                from app_paths import get_install_dir as _gid
                base = Path(_gid())
            except Exception:
                if getattr(sys, "frozen", False):
                    base = Path(sys.executable).resolve().parent
                else:
                    return 0
        for p in base.glob("*.old"):
            try:
                if p.is_file():
                    p.unlink()
                    count += 1
            except Exception:
                pass
        # .oldهای داخل زیرپوشه‌ها (مثلاً DLLها)
        for p in base.rglob("*.old"):
            try:
                if p.is_file():
                    p.unlink()
                    count += 1
            except Exception:
                pass
    except Exception:
        pass
    return count


def apply_update(install_dir, pending_dir, parent_pid, exe_name,
                 log_file=None, relaunch=True):
    """اجرای کامل مرحله‌ی دوم آپدیت. خروجی: کد خروج (۰ یعنی موفق)."""
    install_dir = Path(install_dir)
    pending_dir = Path(pending_dir)
    log_file = Path(log_file) if log_file else install_dir / "update.log"

    _log(log_file, "%s (v2 engine) install=%s pending=%s exe=%s parent=%s"
         % (HANDSHAKE_LINE, install_dir, pending_dir, exe_name, parent_pid))

    # ۱) انتظار برای خروج کامل برنامه
    if not _wait_pid_exit(parent_pid, 120):
        msg = ("برنامه‌ی IAS Viewer بعد از ۱۲۰ ثانیه هنوز باز است؛ "
               "آپدیت لغو شد. لطفاً برنامه را دستی ببندید و دوباره تلاش کنید.")
        _log(log_file, "ERROR: " + msg)
        _write_state(install_dir, "failed", msg)
        _msgbox(msg + "\n\nجزئیات در فایل update.log (پوشه‌ی نصب) ثبت شد.")
        return 2

    # ۲) خواندن مشخصات آپدیت
    info_path = pending_dir / "update_info.json"
    if not info_path.is_file():
        msg = "فایل update_info.json در پوشه‌ی pending_update پیدا نشد؛ آپدیت لغو شد."
        _log(log_file, "ERROR: " + msg)
        _write_state(install_dir, "failed", msg)
        _msgbox(msg)
        return 3
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as e:
        msg = "خواندن update_info.json ممکن نشد: %s" % e
        _log(log_file, "ERROR: " + msg)
        _write_state(install_dir, "failed", msg)
        _msgbox(msg)
        return 4

    new_version = str(info.get("version", "")).strip()
    prev_version = str(info.get("prev_version", "unknown")).strip()
    files = info.get("files") or []
    removed = info.get("removed") or []
    if not new_version or not files:
        msg = "فایل آپدیت ناقص است (نسخه یا فهرست فایل‌ها خالی است)."
        _log(log_file, "ERROR: " + msg)
        _write_state(install_dir, "failed", msg)
        _msgbox(msg)
        return 4
    _log(log_file, "update v%s (prev v%s): %d files, %d removed"
         % (new_version, prev_version, len(files), len(removed)))

    # ۳) بکاپ فایل‌های قدیمی
    backup_root = install_dir / "backup" / ("v" + prev_version)
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
                shutil.copy2(str(dst), str(b))
                backup_count += 1
            except Exception as e:
                _log(log_file, "WARN backup failed: %s : %s" % (rel, e))
    _log(log_file, "backed up %d files to %s" % (backup_count, backup_root))

    def _rollback(reason):
        """برگرداندن فایل‌ها از بکاپ. خروجی: پیام خطای نهایی."""
        _log(log_file, "ROLLBACK started: %s" % reason)
        restored = 0
        for f in files:
            rel = _safe_rel(f.get("path"))
            if not rel:
                continue
            # فایل‌های _internal ران‌تایم پایتونِ خودِ آپدیتر هستند؛
            # در ویندوز memory-mapped و قفل‌اند و نمی‌شود برگرداندشان.
            # چون آپدیتر از نسخه‌ی جدید آن‌ها در حال اجراست، نادیده‌شان می‌گیریم.
            if rel.startswith("_internal" + os.sep) or rel.startswith("_internal/"):
                _log(log_file, "ROLLBACK skip (runtime locked): %s" % rel)
                continue
            b = backup_root / rel
            dst = install_dir / rel
            if b.is_file():
                try:
                    # اگر dst قفل است، اول rename
                    if dst.is_file():
                        try:
                            dst.unlink()
                        except PermissionError:
                            if os.name == "nt":
                                os.rename(str(dst),
                                          str(dst.with_name(dst.name + OLD_SUFFIX)))
                            else:
                                raise
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(b), str(dst))
                    restored += 1
                except Exception as e:
                    _log(log_file, "WARN rollback failed for %s: %s" % (rel, e))
        # version.txt را به نسخه‌ی قبلی برگردان
        try:
            (install_dir / "version.txt").write_text(prev_version, encoding="utf-8")
        except Exception:
            pass
        msg = ("آپدیت ناموفق بود و به نسخه‌ی قبلی برگردانده شد.\n"
               "دلیل: %s\n(%d فایل بازیابی شد)" % (reason, restored))
        _log(log_file, "ROLLBACK done: %d files restored" % restored)
        _write_state(install_dir, "rolled_back", reason)
        return msg

    # ۴) جایگزینی فایل‌ها
    copy_fail = 0
    for f in files:
        rel = _safe_rel(f.get("path"))
        if not rel:
            continue
        src = pending_dir / "files" / rel
        dst = install_dir / rel
        if not src.is_file():
            _log(log_file, "ERROR source missing in package: %s" % rel)
            copy_fail += 1
            continue
        ok, err = _replace_file(src, dst, log_file)
        if not ok:
            _log(log_file, "ERROR copy failed: %s : %s" % (rel, err))
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
            except PermissionError:
                # فایل قفل است — rename به .old تا در استارتاپ بعدی پاک شود
                try:
                    if os.name == "nt":
                        os.rename(str(t), str(t.with_name(t.name + OLD_SUFFIX)))
                    else:
                        raise
                except Exception as e:
                    _log(log_file, "WARN remove failed: %s : %s" % (rel, e))
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

    # اگر کپی یا هش مشکل داشت → rollback
    if copy_fail > 0 or bad_hash > 0:
        reason = "خطا در کپی فایل‌ها (copyFail=%d) یا تطابق هش (badHash=%d)" % (
            copy_fail, bad_hash)
        msg = _rollback(reason)
        _msgbox(msg + "\n\nجزئیات در فایل update.log (پوشه‌ی نصب) ثبت شد.")
        shutil.rmtree(pending_dir, ignore_errors=True)
        return 5

    # ۷) ثبت نسخه‌ی جدید (حیاتی — با تأیید خواندن مجدد)
    try:
        (install_dir / "version.txt").write_text(new_version, encoding="utf-8")
        written = (install_dir / "version.txt").read_text(
            encoding="utf-8").strip().split()[0]
        if written != new_version:
            raise RuntimeError(
                "خواندن مجدد '%s' برگرداند (انتظار: '%s')" % (written, new_version))
        _log(log_file, "version.txt -> %s OK" % new_version)
        mp = pending_dir / "manifest.json"
        if mp.is_file():
            shutil.copy2(str(mp), str(install_dir / "manifest.json"))
    except Exception as e:
        msg = _rollback("ثبت نسخه‌ی جدید ممکن نشد: %s" % e)
        _msgbox(msg + "\n\nجزئیات در فایل update.log (پوشه‌ی نصب) ثبت شد.")
        shutil.rmtree(pending_dir, ignore_errors=True)
        return 6

    shutil.rmtree(pending_dir, ignore_errors=True)
    _log(log_file, "update to v%s OK" % new_version)

    # نشانگر «آپدیت تازه اعمال شد» برای پنجره‌ی اطلاع‌رسانی تغییرات
    try:
        (install_dir / "update_applied.json").write_text(
            json.dumps({"prev_version": prev_version,
                        "new_version": new_version},
                       ensure_ascii=False),
            encoding="utf-8")
    except Exception as e:
        _log(log_file, "WARN could not write update_applied.json: %s" % e)

    _write_state(install_dir, "ok", "به‌روزرسانی به نسخه‌ی %s انجام شد." % new_version)

    # ۸) اجرای مجدد برنامه
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


def read_update_state(install_dir=None):
    """خواندن نتیجه‌ی آخرین آپدیت (برای نمایش در استارتاپ برنامه).

    خروجی: dict با کلیدهای status/detail/time یا None.
    بعد از خواندن، فایل وضعیت پاک می‌شود (یک‌بار مصرف).
    """
    try:
        base = Path(install_dir) if install_dir else None
        if base is None:
            try:
                from app_paths import get_install_dir as _gid
                base = Path(_gid())
            except Exception:
                if getattr(sys, "frozen", False):
                    base = Path(sys.executable).resolve().parent
                else:
                    return None
        sf = base / STATE_FILE
        if not sf.is_file():
            return None
        data = json.loads(sf.read_text(encoding="utf-8"))
        try:
            sf.unlink()
        except Exception:
            pass
        return data
    except Exception:
        return None


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

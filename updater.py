# -*- coding: utf-8 -*-
"""اعمال «فایل آپدیت» IAS Viewer از داخل برنامه.

گردش کار:
  ۱) کاربر از هدر برنامه «⬆️ اعمال آپدیت» را می‌زند؛ دیالوگ مدرن و
     هماهنگ با تم برنامه باز می‌شود و فایل IAS-CMS-Update-vX.Y.Z.zip
     را انتخاب می‌کند.
  ۲) فایل اعتبارسنجی می‌شود (ساختار zip، update_info.json، نسخه،
     تطابق با مانیفست).
  ۳) محتوا به‌صورت امن (بدون path traversal) در
     <install>/pending_update استخراج می‌شود.
  ۴) یک کپی از همین فایل اجرایی (CCTV_CMS_upd.exe) با پرچم
     --apply-update به‌صورت جداگانه اجرا می‌شود (موتور خالص پایتون در
     update_apply.py — بدون PowerShell، بدون وابستگی خارجی)؛ برنامه فقط
     وقتی بسته می‌شود که موتور با نوشتن خط شروع در update.log تأیید کند
     بالا آمده است (handshake) — وگرنه خطا نمایش داده می‌شود و برنامه
     باز می‌ماند. موتور منتظر خروج کامل برنامه می‌ماند، فایل‌ها را با
     بکاپ جایگزین می‌کند و برنامه را دوباره اجرا می‌کند؛ خطاهای مهلک با
     پنجره‌ی پیام قابل‌مشاهده اعلام می‌شوند (نه سکوت).

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


# ------------------------------------------------------------- ابزارها ---
# (تک‌منبع حقیقت در app_paths.py؛ اینجا فقط re-export برای سازگاری)
try:
    from app_paths import is_frozen, get_install_dir as _get_install_dir_str
except Exception:  # pragma: no cover
    def is_frozen():
        return getattr(sys, "frozen", False)

    def _get_install_dir_str():
        if is_frozen():
            return str(Path(sys.executable).resolve().parent)
        return str(Path(__file__).resolve().parent)


def get_install_dir():
    """پوشه‌ی نصب (کنار فایل اجرایی) یا ریشه‌ی مخزن در حالت توسعه."""
    return Path(_get_install_dir_str())


def get_app_version():
    """خواندن نسخه از version.txt کنار برنامه (پیش‌فرض 2.0.0)."""
    try:
        v = (get_install_dir() / "version.txt").read_text(encoding="utf-8").strip()
        return v.split()[0] if v else "2.0.0"
    except Exception:
        return "2.0.0"


def _ver_tuple(v):
    # پسوند پیش‌انتشار (مثل -beta) در مقایسه‌ی عددی نادیده گرفته می‌شود تا
    # «2.0.2-beta» درست با «2.0.2» مقایسه شود.
    # (2.0.111-beta) فرمت‌های مختلف را هم پشتیبانی می‌کند: «2.0.109-beta»،
    # «beta-2.0.109»، «v2.0.109» — با regex اعداد نسخه استخراج می‌شوند.
    import re as _re
    s = str(v).strip()
    # دنباله‌ی اعداد نقطه‌دار (مثل 2.0.109) را پیدا می‌کنیم
    m = _re.search(r"(\d+(?:\.\d+)+)", s)
    core = m.group(1) if m else s.split("-")[0].split("+")[0]
    parts = []
    for p in core.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


# ----------------------------------------------------------------------------
# اطلاع‌رسانی «تغییرات آپدیت»: موتور آپدیت (update_apply.py) بعد از اعمال
# موفق، فایل update_applied.json را کنار برنامه می‌نویسد؛ در استارت بعدی،
# پنجره‌ی تغییرات از روی CHANGELOG_FA.md نمایش داده می‌شود.
# ----------------------------------------------------------------------------

def get_update_notice():
    """خواندن نشانگر آپدیت تازه‌اعمال‌شده (dict با prev_version/new_version)
    یا None اگر آپدیتی در کار نبوده."""
    try:
        import json as _json
        p = get_install_dir() / "update_applied.json"
        if p.is_file():
            data = _json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def clear_update_notice():
    """حذف نشانگر آپدیت (بعد از نمایش پنجره‌ی تغییرات)."""
    try:
        p = get_install_dir() / "update_applied.json"
        if p.is_file():
            p.unlink()
    except Exception:
        pass


def changelog_between(prev_version, new_version, max_chars=6000):
    """ورودی‌های CHANGELOG_FA.md از بعدِ prev_version تا new_version.

    خروجی: لیستی از (version, date, body) از جدید به قدیم. اگر فایل چنج‌لاگ
    کنار برنامه نباشد، لیست خالی برمی‌گردد.
    """
    try:
        text = (get_install_dir() / "CHANGELOG_FA.md").read_text(
            encoding="utf-8")
    except Exception:
        return []
    import re as _re
    entries = []
    cur_ver, cur_date, buf = None, None, []
    for line in text.splitlines():
        m = _re.match(r"^##\s+(\S+)(?:\s+\(([^)]*)\))?\s*$", line)
        if m:
            if cur_ver:
                entries.append((cur_ver, cur_date, "\n".join(buf).strip()))
            cur_ver, cur_date, buf = m.group(1), m.group(2) or "", []
        elif cur_ver is not None:
            buf.append(line)
    if cur_ver:
        entries.append((cur_ver, cur_date, "\n".join(buf).strip()))
    prev_t = _ver_tuple(prev_version or "0")
    new_t = _ver_tuple(new_version or "0")
    # (1.0.0) گذار بتا → پایدار: اگر prev بتا و new پایدار باشد، همه‌ی ورودی‌های
    # بتا را نشان می‌دهیم (چون new_t از نظر عددی کوچک‌تر است).
    _is_beta_to_stable = ("beta" in str(prev_version or "").lower()
                          and "beta" not in str(new_version or "").lower())
    out, total = [], 0
    for ver, date, body in entries:
        vt = _ver_tuple(ver)
        if _is_beta_to_stable:
            # همه‌ی ورودی‌ها را (از جدید به قدیم) نشان می‌دهیم تا به اولین
            # نسخه‌ی پایدار قبلی برسیم
            if "beta" not in ver.lower() and vt <= new_t:
                break
            out.append((ver, date, body))
        else:
            if vt <= prev_t:
                break  # ورودی‌ها از جدید به قدیم‌اند
            if vt > new_t:
                continue
            out.append((ver, date, body))
        total += len(body)
        if total > max_chars:
            break
    return out


def _theme():
    """پالت تم برنامه؛ اگر theme.py در دسترس نبود، مقادیر پیش‌فرض."""
    try:
        import theme as _t
        return {
            "BG_DEEP": _t.BG_DEEP, "BG_PANEL": _t.BG_PANEL,
            "BG_INPUT": _t.BG_INPUT, "BORDER": _t.BORDER,
            "TEXT": _t.TEXT, "TEXT_MUTED": _t.TEXT_MUTED,
            "ACCENT": _t.ACCENT, "ACCENT_HOVER": _t.ACCENT_HOVER,
            "DANGER": _t.DANGER, "LOGO_SHIELD": _t.LOGO_SHIELD,
            "APP_NAME_FA": _t.APP_NAME_FA,
        }
    except Exception:
        return {
            "BG_DEEP": "#1b2227", "BG_PANEL": "#242e34",
            "BG_INPUT": "#20282e", "BORDER": "#3a4b52",
            "TEXT": "#e9eef1", "TEXT_MUTED": "#9b978c",
            "ACCENT": "#0f7cc1", "ACCENT_HOVER": "#2a9bd8",
            "DANGER": "#e74c3c", "LOGO_SHIELD": "",
            "APP_NAME_FA": "IAS Viewer",
        }


# ------------------------------------------------------- اعتبارسنجی ---

def validate_update_zip(zip_path):
    """اعتبارسنجی فایل آپدیت. خروجی: (ok, info_or_error_message)."""
    zp = Path(zip_path)
    if not zp.is_file():
        return False, "فایل انتخاب‌شده وجود ندارد."
    if not zipfile.is_zipfile(zp):
        return False, "فایل انتخاب‌شده یک «فایل آپدیت» معتبر نیست."
    try:
        with zipfile.ZipFile(zp) as z:
            names = set(z.namelist())
            if "update_info.json" not in names:
                return False, "ساختار فایل آپدیت ناقص است (update_info.json پیدا نشد)."
            info = json.loads(z.read("update_info.json").decode("utf-8"))
            # تطابق فایل‌های مانیفست با محتوای واقعی zip
            missing = [f["path"] for f in info.get("files", [])
                       if f"files/{f['path']}" not in names]
            if missing:
                return False, (f"فایل آپدیت ناقص است؛ {len(missing)} فایل "
                               f"در بسته پیدا نشد (مثلاً {missing[0]}).")
    except Exception as e:
        return False, f"خواندن فایل آپدیت ممکن نشد: {e}"
    if info.get("app") != "IAS-CMS":
        return False, "این فایل آپدیت متعلق به IAS Viewer نیست."
    if not info.get("version"):
        return False, "نسخه‌ی فایل آپدیت مشخص نیست."
    if not info.get("files"):
        return False, "فایل آپدیت خالی است."
    cur = _ver_tuple(get_app_version())
    new = _ver_tuple(info["version"])
    # (1.0.0) هندل کردن گذار بتا → پایدار: اگر نسخه‌ی فعلی بتا باشد و نسخه‌ی
    # جدید پایدار (بدون پسوند بتا)، آن را ارتقا حساب می‌کنیم حتی اگر از نظر
    # عددی کوچک‌تر باشد (مثل 2.0.121-beta → 1.0.0).
    _cur_is_beta = "beta" in str(get_app_version()).lower()
    _new_is_beta = "beta" in str(info["version"]).lower()
    if not (new < cur):
        pass  # ارتقای عادی
    elif _cur_is_beta and not _new_is_beta:
        pass  # گذار بتا → پایدار: قبول می‌کنیم
    else:
        return False, (f"نسخه‌ی فایل آپدیت ({info['version']}) از نسخه‌ی فعلی "
                       f"({get_app_version()}) قدیمی‌تر است.")
    return True, info


def safe_extract_zip(zip_path, dest_dir):
    """استخراج امن zip: جلوگیری از path traversal (../ و مسیر مطلق)."""
    dest = Path(dest_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            name = m.filename.replace("\\", "/").lstrip("/")
            if not name or name.startswith(".."):
                raise ValueError(f"مسیر ناامن در فایل آپدیت: {m.filename}")
            target = (dest / name).resolve()
            if target != dest and not str(target).startswith(str(dest) + os.sep):
                raise ValueError(f"مسیر ناامن در فایل آپدیت: {m.filename}")
            if m.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)


# ------------------------------------------------- دیالوگ مدرن آپدیت ---

def wait_updater_handshake(install_dir, proc, spawn_ts, pump_cb=None):
    """انتظار حداکثر ~۱۵ ثانیه تا موتور آپدیت خط شروع را در update.log
    بنویسد. خروجی: (ok, hint). نسخه‌ی ماژولارِ متد قبلی دیالوگ (بدون
    وابستگی مستقیم به PyQt؛ pump_cb برای زنده نگه داشتن UI)."""
    import time as _time
    logf = install_dir / "update.log"
    err_log = install_dir / "update_err.log"
    for _ in range(150):
        _time.sleep(0.1)
        if pump_cb:
            try:
                pump_cb()
            except Exception:
                pass
        if proc.poll() is not None:
            hint = ("فرایند موتور آپدیت بلافاصله بسته شد "
                    "(کد خروج: %s)." % (proc.poll(),))
            try:
                if err_log.is_file():
                    tail = err_log.read_text(
                        encoding="utf-8",
                        errors="ignore").strip().splitlines()[-12:]
                    if tail:
                        hint += "\n\nجزئیات خطا:\n" + "\n".join(tail)
            except Exception:
                pass
            return (False, hint)
        try:
            if logf.is_file() and logf.stat().st_mtime >= spawn_ts - 1:
                tail = logf.read_text(
                    encoding="utf-8", errors="ignore").splitlines()[-30:]
                if any("updater started" in ln for ln in tail):
                    return (True, "")
        except Exception:
            pass
    return (False, "موتور آپدیت در ۱۵ ثانیه شروع به کار نکرد.")


def stage_and_launch_update(zip_path, status_cb=None):
    """هسته‌ی مشترک اعمال «فایل آپدیت» (دیالوگ دستی + آپدیت خودکار) — نسخه‌ی
    بازنویسی‌شده (2.2.0):
    استخراج امن به pending_update → اجرای همین exe با پرچم --apply-update
    (بدون کپی فایل اجرایی) → انتظار برای شروع موتور.
    ورودی باید قبلاً با validate_update_zip اعتبارسنجی شده باشد.
    خروجی: (True, "") یا (False, پیام خطای فارسی).
    در صورت موفقیت، فراخواننده باید برنامه را ببندد (quit + os._exit)."""

    def _st(t):
        if status_cb:
            try:
                status_cb(t)
            except Exception:
                pass

    install_dir = get_install_dir()
    pending = install_dir / "pending_update"
    try:
        if pending.exists():
            shutil.rmtree(pending)
        safe_extract_zip(zip_path, pending)
    except Exception as e:
        return False, f"استخراج فایل آپدیت ممکن نشد:\n{e}"

    # موتور آپدیت = همین فایل اجرایی با پرچم --apply-update (بدون کپی).
    # جایگزینی exe در حال اجرا با ترفند rename داخل update_apply انجام می‌شود.
    try:
        import update_apply as _ua
        src_exe = str(Path(sys.executable).resolve())
        if not Path(src_exe).is_file():
            raise RuntimeError("فایل اجرایی برنامه پیدا نشد.")
        exe_name = Path(src_exe).name
        # فایل‌های .old باقی‌مانده از آپدیت قبلی را پاک کن
        try:
            _ua.cleanup_old_files(str(install_dir))
        except Exception:
            pass
    except Exception as e:
        shutil.rmtree(pending, ignore_errors=True)
        return False, f"آماده‌سازی موتور آپدیت ممکن نشد:\n{e}"

    try:
        import time as _time
        cmd = [src_exe, _ua.APPLY_FLAG, str(install_dir),
               str(pending), str(os.getpid()), exe_name]
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(
                cmd,
                creationflags=creationflags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                close_fds=True,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        shutil.rmtree(pending, ignore_errors=True)
        return False, f"اجرای موتور آپدیت ممکن نشد:\n{e}"

    _st("در حال راه‌اندازی موتور آپدیت…")
    # انتظار ساده: فرایند باید حداقل ۳ ثانیه زنده بماند (یعنی کرش نکرده).
    # موتور اولین کاری که می‌کند نوشتن "updater started" در update.log است.
    try:
        from PyQt6.QtWidgets import QApplication as _QA
        pump = _QA.processEvents
    except Exception:
        pump = None
    import time as _time2
    spawn_ts = _time2.time()
    for _ in range(30):
        _time2.sleep(0.1)
        if pump:
            try:
                pump()
            except Exception:
                pass
        if proc.poll() is not None:
            # فرایند بلافاصله بسته شد — خطا را از لاگ بخوان
            hint = "فرایند موتور آپدیت بلافاصله بسته شد (کد خروج: %s)." % proc.poll()
            try:
                logf = install_dir / "update.log"
                if logf.is_file():
                    tail = logf.read_text(
                        encoding="utf-8", errors="ignore").strip().splitlines()[-8:]
                    if tail:
                        hint += "\n\nجزئیات:\n" + "\n".join(tail)
            except Exception:
                pass
            shutil.rmtree(pending, ignore_errors=True)
            return False, ("موتور آپدیت راه‌اندازی نشد؛ برنامه بسته نشد.\n"
                           f"{hint}\n"
                           "اگر مشکل ادامه داشت، فایل update.log در پوشه‌ی نصب را بفرستید.")
    # ۳ ثانیه گذشت و فرایند زنده است → موتور در حال کار است
    _log_spawned(install_dir, spawn_ts, proc.pid)
    return True, ""


def _log_spawned(install_dir, spawn_ts, pid):
    """ثبت شروع موتور در update.log (برای عیب‌یابی)."""
    try:
        import time as _time
        with open(install_dir / "update.log", "a",
                  encoding="utf-8", errors="replace") as f:
            f.write("[%s] launcher: updater spawned (pid %s)\n"
                    % (_time.strftime("%Y-%m-%d %H:%M:%S"), pid))
    except Exception:
        pass
    return True, ""


# ------------------------------------------------- دیالوگ مدرن آپدیت ---

class UpdateDialog(__import__("PyQt6.QtWidgets", fromlist=["QDialog"]).QDialog):
    """دیالوگ «فایل آپدیت» — مدرن، راست‌چین و هماهنگ با تم برنامه."""

    def __init__(self, parent=None):
        from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                                     QPushButton, QFileDialog, QFrame)
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPixmap
        super().__init__(parent)
        self._QFileDialog = QFileDialog
        self._Qt = Qt
        th = _theme()
        self._th = th
        self.zip_path = None
        self.info = None

        self.setWindowTitle("فایل آپدیت " + th["APP_NAME_FA"])
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(540)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {th['BG_DEEP']}; }}
            QLabel {{ color: {th['TEXT']}; background: transparent; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # --- سربرگ: لوگو + عنوان ---
        head = QHBoxLayout()
        head.setSpacing(12)
        logo_lbl = QLabel()
        if th["LOGO_SHIELD"] and os.path.isfile(th["LOGO_SHIELD"]):
            px = QPixmap(th["LOGO_SHIELD"]).scaled(
                56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            logo_lbl.setPixmap(px)
        head.addWidget(logo_lbl)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        t1 = QLabel("فایل آپدیت")
        t1.setStyleSheet("font-size: 20px; font-weight: bold;")
        t2 = QLabel(f"{th['APP_NAME_FA']} — نسخه‌ی فعلی {get_app_version()}")
        t2.setStyleSheet(f"font-size: 12px; color: {th['TEXT_MUTED']};")
        title_box.addWidget(t1)
        title_box.addWidget(t2)
        head.addLayout(title_box)
        head.addStretch(1)
        root.addLayout(head)

        # --- کارت انتخاب فایل ---
        self.file_card = QFrame()
        self.file_card.setStyleSheet(f"""
            QFrame {{ background-color: {th['BG_PANEL']};
                     border: 1px dashed {th['BORDER']};
                     border-radius: 12px; }}
        """)
        fc_l = QVBoxLayout(self.file_card)
        fc_l.setContentsMargins(18, 16, 18, 16)
        fc_l.setSpacing(10)
        row = QHBoxLayout()
        self.pick_btn = QPushButton("📂 انتخاب فایل آپدیت…")
        self.pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {th['BG_INPUT']};
                          border: 1px solid {th['BORDER']};
                          border-radius: 8px; padding: 10px 16px;
                          font-size: 13px; font-weight: bold; }}
            QPushButton:hover {{ border-color: {th['ACCENT']}; }}
        """)
        self.pick_btn.clicked.connect(self._pick_file)
        row.addWidget(self.pick_btn)
        self.file_lbl = QLabel("فایلی انتخاب نشده است")
        self.file_lbl.setStyleSheet(f"font-size: 12px; color: {th['TEXT_MUTED']};")
        self.file_lbl.setWordWrap(True)
        row.addWidget(self.file_lbl, 1)
        fc_l.addLayout(row)
        root.addWidget(self.file_card)

        # --- کارت اطلاعات آپدیت (پس از اعتبارسنجی) ---
        self.info_card = QFrame()
        self.info_card.setStyleSheet(f"""
            QFrame {{ background-color: {th['BG_PANEL']};
                     border: 1px solid {th['BORDER']};
                     border-radius: 12px; }}
        """)
        ic_l = QVBoxLayout(self.info_card)
        ic_l.setContentsMargins(18, 14, 18, 14)
        ic_l.setSpacing(8)
        self.ver_lbl = QLabel("")
        self.ver_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.ver_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic_l.addWidget(self.ver_lbl)
        self.detail_lbl = QLabel("")
        self.detail_lbl.setStyleSheet(f"font-size: 12px; color: {th['TEXT_MUTED']};")
        self.detail_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_lbl.setWordWrap(True)
        ic_l.addWidget(self.detail_lbl)
        self.note_lbl = QLabel("از نسخه‌ی قبلی بکاپ گرفته می‌شود؛ "
                               "برنامه بسته و پس از آپدیت دوباره اجرا می‌شود.")
        self.note_lbl.setStyleSheet(f"font-size: 11px; color: {th['TEXT_MUTED']};")
        self.note_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note_lbl.setWordWrap(True)
        ic_l.addWidget(self.note_lbl)
        root.addWidget(self.info_card)
        self.info_card.setVisible(False)

        # --- کارت خطا ---
        self.err_card = QFrame()
        self.err_card.setStyleSheet(f"""
            QFrame {{ background-color: #3a2226;
                     border: 1px solid {th['DANGER']};
                     border-radius: 12px; }}
        """)
        ec_l = QVBoxLayout(self.err_card)
        ec_l.setContentsMargins(18, 12, 18, 12)
        self.err_lbl = QLabel("")
        self.err_lbl.setStyleSheet(f"font-size: 12px; color: #f5b7b1;")
        self.err_lbl.setWordWrap(True)
        ec_l.addWidget(self.err_lbl)
        root.addWidget(self.err_card)
        self.err_card.setVisible(False)

        root.addStretch(1)

        # --- دکمه‌ها ---
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.cancel_btn = QPushButton("انصراف")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {th['BG_INPUT']};
                          border: 1px solid {th['BORDER']};
                          border-radius: 8px; padding: 10px 26px;
                          font-size: 13px; }}
            QPushButton:hover {{ border-color: {th['TEXT_MUTED']}; }}
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)
        self.apply_btn = QPushButton("⬆️ اعمال آپدیت")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setEnabled(False)
        self.apply_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {th['ACCENT']}; color: white;
                          border: none; border-radius: 8px;
                          padding: 10px 30px; font-size: 14px;
                          font-weight: bold; }}
            QPushButton:hover:!disabled {{ background-color: {th['ACCENT_HOVER']}; }}
            QPushButton:disabled {{ background-color: {th['BG_INPUT']};
                                    color: {th['TEXT_MUTED']}; }}
        """)
        self.apply_btn.clicked.connect(self._apply)
        btns.addWidget(self.apply_btn)
        root.addLayout(btns)

    # ------------------------------------------------- رفتار ---

    def _pick_file(self):
        path, _ = self._QFileDialog.getOpenFileName(
            self, "انتخاب فایل آپدیت", str(Path.home()),
            "فایل آپدیت IAS Viewer (*.zip)")
        if not path:
            return
        self.file_lbl.setText(Path(path).name)
        self.file_lbl.setStyleSheet(f"font-size: 12px; color: {self._th['TEXT']};")
        ok, info = validate_update_zip(path)
        if not ok:
            self.zip_path, self.info = None, None
            self.err_lbl.setText("⚠️ " + info)
            self.err_card.setVisible(True)
            self.info_card.setVisible(False)
            self.apply_btn.setEnabled(False)
            return
        self.zip_path, self.info = path, info
        self.err_card.setVisible(False)
        cur, new = get_app_version(), info["version"]
        n_files = len(info["files"])
        total_mb = info.get("total_size", 0) / 1e6
        if _ver_tuple(new) == _ver_tuple(cur):
            ver_text = f"{cur} ← {new} (بازنویسی همین نسخه)"
        else:
            ver_text = f"{cur} ← {new}"
        self.ver_lbl.setText(ver_text)
        self.detail_lbl.setText(
            f"{n_files} فایل جایگزین می‌شود ({total_mb:.0f} مگابایت) — "
            "فقط فایل‌های تغییریافته، نه کل برنامه")
        self.info_card.setVisible(True)
        self.apply_btn.setEnabled(True)

    def _apply(self):
        """اعمال «فایل آپدیت» انتخاب‌شده — روی هسته‌ی مشترک
        stage_and_launch_update سوار است (همان منطق قبلی، بدون تغییر رفتار)."""
        from PyQt6.QtWidgets import QApplication
        if not self.zip_path or not self.info:
            return
        if not is_frozen():
            self.err_lbl.setText("ℹ️ اعمال «فایل آپدیت» فقط در نسخه‌ی نصب‌شده "
                                 "کار می‌کند (این حالتِ توسعه است).")
            self.err_card.setVisible(True)
            return
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("در حال آماده‌سازی…")
        QApplication.processEvents()

        def _fail(msg):
            self.err_lbl.setText("⚠️ " + msg)
            self.err_card.setVisible(True)
            self.apply_btn.setEnabled(True)
            self.apply_btn.setText("⬆️ اعمال آپدیت")

        ok, msg = stage_and_launch_update(
            self.zip_path,
            status_cb=lambda t: (self.apply_btn.setText(t),
                                 QApplication.processEvents()))
        if not ok:
            _fail(msg)
            return

        self.accept()
        QApplication.instance().quit()
        os._exit(0)


    def _wait_updater_handshake(self, proc, install_dir, spawn_ts):
        """سازگاری: حالا فقط پوشش نازکی روی wait_updater_handshake ماژولار است."""
        from PyQt6.QtWidgets import QApplication
        return wait_updater_handshake(install_dir, proc, spawn_ts,
                                      pump_cb=QApplication.processEvents)


def show_apply_update_dialog(parent=None):
    """دیالوگ مدرن انتخاب و اعمال «فایل آپدیت» (از هدر برنامه صدا زده می‌شود)."""
    dlg = UpdateDialog(parent=parent)
    dlg.exec()

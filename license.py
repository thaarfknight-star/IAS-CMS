# -*- coding: utf-8 -*-
"""license.py — مدیریت فایل لایسنس IAS-CMS.

مدل لایسنس:
- فایل `license.lic` یک JSON است: {"format", "payload", "signature"}
- payload شامل: مشتری، تاریخ صدور/انقضا، شناسه‌ی سخت‌افزاری (اختیاری)،
  سهمیه‌ی قابلیت‌ها و امضای Ed25519 روی بایت‌های کانونیکال payload.
- امضا با کلید خصوصی فروشنده (فقط نزد طه) در tools/make_license.py ساخته
  می‌شود؛ برنامه فقط با کلید عمومی جاسازی‌شده در license_crypto صحت را
  بررسی می‌کند. دست‌کاری فایل لایسنس امضا را باطل می‌کند.

رفتار بدون لایسنس معتبر: حالت محدود (۱ دوربین، بدون قابلیت هوشمند).
"""

import json
import os
import sys
from datetime import date

try:
    from license_crypto import LICENSE_PUBLIC_KEY, verify_message
except ImportError:  # اجرای مستقیم از tools/
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from license_crypto import LICENSE_PUBLIC_KEY, verify_message

LICENSE_FORMAT = "IAS-CMS-LICENSE-1"
LICENSE_FILENAME = "license.lic"

# سهمیه‌های حالت محدود (وقتی لایسنس معتبر نیست)
DEMO_QUOTAS = {
    "cameras": 1,
    "plate": 0,
    "fire": 0,
    "person_tracking": 0,
}

# ترتیب نمایش سهمیه‌ها در دیالوگ
QUOTA_ORDER = ["cameras", "plate", "fire", "person_tracking"]


# ----------------------------------------------------------------------------
# مسیرها
# ----------------------------------------------------------------------------
def _app_dirs():
    """پوشه‌هایی که فایل لایسنس در آن‌ها جست‌وجو می‌شود."""
    dirs = []
    # کنار فایل اجرایی (حالت exe / portable)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    # پوشه‌ی داده‌ی برنامه
    try:
        from app_paths import get_data_dir
        dirs.append(get_data_dir())
    except Exception:
        pass
    # کنار سورس (حالت توسعه)
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    # پوشه‌ی جاری
    dirs.append(os.getcwd())
    seen, out = set(), []
    for d in dirs:
        d = os.path.abspath(d)
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_license_file():
    for d in _app_dirs():
        p = os.path.join(d, LICENSE_FILENAME)
        if os.path.isfile(p):
            return p
    return None


def default_license_path():
    """مسیر پیشنهادی برای ذخیره‌ی لایسنس بارگذاری‌شده."""
    try:
        from app_paths import get_data_dir
        return os.path.join(get_data_dir(), LICENSE_FILENAME)
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            LICENSE_FILENAME)


# ----------------------------------------------------------------------------
# شناسه‌ی سخت‌افزاری
# ----------------------------------------------------------------------------
def get_hwid():
    """شناسه‌ی یکتای این سیستم (برای قفل سخت‌افزاری لایسنس)."""
    # ویندوز: MachineGuid رجیستری
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            if guid:
                return "WIN-" + str(guid).upper()
    except Exception:
        pass
    # جایگزین: MAC آدرس
    try:
        import uuid
        return "MAC-%012X" % (uuid.getnode() & 0xFFFFFFFFFFFF)
    except Exception:
        return "UNKNOWN"


# ----------------------------------------------------------------------------
# ساخت / بررسی امضا
# ----------------------------------------------------------------------------
def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def create_license_file(payload: dict, private_hex: str) -> dict:
    """ساخت دیکشنری لایسنس امضاشده (استفاده در ابزار فروشنده)."""
    from license_crypto import sign_message
    sig = sign_message(private_hex, canonical_bytes(payload))
    return {"format": LICENSE_FORMAT, "payload": payload, "signature": sig}


def verify_license_data(data: dict):
    """بررسی ساختار و امضای لایسنس؛ برمی‌گرداند (payload, error)."""
    if not isinstance(data, dict):
        return None, "ساختار فایل لایسنس نامعتبر است."
    if data.get("format") != LICENSE_FORMAT:
        return None, "نسخه‌ی فرمت لایسنس پشتیبانی نمی‌شود."
    payload = data.get("payload")
    sig = data.get("signature")
    if not isinstance(payload, dict) or not isinstance(sig, str):
        return None, "ساختار فایل لایسنس نامعتبر است."
    if not verify_message(LICENSE_PUBLIC_KEY, canonical_bytes(payload), sig):
        return None, "امضای لایسنس معتبر نیست (فایل دست‌کاری شده)."
    return payload, None


def check_payload_validity(payload: dict):
    """بررسی انقضا و قفل سخت‌افزاری؛ برمی‌گرداند خطا یا None."""
    exp = payload.get("expires")
    if exp:
        try:
            exp_d = date.fromisoformat(str(exp))
        except ValueError:
            return "تاریخ انقضای لایسنس نامعتبر است."
        if date.today() > exp_d:
            return "مدت لایسنس به پایان رسیده است."
    hwid = payload.get("hwid")
    if hwid and str(hwid).upper() != get_hwid().upper():
        return "این لایسنس متعلق به سیستم دیگری است."
    return None


# ----------------------------------------------------------------------------
# بارگذاری و وضعیت
# ----------------------------------------------------------------------------
class LicenseState:
    def __init__(self, valid=False, customer="", issued="", expires="",
                 quotas=None, hwid=None, error="", path=""):
        self.valid = valid
        self.customer = customer
        self.issued = issued
        self.expires = expires
        self.quotas = quotas or dict(DEMO_QUOTAS)
        self.hwid = hwid
        self.error = error
        self.path = path

    @property
    def days_left(self):
        if not self.valid or not self.expires:
            return None
        try:
            return (date.fromisoformat(self.expires) - date.today()).days
        except ValueError:
            return None


def load_license(path=None) -> LicenseState:
    p = path or find_license_file()
    if not p:
        return LicenseState(error="فایل لایسنس یافت نشد.")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return LicenseState(error="فایل لایسنس خراب است.", path=p)
    payload, err = verify_license_data(data)
    if err:
        return LicenseState(error=err, path=p)
    err = check_payload_validity(payload)
    if err:
        return LicenseState(error=err, path=p,
                            customer=payload.get("customer", ""),
                            expires=payload.get("expires") or "")
    quotas = dict(DEMO_QUOTAS)
    pq = payload.get("quotas") or {}
    for k in quotas:
        try:
            quotas[k] = max(0, int(pq.get(k, 0)))
        except (TypeError, ValueError):
            pass
    return LicenseState(
        valid=True,
        customer=payload.get("customer", ""),
        issued=payload.get("issued", ""),
        expires=payload.get("expires") or "",
        quotas=quotas,
        hwid=payload.get("hwid"),
        path=p,
    )


def effective_quotas(state=None) -> dict:
    """سهمیه‌های مؤثر برنامه: از لایسنس معتبر، وگرنه حالت محدود."""
    st = state or load_license()
    if st.valid:
        return dict(st.quotas)
    return dict(DEMO_QUOTAS)


def install_license_file(src_path: str) -> str:
    """کپی فایل لایسنس به محل استاندارد؛ مسیر نهایی را برمی‌گرداند."""
    dst = default_license_path()
    with open(src_path, "rb") as f:
        blob = f.read()
    # اعتبارسنجی قبل از نصب
    data = json.loads(blob.decode("utf-8"))
    payload, err = verify_license_data(data)
    if err:
        raise ValueError(err)
    err = check_payload_validity(payload)
    if err:
        raise ValueError(err)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(blob)
    return dst


# ----------------------------------------------------------------------------
# دیالوگ مدیریت لایسنس (پشت گیت رمز ادمین باز می‌شود)
# ----------------------------------------------------------------------------
def _quota_title(key: str) -> str:
    try:
        from admin_quota import quota_title
        return quota_title(key)
    except Exception:
        return {"cameras": "تعداد کل دوربین‌ها", "plate": "دوربین‌های پلاک‌خوان",
                "fire": "دوربین‌های تشخیص حریق",
                "person_tracking": "دوربین‌های ردیابی اشخاص"}.get(key, key)


def open_license_dialog(parent, camera_store=None):
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                                 QFileDialog, QMessageBox, QGridLayout,
                                 QApplication)
    from PyQt6.QtCore import Qt

    state = load_license()

    dlg = QDialog(parent)
    dlg.setWindowTitle("🔑 مدیریت لایسنس")
    dlg.setMinimumWidth(430)
    dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    lay = QVBoxLayout(dlg)

    if state.valid:
        status = f"✅ <b>لایسنس معتبر</b> — {state.customer or 'بدون نام'}"
    else:
        status = (f"⛔ <b>لایسنس معتبر نیست</b> — حالت محدود فعال است"
                  f"<br><span style='color:#a00'>{state.error}</span>")
    lab = QLabel(status)
    lab.setWordWrap(True)
    lay.addWidget(lab)

    grid = QGridLayout()
    grid.addWidget(QLabel("مشتری:"), 0, 0)
    grid.addWidget(QLabel(state.customer or "—"), 0, 1)
    grid.addWidget(QLabel("تاریخ انقضا:"), 1, 0)
    grid.addWidget(QLabel(state.expires or "دائمی"), 1, 1)
    if state.valid and state.days_left is not None:
        grid.addWidget(QLabel("روزهای باقی‌مانده:"), 2, 0)
        grid.addWidget(QLabel(str(state.days_left)), 2, 1)
    grid.addWidget(QLabel("قفل سخت‌افزاری:"), 3, 0)
    grid.addWidget(QLabel("فعال" if state.hwid else "غیرفعال"), 3, 1)
    lay.addLayout(grid)

    lay.addWidget(QLabel("<b>سهمیه‌های این لایسنس:</b>"))
    qgrid = QGridLayout()
    try:
        from admin_quota import count_usage
        usage = {k: count_usage(camera_store, k) for k in QUOTA_ORDER} \
            if camera_store else {}
    except Exception:
        usage = {}
    for i, key in enumerate(QUOTA_ORDER):
        q = state.quotas.get(key, 0)
        u = usage.get(key, 0) if isinstance(usage, dict) else 0
        qtext = "نامحدود" if q == 0 else f"{u} از {q}"
        qgrid.addWidget(QLabel(_quota_title(key) + ":"), i, 0)
        qgrid.addWidget(QLabel(qtext), i, 1)
    lay.addLayout(qgrid)

    hwid_row = QLabel(f"شناسه‌ی سخت‌افزاری این سیستم:<br><code>{get_hwid()}</code>")
    hwid_row.setWordWrap(True)
    hwid_row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(hwid_row)

    def _copy_hwid():
        QApplication.clipboard().setText(get_hwid())
        QMessageBox.information(dlg, "کپی شد",
                                "شناسه‌ی سخت‌افزاری در حافظه کپی شد.")

    def _load_file():
        fp, _ = QFileDialog.getOpenFileName(
            dlg, "انتخاب فایل لایسنس", "", "License (*.lic);;All (*)")
        if not fp:
            return
        try:
            dst = install_license_file(fp)
        except Exception as e:
            QMessageBox.warning(dlg, "خطا", f"لایسنس پذیرفته نشد:\n{e}")
            return
        QMessageBox.information(
            dlg, "انجام شد",
            f"لایسنس با موفقیت نصب شد:\n{dst}\n\nبرنامه را یک‌بار ببندید و دوباره باز کنید.")
        dlg.accept()

    btn_copy = QPushButton("📋 کپی شناسه‌ی سخت‌افزاری")
    btn_copy.clicked.connect(_copy_hwid)
    lay.addWidget(btn_copy)

    btn_load = QPushButton("📂 بارگذاری فایل لایسنس…")
    btn_load.clicked.connect(_load_file)
    lay.addWidget(btn_load)

    btn_close = QPushButton("بستن")
    btn_close.clicked.connect(dlg.accept)
    lay.addWidget(btn_close)

    dlg.exec()

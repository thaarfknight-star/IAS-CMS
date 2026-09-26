# -*- coding: utf-8 -*-
"""admin_quota.py — محیط ادمین و سهمیه‌ی قابلیت‌ها.

دو بخش دارد:

۱) **سهمیه‌ی قابلیت‌ها (feature quotas):** برای هر نصبِ برنامه می‌توان
   تعیین کرد هر قابلیت حداکثر روی چند دوربین فعال شود (مثلاً ۲ دوربین
   پلاک‌خوان، ۴ دوربین تشخیص حریق). سهمیه‌ها در app_settings.json با کلید
   `feature_quotas` ذخیره می‌شوند؛ مقدار ۰ یعنی «نامحدود» (پیش‌فرض همه‌ی
   سهمیه‌ها نامحدود است تا نصب‌های فعلی چیزی از دست ندهند).

۲) **محیط ادمین:** بخش «سهمیه‌ها» فقط از یک محیط جداگانه (دیالوگ ادمین)
   قابل تغییر است که با رمز ادمین باز می‌شود. خودِ رمز هرگز به‌صورت متن
   ساده در کد نیست؛ فقط هش SHA-256 آن با یک salt ثابت نگه داشته می‌شود.

نقاط اعمال سهمیه (جلوگیری از فعال‌سازی بیش از سقف):
- plate_library_dialog.py — تیک «پلاک‌خوان» هر دوربین
- fire_alarm_dialog.py — تیک «تشخیص حریق» هر دوربین
- person_track_dialog.py — تیک «ردیابی اشخاص» هر دوربین
- main.py — افزودن دوربین/کانال NVR جدید (سهمیه‌ی کل دوربین‌ها)

رفتار هنگام کم کردن سهمیه از مصرف فعلی: دوربین‌هایی که قبلاً فعال‌اند
سر جایشان می‌مانند (grandfather)؛ فقط فعال‌سازی جدید مسدود می‌شود.
"""

import hashlib

try:
    from PyQt6.QtWidgets import QDialog
except Exception:  # محیط بدون PyQt (تست منطق خالص)
    QDialog = object

# ----------------------------------------------------------------------------
# رمز ادمین: فقط هش نگه داشته می‌شود، نه خود رمز.
# ----------------------------------------------------------------------------
_ADMIN_SALT = "ias-cms-admin-v1::"
_ADMIN_PASSWORD_HASH = (
    "37ec42da48bc283986f0951a285c6f4026a3eaf6961e0dcd1619a86848f3f7f7"
)


def verify_admin_password(password):
    """True اگر رمز ادمین درست باشد."""
    try:
        digest = hashlib.sha256(
            (_ADMIN_SALT + str(password)).encode("utf-8")
        ).hexdigest()
    except Exception:
        return False
    return digest == _ADMIN_PASSWORD_HASH


# ----------------------------------------------------------------------------
# تعریف سهمیه‌ها: (کلید، عنوان فارسی، کلید فلگ در camera_store)
# فلگ None یعنی «تعداد کل دوربین‌ها» (نه یک قابلیت خاص).
# ----------------------------------------------------------------------------
QUOTA_DEFS = [
    ("cameras", "تعداد کل دوربین‌ها", None),
    ("plate", "دوربین پلاک‌خوان", "plate_detection"),
    ("fire", "دوربین تشخیص حریق", "fire_detection"),
    ("person_tracking", "دوربین ردیابی اشخاص", "person_tracking"),
]

_QUOTA_KEY_BY_FEATURE = {key: key for key, _, _ in QUOTA_DEFS}

QUOTA_SETTINGS_KEY = "feature_quotas"


def get_quotas():
    """دیکشنری سهمیه‌ها {کلید: عدد}؛ ۰ یعنی نامحدود. کلید ناموجود = ۰."""
    quotas = {}
    try:
        from app_settings import load_settings
        raw = load_settings().get(QUOTA_SETTINGS_KEY, {}) or {}
        for key, _title, _flag in QUOTA_DEFS:
            try:
                quotas[key] = max(0, int(raw.get(key, 0)))
            except Exception:
                quotas[key] = 0
    except Exception:
        for key, _title, _flag in QUOTA_DEFS:
            quotas[key] = 0
    return quotas


def save_quotas(quotas):
    """ذخیره‌ی سهمیه‌ها در app_settings.json."""
    try:
        from app_settings import load_settings, save_settings
        cfg = load_settings()
        clean = {}
        for key, _title, _flag in QUOTA_DEFS:
            try:
                clean[key] = max(0, int(quotas.get(key, 0)))
            except Exception:
                clean[key] = 0
        cfg[QUOTA_SETTINGS_KEY] = clean
        return bool(save_settings(cfg))
    except Exception:
        return False


def quota_title(feature):
    for key, title, _flag in QUOTA_DEFS:
        if key == feature:
            return title
    return str(feature)


def count_usage(camera_store, feature):
    """تعداد مصرف فعلی یک سهمیه."""
    try:
        cams = camera_store.get_cameras() or []
    except Exception:
        return 0
    if feature == "cameras":
        return len(cams)
    flag = None
    for key, _title, f in QUOTA_DEFS:
        if key == feature:
            flag = f
            break
    if not flag:
        return 0
    return sum(1 for c in cams if isinstance(c, dict) and bool(c.get(flag)))


def check_quota(feature, camera_store, count=1, exclude_cam_id=None):
    """بررسی اینکه آیا می‌توان `count` واحد دیگر از سهمیه‌ی `feature` را
    فعال کرد یا نه.

    برمی‌گرداند: (مجاز؟, مصرف فعلی, سقف). سقف ۰ یعنی نامحدود (همیشه مجاز).
    exclude_cam_id: دوربینی که در شمارش مصرف لحاظ نشود (برای حالتی که
    همان دوربین همین حالا فعال است و دوباره تیک می‌خورد).
    """
    quotas = get_quotas()
    quota = quotas.get(feature, 0)
    if quota <= 0:
        return True, count_usage(camera_store, feature), 0
    used = 0
    try:
        cams = camera_store.get_cameras() or []
    except Exception:
        cams = []
    if feature == "cameras":
        used = len(cams)
    else:
        flag = None
        for key, _title, f in QUOTA_DEFS:
            if key == feature:
                flag = f
                break
        for c in cams:
            if not isinstance(c, dict):
                continue
            if exclude_cam_id is not None and c.get("id") == exclude_cam_id:
                continue
            if flag and bool(c.get(flag)):
                used += 1
    return (used + count) <= quota, used, quota


def quota_denied_message(feature, used, quota):
    return (
        f"سهمیه‌ی «{quota_title(feature)}» تکمیل است.\n"
        f"مصرف فعلی: {used} از {quota}\n\n"
        f"برای افزایش سقف، وارد محیط ادمین شوید."
    )


# ----------------------------------------------------------------------------
# رابط کاربری: گیت رمز + دیالوگ جداگانه‌ی ادمین
# ----------------------------------------------------------------------------
def guard_feature_enable(feature, camera_store, checklist, item, parent):
    """گیت سهمیه برای تیک فعال‌سازی یک قابلیت روی یک دوربین.

    True یعنی مجاز است و فراخواننده می‌تواند ادامه دهد؛ False یعنی سهمیه
    پر است: تیک به حالت خاموش برگردانده شد و پیام هشدار نمایش داده شد.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QMessageBox
    except Exception:
        return True  # بدون PyQt مزاحم کاری نمی‌شویم
    cam_id = item.data(Qt.ItemDataRole.UserRole)
    allowed, used, quota = check_quota(feature, camera_store,
                                       exclude_cam_id=cam_id)
    if allowed:
        return True
    try:
        checklist.blockSignals(True)
        item.setCheckState(Qt.CheckState.Unchecked)
    finally:
        try:
            checklist.blockSignals(False)
        except Exception:
            pass
    try:
        QMessageBox.warning(parent, "سهمیه تکمیل است",
                            quota_denied_message(feature, used, quota))
    except Exception:
        pass
    return False


def prompt_admin_password(parent=None, attempts=3):
    """دیالوگ گرفتن رمز ادمین؛ True یعنی رمز درست وارد شد."""
    try:
        from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox
    except Exception:
        return False
    for i in range(max(1, attempts)):
        try:
            text, ok = QInputDialog.getText(
                parent, "🔐 ورود ادمین", "رمز ادمین را وارد کنید:",
                QLineEdit.EchoMode.Password)
        except Exception:
            return False
        if not ok:
            return False
        if verify_admin_password(text):
            return True
        try:
            QMessageBox.warning(parent, "رمز اشتباه",
                                "رمز ادمین اشتباه است." if i < attempts - 1
                                else "رمز ادمین اشتباه است.")
        except Exception:
            pass
    return False


class AdminQuotaDialog(QDialog):
    """محیط جداگانه‌ی ادمین: تعیین سقف هر سهمیه + نمایش مصرف فعلی."""

    def __init__(self, camera_store=None, parent=None):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QSpinBox, QPushButton, QFormLayout,
                                     QMessageBox)
        super().__init__(parent)
        self.camera_store = camera_store
        self.setWindowTitle("🔐 محیط ادمین — سهمیه‌ی قابلیت‌ها")
        self.setMinimumWidth(420)
        self._spinboxes = {}

        layout = QVBoxLayout(self)
        info = QLabel(
            "در این بخش تعیین می‌کنید هر قابلیت حداکثر روی چند دوربین فعال "
            "شود.\nمقدار ۰ یعنی «نامحدود».")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        quotas = get_quotas()
        for key, title, _flag in QUOTA_DEFS:
            row = QHBoxLayout()
            spin = QSpinBox()
            spin.setRange(0, 9999)
            spin.setValue(quotas.get(key, 0))
            spin.setSpecialValueText("نامحدود")
            row.addWidget(spin)
            used = count_usage(camera_store, key) if camera_store else 0
            usage_lbl = QLabel(f"مصرف فعلی: {used}")
            usage_lbl.setObjectName("quota_usage_label")
            row.addWidget(usage_lbl)
            row.addStretch(1)
            self._spinboxes[key] = spin
            form.addRow(title + ":", row)
        layout.addLayout(form)

        note = QLabel(
            "نکته: اگر سقف را کمتر از مصرف فعلی بگذارید، دوربین‌هایی که "
            "قبلاً فعال‌اند غیرفعال نمی‌شوند؛ فقط فعال‌سازی جدید مسدود "
            "می‌شود.")
        note.setWordWrap(True)
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        save_btn = QPushButton("💾 ذخیره")
        save_btn.clicked.connect(self._on_save)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_save(self):
        from PyQt6.QtWidgets import QMessageBox
        quotas = {k: s.value() for k, s in self._spinboxes.items()}
        if save_quotas(quotas):
            QMessageBox.information(self, "ذخیره شد",
                                    "سهمیه‌ها با موفقیت ذخیره شدند.")
            self.accept()
        else:
            QMessageBox.warning(self, "خطا",
                                "ذخیره‌ی سهمیه‌ها ناموفق بود.")

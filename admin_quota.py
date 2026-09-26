# -*- coding: utf-8 -*-
"""admin_quota.py — گیت رمز ادمین + اعمال سهمیه‌های لایسنس.

سهمیه‌ها از فایل لایسنس امضاشده (license.py) خوانده می‌شوند؛ این ماژول فقط
گیت دسترسی ادمین و منطق مسدودسازی فعال‌سازی را نگه می‌دارد.
تعیین سهمیه فقط با «مدیریت لایسنس IAS» (نرم‌افزار جدای فروشنده) انجام
می‌شود و در برنامه‌ی اصلی قابل تغییر نیست.

نقاط اعمال سهمیه (جلوگیری از فعال‌سازی بیش از سقف):
- plate_library_dialog.py — تیک «پلاک‌خوان» هر دوربین
- fire_alarm_dialog.py — تیک «تشخیص حریق» هر دوربین
- person_track_dialog.py — تیک «ردیابی اشخاص» هر دوربین
- main.py — افزودن دوربین/کانال NVR جدید (سهمیه‌ی کل دوربین‌ها)

رفتار بدون لایسنس معتبر (حالت محدود): تصویر همه‌ی دوربین‌ها نمایش داده
می‌شود؛ فقط شمارش افراد فعال است و هیچ‌یک از قابلیت‌های سهمیه‌ای،
چهره‌خوان یا هشدار ورود به محدوده فعال نمی‌شوند.

رفتار هنگام کم بودن سهمیه از مصرف فعلی: دوربین‌هایی که قبلاً فعال‌اند
سر جایشان می‌مانند (grandfather)؛ فقط فعال‌سازی جدید مسدود می‌شود.
"""

import hashlib

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


def get_quotas():
    """دیکشنری سهمیه‌ها {کلید: عدد} از لایسنس معتبر؛ ۰ یعنی نامحدود.

    اگر لایسنس معتبر نباشد، سهمیه‌های حالت محدود برگردانده می‌شود
    (دوربین نامحدود، بدون قابلیت سهمیه‌ای)."""
    try:
        from license import effective_quotas
        quotas = effective_quotas()
        clean = {}
        for key, _title, _flag in QUOTA_DEFS:
            try:
                clean[key] = max(0, int(quotas.get(key, 0)))
            except Exception:
                clean[key] = 0
        return clean
    except Exception:
        return {key: 0 for key, _t, _f in QUOTA_DEFS}


def _license_valid():
    try:
        from license import load_license
        return bool(load_license().valid)
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
    حالت محدود (بدون لایسنس معتبر): تصویر همه‌ی دوربین‌ها نمایش داده
    می‌شود (سقف دوربین نامحدود) ولی هیچ قابلیت سهمیه‌ای فعال نمی‌شود.

    exclude_cam_id: دوربینی که در شمارش مصرف لحاظ نشود (برای حالتی که
    همان دوربین همین حالا فعال است و دوباره تیک می‌خورد).
    """
    if not _license_valid():
        # حالت محدود — بدون لایسنس معتبر
        if feature != "cameras":
            return False, 0, 0
        used = count_usage(camera_store, "cameras")
        return True, used, 0
    # سهمیه‌ی تعدادی غیرفعال‌شده در لایسنس: صفر واقعی (کاملاً بسته)
    try:
        from license import is_quota_enabled
        if not is_quota_enabled(feature):
            return False, count_usage(camera_store, feature), -1
    except Exception:
        pass
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
    # quota برابر ۱- یعنی این سهمیه در لایسنس کلاً غیرفعال است (صفر واقعی)
    if quota == -1:
        return (
            f"قابلیت «{quota_title(feature)}» در لایسنس فعلی غیرفعال است.\n"
            "برای فعال‌سازی آن با فروشنده‌ی نرم‌افزار در تماس باشید."
        )
    if not _license_valid():
        return (
            "لایسنس معتبر یافت نشد؛ برنامه در حالت محدود است.\n"
            "برای فعال‌سازی این قابلیت، فایل لایسنس را از فروشنده بگیرید و "
            "در صفحه‌ی تنظیمات ← ورود ادمین ← «بارگذاری فایل لایسنس» وارد کنید."
        )
    return (
        f"سهمیه‌ی «{quota_title(feature)}» تکمیل است.\n"
        f"مصرف فعلی: {used} از {quota}\n\n"
        f"برای افزایش سقف با فروشنده‌ی نرم‌افزار در تماس باشید."
    )


def feature_denied_message(feature_name: str) -> str:
    """پیام رد شدن یک قابلیت روشن/خاموش (حالت محدود یا لایسنس بدون آن قابلیت)."""
    try:
        from license import FEATURE_DEFS, is_feature_enabled
        title = FEATURE_DEFS.get(feature_name, feature_name)
    except Exception:
        title = feature_name
    try:
        from license import load_license
        if not load_license().valid:
            return (
                "لایسنس معتبر یافت نشد؛ برنامه در حالت محدود است.\n"
                f"قابلیت «{title}» در حالت محدود فعال نیست.\n"
                "برای فعال‌سازی آن، فایل لایسنس را از فروشنده بگیرید و "
                "در صفحه‌ی تنظیمات ← ورود ادمین ← «بارگذاری فایل لایسنس» وارد کنید."
            )
    except Exception:
        pass
    return (
        f"قابلیت «{title}» در لایسنس فعلی فعال نیست.\n"
        "برای فعال‌سازی آن با فروشنده‌ی نرم‌افزار در تماس باشید."
    )


def require_feature(feature_name: str, parent=None) -> bool:
    """گیت قابلیت‌های روشن/خاموش. True یعنی فعال است و می‌توان ادامه داد؛
    False یعنی غیرفعال است و پیام هشدار نمایش داده شد."""
    try:
        from license import is_feature_enabled
        if is_feature_enabled(feature_name):
            return True
    except Exception:
        return True  # در صورت خطا، مزاحم کاربر نمی‌شویم
    try:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, "قابلیت غیرفعال",
                            feature_denied_message(feature_name))
    except Exception:
        pass
    return False


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
    title = "قابلیت غیرفعال" if quota == -1 else "سهمیه تکمیل است"
    try:
        QMessageBox.warning(parent, title,
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


# ----------------------------------------------------------------------------
# اعمال سقف‌ها: اگر تعداد دوربین‌هایی که قابلیتی را دارند از سقف لایسنس فعلی
# بیشتر شده باشد (مثلاً لایسنس جدید سقف کمتری دارد)، تیک آن قابلیت از روی
# دوربین‌های اضافی برداشته می‌شود تا مصرف دقیقاً به سقف برسد.
# ----------------------------------------------------------------------------

def enforce_quotas(camera_store):
    """اعمال سقف‌های لایسنس فعلی روی دوربین‌ها.

    برمی‌گرداند: لیست خطوط فارسی گزارش (خالی یعنی همه‌چیز در سقف است).

    - برای plate/fire/person_tracking: تیک قابلیت از روی دوربین‌های اضافی
      (بعد از سقف، به ترتیب لیست دوربین‌ها) برداشته و ذخیره می‌شود.
    - اگر سهمیه‌ای در لایسنس کلاً غیرفعال باشد (صفر واقعی)، تیک آن از روی
      همه‌ی دوربین‌ها برداشته می‌شود.
    - برای «تعداد کل دوربین‌ها» حذف خودکار انجام نمی‌شود (از دست رفتن
      اطلاعات دوربین)؛ فقط هشدار داده می‌شود تا کاربر دستی حذف کند.
    - بدون لایسنس معتبر کاری انجام نمی‌شود (حالت محدود را گیت‌های زمان
      اجرا مدیریت می‌کنند).
    """
    lines = []
    if not _license_valid():
        return lines
    try:
        from license import is_quota_enabled, effective_quotas
        quotas = effective_quotas()
    except Exception:
        return lines
    try:
        cams = camera_store.get_cameras() or []
    except Exception:
        return lines

    for key, title, flag in QUOTA_DEFS:
        try:
            enabled = bool(is_quota_enabled(key))
        except Exception:
            enabled = True
        try:
            limit = max(0, int((quotas or {}).get(key, 0)))
        except Exception:
            limit = 0

        if key == "cameras":
            if enabled and limit > 0 and len(cams) > limit:
                lines.append(
                    f"⛔ تعداد دوربین‌ها ({len(cams)}) از سقف لایسنس ({limit}) "
                    f"بیشتر است — {len(cams) - limit} دوربین اضافی را حذف کنید "
                    "تا برنامه از حالت محدود خارج شود.")
            continue
        if not flag:
            continue
        if enabled and limit <= 0:
            continue  # نامحدود
        # سهمیه‌ی غیرفعال (صفر واقعی) = سقف مؤثر صفر
        eff_limit = limit if enabled else 0
        using = [c for c in cams
                 if isinstance(c, dict) and bool(c.get(flag))]
        if len(using) <= eff_limit:
            continue
        excess = using[eff_limit:]
        for c in excess:
            try:
                camera_store.update_camera(c.get("id"), **{flag: False})
            except Exception:
                pass
        names = "، ".join(str(c.get("name") or c.get("ip") or "؟")
                          for c in excess)
        if enabled:
            reason = f"سقف لایسنس جدید: {limit}"
        else:
            reason = "این قابلیت در لایسنس جدید غیرفعال است"
        lines.append(
            f"⚠️ «{title}» از روی {len(excess)} دوربین برداشته شد "
            f"({reason}): {names} — برای فعال‌سازی دوباره، تیک قابلیت را "
            "در تنظیمات دوربین بزنید.")
    return lines

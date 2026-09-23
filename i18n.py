# -*- coding: utf-8 -*-
"""i18n.py — زیرساخت دوزبانه‌ی برنامه (فارسی / English).

الگو (مشابه همان چیزی که صفحه‌ی تنظیمات از 2.0.1 داشت، حالا مشترک):
  هر ماژول یک دیکشنری STRINGS می‌سازد:
      STRINGS = {
          "hello": {"fa": "سلام", "en": "Hello"},
          "bye_x": {"fa": "خداحافظ {name}‏", "en": "Goodbye {name}"},
      }
  و در کد به‌جای رشته‌ی مستقیم می‌نویسد:
      label = QLabel(i18n.t(STRINGS, "hello"))
      msg = i18n.t(STRINGS, "bye_x", name="طه")

- زبان از app_settings خوانده می‌شود (fa | en)؛ پیش‌فرض fa.
- تغییر زبان در تنظیمات ذخیره می‌شود و با راه‌اندازی مجدد کامل اعمال می‌شود
  (متن راهنمای صفحه‌ی تنظیمات هم همین را می‌گوید).
- رشته‌ی ترجمه‌نشده به فارسی (زبان مبدأ) برمی‌گردد؛ پس دوزبانه‌سازی
  ماژول‌به‌ماژول امن است و صفحه‌ی نیمه‌ترجمه‌شده خراب نمی‌شود.
- جهت چیدمان: فارسی راست‌به‌چپ، انگلیسی چپ‌به‌راست.

نکته‌ی قالب‌بندی: چون مقادیر با str.format(**kwargs) پر می‌شوند، اگر در متن
فارسی/انگلیسی آکولاد واقعی لازم بود آن را دوتا بنویسید ({{ و }}).
"""

try:
    from PyQt6.QtCore import Qt
except Exception:  # pragma: no cover - محیط بدون Qt (لایه‌ی دیتا)
    Qt = None


try:
    import app_settings
except Exception:  # pragma: no cover - فقط برای تست‌های ایزوله
    app_settings = None


def get_lang():
    """زبان فعلی برنامه: 'fa' یا 'en'."""
    try:
        if app_settings is not None:
            lang = app_settings.get_language()
            if lang in ("fa", "en"):
                return lang
    except Exception:
        pass
    return "fa"


def is_rtl():
    """آیا جهت فعلی راست‌به‌چپ است (فارسی)؟"""
    return get_lang() == "fa"


def t(strings, key, **kwargs):
    """برگرداندن متن دوزبانه برای کلید key از دیکشنری strings ماژول.

    اگر کلید یا ترجمه‌ی زبان فعلی نبود، به فارسی برمی‌گردد و در نهایت
    خود key (پس هیچ‌وقت استثنا نمی‌دهد و UI خالی نمی‌ماند).
    """
    try:
        entry = strings.get(key)
    except Exception:
        entry = None
    if isinstance(entry, dict):
        lang = get_lang()
        s = entry.get(lang) or entry.get("fa") or key
    elif isinstance(entry, str):
        s = entry
    else:
        s = key
    if kwargs:
        try:
            s = str(s).format(**kwargs)
        except Exception:
            pass
    return s


def apply_direction(widget):
    """ست کردن جهت چیدمان ویجت/دیالوگ بر اساس زبان فعلی."""
    if Qt is None:
        return
    try:
        widget.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl()
            else Qt.LayoutDirection.LeftToRight
        )
    except Exception:
        pass


def tr_app(app):
    """ست کردن جهت کل برنامه (در startup صدا زده می‌شود)."""
    apply_direction(app)

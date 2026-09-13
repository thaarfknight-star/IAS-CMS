"""پروفایل «تنظیمات تصویر» هر دوربین (روشنایی، کنتراست، WDR، ضد مه و...).

هر دوربین پروفایل مستقل خودش را دارد و در cameras.json (فیلد
``image_profile`` همان دیکشنری دوربین) ذخیره می‌شود؛ چند پیش‌فرض آماده هم
داخلش هست تا کاربر سریع یکی را انتخاب کند و بعد در صورت نیاز دستی تنظیمش کند.

اعمال پروفایل فقط روی «فریم نمایشی» انجام می‌شود (در ترد پخش همان دوربین،
قبل از ارسال به رابط کاربری) و ورودی موتورهای تشخیص (چهره/شخص/حریق) دست‌نخورده
می‌ماند تا تنظیمات نمایشی، دقت تشخیص را تغییر ندهد.
"""

import cv2
import numpy as np

# ------------------------------------------------------------- پارامترها --
# همه‌ی پارامترها در یک دیکشنری تخت نگه داشته می‌شوند تا ذخیره‌سازی در
# cameras.json ساده بماند.
#
#   brightness: ‎-100..100‎ (جمع‌شونده)
#   contrast:   ‎0.5..2.0‎ (ضرب‌شونده)
#   saturation: ‎0.0..2.0‎ (ضرب‌شونده)
#   gamma:      ‎0.4..2.5‎ (۱.۰ خنثی؛ بزرگ‌تر = روشن‌تر شدن سایه‌ها)
#   wdr:        ‎0.0..1.0‎ (شدت WDR نرم‌افزاری: CLAHE روی کانال روشنایی)
#   dehaze:     ‎0.0..1.0‎ (شدت ضد مه: ترکیب با نسخه‌ی مه‌زدایی‌شده)
#   denoise:    ‎True/False‎ (کاهش نویز سبک، مناسب حالت شب)

NEUTRAL_PROFILE = {
    "preset": "default",
    "brightness": 0,
    "contrast": 1.0,
    "saturation": 1.0,
    "gamma": 1.0,
    "wdr": 0.0,
    "dehaze": 0.0,
    "denoise": False,
}

# --------------------------------------------------------------- پیش‌فرض‌ها --
# ترتیب نمایش در کمبوباکس دیالوگ همین ترتیب است.
PRESETS = {
    "default": {
        "label": "پیش‌فرض (بدون تغییر)",
        "params": {},
    },
    "wdr": {
        "label": "☀️ نور شدید (WDR)",
        "params": {"wdr": 0.65, "contrast": 1.05, "saturation": 1.05},
    },
    "defog": {
        "label": "🌫 مه و غبار (ضد مه)",
        "params": {"dehaze": 0.65, "contrast": 1.08, "saturation": 1.12},
    },
    "night": {
        "label": "🌙 شب",
        "params": {"gamma": 1.45, "brightness": 8, "wdr": 0.25, "denoise": True},
    },
    "indoor": {
        "label": "🏢 داخلی",
        "params": {"contrast": 1.08, "saturation": 1.12},
    },
    "backlight": {
        "label": "💡 ضد نور پس‌زمینه",
        "params": {"wdr": 0.85, "gamma": 1.2, "contrast": 1.03},
    },
}

CUSTOM_PRESET_KEY = "custom"
CUSTOM_PRESET_LABEL = "✏️ سفارشی"


def default_profile():
    """یک پروفایل کامل و خنثی (کپی تازه تا جایی جهش داده نشود)."""
    return dict(NEUTRAL_PROFILE)


def preset_profile(preset_key):
    """پروفایل کامل یک پیش‌فرض (خنثی + پارامترهای آن پیش‌فرض)."""
    profile = default_profile()
    preset = PRESETS.get(preset_key)
    if preset:
        profile["preset"] = preset_key
        profile.update(preset["params"])
    return profile


def get_camera_profile(cam):
    """پروفایل نهایی یک دوربین: پیش‌فرض انتخاب‌شده + بازنویسی‌های دستیِ ذخیره‌شده.

    ``cam`` دیکشنری دوربین از camera_store است؛ اگر چیزی ذخیره نشده باشد،
    پروفایل خنثی برمی‌گردد. همیشه یک دیکشنری کامل (با همه‌ی کلیدها) می‌دهد.
    """
    saved = (cam or {}).get("image_profile") or {}
    preset_key = saved.get("preset", "default")
    profile = preset_profile(preset_key)
    for key in NEUTRAL_PROFILE:
        if key in saved:
            profile[key] = saved[key]
    profile["preset"] = preset_key if preset_key in PRESETS else CUSTOM_PRESET_KEY
    return profile


def matches_preset(profile, preset_key):
    """آیا پروفایل دقیقاً برابر پارامترهای یک پیش‌فرض است؟"""
    if preset_key not in PRESETS:
        return False
    expected = preset_profile(preset_key)
    for key in NEUTRAL_PROFILE:
        if key == "preset":
            continue
        if abs(float(profile.get(key, 0)) - float(expected.get(key, 0))) > 1e-6:
            return False
    return True


def detect_preset(profile):
    """کلید پیش‌فرضی که پروفایل با آن می‌خواند، یا 'custom'."""
    for key in PRESETS:
        if matches_preset(profile, key):
            return key
    return CUSTOM_PRESET_KEY


def is_neutral(profile):
    """آیا پروفایل هیچ تغییری اعمال نمی‌کند؟ (مسیر سریع: رد شدن از پردازش)"""
    p = profile or {}
    return (
        p.get("brightness", 0) == 0
        and p.get("contrast", 1.0) == 1.0
        and p.get("saturation", 1.0) == 1.0
        and p.get("gamma", 1.0) == 1.0
        and p.get("wdr", 0.0) == 0.0
        and p.get("dehaze", 0.0) == 0.0
        and not p.get("denoise", False)
    )


# ------------------------------------------------------------------ اعمال --
_gamma_lut_cache = {}
_clahe_cache = {}


def _gamma_lut(gamma):
    key = round(float(gamma), 2)
    lut = _gamma_lut_cache.get(key)
    if lut is None:
        inv = 1.0 / max(key, 1e-6)
        lut = np.array([((i / 255.0) ** inv) * 255.0 for i in range(256)],
                       dtype=np.uint8)
        _gamma_lut_cache[key] = lut
    return lut


def _clahe(clip_limit):
    key = round(float(clip_limit), 2)
    clahe = _clahe_cache.get(key)
    if clahe is None:
        clahe = cv2.createCLAHE(clipLimit=key, tileGridSize=(8, 8))
        _clahe_cache[key] = clahe
    return clahe


def _apply_wdr(img, strength):
    """WDR نرم‌افزاری: CLAHE روی کانال روشنایی (LAB).

    برای حفظ نرخ فریم، CLAHE روی نسخه‌ی نیم‌وضوح (حداکثر ۹۶۰ پیکسل) اجرا و
    نتیجه به اندازه‌ی اصلی برگردانده می‌شود - کنتراست محلی عملاً همان است
    ولی حدود ۴ برابر سریع‌تر.
    """
    clip = 1.0 + 3.0 * strength
    h, w = img.shape[:2]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    s = min(1.0, 960.0 / max(h, w))
    if s < 1.0:
        small_l = cv2.resize(l, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        small_l = _clahe(clip).apply(small_l)
        l = cv2.resize(small_l, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        l = _clahe(clip).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def _apply_dehaze(img, strength):
    """مه‌زدایی سریع (تقریب Dark Channel Prior روی نسخه‌ی کوچک‌شده).

    نسخه‌ی سبک‌شده‌ی الگوریتم کلاسیک He و همکاران: کانال تیره روی تصویر
    کوچک (حداکثر ۳۲۰ پیکسل) حساب می‌شود، نور جوی از روشن‌ترین پیکسل‌های
    کانال تیره تخمین زده می‌شود و نقشه‌ی عبور (transmission) با یک بلور
    ساده نرم می‌شود (به‌جای guided filter که گران است). بازیابی روی نسخه‌ی
    نیم‌وضوح (حداکثر ۹۶۰ پیکسل) انجام و به اندازه‌ی اصلی برگردانده می‌شود،
    بعد با شدت strength با تصویر اصلی ترکیب می‌شود.
    """
    h, w = img.shape[:2]
    # وضوح کاری برای بازیابی (مه پدیده‌ی کم‌فرکانس است؛ نیم‌وضوح کافی است)
    s = min(1.0, 960.0 / max(h, w))
    work = cv2.resize(img, (int(w * s), int(h * s)),
                      interpolation=cv2.INTER_AREA) if s < 1.0 else img
    wh, ww = work.shape[:2]

    scale = 320.0 / max(ww, wh)
    sw, sh = max(1, int(ww * scale)), max(1, int(wh * scale))
    small = cv2.resize(work, (sw, sh), interpolation=cv2.INTER_LINEAR).astype(np.float32)

    # نور جوی A: میانگین رنگ روشن‌ترین ۰٫۱٪ پیکسل‌های کانال تیره
    dark = small.min(axis=2)
    dark = cv2.erode(dark, np.ones((3, 3), np.uint8))
    flat = dark.reshape(-1)
    n_top = max(1, int(flat.size * 0.001))
    top_idx = np.argpartition(flat, -n_top)[-n_top:]
    A = small.reshape(-1, 3)[top_idx].mean(axis=0)
    A = np.maximum(A, 1.0)

    # نقشه‌ی عبور روی همان تصویر کوچک، بعد بزرگ‌نمایی به اندازه‌ی تصویر کاری
    normed = small / A.reshape(1, 1, 3)
    dark_n = normed.min(axis=2)
    dark_n = cv2.erode(dark_n, np.ones((3, 3), np.uint8))
    t_small = 1.0 - 0.95 * dark_n
    t_small = cv2.GaussianBlur(t_small, (5, 5), 0)
    t = cv2.resize(t_small, (ww, wh), interpolation=cv2.INTER_LINEAR)
    t = np.clip(t, 0.15, 1.0)[..., None]

    work_f = work.astype(np.float32)
    recovered = (work_f - A.reshape(1, 1, 3)) / t + A.reshape(1, 1, 3)
    recovered = np.clip(recovered, 0, 255).astype(np.uint8)
    if s < 1.0:
        recovered = cv2.resize(recovered, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.addWeighted(img, 1.0 - strength, recovered, strength, 0)


def apply_profile(frame, profile):
    """اعمال یک پروفایل تصویر روی یک فریم BGR (uint8) و برگرداندن نتیجه.

    اگر پروفایل خنثی باشد، همان فریم ورودی (بدون کپی اضافه) برمی‌گردد.
    این تابع هیچ‌وقت استثنا بیرون نمی‌دهد تا ترد پخش زمین نخورد؛ در صورت
    خطای غیرمنتظره، فریم اصلی برگردانده می‌شود.
    """
    try:
        if frame is None or is_neutral(profile):
            return frame
        p = profile
        img = frame

        brightness = float(p.get("brightness", 0))
        contrast = float(p.get("contrast", 1.0))
        if brightness != 0 or contrast != 1.0:
            img = cv2.convertScaleAbs(img, alpha=contrast, beta=brightness)

        gamma = float(p.get("gamma", 1.0))
        if gamma != 1.0:
            img = cv2.LUT(img, _gamma_lut(gamma))

        saturation = float(p.get("saturation", 1.0))
        if saturation != 1.0:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            h, s, v = cv2.split(hsv)
            s = cv2.convertScaleAbs(s, alpha=saturation)
            img = cv2.cvtColor(cv2.merge((h, s, v)), cv2.COLOR_HSV2BGR)

        wdr = float(p.get("wdr", 0.0))
        if wdr > 0.0:
            img = _apply_wdr(img, min(max(wdr, 0.0), 1.0))

        dehaze = float(p.get("dehaze", 0.0))
        if dehaze > 0.0:
            img = _apply_dehaze(img, min(max(dehaze, 0.0), 1.0))

        if p.get("denoise"):
            img = cv2.bilateralFilter(img, 5, 40, 40)

        return img
    except Exception:
        return frame

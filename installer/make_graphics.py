# -*- coding: utf-8 -*-
"""تولید تصاویر پس‌زمینه‌ی نصب‌کننده‌ی NSIS (متن فارسی داخل تصویر bake می‌شود
تا مشکل RTL در NSIS نداشته باشیم).

خروجی: bg_welcome.bmp / bg_dir.bmp / bg_install.bmp / bg_finish.bmp
       + bg_uninstall.bmp / bg_uninstall_progress.bmp / bg_uninstall_finish.bmp
       (منوی حذف‌کننده‌ی مستقل)
ابعاد ثابت: 960×600 (با اندازه‌ی پنجره‌ی نصب‌کننده در installer.nsi هماهنگ است).

اجرا در CI:
    python installer/make_graphics.py --version 2.0.0 --outdir installer/graphics \\
        --font installer/graphics/Vazirmatn-Bold.ttf --font-regular installer/graphics/Vazirmatn-Regular.ttf
برای پیش‌نمایش محلی: --preview-png هم PNG می‌سازد تا دیده شود.
"""

import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 960, 600

# پالت «ایمن آرا سورنا» (همان theme.py)
BG_TOP = (27, 34, 39)
BG_BOTTOM = (13, 18, 21)
CARD = (36, 46, 52)
BLUE = (15, 124, 193)
BLUE_LIGHT = (42, 155, 216)
GOLD = (212, 175, 55)
RED = (200, 70, 70)
RED_DARK = (120, 30, 30)
TEXT = (233, 238, 241)
MUTED = (155, 151, 140)
WHITE = (255, 255, 255)


def fa(text):
    """شکل‌دهی فارسی برای رندر صحیح داخل تصویر (فقط وقتی raqm نداریم)."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(text))


try:
    from PIL import features as _pil_features
    _HAS_RAQM = _pil_features.check("raqm")
except Exception:
    _HAS_RAQM = False


def load_font(path, size, fallback_bold=True):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        # fallback سیستمی (فقط برای اینکه اسکریپت نترکد؛ فارسی ندارد)
        return ImageFont.load_default()


def gradient_bg():
    img = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        c = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)
    return img


def add_glow(img):
    """هاله‌ی آبی ملایم گوشه‌ی بالا-چپ + خطوط تزئینی محو."""
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-260, -260, 420, 420], fill=(18, 80, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img = Image.blend(img, glow, 0.28)
    draw = ImageDraw.Draw(img, "RGBA")
    # خطوط مورب تزئینی پایین-راست
    for i in range(4):
        x0 = W - 320 + i * 46
        draw.line([(x0, H - 190), (x0 + 120, H - 40)], fill=(15, 124, 193, 26), width=10)
    return img


def rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def text_r(draw, xy, s, font, fill, anchor="ra"):
    """متن فارسی (راست‌چین) در مختصات داده‌شده.

    اگر Pillow با raqm ساخته شده باشد، خود raqm شکل‌دهی و bidi را انجام
    می‌دهد پس متن منطقی (logical) با direction='rtl' داده می‌شود؛ در غیر
    این صورت از fa() (ترتیب دیداری آماده) استفاده می‌شود.
    """
    if _HAS_RAQM:
        draw.text(xy, s, font=font, fill=fill, anchor=anchor,
                  direction="rtl", language="fa")
    else:
        draw.text(xy, fa(s), font=font, fill=fill, anchor=anchor)


def text_size(draw, s, font):
    """پهنای متن فارسی برای محاسبه‌ی قاب‌ها."""
    if _HAS_RAQM:
        bb = draw.textbbox((0, 0), s, font=font, direction="rtl", language="fa")
    else:
        bb = draw.textbbox((0, 0), fa(s), font=font)
    return bb[2] - bb[0]


def text_c(draw, xy, s, font, fill):
    """متن فارسی وسط‌چین."""
    text_r(draw, xy, s, font, fill, anchor="mm")


def draw_check(draw, cx, cy, size, color, width=5):
    """تیک تأیید با خط (بدون نیاز به گلیف فونت)."""
    draw.line([(cx - size * 0.45, cy + size * 0.05),
               (cx - size * 0.1, cy + size * 0.4)], fill=color, width=width,
              joint="curve")
    draw.line([(cx - size * 0.1, cy + size * 0.4),
               (cx + size * 0.5, cy - size * 0.35)], fill=color, width=width,
              joint="curve")


def header_band(img, title, subtitle, logo_path, version=None,
                font_bold=None, font_reg=None):
    """نوار هدر مشترک صفحات داخلی: لوگوی سپر + عنوان، تراز عمودی وسط."""
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle([0, 0, W, 118], fill=(16, 22, 26, 255))
    draw.line([(0, 118), (W, 118)], fill=BLUE + (255,), width=2)
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((88, 88), Image.LANCZOS)
        lx = W - 28 - logo.width
        img.paste(logo, (lx, (118 - logo.height) // 2), logo)
    except Exception:
        lx = W - 28
    tx = lx - 24
    text_r(draw, (tx, 24), title, font_bold, WHITE)
    text_r(draw, (tx, 68), subtitle, font_reg, MUTED)
    if version:
        pill_s = "نسخه‌ی " + version
        pw = text_size(draw, pill_s, font_reg) + 36
        draw.rounded_rectangle([24, 40, 24 + pw, 78], radius=19, fill=BLUE)
        text_c(draw, (24 + pw / 2, 59), pill_s, font_reg, WHITE)
    return img


def footer_strip(img):
    """نوار فوتر پایین کادر (y=540..600): لنگر بصری دکمه‌های واقعی NSIS
    که در y=546 قرار می‌گیرند."""
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle([0, 540, W, 600], fill=(13, 18, 22, 255))
    draw.line([(0, 540), (W, 540)], fill=GOLD + (255,), width=2)
    return img


def screen_welcome(logo_full, logo_shield, version, fb, fr):
    img = add_glow(gradient_bg())
    draw = ImageDraw.Draw(img, "RGBA")
    # --- بلوک برند بالا-راست: سپر تمیز + تیتر (تراز عمودی با هم) ---
    try:
        logo = Image.open(logo_shield).convert("RGBA")
        logo.thumbnail((128, 128), Image.LANCZOS)
        lx = W - 48 - logo.width
        img.paste(logo, (lx, 52), logo)
    except Exception:
        lx = W - 48
    tx = lx - 30
    text_r(draw, (tx, 62), "سورنا", fb, WHITE)
    # نشان نسخه دقیقاً زیر تیتر، هم‌تراز با لبه‌ی راست
    pill_s = "نسخه‌ی " + version
    pw = text_size(draw, pill_s, fr) + 40
    draw.rounded_rectangle([tx - pw, 126, tx, 166], radius=20, fill=BLUE)
    text_c(draw, (tx - pw / 2, 146), pill_s, fr, WHITE)
    text_r(draw, (tx, 188),
           "سامانه‌ی هوشمند مدیریت تصاویر مداربسته", fr, MUTED)
    # خط تزئینی آبی زیر توضیح
    draw.rounded_rectangle([tx - 190, 228, tx, 234], radius=3, fill=BLUE)

    # کارت ویژگی‌ها سمت چپ
    feats = [
        "تشخیص چهره و ردیابی اشخاص",
        "پلاک‌خوان فارسی",
        "اعلام حریق هوشمند",
        "نقشه‌ی ساختمان و کنترل تردد",
        "بدون نیاز به نصب هیچ‌چیز",
    ]
    cx0, cy0, cx1 = 40, 64, 380
    card_h = 44 * len(feats) + 96
    rounded(draw, [cx0, cy0, cx1, cy0 + card_h], 18, CARD + (235,))
    draw.line([(cx0, cy0), (cx0, cy0 + card_h)], fill=BLUE + (255,), width=4)
    text_r(draw, (cx1 - 24, cy0 + 30), "چرا سورنا؟", fb, WHITE)
    yy = cy0 + 78
    for f_ in feats:
        draw.ellipse([cx1 - 44, yy + 6, cx1 - 24, yy + 26], fill=(46, 160, 67))
        draw_check(draw, cx1 - 34, yy + 16, 16, WHITE, 3)
        text_r(draw, (cx1 - 56, yy + 4), f_, fr, TEXT)
        yy += 44
    # راهنمای بالای نوار فوتر (دکمه‌های واقعی در y=546 روی نوار فوتر می‌نشینند)
    text_r(draw, (W - 48, 492),
           "برای ادامه، «شروع نصب» را بزنید.", fr, MUTED)
    return footer_strip(img)


def screen_dir(logo_shield, version, fb, fr):
    img = add_glow(gradient_bg())
    img = header_band(img, "انتخاب پوشه‌ی نصب",
                      "ایمن آرا سورنا | IMENARA SORENA", logo_shield, version, fb, fr)
    draw = ImageDraw.Draw(img, "RGBA")
    text_r(draw, (W - 60, 190), "برنامه در کدام پوشه نصب شود؟", fb, WHITE)
    text_r(draw, (W - 60, 232),
           "می‌توانید پوشه‌ی پیشنهادی را بپذیرید یا پوشه‌ی دیگری انتخاب کنید.",
           fr, MUTED)
    # قاب محل کنترل‌های انتخاب مسیر (کنترل‌های واقعی NSIS روی همین ناحیه می‌نشینند)
    draw.rounded_rectangle([60, 300, W - 60, 372], radius=12,
                           outline=(58, 75, 82, 255), width=2)
    text_r(draw, (W - 60, 400),
           "حداقل ۲ گیگابایت فضای خالی لازم است. میان‌برها در منوی استارت و دسکتاپ ساخته می‌شوند.",
           fr, MUTED)
    return footer_strip(img)


def screen_install(logo_shield, version, fb, fr):
    img = add_glow(gradient_bg())
    img = header_band(img, "در حال نصب",
                      "ایمن آرا سورنا | IMENARA SORENA", logo_shield, version, fb, fr)
    draw = ImageDraw.Draw(img, "RGBA")
    text_r(draw, (W - 60, 190), "در حال کپی فایل‌ها…", fb, WHITE)
    text_r(draw, (W - 60, 232),
           "لطفاً تا پایان نصب صبر کنید و پنجره را نبندید.", fr, MUTED)
    # قاب نوار پیشرفت (نوار واقعی NSIS روی همین ناحیه می‌نشیند)
    draw.rounded_rectangle([60, 300, W - 60, 344], radius=17,
                           outline=(58, 75, 82, 255), width=2, fill=(20, 27, 32, 255))
    # باکس یکدست برای متن وضعیت (لیبل NSIS دقیقاً همین‌جا با همین رنگ می‌نشیند)
    draw.rounded_rectangle([60, 358, W - 60, 402], radius=14, fill=(20, 27, 32, 255))
    tips = [
        "نکته: بعد از نصب می‌توانید با «فایل آپدیت»، نسخه‌های جدید را بدون نصب مجدد اعمال کنید.",
    ]
    yy = 440
    for t in tips:
        rounded(draw, [60, yy, W - 60, yy + 56], 12, CARD + (200,))
        text_r(draw, (W - 84, yy + 14), t, fr, TEXT)
        yy += 68
    return footer_strip(img)


def screen_finish(logo_shield, version, fb, fr):
    img = add_glow(gradient_bg())
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        logo = Image.open(logo_shield).convert("RGBA")
        logo.thumbnail((150, 150), Image.LANCZOS)
        img.paste(logo, ((W - logo.width) // 2, 56), logo)
    except Exception:
        pass
    # تیک موفقیت
    cx, cy, r = W // 2, 330, 44
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(46, 160, 67))
    draw_check(draw, cx, cy, 44, WHITE, 7)
    text_c(draw, (cx, 410), "نصب با موفقیت انجام شد", fb, WHITE)
    text_c(draw, (cx, 452),
           "سورنا نسخه " + version + " آماده‌ی استفاده است.", fr, MUTED)
    return footer_strip(img)


def screen_uninstall(logo_shield, version, fb, fr):
    """منوی حذف‌کننده‌ی مستقل: چه چیزهایی برای همیشه پاک می‌شوند."""
    img = add_glow(gradient_bg())
    draw = ImageDraw.Draw(img, "RGBA")
    # بلوک برند بالا-راست با لهجه‌ی قرمز
    try:
        logo = Image.open(logo_shield).convert("RGBA")
        logo.thumbnail((128, 128), Image.LANCZOS)
        lx = W - 48 - logo.width
        img.paste(logo, (lx, 52), logo)
    except Exception:
        lx = W - 48
    tx = lx - 30
    text_r(draw, (tx, 62), "حذف کامل سورنا", fb, WHITE)
    pill_s = "نسخه‌ی " + version
    pw = text_size(draw, pill_s, fr) + 40
    draw.rounded_rectangle([tx - pw, 126, tx, 166], radius=20, fill=RED_DARK)
    text_c(draw, (tx - pw / 2, 146), pill_s, fr, WHITE)
    text_r(draw, (tx, 188),
           "بعد از حذف، هیچ اثری روی سیستم نمی‌ماند", fr, MUTED)
    draw.rounded_rectangle([tx - 190, 228, tx, 234], radius=3, fill=RED)

    # کارت موارد حذفی سمت چپ
    items = [
        "فایل‌ها و پوشه‌ی نصب برنامه",
        "تنظیمات و دوربین‌ها",
        "بانک چهره و سوابق پلاک‌ها",
        "کلیدهای رجیستری و میان‌برها",
        "پوشه‌های داده در AppData",
        "فایل‌های موقت",
    ]
    cx0, cy0, cx1 = 40, 64, 400
    card_h = 44 * len(items) + 96
    rounded(draw, [cx0, cy0, cx1, cy0 + card_h], 18, CARD + (235,))
    draw.line([(cx0, cy0), (cx0, cy0 + card_h)], fill=RED + (255,), width=4)
    text_r(draw, (cx1 - 24, cy0 + 30), "این موارد پاک می‌شوند:", fb, WHITE)
    yy = cy0 + 78
    for it in items:
        draw.ellipse([cx1 - 44, yy + 6, cx1 - 24, yy + 26], fill=RED)
        # ضربدر داخل دایره
        draw.line([(cx1 - 39, yy + 11), (cx1 - 29, yy + 21)], fill=WHITE, width=3)
        draw.line([(cx1 - 29, yy + 11), (cx1 - 39, yy + 21)], fill=WHITE, width=3)
        text_r(draw, (cx1 - 56, yy + 4), it, fr, TEXT)
        yy += 44
    # راهنمای بالای نوار فوتر (دکمه‌های واقعی در y=546 روی نوار فوتر می‌نشینند)
    text_r(draw, (W - 48, 492),
           "برای شروع، «حذف کامل» را بزنید. این عمل غیرقابل بازگشت است.", fr, MUTED)
    return footer_strip(img)


def screen_uninstall_progress(logo_shield, version, fb, fr):
    img = add_glow(gradient_bg())
    img = header_band(img, "در حال حذف کامل",
                      "سورنا | SORENA", logo_shield, version, fb, fr)
    draw = ImageDraw.Draw(img, "RGBA")
    text_r(draw, (W - 60, 190), "در حال پاک‌سازی…", fb, WHITE)
    text_r(draw, (W - 60, 232),
           "لطفاً تا پایان حذف صبر کنید و پنجره را نبندید.", fr, MUTED)
    draw.rounded_rectangle([60, 300, W - 60, 344], radius=17,
                           outline=(58, 75, 82, 255), width=2, fill=(20, 27, 32, 255))
    draw.rounded_rectangle([60, 358, W - 60, 402], radius=14, fill=(20, 27, 32, 255))
    return footer_strip(img)


def screen_uninstall_finish(logo_shield, version, fb, fr):
    img = add_glow(gradient_bg())
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        logo = Image.open(logo_shield).convert("RGBA")
        logo.thumbnail((150, 150), Image.LANCZOS)
        img.paste(logo, ((W - logo.width) // 2, 56), logo)
    except Exception:
        pass
    cx, cy, r = W // 2, 330, 44
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(46, 160, 67))
    draw_check(draw, cx, cy, 44, WHITE, 7)
    text_c(draw, (cx, 410), "حذف کامل انجام شد", fb, WHITE)
    text_c(draw, (cx, 452),
           "هیچ اثری از سورنا روی سیستم باقی نماند.", fr, MUTED)
    return footer_strip(img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--font", required=True, help="فونت ضخیم (Vazirmatn-Bold)")
    ap.add_argument("--font-regular", required=True, help="فونت معمولی")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--preview-png", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    fb = load_font(args.font, 34)
    fr = load_font(args.font_regular, 22)
    fb_big = load_font(args.font, 44)

    logo_full = os.path.join(args.assets, "logo_full.png")
    logo_shield = os.path.join(args.assets, "logo_shield.png")

    screens = {
        "bg_welcome": screen_welcome(logo_full, logo_shield, args.version, fb_big, fr),
        "bg_dir": screen_dir(logo_shield, args.version, fb, fr),
        "bg_install": screen_install(logo_shield, args.version, fb, fr),
        "bg_finish": screen_finish(logo_shield, args.version, fb, fr),
        "bg_uninstall": screen_uninstall(logo_shield, args.version, fb, fr),
        "bg_uninstall_progress": screen_uninstall_progress(logo_shield, args.version, fb, fr),
        "bg_uninstall_finish": screen_uninstall_finish(logo_shield, args.version, fb, fr),
    }
    for name, img in screens.items():
        out = os.path.join(args.outdir, name + ".bmp")
        img.save(out, "BMP")
        print("wrote", out, img.size)
        if args.preview_png:
            img.save(os.path.join(args.outdir, name + ".png"), "PNG")

    # (2.0.38-beta) تصاویر استاندارد MUI2 از روی لوگوی برنامه:
    # welcome_logo.bmp (164×314) برای صفحه‌ی خوش‌آمد و header_logo.bmp
    # (150×57) برای هدر صفحات — جایگزین تصویر پیش‌فرض NSIS.
    if os.path.exists(logo_shield):
        shield = Image.open(logo_shield).convert("RGBA")
        welcome = Image.new("RGB", (164, 314), (255, 255, 255))
        sw = 120
        sh = int(sw * shield.height / shield.width)
        s = shield.resize((sw, sh), Image.LANCZOS)
        welcome.paste(s, ((164 - sw) // 2, 60), s)
        welcome.save(os.path.join(args.outdir, "welcome_logo.bmp"), "BMP")
        header = Image.new("RGB", (150, 57), (255, 255, 255))
        hw = 44
        hh = int(hw * shield.height / shield.width)
        h = shield.resize((hw, hh), Image.LANCZOS)
        header.paste(h, ((150 - hw) // 2, (57 - hh) // 2), h)
        header.save(os.path.join(args.outdir, "header_logo.bmp"), "BMP")
        print("wrote", os.path.join(args.outdir, "welcome_logo.bmp"), "(164, 314)")
        print("wrote", os.path.join(args.outdir, "header_logo.bmp"), "(150, 57)")


if __name__ == "__main__":
    main()

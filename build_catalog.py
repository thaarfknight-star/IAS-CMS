# -*- coding: utf-8 -*-
"""ساخت کاتالوگ آموزشی PDF نرم‌افزار «ایمن آرا سورنا» (IAS-CMS).

قالب تیره‌ی هم‌خون با خود برنامه، کاملاً فارسی و راست‌به‌چپ، با فونت
B Nazanin. خروجی: کاتالوگ آموزشی هر صفحه/بخش برنامه.
"""
import os
import re as _re
import arabic_reshaper
from bidi.algorithm import get_display

from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Image,
    Table, TableStyle, PageBreak, KeepTogether, NextPageTemplate,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase.pdfmetrics import stringWidth


class FaParagraph(Paragraph):
    """پاراگراف فارسی راست‌به‌چپ.

    متن ورودی از قبل reshape و bidi شده (ترتیب بصری) است؛ reportlab
    خطوط را از ابتدای رشته‌ی بصری می‌شکند، پس در پاراگراف چندخطی
    خط اول = پایان جمله می‌شود. این کلاس ترتیب خطوط را معکوس می‌کند
    تا خط اول = آغاز جمله باشد. چون هر wrap از نو می‌شکند، همیشه درست است.
    """

    def wrap(self, availWidth, availHeight):
        w, h = super().wrap(availWidth, availHeight)
        self.blPara.lines = list(reversed(self.blPara.lines))
        return w, h


class FaTOC(TableOfContents):
    """فهرست فارسی راست‌به‌چپ.

    مکانیزم نقطه‌چین/شماره‌صفحه‌ی داخلی reportlab برای پاراگراف
    راست‌به‌چپ درست کار نمی‌کند (شماره را با فونت ~۱pt در لبه می‌کشد)،
    پس نقطه‌چین و شماره صفحه فارسی داخل خود متن ورودی جاسازی می‌شود
    و این کلاس فقط همان متن‌ها را مثل نسخه اصلی جدول می‌کند.
    """

    def wrap(self, availWidth, availHeight):
        # مثل نسخه اصلی: ورودی‌های پاس قبلی رسم می‌شوند تا همگرا شود
        entries = self._lastEntries or [(0, "", 0, None)]
        data = []
        for (level, text, _page, _key) in entries:
            style = self.getLevelStyle(level)
            if style.spaceBefore:
                data.append([Spacer(1, style.spaceBefore)])
            data.append([FaParagraph(text, style)])
        t = Table(data, colWidths=(availWidth,), style=self.tableStyle)
        self._table = t
        self.width, self.height = t.wrapOn(self.canv, availWidth, availHeight)
        return (self.width, self.height)

W, H = A4

# ------------------------------------------------------------ مسیرها ---
HOME = os.path.expanduser("~")
FONT = os.path.join(HOME, "workspace/user/files/BNazanin.ttf")
LOGO_FULL = os.path.join(
    HOME, "workspace/your_files/ias-cms-header-pages/assets/logo_full.png")
LOGO_SHIELD = os.path.join(
    HOME, "workspace/your_files/ias-cms-header-pages/assets/logo_shield.png")
SHOT_PERSON = os.path.join(
    HOME, "workspace/user/media_library/image/73/"
    "730dd13e5ae6dae04ef96a22cf1bfc85b69fa0c576f454a1ff769f2d72c0eded.png")
SHOT_PANEL = os.path.join(
    HOME, "workspace/user/media_library/image/40/"
    "4008a26afd247604de71749501e5a41ab8384c559983e0ff913c82c3631d2b39.png")
OUT = os.path.join(HOME, "workspace/your_files/ias-cms-catalog-imen-ara-sorena.pdf")

# ------------------------------------------------------------ رنگ‌ها ---
BG = HexColor("#10161c")
CARD = HexColor("#182430")
CARD2 = HexColor("#1d2c3a")
CYAN = HexColor("#22d3ee")
BLUE = HexColor("#0f7cc1")
GOLD = HexColor("#e8b64c")
GREEN = HexColor("#4ade80")
RED = HexColor("#f87171")
YELLOW = HexColor("#fbbf24")
TXT = HexColor("#e9eff5")
MUTED = HexColor("#9fb0bf")
LINE = HexColor("#2b3d4f")

pdfmetrics.registerFont(TTFont("BNazanin", FONT))
# فونت کمکی برای حروف لاتین/اعداد انگلیسی (در B Nazanin وجود ندارند)
pdfmetrics.registerFont(TTFont(
    "DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))


def fa(s):
    """آماده‌سازی متن فارسی برای reportlab (شکل‌دهی + جهت + فونت لاتین)."""
    v = get_display(arabic_reshaper.reshape(str(s)))
    # B Nazanin حروف لاتین ندارد؛ آن تکه‌ها با DejaVu رسم می‌شوند
    return _re.sub(r"[A-Za-z0-9][A-Za-z0-9 /]*",
                   r'<font name="DejaVu">\g<0></font>', v)


def fa_plain(s):
    """نسخه بدون تگ فونت، برای رسم مستقیم روی canvas."""
    return get_display(arabic_reshaper.reshape(str(s)))


_FA_D = "۰۱۲۳۴۵۶۷۸۹"


def fan(n):
    """اعداد انگلیسی به فارسی."""
    return "".join(_FA_D[int(d)] for d in str(n))


# ------------------------------------------------------------ استایل‌ها ---
sTitle = ParagraphStyle("Title", fontName="BNazanin", fontSize=30,
                        leading=40, alignment=TA_CENTER, textColor=TXT)
sSub = ParagraphStyle("Sub", fontName="BNazanin", fontSize=17, leading=26,
                      alignment=TA_CENTER, textColor=MUTED)
sH1 = ParagraphStyle("H1", fontName="BNazanin", fontSize=19, leading=28,
                     alignment=TA_RIGHT, textColor=GOLD)
sH2 = ParagraphStyle("H2", fontName="BNazanin", fontSize=15, leading=24,
                     alignment=TA_RIGHT, textColor=CYAN)
sBody = ParagraphStyle("Body", fontName="BNazanin", fontSize=13.5, leading=23,
                       alignment=TA_RIGHT, textColor=TXT, spaceAfter=6)
sBullet = ParagraphStyle("Bullet", parent=sBody, firstLineIndent=0,
                         rightIndent=14, spaceAfter=4,
                         bulletFontName="BNazanin")
sTip = ParagraphStyle("Tip", parent=sBody, fontSize=12.5, leading=21,
                      textColor=TXT)
sCap = ParagraphStyle("Cap", fontName="BNazanin", fontSize=11, leading=16,
                      alignment=TA_CENTER, textColor=MUTED)
sToc1 = ParagraphStyle("Toc1", fontName="BNazanin", fontSize=14, leading=24,
                       alignment=TA_RIGHT, textColor=TXT)
sToc2 = ParagraphStyle("Toc2", parent=sToc1, fontSize=12.5, textColor=MUTED,
                       rightIndent=18)


def P(text, style=sBody):
    return FaParagraph(fa(text), style)


def bullet(text):
    return FaParagraph(fa("»\u00a0" + text), sBullet)


def section_head(num, title):
    """سربرگ بخش: نوار رنگی + شماره و عنوان."""
    t = Table([
        [P(f"{num}. {title}", sH1),
         Paragraph("", ParagraphStyle("bar", fontName="BNazanin", fontSize=19))]
    ], colWidths=[W - 110, 14])
    t.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), CYAN),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (1, 0), (1, 0), 0),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def sub_head(text):
    return P("»\u00a0" + text, sH2)


def tip_box(text):
    inner = [[P("نکته: " + text, sTip)]]
    t = Table(inner, colWidths=[W - 110])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD2),
        ("BOX", (0, 0), (-1, -1), 1, CYAN),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    return t


def warn_box(text):
    inner = [[P("توجه: " + text, sTip)]]
    t = Table(inner, colWidths=[W - 110])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#2c2118")),
        ("BOX", (0, 0), (-1, -1), 1, YELLOW),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    return t


def img(path, width=None, height=None, caption=None):
    im = Image(path)
    iw, ih = im.imageWidth, im.imageHeight
    if width and not height:
        scale = width / iw
        im.drawWidth, im.drawHeight = width, ih * scale
    elif height and not width:
        scale = height / ih
        im.drawWidth, im.drawHeight = iw * scale, height
    items = [im]
    if caption:
        items += [Spacer(1, 4), P(caption, sCap)]
    box = Table([[items]], colWidths=[W - 110])
    box.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 1, LINE),
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return box


# ------------------------------------------------------------ سند ---
class CatalogDoc(BaseDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, pagesize=A4,
                         leftMargin=35, rightMargin=35,
                         topMargin=45, bottomMargin=50, **kw)
        frame = Frame(self.leftMargin, self.bottomMargin,
                      self.width, self.height, id="main")
        self.toc_entries = []
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame], onPage=self._cover_page),
            PageTemplate(id="inner", frames=[frame], onPage=self._inner_page),
        ])

    # -- صفحه‌آرایی --
    def _bg(self, c):
        c.saveState()
        c.setFillColor(BG)
        c.rect(0, 0, W, H, stroke=0, fill=1)
        c.restoreState()

    def _cover_page(self, c, doc):
        self._bg(c)

    def _inner_page(self, c, doc):
        self._bg(c)
        c.saveState()
        # پاصفحه
        c.setStrokeColor(LINE)
        c.setLineWidth(1)
        c.line(35, 38, W - 35, 38)
        try:
            c.drawImage(LOGO_SHIELD, 35, 14, width=16, height=24,
                        preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
        c.setFont("BNazanin", 10)
        c.setFillColor(MUTED)
        c.drawRightString(W - 35, 20, fa_plain("ایمن آرا سورنا"))
        c.drawString(58, 20, fa_plain(f"صفحه {fan(doc.page)}"))
        c.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ("H1", "H2"):
            level = 0 if flowable.style.name == "H1" else 1
            raw = getattr(flowable, "_raw", None) or flowable.text
            if raw == fa("فهرست"):  # سربرگ خود صفحه‌ی فهرست داخل فهرست نمی‌آید
                return
            # نقطه‌چین و شماره صفحه فارسی داخل خود ورودی (راست‌به‌چپ سالم)
            num = fan(self.page)
            size = 14 if level == 0 else 12.5
            plain = _re.sub(r"<[^>]+>", "", raw)
            avail = W - 70 - (18 if level == 1 else 0)
            tw = stringWidth(plain, "BNazanin", size)
            nw = stringWidth(num, "BNazanin", size)
            dw = stringWidth(".", "BNazanin", size)
            n = max(3, int((avail - tw - nw - 24) / dw))
            entry = "%s %s %s" % (raw, "." * n, num)
            # مکانیزم استاندارد فهرست reportlab (همگرایی چندپاسه)
            self.notify("TOCEntry", (level, entry, self.page))


def HP(text, style):
    """پاراگراف عنوان که متن خامش برای فهرست نگه داشته می‌شود."""
    p = FaParagraph(fa(text), style)
    p._raw = fa(text)
    return p


# ------------------------------------------------------------ محتوا ---
def build():
    doc = CatalogDoc(OUT, title=fa("کاتالوگ آموزشی ایمن آرا سورنا"),
                     author=fa("ایمن آرا سورنا"))
    story = []
    toc = FaTOC()
    toc.levelStyles = [sToc1, sToc2]

    # ================= جلد =================
    story.append(Spacer(1, 70))
    story.append(img(LOGO_FULL, width=380))
    story.append(Spacer(1, 30))
    story.append(P("کاتالوگ آموزشی نرم‌افزار", sTitle))
    story.append(Spacer(1, 6))
    story.append(P("سامانه یکپارچه مدیریت نظارت تصویری", sSub))
    story.append(Spacer(1, 24))
    feats = ["پخش زنده دوربین‌ها", "اعلام حریق هوشمند", "تشخیص چهره",
             "پلاک‌خوان فارسی", "ردیابی اشخاص بین دوربین‌ها", "نقشه تعاملی ساختمان"]
    feat_cells = [[P("»\u00a0" + f, ParagraphStyle(
        "feat", parent=sBody, alignment=TA_CENTER, textColor=CYAN))]
        for f in feats]
    ft = Table([[c] for c in feat_cells], colWidths=[W - 110])
    ft.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(ft)
    story.append(Spacer(1, 40))
    story.append(P("ویراست شهریور ۱۴۰۵", ParagraphStyle(
        "ver", parent=sSub, fontSize=13, textColor=GOLD)))
    story.append(PageBreak())
    story.append(NextPageTemplate("inner"))

    # ================= فهرست =================
    story.append(HP("فهرست", sH1))
    story.append(Spacer(1, 12))
    story.append(toc)
    story.append(PageBreak())

    # ================= ۱. معرفی =================
    story.append(HP("۱. معرفی سامانه", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "«ایمن آرا سورنا» یک سامانه یکپارچه مدیریت نظارت تصویری (CMS) است؛ "
        "یعنی به‌جای چند نرم‌افزار جدا، پخش زنده دوربین‌ها، اعلام حریق، تشخیص "
        "چهره، پلاک‌خوان، ردیابی اشخاص و نقشه ساختمان را در یک برنامه فارسی "
        "و یکدست کنار هم دارید."))
    story.append(P(
        "نوار بالای برنامه همیشه در دسترس است و با یک کلیک بین هفت صفحه "
        "اصلی جابه‌جا می‌شوید: صفحه اصلی، اعلام حریق، چهره‌ها، گزارش‌ها، "
        "پلاک‌خوان، ردیابی اشخاص و نقشه ساختمان."))
    story.append(tip_box(
        "هیچ نصب جداگانه‌ای لازم نیست؛ همه موتورها و مدل‌ها داخل خود برنامه "
        "قرار دارند و روی سیستم‌های ضعیف (۴ گیگ رم، بدون کارت گرافیک) هم با "
        "«حالت کم‌رم» خودکار روان کار می‌کند."))

    # ================= ۲. صفحه اصلی =================
    story.append(HP("۲. صفحه اصلی - پخش زنده دوربین‌ها", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "قلب برنامه اینجاست: شبکه‌ای از کادرهای پخش زنده که هر دوربین یا "
        "کانال NVR در یک خانه نمایش داده می‌شود."))
    story.append(sub_head("افزودن دوربین"))
    for b in ["از پنل کناری، «افزودن دوربین» یا «افزودن NVR» را بزنید.",
              "آدرس IP، نام کاربری و رمز را وارد کنید؛ برنامه خودش مسیر "
              "ارتباط (ONVIF/RTSP) را پیدا می‌کند.",
              "برای NVR، کانال‌هایش یک‌جا اضافه می‌شوند و می‌توانید هر کدام "
              "را در یک خانه باز کنید."]:
        story.append(bullet(b))
    story.append(sub_head("کار با هر کادر"))
    for b in ["شمارش افراد: عدد بالای هر کادر، تعداد افراد حاضر در تصویر را "
              "لحظه‌ای نشان می‌دهد.",
              "بزرگ‌نمایی مستقل: با اسکرول روی هر کادر، از ۱ تا ۸ برابر زوم "
              "می‌کنید؛ زوم هر دوربین از بقیه جداست.",
              "تنظیمات تصویر هر دوربین: روشنایی، کنتراست، WDR نرم‌افزاری و "
              "ضد مه - بدون دست‌کاری خود دوربین.",
              "دابل‌کلیک روی یک کادر، آن را تمام‌صفحه می‌کند؛ دابل‌کلیک "
              "دوباره برمی‌گرداند.",
              "کادر آبی دور بدن افراد و کادر سبز/قرمز دور چهره‌ها همیشه روی "
              "تصویر رسم می‌شود تا ببینید تشخیص در حال کار است."]:
        story.append(bullet(b))

    # ================= ۳. اعلام حریق =================
    story.append(HP("۳. اعلام حریق هوشمند", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "برنامه با هوش مصنوعی، دود و شعله را مستقیم از تصویر دوربین‌ها تشخیص "
        "می‌دهد - حتی شعله‌های کوچک - و آژیر می‌کشد."))
    story.append(sub_head("تنظیم حساسیت"))
    for b in ["در صفحه اعلام حریق، برای هر دوربین سطح حساسیت را انتخاب کنید: "
              "کم، متوسط یا زیاد.",
              "تشخیص چند فریم پیاپی تأیید می‌شود تا هشدار اشتباه (مثلاً "
              "انعکاس نور) آژیر نکشد."]:
        story.append(bullet(b))
    story.append(sub_head("خروجی‌های هشدار"))
    for b in ["آژیر صوتی داخل برنامه با صدای استاندارد دودود.",
              "اتصال به تابلوی اعلام حریق ساختمان (شبیه‌ساز، وب‌هوک یا رله).",
              "ثبت خودکار هر رویداد در صفحه گزارش‌ها با ساعت دقیق."]:
        story.append(bullet(b))

    # ================= ۴. چهره‌ها =================
    story.append(HP("۴. بانک چهره‌ها", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "در این صفحه چهره افراد آشنا را یک‌بار ثبت می‌کنید تا برنامه هر جا "
        "آن‌ها را دید، با نام اعلام کند."))
    for b in ["«ثبت چهره از تصویر زنده»: یک دوربین باز را انتخاب کنید، وقتی "
              "شخص روبه‌روی دوربین است دکمه ثبت را بزنید و نامش را وارد کنید.",
              "«ثبت از فایل»: یک عکس پرتره واضح هم کافی است.",
              "چهره‌های شناخته‌شده با کادر سبز و نام، و چهره‌های ناشناس با "
              "کادر قرمز روی تصویر مشخص می‌شوند.",
              "نام چهره‌های شناخته‌شده در ردیابی اشخاص و روی نقشه ساختمان هم "
              "نمایش داده می‌شود."]:
        story.append(bullet(b))
    story.append(tip_box(
        "هرچه عکس ثبت‌شده روشن‌تر و روبه‌روتر باشد، شناسایی در نورهای مختلف "
        "دقیق‌تر می‌شود."))

    # ================= ۵. گزارش‌ها =================
    story.append(HP("۵. گزارش‌ها", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "همه رویدادهای مهم برنامه با تاریخ شمسی و ساعت دقیق اینجاست و قابل "
        "جست‌وجو و خروجی گرفتن است:"))
    for b in ["رویدادهای چهره (شناخته‌شده/ناشناس) با تصویر لحظه عبور.",
              "هشدارهای حریق و دود.",
              "عبور پلاک‌ها با تصویر پلاک.",
              "تاریخچه شمارش افراد هر دوربین."]:
        story.append(bullet(b))

    # ================= ۶. پلاک‌خوان =================
    story.append(HP("۶. پلاک‌خوان فارسی", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "پلاک خودروهای ایرانی (سواری و موتورسیکلت) را از تصویر دوربین می‌خواند "
        "و عبورها را ثبت می‌کند."))
    for b in ["در تب «تعریف پلاک‌ها»، پلاک‌های مجاز را با نام مالک و توضیح "
              "ثبت کنید.",
              "برای هر دوربین مشخص کنید پلاک‌خوانش فعال باشد یا نه.",
              "در تب «گزارش عبور»، هر تردد با شماره پلاک، ساعت، دوربین و "
              "تصویر پلاک ثبت می‌شود؛ خروجی CSV هم دارد.",
              "پلاک‌های تعریف‌نشده با کادر نارنجی و تعریف‌شده‌ها با کادر سبز "
              "مشخص می‌شوند."]:
        story.append(bullet(b))

    # ================= ۷. ردیابی اشخاص =================
    story.append(HP("۷. ردیابی اشخاص بین دوربین‌ها", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "این صفحه مسیر حرکت هر شخص را بین دوربین‌های مختلف دنبال می‌کند؛ "
        "مثلاً ببینید یک فرد ساعت ۱۰ از دوربین ورودی رد شده و ساعت ۱۰:۱۵ "
        "جلوی دوربین انبار دیده شده است."))
    story.append(sub_head("فعال‌سازی"))
    for b in ["بالای صفحه، تیک «ردیابی اشخاص» را برای هر دوربینی که می‌خواهید "
              "روشن کنید.",
              "بنر بالای صفحه وضعیت موتور تشخیص را نشان می‌دهد؛ اگر خطایی "
              "باشد (مثلاً دوربینی باز نیست) همان‌جا با پیام مشخص می‌بینید.",
              "ردیابی فقط روی دوربین‌های باز و در حال پخش در «صفحه اصلی» "
              "انجام می‌شود."]:
        story.append(bullet(b))
    story.append(sub_head("جدول اشخاص و گزارش مسیر"))
    for b in ["هر شخص یک کد یکتا (P-0001 و ...) می‌گیرد؛ رنگ لباس، شلوار، مو و "
              "نام چهره (اگر شناخته‌شده باشد) در جدول دیده می‌شود.",
              "با انتخاب یک شخص و زدن «مشاهده مسیر حرکت»، خط زمانی عبورهایش "
              "از دوربین‌های مختلف نمایش داده می‌شود.",
              "دکمه «نمایش روی نقشه» مسیر را روی نقشه ساختمان می‌اندازد."]:
        story.append(bullet(b))
    story.append(sub_head("نمایش زنده روی نقشه"))
    for b in ["به‌محض ورود شخص به کادر دوربین، نشان زرد چشمک‌زن «در حال "
              "شناسایی» روی نقشه می‌نشیند.",
              "بعد از چند لحظه به نشان سبز با کد شخص تبدیل می‌شود؛ اگر چهره "
              "شناخته‌شده باشد، نامش هم کنار کد می‌آید.",
              "با خروج شخص از کادر، نشان برداشته می‌شود."]:
        story.append(bullet(b))
    story.append(sub_head("تنظیمات تطبیق ظاهری"))
    for b in ["آستانه تطبیق: هرچه کمتر، سخت‌گیرانه‌تر (پیش‌فرض ۰٫۵۰).",
              "پنجره اتصال (دقیقه): فاصله زمانی که دو دیده‌شدن جدا هنوز به "
              "یک شخص نسبت داده می‌شوند.",
              "فریم تأیید: تعداد دیده‌شدن پیاپی لازم برای ثبت نهایی شخص."]:
        story.append(bullet(b))
    story.append(img(SHOT_PERSON, width=480,
                     caption="صفحه ردیابی اشخاص: تیک دوربین‌ها، بنر وضعیت، جدول اشخاص و تنظیمات تطبیق"))
    story.append(Spacer(1, 6))
    story.append(warn_box(
        "برای چهره‌های ناشناس، تطبیق بر اساس ظاهر (لباس) است؛ اگر دو نفر "
        "لباس خیلی شبیه بپوشند ممکن است یکی حساب شوند. برای اطمینان کامل، "
        "چهره افراد مهم را در «بانک چهره‌ها» ثبت کنید."))

    # ================= ۸. نقشه ساختمان =================
    story.append(HP("۸. نقشه تعاملی ساختمان", sH1))
    story.append(Spacer(1, 8))
    story.append(P(
        "نقشه ساختمان را وارد کنید، طبقات را تعریف کنید و دوربین‌ها و تجهیزات "
        "را با درگ روی نقشه بچینید؛ بعد زاویه و پهنای دید هر دوربین را "
        "تنظیم کنید و قطاع دیدش را روی نقشه ببینید."))
    story.append(sub_head("ساخت نقشه"))
    for b in ["از «وارد کردن DXF» فایل نقشه اتوکد را بدهید؛ لایه‌ها، رنگ‌ها و "
              "مقیاس (متر/سانتی‌متر) خودکار خوانده می‌شود.",
              "برای هر طبقه یک نقشه جدا تعریف کنید و بین طبقات جابه‌جا شوید.",
              "از پالت سمت چپ، دوربین، NVR یا سنسور را بکشید و روی نقشه رها "
              "کنید."]:
        story.append(bullet(b))
    story.append(sub_head("تنظیم هر دوربین"))
    for b in ["تک‌کلیک روی دوربین: انتخاب می‌شود و پنل «مشخصات تجهیز» باز "
              "می‌شود - نام، اتصال به دوربین واقعی، زاویه دید و پهنای دید "
              "را تنظیم کنید. تغییر زاویه با اسلایدر یا عدد، خودکار ذخیره "
              "می‌شود.",
              "دابل‌کلیک روی دوربین: پنجره شناور پخش زنده همان دوربین باز "
              "می‌شود، بدون ترک نقشه.",
              "کشیدن دوربین با موس، جایش را روی نقشه عوض و ذخیره می‌کند."]:
        story.append(bullet(b))
    story.append(img(SHOT_PANEL, height=230,
                     caption="پنل «مشخصات تجهیز»: نام، اتصال، زاویه و پهنای دید دوربین انتخاب‌شده"))
    story.append(Spacer(1, 6))
    story.append(sub_head("مسیر اشخاص روی نقشه"))
    for b in ["از صفحه «ردیابی اشخاص» دکمه «نمایش روی نقشه» را بزنید تا مسیر "
              "حرکت شخص با خط زمانی روی نقشه پخش شود.",
              "نشان‌های زنده (زرد: در حال شناسایی / سبز: تأییدشده) روی جای "
              "دوربین‌ها چشمک می‌زنند."]:
        story.append(bullet(b))

    # ================= ۹. نکات =================
    story.append(HP("۹. نکات مهم", sH1))
    story.append(Spacer(1, 8))
    for b in ["پشتیبان‌گیری: فایل‌های cameras.json و persons.db کنار برنامه، "
              "تنظیمات و تاریخچه شما هستند؛ هر از گاهی کپی بگیرید.",
              "حالت کم‌رم: روی سیستم‌های ضعیف، برنامه خودکار سبک کار می‌کند تا "
              "پخش زنده روان بماند.",
              "تاریخ‌ها همه‌جا شمسی‌اند و ساعت‌ها دقیق ثبت می‌شوند.",
              "برای بهترین نتیجه تشخیص چهره و پلاک، دوربین را طوری تنظیم "
              "کنید که چهره/پلاک واضح و روبه‌رو در کادر باشد."]:
        story.append(bullet(b))
    story.append(Spacer(1, 12))
    story.append(tip_box(
        "سؤال یا مشکلی داشتید؟ از همان نصبی که برنامه را گرفته‌اید، آخرین "
        "نسخه را دریافت کنید؛ بیشتر بهبودها بدون هیچ نصبی، فقط با جایگزینی "
        "فایل‌ها اعمال می‌شوند."))

    # ---------- ساخت ----------
    # multiBuild برای اینکه فهرست، شماره صفحه‌های نهایی را بگیرد.
    doc.multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()

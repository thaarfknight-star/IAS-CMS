# -*- coding: utf-8 -*-
"""تست‌های 2.0.36-beta — نصب‌کننده با صفحات استاندارد ویزارد ویندوز (MUI2).

- installer.nsi در حالت نصب باید MUI2 را include کند و از صفحات استاندارد
  WELCOME / DIRECTORY / INSTFILES / FINISH استفاده کند (نه Page custom).
- زبان فارسی (Persian) باید فعال باشد.
- بررسی «برنامه در حال اجراست؟» باید هنگام ترک صفحه‌ی پوشه انجام شود.
- صفحه‌ی پایان باید تیک «اجرای برنامه» داشته باشد.
- حذف‌کننده‌ی مستقل (UNINSTALLER_ONLY) نباید دست بخورد: دکمه‌ی قرمز
  حذف کامل + بک‌گراندهای bg_uninstall*.bmp باید سر جایشان باشند.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NSI = REPO / "installer" / "installer.nsi"


def _read() -> str:
    return NSI.read_text(encoding="utf-8", errors="replace")


def _installer_part(src: str) -> str:
    """بخش نصب‌کننده = کل فایل منهای بلوک UNINSTALLER_ONLY."""
    idx = src.find("!ifdef UNINSTALLER_ONLY\n\nFunction ShowUninstMenu")
    assert idx != -1, "بلوک حذف‌کننده‌ی مستقل یافت نشد"
    return src[:idx]


def test_mui2_included_for_installer():
    src = _installer_part(_read())
    assert re.search(r'^\s*!include\s+"MUI2\.nsh"', src, re.M) is not None, \
        "نصب‌کننده باید MUI2.nsh را include کند"


def test_standard_wizard_pages():
    src = _installer_part(_read())
    for page in ("MUI_PAGE_WELCOME", "MUI_PAGE_DIRECTORY",
                 "MUI_PAGE_INSTFILES", "MUI_PAGE_FINISH"):
        assert f"!insertmacro {page}" in src, f"صفحه‌ی استاندارد {page} باید باشد"


def test_no_custom_installer_page():
    src = _installer_part(_read())
    assert "Page custom ShowMainPage" not in src, \
        "صفحه‌ی custom نصب‌کننده باید حذف شده باشد"
    for fn in ("Function ShowWelcome", "Function ShowDir", "Function ShowInstall",
               "Function DoInstall", "Function ShowFinish", "Function ShowMainPage"):
        assert fn not in src, f"{fn} (رابط سفارشی قدیمی) باید حذف شده باشد"


def test_persian_language():
    src = _installer_part(_read())
    # نام رسمی زبان فارسی در NSIS «Farsi» است؛ «Persian» فایل nlf ندارد
    # و بیلد را می‌شکاند (رفع‌شده در 2.0.37-beta).
    assert '!insertmacro MUI_LANGUAGE "Farsi"' in src, \
        "زبان فارسی باید فعال باشد"
    assert '"Persian"' not in src, \
        "نباید به Persian ارجاع داده شود (چنین فایل زبانی در NSIS نیست)"


def test_app_running_check_on_dir_leave():
    src = _installer_part(_read())
    assert "MUI_PAGE_CUSTOMFUNCTION_LEAVE DirLeave" in src
    assert "Function DirLeave" in src
    assert "CheckAppRunning" in src, "بررسی باز بودن برنامه باید حفظ شود"


def test_finish_page_runs_app():
    src = _installer_part(_read())
    assert 'MUI_FINISHPAGE_RUN "$INSTDIR\\${EXE_NAME}"' in src, \
        "صفحه‌ی پایان باید گزینه‌ی اجرای برنامه داشته باشد"


def test_installer_bg_bmps_not_used():
    src = _installer_part(_read())
    for bmp in ("bg_welcome.bmp", "bg_dir.bmp", "bg_install.bmp", "bg_finish.bmp"):
        assert bmp not in src, f"{bmp} دیگر در نصب‌کننده استفاده نمی‌شود"


def test_uninstaller_kept_intact():
    src = _read()
    # دکمه‌ی قرمز حذف کامل
    assert "Function MakeRedButton" in src
    assert 'Push "حذف کامل"' in src
    assert "Call MakeRedButton" in src
    # بک‌گراندهای حذف‌کننده
    for bmp in ("bg_uninstall.bmp", "bg_uninstall_progress.bmp", "bg_uninstall_finish.bmp"):
        assert bmp in src, f"{bmp} حذف‌کننده باید سر جایش باشد"
    # صفحه‌ی custom حذف‌کننده
    assert "Page custom ShowUninstMain" in src
    # بدنه‌ی پاک‌سازی کامل
    assert "!macro FULL_CLEANUP_BODY" in src
    assert "WriteUninstaller" in src


def test_install_section_has_files_and_shortcuts():
    src = _installer_part(_read())
    sec = src.find('Section "نصب"')
    assert sec != -1
    body = src[sec:src.find("SectionEnd", sec)]
    assert "installer\\files.nsi" in body, "کپی فایل‌ها باید داخل سکشن نصب باشد"
    assert "CreateShortcut" in body, "میان‌برها باید داخل سکشن نصب باشند"
    assert "WriteUninstaller" in body

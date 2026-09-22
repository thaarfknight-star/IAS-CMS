"""تست‌های 2.0.25-beta — نمایش حجم نصب در Settings/Control Panel + آماده‌سازی امضای دیجیتال.

- installer.nsi باید FileFunc.nsh را include کند و با ${GetSize} حجم $INSTDIR را
  حساب کرده و به‌صورت DWORD در کلید Uninstall با نام EstimatedSize ثبت کند.
- ثبت EstimatedSize باید بعد از کپی فایل‌ها (files.nsi) و ساخت uninstall.exe
  و قبل از پایان موفق DoInstall انجام شود.
- build.yml باید مرحله‌ی امضای اختیاری (گیت‌شده روی سکرت WINDOWS_CERT_PFX) داشته باشد.
- نسخه‌ی واقعی باید 2.0.25-beta باشد.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NSI = REPO / "installer" / "installer.nsi"
YML = REPO / ".github" / "workflows" / "build.yml"
VERSION_TXT = REPO / "version.txt"

EXPECTED_VERSION = "2.0.25-beta"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def test_filefunc_included():
    src = _read(NSI)
    assert re.search(r'^\s*!include\s+"FileFunc\.nsh"', src, re.M) is not None, \
        "installer.nsi باید FileFunc.nsh را include کند"


def test_getsize_used_for_instdir():
    src = _read(NSI)
    assert "${GetSize}" in src and "$INSTDIR" in src, \
        "باید با ${GetSize} حجم $INSTDIR محاسبه شود"


def test_estimated_size_written_as_dword():
    src = _read(NSI)
    assert re.search(r'WriteRegDWORD\s+HKCU\s+"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall',
                     src) is not None
    assert '"EstimatedSize"' in src, "کلید EstimatedSize باید ثبت شود"
    # EstimatedSize باید DWORD باشد نه رشته
    m = re.search(r'WriteRegDWORD[^\n]*"EstimatedSize"[^\n]*', src)
    assert m is not None, "EstimatedSize باید با WriteRegDWORD ثبت شود"


def test_estimated_size_order_after_files_and_uninstaller():
    src = _read(NSI)
    pos_files = src.find('installer\\files.nsi')
    pos_uninst = src.find("WriteUninstaller")
    pos_est = src.find('"EstimatedSize"')
    pos_ok = src.find('StrCpy $R9 "ok"')
    assert pos_files != -1 and pos_uninst != -1 and pos_est != -1 and pos_ok != -1
    assert pos_files < pos_uninst < pos_est < pos_ok, \
        "ترتیب باید باشد: کپی فایل‌ها < ساخت uninstaller < ثبت EstimatedSize < پایان موفق"


def test_estimated_size_inside_do_install():
    src = _read(NSI)
    do_install = src.find("Function DoInstall")
    end = src.find("FunctionEnd", do_install)
    pos_est = src.find('"EstimatedSize"')
    assert do_install < pos_est < end, "ثبت EstimatedSize باید داخل Function DoInstall باشد"


def test_version_txt_is_225():
    assert VERSION_TXT.read_text(encoding="utf-8").strip() == EXPECTED_VERSION


def test_workflow_has_optional_sign_steps():
    yml = _read(YML)
    assert "Code-sign app EXE (optional)" in yml, "مرحله‌ی امضای exe اصلی باید باشد"
    assert "Code-sign Setup EXE (optional)" in yml, "مرحله‌ی امضای Setup باید باشد"
    assert "secrets.WINDOWS_CERT_PFX" in yml, "امضا باید گیت‌شده روی سکرت گواهی باشد"
    assert "signtool" in yml, "باید از signtool استفاده شود"
    # بدون سکرت نباید تلاشی برای امضا شود (مرحله skip شود، بیلد قرمز نشود)
    assert yml.count("secrets.WINDOWS_CERT_PFX != ''") >= 2


def test_nsi_still_has_required_uninstall_keys():
    src = _read(NSI)
    for key in ("DisplayName", "DisplayVersion", "Publisher", "InstallLocation",
                "DisplayIcon", "UninstallString", "NoModify", "NoRepair", "EstimatedSize"):
        assert f'"{key}"' in src, f"کلید {key} باید در رجیستری ثبت شود"

# -*- coding: utf-8 -*-
"""tests/test_254_beta.py — رگرسیون 2.0.66-beta (تغییر نام به IAS Viewer).

- نام نمایشی برنامه همه‌جا «IAS Viewer» است.
- شناسه‌های داخلی (پوشه‌ی دیتا، کلید آپدیتر، نام exe) عمداً دست‌نخورده‌اند
  تا دیتای کاربران و زنجیره‌ی آپدیت خودکار نشکند.
"""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OLD_BRANDS = ("ایمن آرا سورنا", "IMENARA SORENA")


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def test_theme_constants():
    tree = ast.parse(_read("theme.py"))
    vals = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in (
                        "APP_NAME_FA", "APP_NAME_EN"):
                    vals[t.id] = ast.literal_eval(node.value)
    assert vals.get("APP_NAME_FA") == "IAS Viewer"
    assert vals.get("APP_NAME_EN") == "IAS VIEWER"


def test_window_title_single_name():
    src = _read("main.py")
    assert "{APP_NAME_FA} | {APP_NAME_EN}" not in src
    assert "IAS Viewer v" in src or "{APP_NAME_FA} v" in src


def test_no_old_brand_in_user_visible_code():
    for name in ("theme.py", "main.py", "updater.py", "update_apply.py",
                 "build_catalog.py", "installer/make_graphics.py",
                 "installer/make_update.py"):
        src = _read(name)
        for brand in OLD_BRANDS:
            assert brand not in src, f"{name} هنوز «{brand}» دارد"


def test_installer_display_name():
    src = _read("installer/installer.nsi")
    assert '!define APP_NAME "IAS Viewer"' in src
    # ناشر (شرکت) همان می‌ماند
    assert '!define PUBLISHER "ایمن آرا سورنا"' in src


def test_internal_identifiers_unchanged():
    # پوشه‌ی دیتا: تغییرش دیتای کاربران را یتیم می‌کند
    assert "ImenaraSorena" in _read("app_paths.py")
    # کلید زنجیره‌ی آپدیت خودکار
    assert '"IAS-CMS"' in _read("installer/make_update.py")
    assert 'info.get("app") != "IAS-CMS"' in _read("updater.py")
    # نام باینری‌ها
    assert "CCTV_CMS" in _read("update_apply.py")


def test_manual_pdf_branded():
    src = open("/home/hatch/workspace/iascms-pdf/build_manual.py",
               encoding="utf-8").read()
    for brand in OLD_BRANDS:
        assert brand not in src
    assert "IAS Viewer" in src

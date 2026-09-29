# -*- coding: utf-8 -*-
"""تست 2.0.76-beta:
۱) بررسی خودکار آپدیت هر ۱ ساعت انجام می‌شود (نه ۶ ساعت).
۲) رگرسیون گزارش طه (2026-09-29): ریلیز «IAS-Viewer-2.0.75» با فایل
   «IAS-CMS-Update-v2.0.75-beta.zip» منتشر شده بود ولی آپدیت‌چکر آن را
   نمی‌دید چون تگ با «X.Y.Z» شروع نمی‌شد (parse_version → ‎(0,0,0,0)).
   حالا نسخه از روی نام فایل آپدیت (و اگر نشد از روی تگ) استخراج می‌شود.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _release(tag_name, asset_name):
    return {
        "tag_name": tag_name,
        "draft": False,
        "prerelease": True,
        "body": "notes",
        "published_at": "2026-09-29",
        "assets": [{
            "name": asset_name,
            "browser_download_url":
                f"https://github.com/x/y/releases/download/x/{asset_name}",
        }],
    }


def test_manual_tag_release_is_detected():
    """سناریوی واقعی طه: تگ «IAS-Viewer-2.0.75» + فایل آپدیت 2.0.75-beta."""
    import update_checker as uc
    real = uc._api_get
    try:
        uc._api_get = lambda url, timeout=12: [
            _release("IAS-Viewer-2.0.75", "IAS-CMS-Update-v2.0.75-beta.zip")]
        info = uc.check_for_updates("2.0.0")
        assert info is not None, "ریلیز دستی باید دیده شود"
        assert info["version"] == "2.0.75-beta", info["version"]
        assert info["download_url"].endswith(".zip")
        # بعد از آپدیت، دوباره هشدار ندهد
        assert uc.check_for_updates("2.0.75-beta") is None
    finally:
        uc._api_get = real


def test_extract_version_tolerates_prefixes():
    import update_checker as uc
    assert uc.extract_version("IAS-Viewer-2.0.75") == "2.0.75"
    assert uc.extract_version("v2.0.75-beta") == "2.0.75-beta"
    assert uc.extract_version("2.0.75-beta") == "2.0.75-beta"
    assert uc.is_newer(uc.extract_version("IAS-Viewer-2.0.75"), "2.0.74-beta")


def test_update_interval_is_one_hour():
    """تایمر بررسی خودکار = ۳۶۰۰۰۰۰ میلی‌ثانیه (۱ ساعت)."""
    import re
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(repo, "main.py"), encoding="utf-8").read()
    m = re.search(r"def _schedule_update_checks\(self\):(.*?)"
                  r"def _auto_check_enabled",
                  src, re.S)
    assert m, "متد _schedule_update_checks پیدا نشد"
    body = m.group(1)
    assert "6 * 3600" not in body and "6*3600" not in body, \
        "فاصله‌ی ۶ ساعته نباید باقی مانده باشد"
    assert re.search(r"setInterval\(\s*3600\s*\*\s*1000\s*\)", body), \
        "تایمر باید هر ۱ ساعت باشد"
    assert "هر ۱ ساعت" in body, "داک‌استرینگ باید به‌روز باشد"


def test_settings_label_says_one_hour():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(repo, "settings_page.py"), encoding="utf-8").read()
    assert "هر ۱ ساعت" in src
    assert "هر ۶ ساعت" not in src

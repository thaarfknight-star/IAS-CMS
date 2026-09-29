# -*- coding: utf-8 -*-
"""update_checker.py — بررسی خودکار آپدیت جدید از GitHub Releases (2.0.72-beta).

مدل اعتماد:
  * فقط HTTPS به api.github.com و github.com (ریپوی رسمی thaarfknight-star/IAS-CMS).
  * هشدار فقط وقتی نمایش داده می‌شود که یک Release رسمی، فایل
    «IAS-CMS-Update-vX.Y.Z.zip» را به‌عنوان asset داشته باشد.
  * فایل دانلودشده قبل از اعمال، با همان اعتبارسنجی updater.validate_update_zip
    بررسی می‌شود (ساختار، update_info.json، app=IAS-CMS، نسخه‌ی جدیدتر، تطابق مانیفست).
"""

import json
import os
import re
import urllib.request

GITHUB_REPO = "thaarfknight-star/IAS-CMS"
# لیست Releaseها (نه /latest) — چون نسخه‌ها بتا هستند و GitHub نسخه‌ی
# prerelease را در /releases/latest برنمی‌گرداند. در کد، draftها رد می‌شوند.
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
_USER_AGENT = "IAS-Viewer-UpdateChecker"


def parse_version(v: str) -> tuple:
    """«2.0.72-beta» → (2, 0, 72, 0) ؛ «2.0.72» → (2, 0, 72, 1).

    نسخه‌ی نهایی (بدون پسوند) از بتای هم‌شماره جدیدتر حساب می‌شود.
    """
    s = (v or "").strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", s)
    if not m:
        return (0, 0, 0, 0)
    major, minor, patch, suffix = m.groups()
    stable = 0 if suffix and "beta" in suffix.lower() else 1
    return (int(major), int(minor), int(patch), stable)


def is_newer(remote: str, current: str) -> bool:
    return parse_version(remote) > parse_version(current)


def extract_version(tag: str) -> str:
    """استخراج «X.Y.Z...» از تگ ریلیز؛ تگ‌های دستی مثل «IAS-Viewer-2.0.75»
    یا «v2.0.75-beta» هم پشتیبانی می‌شوند (2.0.76-beta)."""
    s = (tag or "").strip().lstrip("vV")
    m = re.search(r"(\d+\.\d+\.\d+\S*)", s)
    return m.group(1) if m else s


def _asset_version(name: str) -> str:
    """نسخه از روی نام فایل آپدیت، مثلاً
    «IAS-CMS-Update-v2.0.75-beta.zip» → «2.0.75-beta»."""
    m = re.search(r"ias-cms-update-v(.+?)\.zip\s*$",
                  (name or "").strip(), re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _api_get(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_updates(current_version: str, timeout: int = 12):
    """بررسی Release جدید. خروجی: dict با کلیدهای
    version/download_url/notes/published_at — یا None (آپدیتی نیست/خطا).

    هیچ استثنایی به بیرون درز نمی‌کند؛ خطا = None (سکوت، مثل قبل).
    """
    try:
        data = _api_get(RELEASES_API, timeout=timeout)
        if not isinstance(data, list):
            return None
        # گیت‌هاب جدیدترین را اول می‌دهد؛ draftها رد می‌شوند. نسخه از روی
        # نام فایل آپدیت استخراج می‌شود (دقیق‌تر) و اگر نشد از روی تگ —
        # تا تگ‌های دستی مثل «IAS-Viewer-2.0.75» هم دیده شوند (2.0.76-beta).
        for rel in data:
            if not isinstance(rel, dict) or rel.get("draft"):
                continue
            tag_name = str(rel.get("tag_name", "") or "").strip()
            dl_url = None
            asset_name = ""
            for asset in rel.get("assets", []) or []:
                name = str(asset.get("name", "") or "")
                nl = name.lower()
                if nl.startswith("ias-cms-update-v") and nl.endswith(".zip"):
                    dl_url = asset.get("browser_download_url")
                    asset_name = name
                    break
            if not dl_url:
                continue  # این Release فایل آپدیت ندارد → بعدی
            ver = _asset_version(asset_name) or extract_version(tag_name)
            if not ver or not is_newer(ver, current_version):
                continue
            return {
                "version": ver,
                "download_url": dl_url,
                "notes": str(rel.get("body", "") or "")[:2000],
                "published_at": str(rel.get("published_at", "") or ""),
            }
        return None
    except Exception:
        return None


def download_update(url: str, dest_path: str, progress_cb=None,
                    timeout: int = 30, chunk: int = 256 * 1024):
    """دانلود فایل آپدیت با گزارش پیشرفت (۰ تا ۱۰۰)."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    total = 0
    done = 0
    tmp = dest_path + ".part"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            try:
                total = int(resp.headers.get("Content-Length", 0) or 0)
            except Exception:
                total = 0
            with open(tmp, "wb") as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if progress_cb and total > 0:
                        try:
                            progress_cb(min(100, int(done * 100 / total)))
                        except Exception:
                            pass
        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.rename(tmp, dest_path)
        return True
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise RuntimeError(f"دانلود آپدیت ممکن نشد: {e}")

# -*- coding: utf-8 -*-
"""پرس‌وجوی «وضعیت هارددیسک» و «جست‌وجوی بازه‌های واقعاً ضبط‌شده» روی خودِ
NVR، از همان API پیکربندی وب رسمی سازنده که ``nvr_http_api.py`` برای کشف
کانال‌ها استفاده می‌کند (نه RTSP، نه ONVIF؛ HTTP ساده به پورت وب دستگاه).

چرا این ماژول جدا از nvr_http_api.py است؟
------------------------------------------
nvr_http_api.py فقط «چند کانال و با چه نامی» را می‌پرسد - یک درخواست ساده.
اینجا دو قابلیت اضافه می‌شود که پیچیدگی متفاوتی دارند:

  1) وضعیت هارد/فضای ذخیره (تعداد هارد، ظرفیت، فضای آزاد، سالم/خراب) -
     یک درخواست ساده، شبیه کشف کانال.
  2) جست‌وجوی «چه بازه‌های زمانی‌ای واقعاً روی هارد ضبط شده» برای یک کانال
     و یک بازه‌ی تاریخ - در Hikvision یک POST با بدنه‌ی XML (ISAPI
     ContentMgmt/search)، در Dahua یک گفت‌وگوی سه‌مرحله‌ای stateful
     (factory.create -> findFile -> findNextFile[، تکرار تا ته لیست] ->
     close/destroy) - نه یک درخواست ساده‌ی GET.

نکته‌ی مهم درباره‌ی محدودیت این روش: این AP  Iها فقط «فهرست بازه‌های زمانی
ضبط‌شده» را می‌دهند (چه زمانی، چه کانالی، چه نوع ضبطی - پیوسته/حرکتی/
هشدار)، نه خودِ ویدیو را. دانلود/پخش خودِ فایل ضبط‌شده مرحله‌ی جداگانه‌ای
است (endpointهای دیگری مثل ISAPI/ContentMgmt/download یا Dahua
loadfile.cgi) که این ماژول پیاده‌سازی نمی‌کند - چون معمولاً برای «دیدن
اینکه چه ویدیویی ضبط شده» همین فهرست زمانی کافی است؛ برای تماشای واقعی
فایل، بازکردن پنل وب/کلاینت خودِ NVR (نگاه کنید به nvr_webview_dialog.py)
ساده‌تر و مطمئن‌تر از پیاده‌سازی دوباره‌ی دانلود است.

مثل nvr_http_api.py، برند دستگاه اینجا فیلتر نمی‌شود - هر دو پروتکل
(Hikvision/Dahua) روی هر پورت امتحان می‌شوند و اولین پاسخ معتبر برگردانده
می‌شود؛ اگر دستگاه هیچ‌کدام را نداشت یا احراز هویت رد شد، ``None``
برمی‌گردد (نه استثنا) تا کد فراخوان (``nvr_storage_dialog.py``) بتواند
پیام مناسب نشان دهد.
"""

import re
import uuid

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

DEFAULT_TIMEOUT = 6
COMMON_HTTP_PORTS = [80, 8080]


def _auth_variants(user, pwd):
    return [HTTPDigestAuth(user, pwd), HTTPBasicAuth(user, pwd)]


def _get(url, user, pwd, timeout, params=None):
    for auth in _auth_variants(user, pwd):
        try:
            resp = requests.get(url, auth=auth, timeout=timeout, params=params)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            return resp
    return None


def _post(url, user, pwd, timeout, data, headers):
    for auth in _auth_variants(user, pwd):
        try:
            resp = requests.post(url, auth=auth, timeout=timeout, data=data, headers=headers)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            return resp
    return None


def _parse_kv_lines(text):
    """پارس متن ``key=value`` خط‌به‌خط - فرمت رایج پاسخ CGIهای Dahua."""
    result = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip()
    return result


def _group_indexed(kv, prefix):
    """کلیدهایی مثل ``info[0].Name`` / ``info[0].TotalBytes`` را به لیستی از
    دیکشنری‌ها گروه‌بندی می‌کند (به ازای هر اندیس یک دیکشنری)."""
    pattern = re.compile(rf"^{re.escape(prefix)}\[(\d+)\]\.(.+)$")
    grouped = {}
    for k, v in kv.items():
        m = pattern.match(k)
        if not m:
            continue
        idx, field = int(m.group(1)), m.group(2)
        grouped.setdefault(idx, {})[field] = v
    return [grouped[i] for i in sorted(grouped)]


# =============================================================== هارددیسک ==

def _hikvision_storage(ip, port, user, pwd, timeout):
    url = f"http://{ip}:{port}/ISAPI/ContentMgmt/Storage"
    resp = _get(url, user, pwd, timeout)
    if resp is None or "<id>" not in resp.text:
        return None

    blocks = re.findall(r"<hdd>(.*?)</hdd>", resp.text, re.S)
    blocks += re.findall(r"<nas>(.*?)</nas>", resp.text, re.S)
    if not blocks:
        return None

    drives = []
    for i, block in enumerate(blocks):
        def _field(tag, _block=block):
            m = re.search(rf"<{tag}>([^<]*)</{tag}>", _block)
            return m.group(1).strip() if m else ""

        capacity_mb, free_mb = _field("capacity"), _field("freeSpace")
        drives.append({
            "id": _field("id") or str(i + 1),
            "status": _field("status") or "نامشخص",
            "property": _field("property") or "",
            "capacity_gb": round(int(capacity_mb) / 1024, 1) if capacity_mb.isdigit() else None,
            "free_gb": round(int(free_mb) / 1024, 1) if free_mb.isdigit() else None,
        })
    return drives or None


def _dahua_storage(ip, port, user, pwd, timeout):
    url = f"http://{ip}:{port}/cgi-bin/storageDevice.cgi"
    resp = _get(url, user, pwd, timeout, params={"action": "getDeviceAllInfo"})
    if resp is None:
        return None

    kv = _parse_kv_lines(resp.text)
    items = _group_indexed(kv, "info")
    if not items:
        return None

    drives = []
    for it in items:
        def _to_gb(key):
            raw = it.get(key)
            try:
                return round(int(raw) / (1024 ** 3), 1) if raw is not None else None
            except (TypeError, ValueError):
                return None

        total_gb, used_gb = _to_gb("TotalBytes"), _to_gb("UsedBytes")
        free_gb = round(total_gb - used_gb, 1) if (total_gb is not None and used_gb is not None) else None
        drives.append({
            "id": it.get("Name") or it.get("Path") or "",
            "status": it.get("State", "نامشخص"),
            "property": it.get("Type", ""),
            "capacity_gb": total_gb,
            "free_gb": free_gb,
        })
    return drives or None


def get_storage_status(ip, user, pwd, ports=None, timeout=DEFAULT_TIMEOUT):
    """لیست هارددیسک/فضای ذخیره‌ی NVR را برمی‌گرداند:
    ``[{"id", "status", "property", "capacity_gb", "free_gb"}, ...]``.

    ``None`` یعنی هیچ‌کدام از دو API جواب ندادند (نه اینکه هارد نداشته
    باشد) - تفاوتش با لیست خالی برای پیام درست به کاربر لازم است."""
    if not user:
        return None
    for port in (ports or COMMON_HTTP_PORTS):
        for prober in (_hikvision_storage, _dahua_storage):
            try:
                result = prober(ip, port, user, pwd, timeout)
            except Exception:
                result = None
            if result:
                return result
    return None


# ============================================================ جست‌وجوی ضبط ==

def _hikvision_search_recordings(ip, port, user, pwd, channel, start_dt, end_dt, timeout):
    url = f"http://{ip}:{port}/ISAPI/ContentMgmt/search"
    track_id = int(channel) * 100 + 1  # کانال ۱ -> ترک ۱۰۱، کانال ۲ -> ۲۰۱ و ... (قرارداد ISAPI)
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<CMSearchDescription>"
        f"<searchID>{uuid.uuid4()}</searchID>"
        f"<trackList><trackID>{track_id}</trackID></trackList>"
        "<timeSpanList><timeSpan>"
        f"<startTime>{start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}</startTime>"
        f"<endTime>{end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}</endTime>"
        "</timeSpan></timeSpanList>"
        "<maxResults>200</maxResults>"
        "<searchResultPostion>0</searchResultPostion>"
        "</CMSearchDescription>"
    )
    resp = _post(url, user, pwd, timeout, data=body.encode("utf-8"),
                 headers={"Content-Type": "application/xml; charset=\"UTF-8\""})
    if resp is None:
        return None
    text = resp.text
    # پاسخ موفق همیشه numOfMatches یا searchMatchItem دارد (طبق اسکیمای
    # CMSearchResult)؛ اگر هیچ‌کدام نبود ولی یک ResponseStatus/statusCode
    # خطا برگشته، یعنی این endpoint روی این دستگاه پشتیبانی نمی‌شود (مثلاً
    # methodNotAllowed) - باید به Dahua سوییچ کرد، نه لیست خالی برگرداند.
    if "numOfMatches" not in text and "searchMatchItem" not in text:
        return None

    items = []
    for block in re.findall(r"<searchMatchItem>(.*?)</searchMatchItem>", resp.text, re.S):
        def _field(tag, _block=block):
            m = re.search(rf"<{tag}>([^<]*)</{tag}>", _block)
            return m.group(1).strip() if m else ""

        items.append({
            "start": _field("startTime"),
            "end": _field("endTime"),
            "type": _field("contentType") or "video",
        })
    return items  # لیست خالی یعنی وصل شدیم ولی چیزی در این بازه ضبط نشده - نه شکست


def _dahua_search_recordings(ip, port, user, pwd, channel, start_dt, end_dt, timeout):
    base = f"http://{ip}:{port}/cgi-bin/mediaFileFind.cgi"

    create_resp = _get(base, user, pwd, timeout, params={"action": "factory.create"})
    if create_resp is None or "result=" not in create_resp.text:
        return None
    obj_id = create_resp.text.strip().split("=", 1)[1].strip()

    try:
        find_resp = _get(base, user, pwd, timeout, params={
            "action": "findFile",
            "object": obj_id,
            "condition.Channel": max(int(channel) - 1, 0),  # این API صفرمبناست
            "condition.StartTime": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "condition.EndTime": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "condition.Types[0]": "dav",
        })
        if find_resp is None:
            return None

        items = []
        while True:
            next_resp = _get(base, user, pwd, timeout, params={
                "action": "findNextFile", "object": obj_id, "count": 100,
            })
            if next_resp is None:
                break
            kv = _parse_kv_lines(next_resp.text)
            batch = _group_indexed(kv, "items")
            for it in batch:
                items.append({
                    "start": it.get("StartTime", ""),
                    "end": it.get("EndTime", ""),
                    "type": it.get("Type", "dav"),
                })
            found_raw = kv.get("found")
            try:
                found_n = int(found_raw) if found_raw is not None else len(batch)
            except ValueError:
                found_n = len(batch)
            if found_n < 100 or not batch:
                break
        return items
    finally:
        # صرف‌نظر از موفقیت جست‌وجو، سشن باز روی NVR باید بسته شود؛ وگرنه با
        # هر بار جست‌وجو یک شیء «find» روی دستگاه باقی می‌ماند.
        _get(base, user, pwd, timeout, params={"action": "close", "object": obj_id})
        _get(base, user, pwd, timeout, params={"action": "destroy", "object": obj_id})


def search_recordings(ip, user, pwd, channel, start_dt, end_dt, ports=None, timeout=DEFAULT_TIMEOUT):
    """بازه‌های زمانی واقعاً ضبط‌شده روی هارد NVR برای یک کانال و بازه‌ی
    تاریخ/ساعت مشخص را برمی‌گرداند: ``[{"start", "end", "type"}, ...]``
    (ساعت/تاریخ دقیقاً همان‌طور که خودِ دستگاه گزارش می‌دهد).

    ``None`` یعنی نتوانستیم به هیچ API‌ای وصل شویم (پورت بسته/رمز اشتباه/
    برند پشتیبانی‌نشده)؛ لیست خالی یعنی وصل شدیم ولی در این بازه چیزی ضبط
    نشده - این تفاوت برای پیام درست به کاربر لازم است."""
    if not user:
        return None
    for port in (ports or COMMON_HTTP_PORTS):
        for prober in (_hikvision_search_recordings, _dahua_search_recordings):
            try:
                result = prober(ip, port, user, pwd, channel, start_dt, end_dt, timeout)
            except Exception:
                result = None
            if result is not None:
                return result
    return None

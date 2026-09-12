"""اتصال به سنسور/پنل *فیزیکی* اعلام حریق - نه تشخیص تصویری از روی دوربین
(برای آن، رجوع کنید به fire_smoke_detector.py).

چرا این ماژول جدا از fire_smoke_detector.py است:
--------------------------------------------------
تشخیص تصویری هیچ‌وقت جایگزین کامل یک دتکتور دود واقعی (یونیزاسیون/فوتوالکتریک)
یا یک پنل اعلام حریق آدرس‌پذیر استاندارد نمی‌شود - دوربین فقط چیزی را می‌بیند
که در کادرش باشد و نور/زاویه کافی داشته باشد. اکثر ساختمان‌ها از قبل سنسور/پنل
فیزیکی اعلام حریق نصب‌شده دارند؛ این ماژول به آن سخت‌افزار *موجود* وصل می‌شود
تا وقتی آلارم واقعی فعال شد، همین نرم‌افزار CMS هم بلافاصله مطلع و اعلام کند -
مکمل تشخیص تصویری، نه رقیب آن.

سه روش اتصال پشتیبانی می‌شود (رایج‌ترین‌ها در عمل):

  ۱. Hikvision ISAPI (``hikvision_isapi``): بسیاری از NVR/دوربین‌های Hikvision
     چند «ورودی آلارم» (Alarm Input) فیزیکی دارند - دقیقاً برای همین منظور که
     کنتاکت خشک (dry contact) خروجی رله‌ی یک پنل/دتکتور دود به آن‌ها وصل شود.
     وضعیت لحظه‌ای هر ورودی از ``/ISAPI/System/IO/inputs/<id>/status`` خوانده
     می‌شود.

  ۲. Dahua CGI (``dahua_cgi``): معادل همان قابلیت روی NVR/دوربین‌های Dahua،
     از طریق ``/cgi-bin/alarm.cgi``.

  ۳. Modbus TCP عمومی (``modbus_tcp``): برای اتصال مستقیم (بدون واسطه‌ی NVR)
     به هر ماژول رله/ورودی دیجیتال شبکه‌ای عمومی (I/O module) که کنتاکت خشک
     پنل اعلام حریق به آن وصل شده - پروتکل استاندارد صنعتی، مستقل از برند.

هر سه روش با همان الگوی «آماده در پس‌زمینه چک شود، فقط با *تغییر واقعی*
وضعیت سیگنال بفرست» که در بقیه‌ی برنامه استفاده شده (مثلاً
people_count_signal در camera_stream.py) پیاده‌سازی شده‌اند.
"""

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from PyQt6.QtCore import QThread, pyqtSignal

DEFAULT_TIMEOUT = 4
DEFAULT_POLL_INTERVAL = 3  # ثانیه

PANEL_TYPE_LABELS_FA = {
    "hikvision_isapi": "NVR/دوربین Hikvision - ورودی آلارم (ISAPI)",
    "dahua_cgi": "NVR/دوربین Dahua - ورودی آلارم (CGI)",
    "modbus_tcp": "ماژول رله/IO عمومی - Modbus TCP",
}

DEFAULT_PORTS = {"hikvision_isapi": 80, "dahua_cgi": 80, "modbus_tcp": 502}


# --------------------------------------------------------------- HTTP -----

def _auth_variants(user, pwd):
    # همان اولویت nvr_http_api.py: اول Digest (رایج‌تر در فریمورهای جدید)،
    # بعد Basic به‌عنوان جایگزین.
    return [HTTPDigestAuth(user, pwd), HTTPBasicAuth(user, pwd)]


def _get(url, user, pwd, timeout):
    for auth in _auth_variants(user, pwd):
        try:
            resp = requests.get(url, auth=auth, timeout=timeout)
        except requests.RequestException:
            continue
        if resp.status_code == 200 and resp.text:
            return resp
    return None


def check_hikvision_alarm_input(ip, port, user, pwd, input_id=1, timeout=DEFAULT_TIMEOUT):
    """وضعیت لحظه‌ای یک ورودی آلارم Hikvision را می‌خواند.
    خروجی: True (تریگر/فعال)، False (عادی) یا None (نامشخص/بدون پاسخ معتبر)."""
    url = f"http://{ip}:{port}/ISAPI/System/IO/inputs/{input_id}/status"
    resp = _get(url, user, pwd, timeout)
    if resp is None:
        return None
    text = resp.text.lower()
    # نمونه‌ی پاسخ واقعی: <IOInputPortStatus><ioState>active</ioState></...>
    if "<iostate>active</iostate>" in text or ">active<" in text:
        return True
    if "<iostate>inactive</iostate>" in text or ">inactive<" in text:
        return False
    return None


def check_dahua_alarm_input(ip, port, user, pwd, input_id=1, timeout=DEFAULT_TIMEOUT):
    """معادل Dahua - چند مسیر شناخته‌شده امتحان می‌شود چون فرمت پاسخ بین
    فریمورهای مختلف کمی فرق دارد."""
    candidates = [
        f"http://{ip}:{port}/cgi-bin/alarm.cgi?action=getInState&Input={input_id}",
        f"http://{ip}:{port}/cgi-bin/alarm.cgi?action=getState&Channel={input_id}",
    ]
    for url in candidates:
        resp = _get(url, user, pwd, timeout)
        if resp is None:
            continue
        text = resp.text.strip().lower()
        if "true" in text or "=open" in text or text.endswith("=1"):
            return True
        if "false" in text or "=close" in text or text.endswith("=0"):
            return False
    return None


# ------------------------------------------------------------- Modbus -----

def check_modbus_discrete_input(ip, port, address, timeout=DEFAULT_TIMEOUT):
    """خواندن یک ورودی دیجیتال (Discrete Input) از یک ماژول رله/IO عمومی
    شبکه‌ای، از طریق پروتکل استاندارد صنعتی Modbus TCP.

    وابستگی اختیاری: نیازمند کتابخانه‌ی ``pymodbus`` (رجوع کنید به
    requirements.txt). اگر نصب نباشد، خطای روشنی پرتاب می‌شود تا کاربر بداند
    دقیقاً چه چیزی نصب کند - نه یک AttributeError مبهم."""
    try:
        from pymodbus.client import ModbusTcpClient
    except Exception as e:
        raise RuntimeError(
            "برای اتصال به ماژول Modbus TCM، کتابخانه‌ی pymodbus لازم است "
            f"(pip install pymodbus). خطا: {e}"
        )

    client = ModbusTcpClient(ip, port=port, timeout=timeout)
    try:
        if not client.connect():
            return None
        result = client.read_discrete_inputs(address, 1)
        if result is None or result.isError():
            return None
        return bool(result.bits[0])
    finally:
        try:
            client.close()
        except Exception:
            pass


# ---------------------------------------------------------- مانیتور ترد ---

class FireAlarmMonitorThread(QThread):
    """یک پنل/سنسور فیزیکی اعلام حریق را در پس‌زمینه، با فاصله‌ی زمانی
    مشخص (poll_interval) چک می‌کند. فقط با *تغییر واقعی* وضعیت (نه هر بار
    چک) سیگنال می‌فرستد تا در main.py هر بار یک هشدار تازه ثبت/اعلام نشود."""

    alarm_triggered = pyqtSignal()
    alarm_cleared = pyqtSignal()
    status_error = pyqtSignal(str)  # فقط برای لاگ/اطلاع - آلارم صادر نمی‌کند

    def __init__(self, panel: dict, parent=None):
        super().__init__(parent)
        self.panel = dict(panel)
        self._run_flag = True

    def _check_once(self):
        p = self.panel
        ptype = p.get("type")
        try:
            if ptype == "hikvision_isapi":
                return check_hikvision_alarm_input(
                    p["ip"], p.get("port", 80), p.get("user", ""), p.get("pass", ""),
                    input_id=p.get("input_id", 1),
                )
            if ptype == "dahua_cgi":
                return check_dahua_alarm_input(
                    p["ip"], p.get("port", 80), p.get("user", ""), p.get("pass", ""),
                    input_id=p.get("input_id", 1),
                )
            if ptype == "modbus_tcp":
                return check_modbus_discrete_input(
                    p["ip"], p.get("port", 502), p.get("input_id", 0),
                )
        except Exception as e:
            self.status_error.emit(str(e))
            return None
        return None

    def run(self):
        was_active = False
        poll_interval = max(1, int(self.panel.get("poll_interval", DEFAULT_POLL_INTERVAL)))
        while self._run_flag:
            state = self._check_once()
            if state is not None:
                if state and not was_active:
                    self.alarm_triggered.emit()
                elif not state and was_active:
                    self.alarm_cleared.emit()
                was_active = state
            # خواب پلکانی (به‌جای یک msleep طولانی) تا stop() با تاخیر
            # زیاد مواجه نشود - دقیقاً همان الگوی لغو سریع در سایر تردهای
            # پروژه (مثلاً NVRScanThread.cancel).
            for _ in range(poll_interval * 10):
                if not self._run_flag:
                    break
                self.msleep(100)

    def stop(self):
        self._run_flag = False
        self.wait(3000)

# -*- coding: utf-8 -*-
"""اتصال به پنل‌های فیزیکی اعلام حریق و پایش وضعیت آن‌ها در یک QThread
جداگانه، به همان الگوی nvr_scanner.py/network_scan (ترد پس‌زمینه + سیگنال
به UI). سه پروتکل پشتیبانی می‌شود:

  - Hikvision ISAPI  (protocol="isapi")   - نیازمند requests (از قبل در
    requirements.txt پروژه هست).
  - Dahua CGI        (protocol="cgi")     - نیازمند requests.
  - Modbus TCP        (protocol="modbus") - نیازمند pymodbus (اختیاری؛
    اگر نصب نباشد، این پنل فقط یک رویداد خطای اتصال می‌دهد و برنامه کرش
    نمی‌کند - رجوع کنید به README).

FireAlarmMonitorThread فقط وقتی وضعیت یک منطقه/ورودی *تغییر* می‌کند سیگنال
می‌دهد (نه هر بار poll)، تا لیست رویدادهای UI و پایگاه‌داده‌ی گزارش‌ها با
رویدادهای تکراری پر نشوند.
"""

import time

from PyQt6.QtCore import QThread, pyqtSignal

POLL_INTERVAL_SECONDS = 3.0


class FireAlarmMonitorThread(QThread):
    # (zone/point) - عنوان منطقه یا شماره‌ی ورودی دیجیتال که فعال شده
    panel_triggered = pyqtSignal(str)
    # (zone/point) - همان منطقه که به حالت عادی برگشته
    panel_cleared = pyqtSignal(str)
    # پیام خطا برای مشکلات اتصال/وابستگی نصب‌نشده (پنل غیرفعال می‌ماند، برنامه کرش نمی‌کند)
    connection_error = pyqtSignal(str)

    def __init__(self, panel: dict, parent=None):
        super().__init__(parent)
        self.panel = panel
        self._running = False
        # {zone_id: bool} - آخرین وضعیت شناخته‌شده‌ی هر منطقه/ورودی، برای
        # تشخیص «تغییر وضعیت» بین دو poll متوالی.
        self._last_state: dict[str, bool] = {}

    def stop(self):
        """توقف مشارکتی: run() این پرچم را در حلقه‌ی خودش چک می‌کند."""
        self._running = False

    def run(self):
        self._running = True
        protocol = (self.panel.get("protocol") or "").lower()
        poller = {
            "isapi": self._poll_isapi,
            "cgi": self._poll_cgi,
            "modbus": self._poll_modbus,
        }.get(protocol)

        if poller is None:
            self.connection_error.emit(f"پروتکل ناشناخته: {protocol}")
            return

        while self._running:
            try:
                current_state = poller()
                self._emit_state_changes(current_state)
            except Exception as e:
                self.connection_error.emit(str(e))
                # در صورت خطا هم حلقه ادامه پیدا می‌کند (شاید موقتی باشد -
                # مثلاً قطعی لحظه‌ای شبکه) اما با فاصله‌ی بیشتر تلاش می‌شود
                # تا لاگ خطا/رویدادهای UI را پر نکند.
                self.msleep(int(POLL_INTERVAL_SECONDS * 1000 * 3))
                continue
            self.msleep(int(POLL_INTERVAL_SECONDS * 1000))

    def _emit_state_changes(self, current_state: dict[str, bool]):
        for zone, is_active in current_state.items():
            was_active = self._last_state.get(zone, False)
            if is_active and not was_active:
                self.panel_triggered.emit(zone)
            elif was_active and not is_active:
                self.panel_cleared.emit(zone)
        self._last_state.update(current_state)

    # ------------------------------------------------------------ ISAPI --

    def _poll_isapi(self) -> dict:
        """Hikvision ISAPI: GET /ISAPI/Event/notification/alertStream یا
        endpoint وضعیت لحظه‌ای (بسته به مدل پنل). این تابع یک پیاده‌سازی
        حداقلی/نمونه است - برای پنل واقعی endpoint دقیق را طبق مستندات
        دستگاه جایگزین کنید."""
        import requests
        from requests.auth import HTTPDigestAuth

        url = f"http://{self.panel['ip']}:{self.panel.get('port', 80)}/ISAPI/System/IO/inputs/status"
        auth = HTTPDigestAuth(self.panel.get("user", ""), self.panel.get("pass", ""))
        resp = requests.get(url, auth=auth, timeout=5)
        resp.raise_for_status()
        return self._parse_isapi_status(resp.text)

    @staticmethod
    def _parse_isapi_status(xml_text: str) -> dict:
        """پارس حداقلی پاسخ XML؛ در نبود کتابخانه‌ی XML اضافه، با استخراج
        ساده‌ی رشته‌ای انجام می‌شود. برای پنل واقعی طبق ساختار دقیق پاسخ
        دستگاه تنظیم شود."""
        import re
        state = {}
        for match in re.finditer(
            r"<id>(\d+)</id>\s*<ioState>(active|inactive)</ioState>", xml_text
        ):
            zone_id, io_state = match.groups()
            state[f"zone-{zone_id}"] = (io_state == "active")
        return state

    # -------------------------------------------------------------- CGI --

    def _poll_cgi(self) -> dict:
        """Dahua CGI: GET /cgi-bin/alarm.cgi?action=getConfig یا
        alarm status CGI (بسته به مدل). نمونه‌ی حداقلی."""
        import requests

        url = (f"http://{self.panel['ip']}:{self.panel.get('port', 80)}"
               f"/cgi-bin/alarm.cgi?action=getInStatus")
        resp = requests.get(
            url,
            auth=(self.panel.get("user", ""), self.panel.get("pass", "")),
            timeout=5,
        )
        resp.raise_for_status()
        return self._parse_cgi_status(resp.text)

    @staticmethod
    def _parse_cgi_status(text: str) -> dict:
        """پاسخ Dahua CGI معمولاً به شکل خطوطِ ``status.status[0]=Open``
        است. برای پنل واقعی طبق خروجی دقیق دستگاه تنظیم شود."""
        state = {}
        for line in text.splitlines():
            line = line.strip()
            if "=" not in line or "status[" not in line:
                continue
            key, _, value = line.partition("=")
            idx = key.split("[")[-1].rstrip("]")
            state[f"zone-{idx}"] = (value.strip().lower() in ("open", "active", "1", "true"))
        return state

    # ----------------------------------------------------------- Modbus --

    def _poll_modbus(self) -> dict:
        """Modbus TCP discrete inputs. اختیاری: نیازمند pymodbus. اگر نصب
        نباشد، ImportError بالا می‌رود که در run() به connection_error
        تبدیل می‌شود (رجوع کنید به README برای نصب اختیاری)."""
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError as e:
            raise ImportError(
                "pymodbus نصب نیست؛ برای پایش پنل‌های Modbus TCP دستور "
                "'pip install pymodbus' را اجرا کنید."
            ) from e

        client = ModbusTcpClient(self.panel["ip"], port=int(self.panel.get("port", 502)))
        try:
            if not client.connect():
                raise ConnectionError(f"اتصال Modbus به {self.panel['ip']} ناموفق بود.")
            input_count = int(self.panel.get("input_count", 8))
            result = client.read_discrete_inputs(0, input_count)
            if result.isError():
                raise ConnectionError("خواندن ورودی‌های دیجیتال Modbus ناموفق بود.")
            return {f"zone-{i}": bool(bit) for i, bit in enumerate(result.bits[:input_count])}
        finally:
            client.close()

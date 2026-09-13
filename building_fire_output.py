# -*- coding: utf-8 -*-
"""
building_fire_output.py — اتصال تشخیص حریق IAS-CMS به سیستم اعلام حریق ساختمان.

وقتی دوربین‌ها آتش/دود تشخیص می‌دهند، این ماژول سیگنال را به پنل اعلام حریق
ساختمان می‌فرستد. چند روش (backend) پشتیبانی می‌شود چون پنل‌های مختلف
ورودی متفاوت دارند:

  1. webhook      : ارسال HTTP POST به یک آدرس (پنل‌های تحت شبکه، رله‌های
                    هوشمند، Home Assistant و ...). عمومی‌ترین روش.
  2. modbus_tcp   : نوشتن روی یک Coil/رجیستر در پنل‌های Modbus TCP.
  3. serial_relay : رله‌ی USB سریال (مثلاً ماژول ۱ کاناله) که کنتاکت خشک
                    را به ورودی زون پنل وصل می‌کنید. مطمئن‌ترین روش برای
                    پنل‌های سنتی.
  4. simulator    : ارسال به شبیه‌ساز محلی (fire_panel_simulator.py) برای
                    تست بدون داشتن پنل واقعی.

همه‌ی ارسال‌ها در ترد پس‌زمینه انجام می‌شوند تا UI قفل نشود، و نبود
کتابخانه‌های اختیاری (pymodbus/pyserial) باعث کرش نمی‌شود.

تنظیمات در فایل building_fire_config.json کنار همین فایل ذخیره می‌شود.
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "building_fire_config.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "backend": "webhook",          # webhook | modbus_tcp | serial_relay | simulator
    "webhook_url": "http://127.0.0.1:8910/api/fire-alarm",
    "webhook_method": "POST",
    # modbus
    "modbus_host": "192.168.1.100",
    "modbus_port": 502,
    "modbus_coil": 0,              # شماره کویل آلارم
    "modbus_slave": 1,
    # serial relay
    "serial_port": "COM3",         # در ویندوز COMx ، در لینوکس /dev/ttyUSB0
    "serial_baud": 9600,
    # simulator
    "simulator_url": "http://127.0.0.1:8910/api/fire-alarm",
    # رفتار
    "auto_clear_seconds": 0,       # ۰ یعنی پاک‌سازی دستی؛ مثلاً ۶۰ = بعد از ۶۰ ثانیه ریست
    "trigger_on_smoke": True,      # دود هم پنل ساختمان را فعال کند؟
    "trigger_on_fire": True,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update(user)
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


class BuildingFireOutput:
    """ارسال آلارم حریق به پنل ساختمان. نمونه‌ی سراسری بسازید و نگه دارید."""

    def __init__(self, config=None, logger=None):
        self.config = config or load_config()
        # logger: تابعی مثل print که پیام‌های فارسی را نمایش می‌دهد (اختیاری)
        self._log = logger or (lambda msg: None)
        self._lock = threading.Lock()
        self._alarm_active = False
        self._clear_timer = None

    # -- API عمومی -------------------------------------------------------
    def trigger(self, camera_name="", kind="fire", confidence=0.0, test=False):
        """فعال‌سازی آلارم ساختمان. همیشه در ترد پس‌زمینه اجرا می‌شود."""
        cfg = self.config
        if not cfg.get("enabled") and not test:
            return
        if kind == "smoke" and not cfg.get("trigger_on_smoke", True) and not test:
            return
        if kind == "fire" and not cfg.get("trigger_on_fire", True) and not test:
            return

        def _run():
            ok, msg = self._send_trigger_sync(camera_name, kind, confidence, test)
            with self._lock:
                if ok:
                    self._alarm_active = True
            self._log(msg)
            auto_clear = int(cfg.get("auto_clear_seconds") or 0)
            if ok and auto_clear > 0 and not test:
                self._schedule_auto_clear(auto_clear)

        threading.Thread(target=_run, daemon=True).start()

    def clear(self):
        """ریست/پاک‌سازی آلارم ساختمان."""
        def _run():
            ok, msg = self._send_clear_sync()
            with self._lock:
                if ok:
                    self._alarm_active = False
            self._log(msg)

        threading.Thread(target=_run, daemon=True).start()

    def test_connection(self):
        """تست اتصال: یک آلارم آزمایشی می‌فرستد (در لاگ مشخص است که تست است)."""
        self.trigger(camera_name="تست اتصال", kind="fire", confidence=1.0, test=True)

    @property
    def alarm_active(self):
        with self._lock:
            return self._alarm_active

    def reload(self):
        self.config = load_config()

    # -- پیاده‌سازی backend ها -------------------------------------------
    def _payload(self, camera_name, kind, confidence, test, state):
        return {
            "source": "IAS-CMS",
            "event": "fire_alarm",
            "state": state,  # "trigger" یا "clear"
            "test": bool(test),
            "camera": camera_name,
            "kind": kind,    # fire | smoke
            "confidence": round(float(confidence or 0.0), 3),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _send_trigger_sync(self, camera_name, kind, confidence, test):
        backend = self.config.get("backend", "webhook")
        try:
            if backend == "webhook":
                return self._webhook(self.config.get("webhook_url"),
                                     self._payload(camera_name, kind, confidence, test, "trigger"))
            elif backend == "simulator":
                return self._webhook(self.config.get("simulator_url"),
                                     self._payload(camera_name, kind, confidence, test, "trigger"),
                                     label="شبیه‌ساز")
            elif backend == "modbus_tcp":
                return self._modbus_write(True)
            elif backend == "serial_relay":
                return self._serial_relay(True)
            else:
                return False, f"روش اتصال ناشناخته: {backend}"
        except Exception as e:
            return False, f"خطا در ارسال آلارم به سیستم ساختمان: {e}"

    def _send_clear_sync(self):
        backend = self.config.get("backend", "webhook")
        try:
            if backend == "webhook":
                return self._webhook(self.config.get("webhook_url"),
                                     self._payload("", "", 0.0, False, "clear"))
            elif backend == "simulator":
                return self._webhook(self.config.get("simulator_url"),
                                     self._payload("", "", 0.0, False, "clear"),
                                     label="شبیه‌ساز")
            elif backend == "modbus_tcp":
                return self._modbus_write(False)
            elif backend == "serial_relay":
                return self._serial_relay(False)
            else:
                return False, f"روش اتصال ناشناخته: {backend}"
        except Exception as e:
            return False, f"خطا در پاک‌سازی آلارم ساختمان: {e}"

    # -- webhook / simulator ----------------------------------------------
    def _webhook(self, url, payload, label="پنل ساختمان"):
        if not url:
            return False, "آدرس Webhook خالی است؛ ابتدا آن را در تنظیمات وارد کنید."
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read(500).decode("utf-8", "ignore")
            state = "فعال شد ✅" if payload["state"] == "trigger" else "ریست شد ✅"
            tag = " (تست)" if payload.get("test") else ""
            return True, f"{label}: آلارم {state}{tag} — پاسخ: {body[:80]}"
        except urllib.error.URLError as e:
            return False, (f"{label}: اتصال ناموفق بود ❌ ({e}). "
                           "آدرس/پورت را بررسی کنید و مطمئن شوید پنل یا شبیه‌ساز روشن است.")

    # -- modbus -------------------------------------------------------------
    def _modbus_write(self, on):
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError:
            return False, "کتابخانه‌ی pymodbus نصب نیست (pip install pymodbus)."
        host = self.config.get("modbus_host", "192.168.1.100")
        port = int(self.config.get("modbus_port", 502))
        coil = int(self.config.get("modbus_coil", 0))
        slave = int(self.config.get("modbus_slave", 1))
        try:
            client = ModbusTcpClient(host, port=port, timeout=5)
            if not client.connect():
                return False, f"اتصال Modbus به {host}:{port} ناموفق بود ❌"
            try:
                r = client.write_coil(coil, on, slave=slave)
                if r is not None and getattr(r, "isError", lambda: False)():
                    return False, f"خطای Modbus هنگام نوشتن کویل {coil}: {r}"
            finally:
                client.close()
            return True, f"پنل Modbus: کویل {coil} {'فعال' if on else 'ریست'} شد ✅"
        except Exception as e:
            return False, f"خطای Modbus ❌: {e}"

    # -- serial relay --------------------------------------------------------
    def _serial_relay(self, on):
        try:
            import serial
        except ImportError:
            return False, "کتابخانه‌ی pyserial نصب نیست (pip install pyserial)."
        port = self.config.get("serial_port", "COM3")
        baud = int(self.config.get("serial_baud", 9600))
        # پروتکل رایج ماژول‌های رله‌ی USB تک‌کاناله: بایت‌های ساده
        # ON  = 0xA0 0x01 0x01 0xA2   /  OFF = 0xA0 0x01 0x00 0xA1
        # اگر ماژول شما پروتکل دیگری دارد، این دو خط را عوض کنید.
        cmd = b"\xA0\x01\x01\xA2" if on else b"\xA0\x01\x00\xA1"
        try:
            ser = serial.Serial(port, baud, timeout=3)
            try:
                ser.write(cmd)
                ser.flush()
            finally:
                ser.close()
            return True, f"رله‌ی سریال ({port}): {'فعال' if on else 'ریست'} شد ✅"
        except Exception as e:
            return False, f"خطای رله‌ی سریال ({port}) ❌: {e}"

    # -- auto clear -----------------------------------------------------------
    def _schedule_auto_clear(self, seconds):
        if self._clear_timer is not None:
            self._clear_timer.cancel()
        self._clear_timer = threading.Timer(seconds, self.clear)
        self._clear_timer.daemon = True
        self._clear_timer.start()

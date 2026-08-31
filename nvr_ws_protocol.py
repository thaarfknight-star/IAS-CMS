"""پروتکل وب اختصاصی (WebSocket) برخی NVRها -- بازسازی‌شده از تحلیل کد جاوااسکریپت
وب‌کلاینت خود دستگاه (نه از مستندات رسمی؛ به همین دلیل چند نکته زیر صراحتاً
best-effort/قابل‌تنظیم علامت‌گذاری شده‌اند).

این پروتکل برای NVRهایی استفاده می‌شود که سرویس RTSP استاندارد ندارند یا آن را
باز نمی‌گذارند، اما وب‌کلاینت «بدون پلاگین» (no-plugin) خودشان مستقیماً از طریق
یک اتصال WebSocket با ساب‌پروتکل ``dev_man_protocol`` روی همان دستگاه پخش زنده
می‌گیرد.

هندشیک احراز هویت (تایید شده از JS واقعی -- GlobalFile.js):
    Client -> {"cmd":"get_login_key","data":{"channel":0}}
    Server -> {"code":0,"cmd":"get_login_key","data":{"key":"<challenge>"}}
    Client -> {"cmd":"dev_conn_auth","data":{"account":user,"key":challenge,
                                              "value":MD5(challenge),"mold":5}}
    Server -> {"code":0,"cmd":"dev_conn_auth", ...}

دستور باز/بستن پخش زنده‌ی یک کانال (تایید شده از JS):
    {"cmd":"live","data":{"action":"open"|"close","params":[{"type":stream,"channel":ch}]}}

نکته‌ی مهم/محدودیت شناخته‌شده:
    فرمت دقیق هدر فریم‌های باینری (بعد از باز شدن پخش زنده) در JS قابل‌خواندن
    نبود -- مستقیم و دست‌نخورده به یک Worker با یک ماژول کامپایل‌شده (WASM)
    پاس داده می‌شود. بنابراین اینجا فرض می‌شود payload یا مستقیماً Annex-B
    (شروع‌شده با start code های 00 00 00 01 / 00 00 01) است یا این start code
    بعد از چند بایت هدر ناشناخته می‌آید؛ به همین دلیل استخراج NAL با اسکن
    start-code انجام می‌شود (رجوع کنید به annexb_extract.py) که نسبت به طول/
    محتوای دقیق هدر ناشناخته مقاوم است. اگر این فرض برای یک دستگاه خاص درست
    از آب درنیاید (کدک/محتوای متفاوت)، ``camera_stream.py`` این را با خطای
    روشن گزارش می‌کند و فریم‌های خام را برای بررسی دستی ذخیره می‌کند (رجوع
    کنید به ``dump_path``).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import websocket  # pip install websocket-client


class NVRWebSocketError(Exception):
    pass


def encrypt_md5(challenge: str) -> str:
    """رجوع کنید به encryptMD5() در GlobalFile.js.

    نکته: این پیاده‌سازی فرض می‌کند مقدار ارسالی صرفاً md5(challenge) است.
    اگر روی یک دستگاه خاص احراز هویت رد شد (کد غیر صفر برای dev_conn_auth)،
    محتمل‌ترین علت این است که سرور واقعاً md5(user+pwd+challenge) یا
    md5(pwd+challenge) می‌خواهد -- فایل md5_....js.download خود دستگاه (از
    طریق DevTools) را چک کنید و این تابع را مطابق آن اصلاح کنید.
    """
    return hashlib.md5(challenge.encode("utf-8")).hexdigest()


class NVRWebSocketSession:
    """یک اتصال WebSocket احراز هویت‌شده به کانال کنترل NVR.

    استفاده:
        s = NVRWebSocketSession(ip, port, user, pwd)
        s.connect_and_auth()
        s.open_live(channel=1)
        while True:
            frame = s.recv_frame(timeout=2)
            if frame:
                ...
    """

    def __init__(self, ip: str, port: int, user: str = "", pwd: str = "",
                 use_tls: bool = False, timeout: float = 5.0):
        self.ip = ip
        self.port = int(port)
        self.user = user
        self.pwd = pwd
        self.use_tls = use_tls
        self.timeout = timeout
        self.ws: Optional[websocket.WebSocket] = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        scheme = "wss" if self.use_tls else "ws"
        return f"{scheme}://{self.ip}:{self.port}"

    def connect_and_auth(self):
        try:
            self.ws = websocket.create_connection(
                self.url, subprotocols=["dev_man_protocol"], timeout=self.timeout,
            )
        except Exception as exc:
            raise NVRWebSocketError(f"اتصال WebSocket به {self.url} برقرار نشد: {exc}") from exc

        try:
            self._send({"cmd": "get_login_key", "data": {"channel": 0}})
            resp = self._recv_json(required_cmd="get_login_key")
            challenge = resp["data"]["key"]

            self._send({
                "cmd": "dev_conn_auth",
                "data": {
                    "account": self.user,
                    "key": challenge,
                    "value": encrypt_md5(challenge),
                    "mold": 5,
                },
            })
            auth_resp = self._recv_json(required_cmd="dev_conn_auth")
            if auth_resp.get("code") != 0:
                raise NVRWebSocketError(
                    f"احراز هویت رد شد (code={auth_resp.get('code')}) -- نام کاربری/رمز "
                    "عبور را بررسی کنید (یا الگوریتم encrypt_md5 را با md5.js دستگاه مقایسه کنید)."
                )
        except NVRWebSocketError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise NVRWebSocketError(f"خطا در هندشیک احراز هویت: {exc}") from exc

    def open_live(self, channel: int, stream_type: str = "video1"):
        self._send({
            "cmd": "live",
            "data": {"action": "open", "params": [{"type": stream_type, "channel": channel}]},
        })

    def close_live(self, channel: int, stream_type: str = "video1"):
        try:
            self._send({
                "cmd": "live",
                "data": {"action": "close", "params": [{"type": stream_type, "channel": channel}]},
            })
        except Exception:
            pass  # بستن اتصال؛ نیازی به گزارش خطا نیست

    def recv_frame(self, timeout: Optional[float] = None):
        """یک فریم را برمی‌گرداند: bytes برای داده‌ی باینری (تصویر)، یا dict
        برای پیام‌های کنترلی متنی (مثلاً تایید/خطای دستور live)، یا None اگر
        در بازه‌ی timeout چیزی نرسید."""
        if self.ws is None:
            raise NVRWebSocketError("اتصال برقرار نیست")
        prev_timeout = self.ws.gettimeout()
        try:
            self.ws.settimeout(timeout)
            opcode, data = self.ws.recv_data()
        except websocket.WebSocketTimeoutException:
            return None
        finally:
            try:
                self.ws.settimeout(prev_timeout)
            except Exception:
                pass

        if opcode == websocket.ABNF.OPCODE_BINARY:
            return data
        if opcode == websocket.ABNF.OPCODE_TEXT:
            try:
                return json.loads(data)
            except ValueError:
                return None
        return None

    def _send(self, obj: dict):
        if self.ws is None:
            raise NVRWebSocketError("اتصال برقرار نیست")
        self.ws.send(json.dumps(obj))

    def _recv_json(self, required_cmd: str, attempts: int = 5) -> dict:
        for _ in range(attempts):
            frame = self.recv_frame(timeout=self.timeout)
            if isinstance(frame, dict) and frame.get("cmd") == required_cmd:
                return frame
            # پیام باینری/متنی نامرتبط (مثلاً باقی‌مانده‌ی یک پخش قبلی) -- رد شود.
        raise NVRWebSocketError(f"پاسخ '{required_cmd}' از NVR دریافت نشد (timeout)")

    def close(self):
        with self._lock:
            if self.ws is not None:
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None


@dataclass
class ChannelProbeResult:
    channel: int
    ok: bool
    detail: str = ""


def probe_channels(
    ip: str, port: int, user: str, pwd: str,
    max_channels: int = 16, stream_type: str = "video1",
    wait_sec: float = 1.5, use_tls: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> List[ChannelProbeResult]:
    """کانال‌های ۱..max_channels را روی یک اتصال WS اشتراکی امتحان می‌کند: هر
    کانال باز می‌شود و اگر حداکثر تا wait_sec ثانیه حتی یک فریم باینری برسد،
    کانال «موجود» در نظر گرفته می‌شود (بدون نیاز به دیکود واقعی محتوا -- فقط
    برای کشف تعداد/شماره‌ی کانال‌های فعال، دقیقاً مثل DESCRIBE سریع در
    rtsp_probe.py برای RTSP).

    برخلاف brute-force RTSP (که هر کانال یک اتصال TCP/احراز هویت جداگانه
    نیاز دارد)، اینجا فقط یک هندشیک انجام می‌شود و همه‌ی کانال‌ها روی همان
    اتصال امتحان می‌شوند -- در نتیجه برای NVRهای پرکانال معمولاً بسیار
    سریع‌تر از brute-force RTSP است.
    """
    session = NVRWebSocketSession(ip, port, user, pwd, use_tls=use_tls)
    results: List[ChannelProbeResult] = []
    try:
        session.connect_and_auth()
    except NVRWebSocketError as exc:
        return [ChannelProbeResult(channel=0, ok=False, detail=str(exc))]

    try:
        for ch in range(1, max_channels + 1):
            if cancel_check and cancel_check():
                break
            try:
                session.open_live(ch, stream_type)
            except NVRWebSocketError as exc:
                results.append(ChannelProbeResult(ch, False, str(exc)))
                continue

            got_binary = False
            deadline = time.monotonic() + wait_sec
            while time.monotonic() < deadline:
                frame = session.recv_frame(timeout=max(0.05, deadline - time.monotonic()))
                if isinstance(frame, (bytes, bytearray)) and frame:
                    got_binary = True
                    break
                if isinstance(frame, dict) and frame.get("cmd") == "live" and frame.get("code", 0) not in (0,):
                    break  # سرور صریحاً این کانال را رد کرد

            session.close_live(ch, stream_type)
            results.append(ChannelProbeResult(ch, got_binary))
    finally:
        session.close()

    return results

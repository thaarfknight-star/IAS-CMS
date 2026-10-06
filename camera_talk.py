# -*- coding: utf-8 -*-
"""اتصال به بلندگوی دوربین — «صحبت با دوربین» (نسخه‌ی 2.0.81-beta).

معماری (استاندارد و بدون وابستگی جدید):
- شناسایی پشتیبانی: ONVIF Media Service → GetAudioOutputConfigurations
  (اگر دوربین خروجی صدا داشته باشد، از بک‌چنل صوتی پشتیبانی می‌کند).
- ارسال صدا: RTSP Audio Backchannel استاندارد ONVIF —
  ANNOUNCE با SDP (PCMU/8000) ← SETUP با
  Require: www.onvif.org/ver20/backchannel ← RECORD ← بسته‌های RTP
  به‌صورت interleaved روی همان اتصال TCP.
- ضبط میکروفون: QAudioSource از PyQt6.QtMultimedia (از قبل برای صدای آژیر
  در alarm_sound.py استفاده می‌شود؛ چیزی به requirements اضافه نشد).
- انکد G.711 μ-law با پایتون خالص؛ اگر میکروفون ۸kHz مونو ندهد، با numpy
  (که از قبل وابستگی است) به ۸kHz مونو ری‌سمپل می‌شود.

محدوده: فقط دوربین‌های مستقیم (بدون NVR). برای کانال‌های NVR پیام روشن
نمایش داده می‌شود.

مدل تردینگ: خود TalkSession در ترد اصلی (GUI) زندگی می‌کند؛ ضبط میکروفون
(QAudioSource) هم همان‌جاست و فقط ارسال بسته‌های کوچک RTP روی سوکت انجام
می‌شود (بلاکینگ نیست). فقط `connect_to_camera` (هندشیک چندثانیه‌ای) و
`detect_talk_support` بلاکینگ‌اند و باید در ترد جدا اجرا شوند (مثل الگوی
_DetectThread در ptz_dialog.py). کال‌بک‌ها ممکن است از ترد ورکر صدا زده
شوند؛ صداکننده باید آن‌ها را thread-safe کند (مثلاً با pyqtSignal).
"""

import hashlib
import random
import socket
import struct
import time
from urllib.parse import urlparse

from app_paths import get_onvif_wsdl_dir

# پورت‌های کاندید ONVIF — همان الگوی ptz_control.py
COMMON_ONVIF_PORTS = [80, 8080, 8000, 8899, 8008, 8081]

# صدا: ۸kHz مونو؛ هر بسته‌ی RTP دقیقاً ۲۰ میلی‌ثانیه = ۱۶۰ نمونه
TALK_SAMPLE_RATE = 8000
TALK_FRAME_SAMPLES = 160
TALK_PT = 0  # PCMU


# ---------------------------------------------------------------------------
# G.711 μ-law (پایتون خالص، بر اساس ITU-T G.711)
# ---------------------------------------------------------------------------

def linear_to_ulaw(pcm: int) -> int:
    """نمونه‌ی ۱۶ بیتی علامت‌دار PCM ← یک بایت μ-law."""
    BIAS = 0x84
    CLIP = 32635
    pcm = int(pcm)
    sign = 0x80 if pcm < 0 else 0x00
    if pcm < 0:
        pcm = -pcm
    if pcm > CLIP:
        pcm = CLIP
    pcm += BIAS
    # اکسپوننت: موقعیت بالاترین بیت ۱ در بیت‌های ۱۴..۷
    exp = 0
    for i in range(7, 0, -1):
        if pcm & (1 << (i + 7)):
            exp = i
            break
    mantissa = (pcm >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mantissa)) & 0xFF


def encode_ulaw_frame(pcm16_bytes: bytes) -> bytes:
    """بافر PCM16 لیتل‌اندین مونو ← بایت‌های μ-law (نصف حجم)."""
    import numpy as np
    samples = np.frombuffer(pcm16_bytes, dtype="<i2")
    return bytes(linear_to_ulaw(int(s)) for s in samples)


def ulaw_frame_rms(pcm16_bytes: bytes) -> float:
    """سطح صدای فریم (۰ تا ۱) برای نمایش میتر میکروفون."""
    import numpy as np
    samples = np.frombuffer(pcm16_bytes, dtype="<i2").astype("float32")
    if samples.size == 0:
        return 0.0
    rms = float((samples ** 2).mean() ** 0.5) / 32768.0
    return max(0.0, min(1.0, rms))


# ---------------------------------------------------------------------------
# RTP
# ---------------------------------------------------------------------------

def build_rtp_packet(seq: int, timestamp: int, ssrc: int, payload: bytes,
                     marker: bool = False) -> bytes:
    """هدر RTP (نسخه‌ی ۲، PCMU) + پیلود."""
    b0 = 0x80
    b1 = (0x80 if marker else 0x00) | (TALK_PT & 0x7F)
    header = struct.pack(">BBHII", b0, b1, seq & 0xFFFF,
                         timestamp & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)
    return header + payload


def build_rtp_interleaved(seq: int, timestamp: int, ssrc: int,
                          payload: bytes) -> bytes:
    """فریم interleaved روی TCP: ‎$‎ + کانال ۰ + طول + بسته‌ی RTP."""
    rtp = build_rtp_packet(seq, timestamp, ssrc, payload)
    return b"$" + bytes((0,)) + struct.pack(">H", len(rtp)) + rtp


# ---------------------------------------------------------------------------
# SDP برای ANNOUNCE بک‌چنل
# ---------------------------------------------------------------------------

def build_backchannel_sdp(client_ip: str) -> str:
    ntp = int(time.time()) + 2208988800  # ثانیه‌های NTP
    lines = [
        "v=0",
        f"o=- {ntp} {ntp} IN IP4 {client_ip}",
        "s=IAS Camera Talk",
        f"c=IN IP4 {client_ip}",
        "t=0 0",
        "m=audio 0 RTP/AVP 0",
        "a=rtpmap:0 PCMU/8000",
        "a=sendonly",
        "",
    ]
    return "\r\n".join(lines)


# ---------------------------------------------------------------------------
# احراز هویت Digest برای RTSP (RFC 2617)
# ---------------------------------------------------------------------------

def _parse_challenge(header_value: str) -> dict:
    out = {}
    rest = header_value.strip()
    if rest.lower().startswith("digest"):
        rest = rest[6:].strip()
    # پارس ساده‌ی key="value" / key=value
    import re
    for m in re.finditer(r'(\w+)=("(?:[^"\\]|\\.)*"|[^,\s]+)', rest):
        k, v = m.group(1), m.group(2)
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k.lower()] = v
    return out


def build_digest_auth(username: str, password: str, method: str, uri: str,
                      challenge: dict, cnonce: str = None) -> str:
    """ساخت هدر Authorization برای Digest."""
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = challenge.get("qop", "")
    algorithm = challenge.get("algorithm", "MD5")
    opaque = challenge.get("opaque", "")

    def H(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    def KD(s: str) -> str:
        return H(s)

    ha1 = H(f"{username}:{realm}:{password}")
    ha2 = H(f"{method}:{uri}")
    # (2.0.118-beta) algorithm را فقط اگر سرور در چلنج فرستاده باشد می‌فرستیم؛
    # بعضی دوربین‌ها (مثل Sunell) با algorithm صریح مشکل دارند.
    _alg_given = bool(challenge.get("algorithm"))
    if qop:
        # qop ممکن است "auth,auth-int" باشد؛ auth را برمی‌داریم
        qop = [q.strip() for q in qop.split(",") if "auth" in q][0]
        nc = "00000001"
        cnonce = cnonce or hashlib.md5(str(random.random()).encode()).hexdigest()[:16]
        resp = KD(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        parts = [f'username="{username}"', f'realm="{realm}"',
                 f'nonce="{nonce}"', f'uri="{uri}"', f"response={resp}"]
        if _alg_given:
            parts.append(f"algorithm={algorithm}")
        parts += [f"cnonce=\"{cnonce}\"",
                 f'opaque="{opaque}"' if opaque else None,
                 f"qop={qop}", "nc=00000001"]
    else:
        resp = KD(f"{ha1}:{nonce}:{ha2}")
        parts = [f'username="{username}"', f'realm="{realm}"',
                 f'nonce="{nonce}"', f'uri="{uri}"', f"response={resp}"]
        if _alg_given:
            parts.append(f"algorithm={algorithm}")
        if opaque:
            parts.append(f'opaque="{opaque}"')
    parts = [p for p in parts if p]
    return "Digest " + ", ".join(parts)


# ---------------------------------------------------------------------------
# کلاینت RTSP برای بک‌چنل صوتی
# ---------------------------------------------------------------------------

class RTSPError(Exception):
    pass


class RTSPBackchannel:
    """بک‌چنل صوتی ONVIF روی RTSP (فقط ارسال: میکروفون ← بلندگوی دوربین)."""

    BACKCHANNEL_REQUIRE = "www.onvif.org/ver20/backchannel"

    def __init__(self, url: str, username: str = "", password: str = "",
                 timeout: float = 8.0):
        # یوزر/پس را از URL جدا می‌کنیم؛ احرازهویت با Digest دستی انجام می‌شود
        parsed = urlparse(url)
        self.host = parsed.hostname or ""
        self.port = parsed.port or 554
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self.url = f"rtsp://{self.host}:{self.port}{path}"
        self.username = username or (parsed.username or "")
        self.password = password or (parsed.password or "")
        self.timeout = timeout
        self.sock = None
        self.cseq = 0
        self.session_id = ""
        self._auth_header = ""
        self.ssrc = random.getrandbits(32)
        self.seq = random.getrandbits(16)
        self.timestamp = random.getrandbits(32)

    # -- سطح پایین ----------------------------------------------------------
    def _read_response(self):
        f = self.sock.makefile("rb")
        try:
            status_line = f.readline().decode("iso-8859-1").strip()
            if not status_line.startswith("RTSP/"):
                raise RTSPError(f"پاسخ نامعتبر RTSP: {status_line[:60]}")
            code = int(status_line.split(" ", 2)[1])
            headers = {}
            while True:
                line = f.readline().decode("iso-8859-1")
                if line in ("\r\n", "\n", ""):
                    break
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            body = b""
            try:
                n = int(headers.get("content-length", "0"))
            except ValueError:
                n = 0
            if n > 0:
                body = f.read(n)
            return code, headers, body
        finally:
            f.close()

    def _request(self, method: str, headers: dict = None,
                 body: bytes = b"") -> tuple:
        self.cseq += 1
        lines = [f"{method} {self.url} RTSP/1.0",
                 f"CSeq: {self.cseq}"]
        if self.session_id:
            lines.append(f"Session: {self.session_id}")
        if self._auth_header:
            lines.append(f"Authorization: {self._auth_header}")
        for k, v in (headers or {}).items():
            lines.append(f"{k}: {v}")
        if body:
            lines.append(f"Content-Length: {len(body)}")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body
        self.sock.sendall(raw)
        code, resp_headers, resp_body = self._read_response()
        if code == 401 and not self._auth_header and self.username:
            www = resp_headers.get("www-authenticate", "")
            if "digest" in www.lower():
                challenge = _parse_challenge(www)
                self._auth_header = build_digest_auth(
                    self.username, self.password, method, self.url, challenge)
                return self._request(method, headers, body)
            raise RTSPError("دوربین احرازهویت Basic می‌خواهد (پشتیبانی نمی‌شود)")
        return code, resp_headers, resp_body

    # -- handshake ----------------------------------------------------------
    def connect(self):
        """اتصال TCP + ‏OPTIONS/ANNOUNCE/SETUP/RECORD. خطا ← RTSPError."""
        try:
            self.sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise RTSPError(f"اتصال به {self.host}:{self.port} نشد: {e}")
        self.sock.settimeout(self.timeout)

        code, _, _ = self._request("OPTIONS")
        if code not in (200,):
            raise RTSPError(f"OPTIONS رد شد (کد {code})")

        client_ip = self.sock.getsockname()[0]
        sdp = build_backchannel_sdp(client_ip).encode("utf-8")
        code, _, _ = self._request(
            "ANNOUNCE",
            {"Content-Type": "application/sdp",
             "Require": self.BACKCHANNEL_REQUIRE},
            sdp)
        if code != 200:
            raise RTSPError(f"ANNOUNCE رد شد (کد {code}) — دوربین بک‌چنل را قبول نکرد")

        code, headers, _ = self._request(
            "SETUP",
            {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1",
             "Require": self.BACKCHANNEL_REQUIRE})
        if code != 200:
            raise RTSPError(f"SETUP رد شد (کد {code})")
        session = headers.get("session", "")
        self.session_id = session.split(";")[0].strip()
        if not self.session_id:
            raise RTSPError("دوربین Session برنگرداند")

        code, _, _ = self._request(
            "RECORD",
            {"Require": self.BACKCHANNEL_REQUIRE, "Range": "npt=0-"})
        if code != 200:
            raise RTSPError(f"RECORD رد شد (کد {code})")

    def send_audio_frame(self, ulaw_payload: bytes):
        """ارسال یک فریم ۲۰ms صدای μ-law (۱۶۰ بایت)."""
        if not self.sock:
            raise RTSPError("اتصال باز نیست")
        frame = build_rtp_interleaved(self.seq, self.timestamp, self.ssrc,
                                      ulaw_payload)
        self.sock.sendall(frame)
        self.seq = (self.seq + 1) & 0xFFFF
        self.timestamp = (self.timestamp + TALK_FRAME_SAMPLES) & 0xFFFFFFFF

    def close(self):
        try:
            if self.sock and self.session_id:
                try:
                    self._request("TEARDOWN")
                except Exception:
                    pass
        finally:
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None
            self.session_id = ""
            self._auth_header = ""


# ---------------------------------------------------------------------------
# شناسایی پشتیبانی از بلندگو (ONVIF)
# ---------------------------------------------------------------------------

def _iter_onvif_ports(cam):
    """پورت‌های کاندید ONVIF: اول onvif_port، بعد port، بعد پورت‌های رایج."""
    seen = []
    for key in ("onvif_port", "port"):
        try:
            v = int(cam.get(key) or 0)
        except Exception:
            v = 0
        if v > 0 and v not in seen:
            seen.append(v)
            yield v
    for p in COMMON_ONVIF_PORTS:
        if p not in seen:
            yield p


def _cam_ip(cam):
    return (cam.get("camera_ip") or cam.get("ip") or "").strip()


def detect_talk_support(cam) -> dict:
    """آیا دوربین خروجی صدا (بلندگو) دارد؟

    خروجی: {"supported": bool, "onvif_port": int|None, "error": str|None}
    """
    if cam.get("nvr_id"):
        return {"supported": False, "onvif_port": None,
                "error": "nvr_channel"}
    try:
        from onvif import ONVIFCamera
    except ImportError:
        return {"supported": False, "onvif_port": None,
                "error": "کتابخانه‌ی onvif نصب نیست"}
    ip = _cam_ip(cam)
    user = cam.get("user", "") or ""
    pwd = cam.get("pass", "") or ""
    last_err = ""
    for port in _iter_onvif_ports(cam):
        try:
            dev = ONVIFCamera(ip, int(port), user, pwd,
                              wsdl_dir=get_onvif_wsdl_dir())
            media = dev.create_media_service()
            # راه اصلی: لیست کانفیگ‌های خروجی صدا
            try:
                cfgs = media.GetAudioOutputConfigurations()
                if cfgs:
                    return {"supported": True, "onvif_port": int(port),
                            "error": None}
            except Exception:
                pass
            # راه جایگزین: خروجی صدا داخل یکی از پروفایل‌ها
            try:
                for profile in media.GetProfiles() or []:
                    if getattr(profile, "AudioOutputConfiguration", None):
                        return {"supported": True, "onvif_port": int(port),
                                "error": None}
            except Exception:
                pass
            last_err = "خروجی صدا پیدا نشد"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:80]
            continue
    return {"supported": False, "onvif_port": None,
            "error": last_err or "دوربین در دسترس نیست"}


def build_backchannel_url(cam) -> str:
    """آدرس RTSP برای بک‌چنل — همان آدرس استریم دوربین (بدون یوزر/پس در URL)."""
    from camera_store import CameraStore
    url = CameraStore.build_rtsp_url(cam)
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    host = parsed.hostname or _cam_ip(cam)
    port = parsed.port or int(cam.get("port") or 554)
    return f"rtsp://{host}:{port}{path}"


# ---------------------------------------------------------------------------
# نشست صحبت (میکروفون ← بک‌چنل) — در QThread جدا اجرا می‌شود
# ---------------------------------------------------------------------------

class TalkSession:
    """مدیریت کامل یک نشست push-to-talk (در ترد اصلی/GUI).

    استفاده:
        session = TalkSession()
        session.on_state = lambda s: ...
        session.on_error = lambda s: ...
        session.on_level = lambda v: ...
        # در ترد ورکر (بلاکینگ):
        session.connect_to_camera(cam)
        # در ترد اصلی:
        session.start_talking()          # شروع ضبط میکروفون و ارسال RTP
        session.stop_talking()           # توقف ضبط (اتصال باز می‌ماند)
        session.disconnect_session()     # بستن اتصال
    """

    def __init__(self):
        self.on_state = None
        self.on_error = None
        self.on_level = None
        self._rtsp = None
        self._audio = None
        self._io = None
        self._talking = False
        self._buf = b""
        self._need_resample = False
        self._src_rate = TALK_SAMPLE_RATE
        self._src_ch = 1

    def _emit_state(self, s: str):
        if self.on_state:
            self.on_state(s)

    def _emit_error(self, s: str):
        if self.on_error:
            self.on_error(s)

    # -- اتصال -------------------------------------------------------------
    def connect_to_camera(self, cam: dict):
        self._emit_state("connecting")
        url = build_backchannel_url(cam)
        self._rtsp = RTSPBackchannel(url, cam.get("user", "") or "",
                                    cam.get("pass", "") or "")
        try:
            self._rtsp.connect()
        except RTSPError as e:
            self._rtsp = None
            self._emit_error(str(e))
            return False
        except Exception as e:  # noqa: BLE001
            self._rtsp = None
            self._emit_error(f"خطای اتصال: {e}"[:120])
            return False
        self._emit_state("connected")
        return True

    # -- میکروفون -----------------------------------------------------------
    def _open_microphone(self) -> bool:
        try:
            from PyQt6.QtMultimedia import (QAudioDevice, QAudioFormat,
                                            QAudioSource, QMediaDevices)
        except ImportError:
            self._emit_error("QtMultimedia در دسترس نیست")
            return False
        dev: QAudioDevice = QMediaDevices.defaultAudioInput()
        if dev.isNull():
            self._emit_error("میکروفونی پیدا نشد")
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(TALK_SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._need_resample = False
        if not dev.isFormatSupported(fmt):
            fmt = dev.preferredFormat()
            self._need_resample = True
            self._src_rate = fmt.sampleRate()
            self._src_ch = max(1, fmt.channelCount())
        self._audio = QAudioSource(dev, fmt)
        self._io = self._audio.start()
        try:
            self._io.readyRead.connect(self._on_audio_ready)
        except Exception:
            pass
        self._buf = b""
        return True

    def _resample_to_8k(self, pcm_bytes: bytes) -> bytes:
        """preferredFormat ← ۸kHz مونو (numpy)."""
        import numpy as np
        samples = np.frombuffer(pcm_bytes, dtype="<i2").astype("float32")
        if self._src_ch > 1 and samples.size:
            samples = samples.reshape(-1, self._src_ch).mean(axis=1)
        if samples.size == 0 or self._src_rate == TALK_SAMPLE_RATE:
            return np.clip(samples, -32768, 32767).astype("<i2").tobytes()
        n_out = max(1, int(samples.size * TALK_SAMPLE_RATE / self._src_rate))
        old_idx = np.arange(samples.size)
        new_idx = np.linspace(0, samples.size - 1, n_out)
        res = np.interp(new_idx, old_idx, samples)
        return np.clip(res, -32768, 32767).astype("<i2").tobytes()

    def _on_audio_ready(self):
        if not self._talking or not self._io:
            return
        try:
            chunk = bytes(self._io.readAll())
        except Exception:
            return
        if not chunk:
            return
        if self._need_resample:
            chunk = self._resample_to_8k(chunk)
        self._buf += chunk
        frame_bytes = TALK_FRAME_SAMPLES * 2
        while len(self._buf) >= frame_bytes:
            frame = self._buf[:frame_bytes]
            self._buf = self._buf[frame_bytes:]
            try:
                ulaw = encode_ulaw_frame(frame)
                if self._rtsp:
                    self._rtsp.send_audio_frame(ulaw)
                if self.on_level:
                    self.on_level(ulaw_frame_rms(frame))
            except Exception as e:  # noqa: BLE001
                self._emit_error(f"خطا در ارسال صدا: {e}"[:100])
                self.stop_talking()
                break

    def start_talking(self):
        if self._talking or not self._rtsp:
            return False
        if not self._open_microphone():
            return False
        self._talking = True
        self._emit_state("talking")
        return True

    def stop_talking(self):
        self._talking = False
        try:
            if self._audio:
                self._audio.stop()
        except Exception:
            pass
        self._audio = None
        self._io = None
        self._buf = b""
        if self.on_level:
            self.on_level(0.0)
        self._emit_state("connected")

    def disconnect_session(self):
        self.stop_talking()
        try:
            if self._rtsp:
                self._rtsp.close()
        except Exception:
            pass
        self._rtsp = None
        self._emit_state("idle")

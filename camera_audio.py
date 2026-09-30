# -*- coding: utf-8 -*-
"""شنیدن صدای دوربین — «🎧 شنیدن صدای دوربین» (نسخه‌ی 2.0.82-beta).

معماری (استاندارد و بدون وابستگی جدید):
- دریافت صدا: RTSP استاندارد — OPTIONS ← DESCRIBE (پیداکردن ترک صوتی در
  SDP) ← SETUP ترک صوتی (RTP/AVP/TCP interleaved) ← PLAY ← بسته‌های RTP
  صوتی روی همان اتصال TCP.
- دیکد: G.711 μ-law (رایج‌ترین) و A-law به PCM16.
- پخش: QAudioSink از PyQt6.QtMultimedia (از قبل برای صدای آژیر استفاده
  می‌شود؛ چیزی به requirements اضافه نشد) — ۸kHz مونو.
- احراز هویت Digest از camera_talk بازاستفاده می‌شود (همان RFC 2617).

مدل تردینگ: RTSPAudioClient خالص و بلاکینگ است و در QThread ورکر اجرا
می‌شود؛ فریم‌های PCM با pyqtSignal به ترد اصلی می‌آیند و همان‌جا در
QAudioSink نوشته می‌شوند. ولوم/میوت با QAudioSink.setVolume اعمال می‌شود.

محدوده: هر دوربینی که ترک صوتی در استریم RTSP داشته باشد (مستقیم یا کانال
NVR)؛ اگر ترک صوتی نباشد پیام روشن نمایش داده می‌شود.
"""

import re
import socket
import struct
import time
from urllib.parse import urlparse

import numpy as np

from camera_talk import RTSPError, _parse_challenge, build_digest_auth

# صدای دوربین‌ها معمولاً ۸kHz مونو است؛ خروجی QAudioSink هم همین است.
AUDIO_SAMPLE_RATE = 8000
AUDIO_CHANNELS = 1

# نگاشت payload type ثابت به کدک (RFC 3551)
STATIC_PT_CODEC = {0: "PCMU", 8: "PCMA"}


# ---------------------------------------------------------------------------
# G.711 — دیکد μ-law و A-law (پایتون خالص، بر اساس ITU-T G.711)
# ---------------------------------------------------------------------------

def ulaw_to_linear(u: int) -> int:
    """یک بایت μ-law ← نمونه‌ی ۱۶ بیتی علامت‌دار PCM."""
    u = (~u) & 0xFF
    sign = u & 0x80
    exp = (u >> 4) & 0x07
    mant = u & 0x0F
    sample = ((mant << 3) + 0x84) << exp
    if sign:
        return 0x84 - sample
    return sample - 0x84


def alaw_to_linear(a: int) -> int:
    """یک بایت A-law ← نمونه‌ی ۱۶ بیتی علامت‌دار PCM."""
    a ^= 0x55
    sign = a & 0x80
    exp = (a >> 4) & 0x07
    mant = a & 0x0F
    t = (mant << 4) + (0x100 if exp else 8)
    if exp:
        t <<= (exp - 1)
    sample = t
    return -sample if sign else sample


def decode_audio_payload(payload: bytes, codec: str) -> bytes:
    """پیلود RTP صوتی ← بافر PCM16 لیتل‌اندین مونو."""
    codec = (codec or "PCMU").upper()
    if codec == "PCMA":
        conv = alaw_to_linear
    else:  # PCMU پیش‌فرض
        conv = ulaw_to_linear
    out = bytearray(len(payload) * 2)
    for i, b in enumerate(payload):
        struct.pack_into("<h", out, i * 2, conv(b))
    return bytes(out)


def pcm16_rms(pcm16_bytes: bytes) -> float:
    """سطح صدای فریم (۰ تا ۱) برای میتر."""
    samples = np.frombuffer(pcm16_bytes, dtype="<i2").astype("float32")
    if samples.size == 0:
        return 0.0
    rms = float((samples ** 2).mean() ** 0.5) / 32768.0
    return max(0.0, min(1.0, rms))


def resample_to_8k(pcm16_bytes: bytes, src_rate: int) -> bytes:
    """ری‌سمپل ساده به ۸kHz مونو (برای دوربین‌هایی با کلاک غیر ۸kHz)."""
    if src_rate == AUDIO_SAMPLE_RATE or not pcm16_bytes:
        return pcm16_bytes
    samples = np.frombuffer(pcm16_bytes, dtype="<i2").astype("float32")
    n_out = max(1, int(samples.size * AUDIO_SAMPLE_RATE / src_rate))
    new_idx = np.linspace(0, samples.size - 1, n_out)
    res = np.interp(new_idx, np.arange(samples.size), samples)
    return np.clip(res, -32768, 32767).astype("<i2").tobytes()


# ---------------------------------------------------------------------------
# پارس RTP دریافتی
# ---------------------------------------------------------------------------

def parse_rtp_packet(pkt: bytes):
    """بسته‌ی RTP ← (payload_type, seq, timestamp, payload) یا None."""
    if len(pkt) < 12 or (pkt[0] >> 6) != 2:
        return None
    b0, b1 = pkt[0], pkt[1]
    cc = b0 & 0x0F
    pt = b1 & 0x7F
    seq, ts = struct.unpack(">HI", pkt[2:8])
    hlen = 12 + 4 * cc
    if b0 & 0x10:  # extension header
        if len(pkt) < hlen + 4:
            return None
        ext_words = struct.unpack(">H", pkt[hlen + 2:hlen + 4])[0]
        hlen += 4 + 4 * ext_words
    if len(pkt) < hlen:
        return None
    payload = pkt[hlen:]
    if b0 & 0x20 and payload:  # padding
        payload = payload[:len(payload) - payload[-1]]
    return pt, seq, ts, payload


# ---------------------------------------------------------------------------
# پارس SDP — پیداکردن ترک صوتی
# ---------------------------------------------------------------------------

def parse_sdp_audio(sdp_text: str, base_url: str):
    """پیداکردن اولین ترک صوتی قابل‌پخش در SDP.

    خروجی: dict با کلیدهای control_url / pt / codec / clock / channels
    یا None اگر ترک صوتی نباشد.
    """
    sections = []
    current = None
    for raw_line in sdp_text.splitlines():
        line = raw_line.strip()
        if line.startswith("m="):
            current = {"m": line[2:], "rtpmap": {}, "control": None}
            sections.append(current)
        elif current is not None and line.startswith("a="):
            attr = line[2:]
            if attr.startswith("rtpmap:"):
                # a=rtpmap:0 PCMU/8000[/1]
                try:
                    pt_s, codec_s = attr[7:].split(None, 1)
                    pt = int(pt_s)
                    parts = codec_s.split("/")
                    current["rtpmap"][pt] = {
                        "codec": parts[0],
                        "clock": int(parts[1]) if len(parts) > 1 else 8000,
                        "channels": int(parts[2]) if len(parts) > 2 else 1,
                    }
                except (ValueError, IndexError):
                    pass
            elif attr.startswith("control:"):
                current["control"] = attr[8:].strip()

    for sec in sections:
        parts = sec["m"].split()
        if len(parts) < 4 or parts[0].lower() != "audio":
            continue
        pt_list = []
        for p in parts[3:]:
            try:
                pt_list.append(int(p))
            except ValueError:
                pass
        for pt in pt_list:
            info = sec["rtpmap"].get(pt)
            if info:
                codec, clock, ch = (info["codec"], info["clock"],
                                    info["channels"])
            else:
                codec = STATIC_PT_CODEC.get(pt)
                if not codec:
                    continue
                clock, ch = 8000, 1
            control = sec["control"] or ""
            return {
                "control_url": _resolve_control_url(control, base_url),
                "pt": pt,
                "codec": codec,
                "clock": clock,
                "channels": ch,
            }
    return None


def _resolve_control_url(control: str, base_url: str) -> str:
    control = control.strip().strip("<>")
    if "://" in control:
        return control
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        origin += f":{parsed.port}"
    if control.startswith("/"):
        return origin + control
    base = base_url.rsplit("/", 1)[0] if "/" in base_url else base_url
    # query را از انتهای base حذف می‌کنیم
    base = base.split("?")[0]
    return f"{base}/{control}" if control else base_url


# ---------------------------------------------------------------------------
# کلاینت RTSP گیرنده‌ی صدا (خالص، بلاکینگ — در ترد ورکر اجرا شود)
# ---------------------------------------------------------------------------

class RTSPAudioClient:
    """گیرنده‌ی ترک صوتی استریم RTSP (فقط دریافت).

    استفاده:
        c = RTSPAudioClient(url, user, pwd)
        info = c.connect()          # dict ترک صوتی؛ بدون ترک ← RTSPError
        while True:
            item = c.read_audio_frame()   # (codec, clock, payload) یا None
        c.close()
    """

    def __init__(self, url: str, username: str = "", password: str = "",
                 timeout: float = 8.0, debug: bool = False):
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
        self.audio = None  # dict ترک صوتی پس از connect
        self._mode = "tcp"  # یا "udp" پس از fallback
        self._rtp_channel = 0
        self._udp_sock = None
        # لاگ عیب‌یابی handshake (بدون هدر Authorization و بدون رمز)
        self._debug = [] if debug else None

    # -- سطح پایین ----------------------------------------------------------
    def _read_exactly(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RTSPError("اتصال RTSP بسته شد")
            buf += chunk
        return buf

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

    def _request(self, method: str, url: str = None, headers: dict = None,
                 body: bytes = b"", _auth_tries: int = 0) -> tuple:
        self.cseq += 1
        target = url or self.url
        lines = [f"{method} {target} RTSP/1.0", f"CSeq: {self.cseq}"]
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
        if self._debug is not None:
            # فقط متد و کد پاسخ — هرگز هدر Authorization لاگ نمی‌شود
            self._debug.append(f"{method} -> {code}")
        if code == 401 and self.username and _auth_tries < 3:
            # پاسخ Digest به متد و URI بستگی دارد؛ پس هدر کش‌شده‌ی DESCRIBE
            # برای SETUP/PLAY معتبر نیست و بعضی دوربین‌ها با nonce تازه ۴۰۱
            # می‌دهند — با چلنج جدید دوباره می‌سازیم.
            www = resp_headers.get("www-authenticate", "")
            if "digest" in www.lower():
                challenge = _parse_challenge(www)
                new_auth = build_digest_auth(
                    self.username, self.password, method, target, challenge)
                if new_auth != self._auth_header:
                    self._auth_header = new_auth
                    return self._request(method, url, headers, body,
                                         _auth_tries + 1)
                raise RTSPError(
                    "احرازهویت دوربین رد شد (نام کاربری/رمز را بررسی کنید)")
            raise RTSPError("دوربین احرازهویت Basic می‌خواهد (پشتیبانی نمی‌شود)")
        return code, resp_headers, resp_body

    # -- handshake ----------------------------------------------------------
    def connect(self) -> dict:
        """OPTIONS ← DESCRIBE ← SETUP ← PLAY. خروجی: dict ترک صوتی."""
        try:
            self.sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise RTSPError(f"اتصال به {self.host}:{self.port} نشد: {e}")
        self.sock.settimeout(self.timeout)
        self._mode = "tcp"
        self._rtp_channel = 0
        self._udp_sock = None

        code, _, _ = self._request("OPTIONS")
        if code == 401:
            raise RTSPError(
                "احرازهویت دوربین رد شد (نام کاربری/رمز را بررسی کنید)")
        if code != 200:
            raise RTSPError(f"OPTIONS رد شد (کد {code})")

        code, _, body = self._request(
            "DESCRIBE", headers={"Accept": "application/sdp"})
        if code == 401:
            raise RTSPError(
                "احرازهویت دوربین رد شد (نام کاربری/رمز را بررسی کنید)")
        if code != 200:
            raise RTSPError(f"DESCRIBE رد شد (کد {code})")
        sdp = body.decode("utf-8", errors="replace")
        if self._debug is not None:
            # سکشن صوتی SDP — برای فهمیدن اینکه دوربین دقیقاً چه ترکی
            # معرفی می‌کند (control URL، کدک، ...)
            in_audio = False
            for line in sdp.splitlines():
                ls = line.strip()
                if ls.startswith("m="):
                    in_audio = ls.startswith("m=audio")
                if in_audio:
                    self._debug.append(f"  sdp: {ls}")
        audio = parse_sdp_audio(sdp, self.url)
        if not audio:
            raise RTSPError("no_audio_track")
        self.audio = audio

        # بعضی دوربین‌ها RTP-over-TCP را قبول ندارند (خطای ۴۶۱)؛
        # به‌ترتیب امتحان می‌کنیم: TCP/interleaved ← TCP ← UDP
        transports = [
            ("tcp", "RTP/AVP/TCP;unicast;interleaved=0-1"),
            ("tcp", "RTP/AVP/TCP;unicast"),
        ]
        udp_sock, udp_port = self._open_udp_pair()
        if udp_sock is not None:
            transports.append(
                ("udp", f"RTP/AVP;unicast;client_port={udp_port}-{udp_port + 1}"))
        last_err = None
        for mode, transport in transports:
            try:
                self._setup_audio_track(transport, mode, udp_sock, udp_port)
                self._mode = mode
                break
            except RTSPError as e:
                last_err = e
                # فقط اگر مشکل Transport بود ادامه می‌دهیم
                if "(کد 461)" not in str(e) and "(کد 4" not in str(e):
                    raise
        else:
            raise last_err
        if self._mode == "tcp" and udp_sock is not None:
            try:
                udp_sock.close()
            except Exception:
                pass

        code, _, _ = self._request("PLAY", headers={"Range": "npt=0-"})
        if code != 200:
            raise RTSPError(f"PLAY رد شد (کد {code})")
        return audio

    def _open_udp_pair(self):
        """سوکت UDP روی یک پورت زوج آزاد؛ خروجی (sock, port) یا (None, 0)."""
        for port in range(50000, 50200, 2):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.bind(("0.0.0.0", port))
                s.settimeout(self.timeout)
                return s, port
            except OSError:
                try:
                    s.close()
                except Exception:
                    pass
                continue
        return None, 0

    def _setup_audio_track(self, transport: str, mode: str,
                           udp_sock=None, udp_port: int = 0):
        code, headers, _ = self._request(
            "SETUP", url=self.audio["control_url"],
            headers={"Transport": transport})
        if self._debug is not None:
            self._debug.append(f"  transport sent: {transport}")
            self._debug.append(
                f"  transport recv: {headers.get('transport', '')}")
        if code != 200:
            raise RTSPError(f"SETUP ترک صوتی رد شد (کد {code})")
        session = headers.get("session", "")
        self.session_id = session.split(";")[0].strip()
        if not self.session_id:
            raise RTSPError("دوربین Session برنگرداند")
        if mode == "udp":
            self._udp_sock = udp_sock
        else:
            # کانال interleaved واقعی را از پاسخ دوربین می‌خوانیم
            resp_transport = headers.get("transport", "")
            m = re.search(r"interleaved=(\d+)", resp_transport)
            if m:
                self._rtp_channel = int(m.group(1))

    def read_audio_frame(self):
        """خواندن یک فریم صوتی؛ خروجی (codec, clock, payload).

        فریم‌های غیرصوتی (RTCP/ویدیو) رد می‌شوند. timeout سوکت ←
        socket.timeout. بسته‌شدن اتصال ← RTSPError.
        """
        if self._mode == "udp":
            return self._read_audio_frame_udp()
        while True:
            first = self._read_exactly(1)
            if first != b"$":
                # پاسخ متنی RTSP وسط استریم — خط را می‌خوانیم و رد می‌شویم
                line = first
                while not line.endswith(b"\n"):
                    line += self._read_exactly(1)
                continue
            channel = self._read_exactly(1)[0]
            length = struct.unpack(">H", self._read_exactly(2))[0]
            pkt = self._read_exactly(length)
            if channel != self._rtp_channel:
                continue  # RTCP یا کانال دیگر
            parsed = parse_rtp_packet(pkt)
            if not parsed:
                continue
            pt, _seq, _ts, payload = parsed
            if pt != self.audio["pt"]:
                continue
            return self.audio["codec"], self.audio["clock"], payload

    def _read_audio_frame_udp(self):
        """خواندن یک دیتاگرام UDP (هر دیتاگرام = یک بسته‌ی RTP)."""
        while True:
            pkt, _addr = self._udp_sock.recvfrom(65535)
            parsed = parse_rtp_packet(pkt)
            if not parsed:
                continue
            pt, _seq, _ts, payload = parsed
            if pt != self.audio["pt"]:
                continue
            return self.audio["codec"], self.audio["clock"], payload

    def close(self):
        try:
            if self.sock and self.session_id:
                try:
                    self._request("TEARDOWN")
                except Exception:
                    pass
        finally:
            for s in (self.sock, getattr(self, "_udp_sock", None)):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass
            self.sock = None
            self._udp_sock = None
            self.session_id = ""
            self._auth_header = ""
            self.audio = None


def build_listen_url(cam) -> str:
    """آدرس RTSP استریم دوربین (بدون یوزر/پس در URL) برای DESCRIBE."""
    from camera_store import CameraStore
    url = CameraStore.build_rtsp_url(cam)
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    host = parsed.hostname or (cam.get("camera_ip") or cam.get("ip") or "")
    port = parsed.port or int(cam.get("port") or 554)
    return f"rtsp://{host}:{port}{path}"


# ---------------------------------------------------------------------------
# نشست شنیدن (ترد ورکر + پخش در ترد اصلی) — Qt
# ---------------------------------------------------------------------------

class ListenSession:
    """مدیریت کامل یک نشست «شنیدن صدای دوربین».

    استفاده:
        session = ListenSession()
        session.state_changed = lambda s: ...
        session.error_occurred = lambda s: ...
        session.level_changed = lambda v: ...
        session.start(cam)          # اتصال در ترد جدا؛ صدا خودکار پخش می‌شود
        session.set_volume(0.0-1.0)
        session.set_muted(True/False)
        session.stop()
    """

    def __init__(self):
        from PyQt6.QtCore import QObject, QThread, pyqtSignal

        class _Worker(QThread):
            pcm_ready = pyqtSignal(bytes)
            failed = pyqtSignal(str)
            connected = pyqtSignal(dict)

            def __init__(self, url, user, pwd):
                super().__init__()
                self._url, self._user, self._pwd = url, user, pwd
                self._stop = False
                self._client = None

            def request_stop(self):
                self._stop = True
                try:
                    if self._client:
                        self._client.close()
                except Exception:
                    pass

            def _write_debug_log(self, client):
                """ذخیره‌ی لاگ handshake برای عیب‌یابی (بدون رمز)."""
                try:
                    import os
                    path = os.path.join(os.path.expanduser("~"),
                                        "IAS-Viewer-listen-debug.log")
                    with open(path, "a", encoding="utf-8") as f:
                        f.write("\n=== %s ===\n"
                                % time.strftime("%Y-%m-%d %H:%M:%S"))
                        for line in client._debug or []:
                            f.write(line + "\n")
                    return path
                except Exception:
                    return ""

            def run(self):
                client = RTSPAudioClient(self._url, self._user, self._pwd,
                                         debug=True)
                self._client = client
                try:
                    info = client.connect()
                except RTSPError as e:
                    path = self._write_debug_log(client)
                    msg = str(e)
                    if path:
                        msg += f"\nلاگ عیب‌یابی: {path}"
                    self.failed.emit(msg)
                    return
                except Exception as e:  # noqa: BLE001
                    self.failed.emit(f"خطای اتصال: {e}"[:120])
                    return
                self.connected.emit(info)
                while not self._stop:
                    try:
                        codec, clock, payload = client.read_audio_frame()
                    except RTSPError as e:
                        if not self._stop:
                            self.failed.emit(str(e))
                        break
                    except OSError:
                        continue  # timeout خواندن — حلقه ادامه می‌دهد تا stop
                    except Exception as e:  # noqa: BLE001
                        if not self._stop:
                            self.failed.emit(f"خطا در دریافت صدا: {e}"[:100])
                        break
                    try:
                        pcm = decode_audio_payload(payload, codec)
                        if clock != AUDIO_SAMPLE_RATE:
                            pcm = resample_to_8k(pcm, clock)
                        # استریو ← مونو (میانگین)
                        info_ch = info.get("channels", 1)
                        if info_ch > 1 and pcm:
                            s = np.frombuffer(pcm, dtype="<i2").astype("float32")
                            s = s.reshape(-1, info_ch).mean(axis=1)
                            pcm = np.clip(s, -32768, 32767).astype("<i2").tobytes()
                        self.pcm_ready.emit(pcm)
                    except Exception:
                        continue
                try:
                    client.close()
                except Exception:
                    pass

        self._Worker = _Worker
        self.state_changed = None
        self.error_occurred = None
        self.level_changed = None
        self._worker = None
        self._sink = None
        self._io = None
        self._volume = 0.8
        self._muted = False
        self._last_level = -1

    # -- کال‌بک‌های داخلی ----------------------------------------------------
    def _emit_state(self, s: str):
        if self.state_changed:
            self.state_changed(s)

    def _emit_error(self, s: str):
        if self.error_occurred:
            self.error_occurred(s)

    def _on_pcm(self, pcm: bytes):
        if self._io and not self._muted:
            try:
                self._io.write(pcm)
            except Exception:
                pass
        if self.level_changed:
            v = pcm16_rms(pcm)
            pct = int(v * 100)
            if abs(pct - self._last_level) >= 4:
                self._last_level = pct
                self.level_changed(v)

    def _on_worker_connected(self, info: dict):
        self._emit_state("playing")

    def _on_worker_failed(self, msg: str):
        if msg == "no_audio_track":
            self._emit_error("این دوربین صدا ندارد (ترک صوتی در استریم نیست).")
        else:
            self._emit_error(msg)
        self._emit_state("error")

    # -- API عمومی ------------------------------------------------------------
    def _open_output(self) -> bool:
        try:
            from PyQt6.QtMultimedia import (QAudioFormat, QAudioSink,
                                            QMediaDevices)
        except ImportError:
            self._emit_error("QtMultimedia در دسترس نیست")
            return False
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            self._emit_error("خروجی صدا (بلندگو) پیدا نشد")
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(AUDIO_SAMPLE_RATE)
        fmt.setChannelCount(AUDIO_CHANNELS)
        from PyQt6.QtMultimedia import QAudioFormat as _F
        fmt.setSampleFormat(_F.SampleFormat.Int16)
        if not dev.isFormatSupported(fmt):
            self._emit_error("فرمت صوتی پشتیبانی نشد")
            return False
        self._sink = QAudioSink(dev, fmt)
        self._sink.setVolume(0.0 if self._muted else self._volume)
        self._io = self._sink.start()
        return True

    def start(self, cam: dict) -> bool:
        """شروع اتصال و پخش (غیربلاکینگ)."""
        if self._worker:
            return False
        if not self._open_output():
            return False
        self._emit_state("connecting")
        url = build_listen_url(cam)
        self._worker = self._Worker(url, cam.get("user", "") or "",
                                   cam.get("pass", "") or "")
        self._worker.pcm_ready.connect(self._on_pcm)
        self._worker.connected.connect(self._on_worker_connected)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()
        return True

    def set_volume(self, v: float):
        self._volume = max(0.0, min(1.0, v))
        if self._sink and not self._muted:
            try:
                self._sink.setVolume(self._volume)
            except Exception:
                pass

    def set_muted(self, muted: bool):
        self._muted = bool(muted)
        if self._sink:
            try:
                self._sink.setVolume(0.0 if self._muted else self._volume)
            except Exception:
                pass

    def is_muted(self) -> bool:
        return self._muted

    def stop(self):
        if self._worker:
            try:
                self._worker.request_stop()
                self._worker.wait(5000)
            except Exception:
                pass
            self._worker = None
        try:
            if self._sink:
                self._sink.stop()
        except Exception:
            pass
        self._sink = None
        self._io = None
        if self.level_changed:
            self.level_changed(0.0)
        self._emit_state("idle")

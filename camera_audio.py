# -*- coding: utf-8 -*-
"""شنیدن صدای دوربین — «🎧 شنیدن صدای دوربین» (نسخه‌ی 2.0.82-beta).

معماری (استاندارد و بدون وابستگی جدید):
- دریافت صدا: RTSP استاندارد — OPTIONS ← DESCRIBE (پیداکردن ترک صوتی در
  SDP) ← SETUP ترک صوتی (RTP/AVP/TCP interleaved) ← PLAY ← بسته‌های RTP
  صوتی روی همان اتصال TCP. اگر دوربین SETUP تکیِ صدا را ۴۶۱ بدهد، مسیر
  دوم (aggregate مثل VLC) امتحان می‌شود: اول SETUP ویدیو، بعد SETUP صدا
  با همان Session. اگر آن هم ۴۶۱ بخورد، مسیر سوم (session-level
  aggregate) امتحان می‌شود: SETUP روی آدرس اصلی استریم و تفکیک صدا از
  ویدیو با payload-type.
  (2.0.88-beta) سه بهبود handshake: هدر User-Agent مشابه FFmpeg/Lavf
  (مسیر ویدیوی برنامه با همان هویت روی این دوربین‌ها کار می‌کند)، ثبت
  آدرس دقیق SETUP در لاگ عیب‌یابی، و fallback آدرس ترک: اگر فرم کوتاه‌شده
  ۴۶۱ خورد، فرم کامل (با query آدرس اصلی) هم امتحان می‌شود.
- دیکد: G.711 μ-law (رایج‌ترین) و A-law به PCM16.
- پخش: QAudioSink از PyQt6.QtMultimedia (از قبل برای صدای آژیر استفاده
  می‌شود؛ چیزی به requirements اضافه نشد) — ۸kHz مونو.
- احراز هویت Digest از camera_talk بازاستفاده می‌شود (همان RFC 2617).

مدل تردینگ: RTSPAudioClient خالص و بلاکینگ است و در QThread ورکر اجرا
می‌شود؛ فریم‌های PCM با pyqtSignal به ترد اصلی می‌آیند و همان‌جا در
QAudioSink نوشته می‌شوند. ولوم/میوت با QAudioSink.setVolume اعمال می‌شود.

محدوده: هر دوربینی که ترک صوتی در استریم RTSP داشته باشد (مستقیم یا کانال
NVR)؛ اگر ترک صوتی نباشد پیام روشن نمایش داده می‌شود.

2.0.90-beta: راستی‌آزمایی جریان صوت به داخل connect() آمد — هر مسیر
(مستقیم، aggregate، session-level) بعد از PLAY باید واقعاً یک بسته‌ی صوتی
تحویل دهد، وگرنه TEARDOWN + اتصال تازه و تلاش مسیر بعدی (لاگ 2.0.89 نشان
داد SETUP مستقیم ۲۰۰ می‌دهد ولی دوربین فقط ویدیو می‌فرستد). مسیر aggregate
حالا برای هر دو SETUP هر دو فرم آدرس را امتحان می‌کند. فیکس بافر دریافت:
بایت‌های RTP زودرسیده که _read_response بیشتر از هدرها می‌خواند دیگر گم
نمی‌شوند.

2.0.91-beta: دیالوگ حین اتصال مسیر فعلی را نشان می‌دهد (on_progress)؛
لاگ عیب‌یابی تایم‌استمپ دارد؛ و اگر هیچ مسیری روی آدرس تنظیم‌شده صدا
نداد، اسکن ONVIF (GetCapabilities ← GetProfiles ← GetStreamUri) اجرا
می‌شود و URIهای متفاوتی که خود دوربین معرفی می‌کند امتحان می‌شوند.
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

def _parse_sdp_section(sdp_text: str, base_url: str, media: str):
    """پیداکردن اولین سکشن از نوع media («audio» یا «video») در SDP.

    خروجی: dict با کلیدهای control_url / pt / codec / clock / channels
    یا None اگر چنین سکشنی نباشد.
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
        if len(parts) < 4 or parts[0].lower() != media:
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
                "control": control,  # خام؛ برای ساخت URL جایگزین در fallback
                "pt": pt,
                "codec": codec,
                "clock": clock,
                "channels": ch,
            }
    return None


def parse_sdp_tracks(sdp_text: str, base_url: str) -> dict:
    """سکشن‌های ویدیو و صدا: {"video": ...|None, "audio": ...|None}."""
    return {
        "video": _parse_sdp_section(sdp_text, base_url, "video"),
        "audio": _parse_sdp_section(sdp_text, base_url, "audio"),
    }


def parse_sdp_audio(sdp_text: str, base_url: str):
    """پیداکردن اولین ترک صوتی قابل‌پخش در SDP (سازگار با نسخه‌های قبل)."""
    return parse_sdp_tracks(sdp_text, base_url)["audio"]


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


# هویت کلاینتی که در هدر User-Agent معرفی می‌کنیم — رجوع به کامنت داخل _request.
_RTSP_USER_AGENT = "Lavf/61.7.100"


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
        self._rx_buf = b""  # بایت‌های اضافی خوانده‌شده از سوکت (بعد از هدرها)
        self.session_id = ""
        self._auth_header = ""
        self.audio = None  # dict ترک صوتی پس از connect
        self._mode = "tcp"  # یا "udp" پس از fallback
        self._rtp_channel = 0
        self._demux_by_pt = False  # مسیر session-level: تفکیک فقط با payload-type
        self._udp_sock = None
        # آمار بسته‌های RTP دیده‌شده (برای تشخیص «وصل شد ولی صدا نرسید»)
        self._observed_pts = {}  # payload-type ← تعداد
        self._rtp_total = 0
        # لاگ عیب‌یابی handshake (بدون هدر Authorization و بدون رمز)
        self._debug = [] if debug else None
        self._t0 = None  # شروع connect() برای تایم‌استمپ لاگ
        self._last_err = None  # آخرین خطای _try_all_paths
        # کال‌بک پیشرفت: on_progress("aggregate") — دیالوگ مسیر فعلی را
        # نشان می‌دهد تا «در حال اتصال» خشک به‌نظر نرسد
        self.on_progress = None

    def _log(self, msg: str):
        if self._debug is not None:
            dt = time.time() - self._t0 if self._t0 else 0.0
            self._debug.append(f"[{dt:6.1f}s] {msg}")

    def _progress(self, name: str):
        self._log(f"── تلاش مسیر: {name} ──")
        if self.on_progress:
            try:
                self.on_progress(name)
            except Exception:
                pass

    # -- سطح پایین ----------------------------------------------------------
    def _read_exactly(self, n: int) -> bytes:
        # اول از بافر باقی‌مانده‌ی پاسخ‌های قبلی می‌خوانیم — بایت‌هایی که
        # _read_response بیشتر از هدرها از سوکت کشیده است (مثلاً بسته‌های
        # RTP که زودتر از خواندن ما رسیده‌اند) نباید گم شوند.
        buf = self._rx_buf[:n]
        self._rx_buf = self._rx_buf[n:]
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RTSPError("اتصال RTSP بسته شد")
            buf += chunk
        return buf

    def _read_line(self) -> bytes:
        while b"\n" not in self._rx_buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self._rx_buf += chunk
        line, sep, rest = self._rx_buf.partition(b"\n")
        if sep:
            self._rx_buf = rest
            return line + sep
        self._rx_buf = b""
        return line

    def _read_response(self):
        status_line = self._read_line().decode("iso-8859-1").strip()
        if not status_line.startswith("RTSP/"):
            raise RTSPError(f"پاسخ نامعتبر RTSP: {status_line[:60]}")
        code = int(status_line.split(" ", 2)[1])
        headers = {}
        while True:
            line = self._read_line().decode("iso-8859-1")
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
            body = self._read_exactly(n)
        return code, headers, body

    def _request(self, method: str, url: str = None, headers: dict = None,
                 body: bytes = b"", _auth_tries: int = 0) -> tuple:
        self.cseq += 1
        target = url or self.url
        lines = [f"{method} {target} RTSP/1.0", f"CSeq: {self.cseq}"]
        # بعضی فریمورها (مثل XM) به User-Agent حساس‌اند و کلاینت ناشناس را
        # با ۴۶۱ رد می‌کنند؛ مسیر ویدیوی اصلی برنامه با FFmpeg/Lavf روی همین
        # دوربین‌ها کار می‌کند، پس همان هویت را معرفی می‌کنیم.
        lines.append(f"User-Agent: {_RTSP_USER_AGENT}")
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
            self._log(f"{method} -> {code}")
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
        """OPTIONS ← DESCRIBE ← SETUP ← PLAY (+ اسکن ONVIF). خروجی: dict ترک صوتی."""
        self._t0 = time.time()
        self._progress("شروع")
        try:
            self.sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise RTSPError(f"اتصال به {self.host}:{self.port} نشد: {e}")
        self.sock.settimeout(self.timeout)
        self._mode = "tcp"
        self._rtp_channel = 0
        self._udp_sock = None

        audio, video = self._options_and_describe(self.url)
        if not audio:
            raise RTSPError("no_audio_track")
        self.audio = audio

        if self._try_all_paths(audio, video):
            return self._finalize_ok()

        # آدرس اصلی صدا نداد — اسکن ONVIF (استاندارد کشف دوربین): URIهای
        # واقعی استریم را از خود دوربین می‌گیریم؛ اگر با آدرس تنظیم‌شده
        # فرق داشتند، به‌عنوان کاندیدا امتحان می‌شوند.
        self._progress("اسکن ONVIF")
        onvif_uris = []
        try:
            from onvif_scan import onvif_get_stream_uris
            onvif_uris = onvif_get_stream_uris(
                self.host, self.username, self.password,
                debug_log=self._log)
        except Exception as e:  # noqa: BLE001
            self._log(f"ONVIF: خطا در اسکن ({e})")

        # کاندیداهای ONVIF که با آدرس اصلی فرق دارند
        for uri in onvif_uris:
            if self._same_rtsp_url(uri, self.url):
                continue
            self._progress(f"ONVIF: {uri}")
            try:
                audio2, video2 = self._describe_only(uri)
            except RTSPError as e:
                self._log(f"ONVIF: DESCRIBE ناموفق ({e})")
                continue
            if not audio2:
                self._log("ONVIF: این URI ترک صوتی ندارد")
                continue
            orig_url = self.url
            self.url = uri
            try:
                if self._try_all_paths(audio2, video2):
                    self.audio = audio2
                    return self._finalize_ok()
            finally:
                self.url = orig_url
        raise self._last_err

    def _finalize_ok(self):
        if self._mode == "tcp" and self._udp_sock is not None:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None
        return self.audio

    @staticmethod
    def _same_rtsp_url(a: str, b: str) -> bool:
        from urllib.parse import urlparse
        pa, pb = urlparse(a), urlparse(b)
        return ((pa.hostname or "").lower() == (pb.hostname or "").lower()
                and (pa.port or 554) == (pb.port or 554)
                and (pa.path or "/") == (pb.path or "/"))

    def _options_and_describe(self, url: str):
        """OPTIONS + DESCRIBE روی url. خروجی: (audio, video)."""
        code, _, _ = self._request("OPTIONS")
        if code == 401:
            raise RTSPError(
                "احرازهویت دوربین رد شد (نام کاربری/رمز را بررسی کنید)")
        if code != 200:
            raise RTSPError(f"OPTIONS رد شد (کد {code})")
        return self._describe_only(url)

    def _describe_only(self, url: str):
        """DESCRIBE روی url (روی اتصال فعلی). خروجی: (audio, video)."""
        code, _, body = self._request(
            "DESCRIBE", url=url, headers={"Accept": "application/sdp"})
        if code == 401:
            raise RTSPError(
                "احرازهویت دوربین رد شد (نام کاربری/رمز را بررسی کنید)")
        if code != 200:
            raise RTSPError(f"DESCRIBE رد شد (کد {code})")
        sdp = body.decode("utf-8", errors="replace")
        # سکشن‌های صوتی/ویدیویی SDP — برای فهمیدن اینکه دوربین دقیقاً
        # چه ترک‌هایی معرفی می‌کند (control URL، کدک، ...)
        in_av = False
        for line in sdp.splitlines():
            ls = line.strip()
            if ls.startswith("m="):
                in_av = ls.startswith("m=audio") or ls.startswith("m=video")
            if in_av:
                self._log(f"  sdp: {ls}")
        tracks = parse_sdp_tracks(sdp, url)
        return tracks["audio"], tracks["video"]

    def _try_all_paths(self, audio: dict, video: dict) -> bool:
        """همه‌ی مسیرها (مستقیم ← aggregate ← session-level) روی self.url
        فعلی. خروجی True یعنی صدا وصل شد؛ وگرنه self._last_err تنظیم می‌شود."""
        # بعضی دوربین‌ها RTP-over-TCP را قبول ندارند (خطای ۴۶۱)؛
        # به‌ترتیب امتحان می‌کنیم: TCP/interleaved ← TCP ← UDP.
        # برای هر مسیر: SETUP ← PLAY ← راستی‌آزمایی جریان واقعی صوت.
        # (لاگ 2.0.89: SETUP مستقیم ۲۰۰ می‌دهد ولی دوربین فقط ویدیو
        # می‌فرستد — پس «۲۰۰ گرفتن» به‌تنهایی کافی نیست.)
        transports = [
            ("مستقیم (TCP)", "tcp", "RTP/AVP/TCP;unicast;interleaved=0-1"),
            ("مستقیم (TCP)", "tcp", "RTP/AVP/TCP;unicast"),
        ]
        udp_sock, udp_port = self._open_udp_pair()
        if udp_sock is not None:
            transports.append(
                ("مستقیم (UDP)", "udp",
                 f"RTP/AVP;unicast;client_port={udp_port}-{udp_port + 1}"))
        self._last_err = None
        connected_ok = False
        for label, mode, transport in transports:
            self._progress(label)
            try:
                self._setup_audio_track(transport, mode, udp_sock, udp_port)
            except RTSPError as e:
                self._last_err = e
                # فقط اگر مشکل Transport بود ادامه می‌دهیم
                if "(کد 461)" not in str(e) and "(کد 4" not in str(e):
                    raise
                continue
            self._mode = mode
            if self._play_and_verify_audio("direct"):
                connected_ok = True
                break
            self._last_err = RTSPError("no_audio_data")
            self._reset_for_retry()
        if not connected_ok:
            # مسیر دوم (aggregate، مثل VLC): بعضی فریمورها (مثل XM) ترک
            # صوتی را فقط داخل Session ساخته‌شده با SETUP ویدیو قبول
            # می‌کنند. (لاگ 2.0.89: SETUP مستقیم ۲۰۰ ولی بدون صدا.)
            self._progress("aggregate (مثل VLC)")
            if udp_sock is not None:
                try:
                    udp_sock.close()
                except Exception:
                    pass
            if video is None:
                raise self._last_err
            try:
                self._connect_aggregate(video, audio)
            except RTSPError as e:
                self._last_err = e
                self._reset_for_retry()
            else:
                if self._play_and_verify_audio("aggregate"):
                    connected_ok = True
                else:
                    self._last_err = RTSPError("no_audio_data")
                    self._reset_for_retry()
        if not connected_ok:
            # مسیر سوم: session-level aggregate — بعضی فریمورها SETUP تکیِ
            # هیچ ترکی را قبول نمی‌کنند و فقط SETUP روی آدرس اصلی ارائه
            # (presentation URL) را می‌پذیرند؛ بعد از PLAY، صدا و ویدیو
            # قاطی می‌آیند و با payload-type جدا می‌شوند.
            self._progress("session-level aggregate")
            try:
                self._connect_aggregate_session()
            except RTSPError as e:
                self._last_err = e
            else:
                if self._play_and_verify_audio("session"):
                    connected_ok = True
                else:
                    self._last_err = RTSPError("no_audio_data")
        return connected_ok

    def _play_and_verify_audio(self, path_name: str,
                               timeout: float = 4.0) -> bool:
        """PLAY و راستی‌آزمایی جریان واقعی صوت.

        خروجی True یعنی دست‌کم یک بسته‌ی RTP با payload-type صوتی رسید
        (read_audio_frame فقط همان را برمی‌گرداند). آمار هر مسیر در لاگ
        عیب‌یابی ثبت می‌شود تا معلوم باشد دوربین چه فرستاده است.
        """
        code, _, _ = self._request("PLAY", headers={"Range": "npt=0-"})
        if code != 200:
            if self._debug is not None:
                self._log(f"PLAY -> {code} ({path_name})")
            return False
        self._observed_pts = {}
        self._rtp_total = 0
        old_tcp_to, old_udp_to = None, None
        try:
            old_tcp_to = self.sock.gettimeout()
            self.sock.settimeout(1.0)
            if self._udp_sock is not None:
                old_udp_to = self._udp_sock.gettimeout()
                self._udp_sock.settimeout(1.0)
        except Exception:
            pass
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    self.read_audio_frame()
                except OSError:
                    continue  # timeout — هنوز منتظر می‌مانیم
                except RTSPError:
                    break  # اتصال بسته شد
                if self._debug is not None:
                    self._log(
                        f"{path_name}: audio packet OK "
                        f"(rtp={self._rtp_total} "
                        f"pts={dict(sorted(self._observed_pts.items()))})")
                return True
            if self._debug is not None:
                self._log(
                    f"{path_name}: no audio packet "
                    f"(rtp={self._rtp_total} "
                    f"pts={dict(sorted(self._observed_pts.items()))})")
            return False
        finally:
            try:
                if old_tcp_to is not None:
                    self.sock.settimeout(old_tcp_to)
                if self._udp_sock is not None and old_udp_to is not None:
                    self._udp_sock.settimeout(old_udp_to)
            except Exception:
                pass

    def _reset_for_retry(self):
        """بستن سشن فعلی و باز کردن اتصال TCP تازه برای مسیر بعدی.

        سوکت UDP به connect تعلق دارد و دست نمی‌خورد. cseq و هدر احراز
        هویت نگه داشته می‌شوند (با ۴۰۱ بازسازی می‌شود).
        """
        try:
            self._request("TEARDOWN")
        except Exception:
            pass
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        try:
            self.sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise RTSPError(f"اتصال مجدد به {self.host}:{self.port} نشد: {e}")
        self.sock.settimeout(self.timeout)
        self.session_id = ""
        self._rx_buf = b""
        self._mode = "tcp"
        self._rtp_channel = 0
        self._demux_by_pt = False
        self._observed_pts = {}
        self._rtp_total = 0

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

    def _connect_aggregate(self, video: dict, audio: dict):
        """مسیر aggregate (مثل VLC): SETUP ویدیو ← Session ← SETUP صدا.

        بعضی فریمورها (مثل XM) ترک صوتی را فقط داخل Session ساخته‌شده با
        SETUP ویدیو قبول می‌کنند. ویدیو روی کانال ۰-۱ و صدا روی ۲-۳
        می‌آید؛ فریم‌های ویدیو در read_audio_frame رد می‌شوند.
        برای هر دو SETUP، آدرس‌های کوتاه و کامل (با query) امتحان می‌شود —
        لاگ 2.0.89 نشان داد همین دوربین فرم کوتاه را ۴۶۱ می‌دهد ولی فرم
        کامل را ۲۰۰.
        """
        if self._debug is not None:
            self._log("aggregate: video-first SETUP")
        session, last_code = "", None
        for vurl in self._track_url_variants(video):
            code, headers, _ = self._request(
                "SETUP", url=vurl,
                headers={"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
            if self._debug is not None:
                self._log(f"  setup url: {vurl}")
                self._log(f"  video SETUP -> {code}")
            last_code = code
            if code == 200:
                session = headers.get("session", "").split(";")[0].strip()
                break
        if last_code != 200 or not session:
            raise RTSPError(f"SETUP ویدیو رد شد (کد {last_code})")
        # از اینجا _request خودش هدر Session را می‌فرستد
        self.session_id = session
        last_code = None
        for aurl in self._track_url_variants(audio):
            code, headers, _ = self._request(
                "SETUP", url=aurl,
                headers={"Transport": "RTP/AVP/TCP;unicast;interleaved=2-3"})
            if self._debug is not None:
                self._log(f"  setup url: {aurl}")
                self._log(f"  audio SETUP (aggregate) -> {code}")
                self._log(
                    f"  transport recv: {headers.get('transport', '')}")
            last_code = code
            if code == 200:
                session2 = headers.get("session", "").split(";")[0].strip()
                if session2:
                    self.session_id = session2
                m = re.search(r"interleaved=(\d+)",
                              headers.get("transport", ""))
                self._rtp_channel = int(m.group(1)) if m else 2
                break
        if last_code != 200:
            raise RTSPError(f"SETUP ترک صوتی رد شد (کد {last_code})")
        self._mode = "tcp"

    def _connect_aggregate_session(self):
        """مسیر session-level aggregate: SETUP روی presentation URL.

        بعضی فریمورها SETUP تکیِ هیچ ترکی (نه صدا، نه ویدیو) را قبول
        نمی‌کنند و فقط SETUP روی آدرس اصلی استریم را می‌پذیرند. بعد از
        PLAY، بسته‌های RTP صدا و ویدیو روی کانال‌های interleaved قاطی
        می‌آیند؛ تفکیک با payload-type انجام می‌شود (نه شماره‌ی کانال).
        """
        if self._debug is not None:
            self._log("aggregate2: session-level SETUP")
        code, headers, _ = self._request(
            "SETUP", url=self.url,
            headers={"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
        if self._debug is not None:
            self._log(f"  setup url: {self.url}")
            self._log(f"  session SETUP -> {code}")
            self._log(
                f"  transport recv: {headers.get('transport', '')}")
        if code != 200:
            raise RTSPError(f"SETUP سشن رد شد (کد {code})")
        session = headers.get("session", "").split(";")[0].strip()
        if not session:
            raise RTSPError("دوربین Session برنگرداند")
        self.session_id = session
        # کانال‌ها را دوربین تعیین می‌کند؛ فقط payload-type ملاک است
        self._demux_by_pt = True
        self._mode = "tcp"

    def _track_url_variants(self, track: dict):
        """ترتیب امتحان آدرس‌های SETUP برای یک ترک.

        بعضی فریمورها (مثل XM با آدرس‌های queryدار) فقط آدرسی را قبول
        می‌کنند که query آدرس اصلی DESCRIBE را هم داشته باشد؛ بعضی دیگر
        فقط فرم کوتاه‌شده. هر دو را به‌ترتیب امتحان می‌کنیم.
        """
        primary = track["control_url"]
        yield primary
        control = (track.get("control") or "").strip().strip("<>")
        if control and "://" not in control:
            variant = self.url.rstrip("/") + "/" + control.lstrip("/")
            if variant != primary:
                yield variant

    def _setup_audio_track(self, transport: str, mode: str,
                           udp_sock=None, udp_port: int = 0):
        last_code, last_headers = None, {}
        for track_url in self._track_url_variants(self.audio):
            code, headers, _ = self._request(
                "SETUP", url=track_url,
                headers={"Transport": transport})
            if self._debug is not None:
                self._log(f"  setup url: {track_url}")
                self._log(f"  transport sent: {transport}")
                self._log(
                    f"  transport recv: {headers.get('transport', '')}")
            if code == 200:
                last_code, last_headers = code, headers
                break
            last_code, last_headers = code, headers
            # فقط ۴۶۱ (احتمال آدرس اشتباه ترک) ارزش امتحان URL بعدی را دارد
            if code != 461:
                break
        code, headers = last_code, last_headers
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
            if not self._demux_by_pt and channel != self._rtp_channel:
                continue  # RTCP یا کانال دیگر
            parsed = parse_rtp_packet(pkt)
            if not parsed:
                continue
            pt, _seq, _ts, payload = parsed
            self._rtp_total += 1
            self._observed_pts[pt] = self._observed_pts.get(pt, 0) + 1
            if pt != self.audio["pt"]:
                continue  # ویدیو یا ترک دیگر
            return self.audio["codec"], self.audio["clock"], payload

    def _read_audio_frame_udp(self):
        """خواندن یک دیتاگرام UDP (هر دیتاگرام = یک بسته‌ی RTP)."""
        while True:
            pkt, _addr = self._udp_sock.recvfrom(65535)
            parsed = parse_rtp_packet(pkt)
            if not parsed:
                continue
            pt, _seq, _ts, payload = parsed
            self._rtp_total += 1
            self._observed_pts[pt] = self._observed_pts.get(pt, 0) + 1
            if pt != self.audio["pt"]:
                continue
            return self.audio["codec"], self.audio["clock"], payload

    def audio_stats(self):
        """آمار بسته‌های RTP دیده‌شده برای عیب‌یابی (خطوط لاگ)."""
        lines = [f"rtp packets seen: {self._rtp_total}",
                 f"observed payload-types: {dict(sorted(self._observed_pts.items()))}"]
        if self.audio:
            lines.append(
                f"expected audio pt={self.audio['pt']} codec={self.audio['codec']} "
                f"clock={self.audio['clock']}")
        return lines

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
                self._info = None

            def request_stop(self):
                self._stop = True
                try:
                    if self._client:
                        self._client.close()
                except Exception:
                    pass

            def _process_frame(self, codec, clock, payload, info):
                """دیکد یک فریم و ارسال PCM (خطا ← نادیده)."""
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
                    pass

            def _emit_first(self, first):
                codec, clock, payload = first
                self._process_frame(codec, clock, payload, self._info or {})

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
                        try:
                            for line in client.audio_stats():
                                f.write(line + "\n")
                        except Exception:
                            pass
                    return path
                except Exception:
                    return ""

            def run(self):
                client = RTSPAudioClient(self._url, self._user, self._pwd,
                                         debug=True)
                self._client = client
                # پیشرفت مسیرها را به دیالوگ می‌فرستیم تا «در حال اتصال»
                # خشک و بی‌خبر به‌نظر نرسد
                client.on_progress = lambda name: (
                    self.state_changed(f"connecting:{name}")
                    if self.state_changed else None)
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
                self._info = info
                # انتظار برای اولین بسته‌ی صوتی واقعی (نه فقط اتصال RTSP)
                deadline = time.time() + 6.0
                first = None
                fail_msg = None
                while not self._stop and time.time() < deadline:
                    try:
                        first = client.read_audio_frame()
                        break
                    except RTSPError as e:
                        fail_msg = str(e)
                        break
                    except OSError:
                        continue  # timeout خواندن — هنوز منتظر می‌مانیم
                    except Exception as e:  # noqa: BLE001
                        fail_msg = f"خطا در دریافت صدا: {e}"[:100]
                        break
                if first is None and not self._stop:
                    path = self._write_debug_log(client)
                    msg = fail_msg or "no_audio_data"
                    if path:
                        msg += f"\nلاگ عیب‌یابی: {path}"
                    self.failed.emit(msg)
                    try:
                        client.close()
                    except Exception:
                        pass
                    return
                if self._stop:
                    try:
                        client.close()
                    except Exception:
                        pass
                    return
                self._emit_first(first)
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
                    self._process_frame(codec, clock, payload, info)
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
        elif msg.startswith("no_audio_data"):
            tail = msg[len("no_audio_data"):]
            self._emit_error(
                "به دوربین وصل شدیم ولی هیچ بسته‌ی صوتی نرسید. "
                "احتمالاً میکروفون در تنظیمات وب دوربین خاموش است؛ "
                "آن را روشن کنید و دوباره امتحان کنید." + tail)
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

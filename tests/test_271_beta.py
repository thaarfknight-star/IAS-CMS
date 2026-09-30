# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.87-beta — مسیر session-level aggregate.

زمینه: لاگ سوم طه (IAS-Viewer-listen-debug-3.log) نشان داد دوربین
IAS.NB-Q2501F حتی SETUP ویدیو را هم ۴۶۱ می‌دهد؛ یعنی SETUP تکیِ هیچ ترکی
قبول نیست. بعضی فریمورها فقط SETUP روی presentation URL (سطح سشن) را
می‌پذیرند و بعد از PLAY صدا/ویدیو را قاطی روی interleaved می‌فرستند.

پوشش:
1. وقتی همه‌ی SETUPهای تکی ۴۶۱ می‌خورند ولی SETUP سشن ۲۰۰ می‌دهد،
   connect() موفق است و _demux_by_pt فعال می‌شود.
2. اگر SETUP سشن هم رد شود، همان خطای ۴۶۱ اصلی بالا می‌آید.
3. read_audio_frame در حالت demux_by_pt بسته‌های ویدیو را (حتی روی همان
   کانال صدا) رد می‌کند و صدا را از هر کانالی می‌خواند.
4. وقتی demux_by_pt خاموش است، فیلتر کانال مثل قبل کار می‌کند.
"""

import socket
import struct
import threading
import time

import pytest

from camera_audio import RTSPAudioClient, RTSPError

SDP_XM = """v=0
o=- 0 0 IN IP4 192.168.1.10
s=XM session
c=IN IP4 0.0.0.0
t=0 0
m=video 0 RTP/AVP 96
a=rtpmap:96 H264/90000
a=control:trackID=0
m=audio 0 RTP/AVP 8
a=rtpmap:8 PCMA/8000
a=fmtp:8 octet-align=1;decode_buf=400
a=control:trackID=1
a=recvonly
"""


def _rtp(pt: int, payload: bytes) -> bytes:
    hdr = struct.pack(">BBHII", 0x80, pt & 0x7F, 1, 2, 3)
    return hdr + payload


def _interleaved(channel: int, pkt: bytes) -> bytes:
    return b"$" + bytes([channel]) + struct.pack(">H", len(pkt)) + pkt


class SessionAggregateServer(threading.Thread):
    """فریمور سرسخت: هر SETUP تکی ← ۴۶۱؛ فقط SETUP روی آدرس اصلی ← ۲۰۰.

    با reject_session=True حتی SETUP سشن هم ۴۶۱ می‌شود.
    بعد از PLAY، ویدیو و صدا را قاطی روی interleaved می‌فرستد.
    """

    def __init__(self, reject_session=False):
        super().__init__(daemon=True)
        self.reject_session = reject_session
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)

    def _read_request(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        head = data.decode("iso-8859-1")
        lines = head.split("\r\n")
        method = lines[0].split(" ", 2)[0] if lines and " " in lines[0] else ""
        target = lines[0].split(" ", 2)[1] if lines and " " in lines[0] else ""
        headers, cseq = {}, "1"
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            if ln.lower().startswith("cseq:"):
                cseq = ln.split(":", 1)[1].strip()
        return method, target, cseq, headers

    def _respond(self, conn, cseq, code, msg, headers=None, body=b""):
        headers = headers or {}
        out = [f"RTSP/1.0 {code} {msg}", f"CSeq: {cseq}"]
        for k, v in headers.items():
            out.append(f"{k}: {v}")
        if body:
            out.append(f"Content-Length: {len(body)}")
        conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + body)

    def run(self):
        # از 2.0.90: کلاینت بین مسیرها reconnect می‌کند — چند اتصال
        # پشت سر هم را قبول می‌کنیم.
        while True:
            try:
                self.sock.settimeout(0.5)
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                with conn:
                    self._serve(conn)
            except OSError:
                pass

    def _serve(self, conn):
        conn.settimeout(10)
        while True:
            try:
                method, target, cseq, headers = self._read_request(conn)
            except OSError:
                break
            if not method:
                break
            if method == "OPTIONS":
                self._respond(conn, cseq, 200, "OK")
            elif method == "DESCRIBE":
                self._respond(conn, cseq, 200, "OK",
                              {"Content-Type": "application/sdp"},
                              SDP_XM.encode())
            elif method == "SETUP":
                if "trackID" in target:
                    self._respond(conn, cseq, 461,
                                  "Unsupported Transport")
                elif self.reject_session:
                    self._respond(conn, cseq, 461,
                                  "Unsupported Transport")
                else:
                    self._respond(
                        conn, cseq, 200, "OK",
                        {"Session": "SES999",
                         "Transport": ("RTP/AVP/TCP;unicast;"
                                       "interleaved=0-1")})
            elif method == "PLAY":
                self._respond(conn, cseq, 200, "OK",
                              {"Session": "SES999"})
                # ویدیو و صدا قاطی؛ صدا روی کانال‌های مختلف.
                # از 2.0.90: verify داخل connect() یک بسته‌ی صوتی مصرف
                # می‌کند، پس سه بسته می‌فرستیم تا دو خواندن بعدی تست هم
                # به ترتیب AA و BB برسند.
                blob = b"".join([
                    _interleaved(0, _rtp(96, b"\x11" * 40)),   # ویدیو
                    _interleaved(2, _rtp(8, b"\xAA" * 160)),   # صدا (verify)
                    _interleaved(0, _rtp(96, b"\x22" * 40)),   # ویدیو
                    _interleaved(2, _rtp(8, b"\xAA" * 160)),   # صدا
                    _interleaved(0, _rtp(96, b"\x33" * 40)),   # ویدیو
                    _interleaved(0, _rtp(8, b"\xBB" * 160)),   # صدا
                ])
                try:
                    conn.sendall(blob)
                except OSError:
                    pass
                time.sleep(1.5)
                break
            elif method == "TEARDOWN":
                self._respond(conn, cseq, 200, "OK")
                break

    def join(self, timeout=None):
        super().join(timeout)
        try:
            self.sock.close()
        except Exception:
            pass


def test_session_aggregate_connects_when_track_setups_rejected():
    srv = SessionAggregateServer()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/stream",
                        "", "", timeout=6.0, debug=True)
    try:
        info = c.connect()
        log = "\n".join(c._debug)
        assert info["codec"] == "PCMA"
        assert info["pt"] == 8
        assert c._mode == "tcp"
        assert c._demux_by_pt is True
        assert c.session_id == "SES999"
        assert "aggregate: video-first SETUP" in log
        assert "aggregate2: session-level SETUP" in log
        assert "session SETUP -> 200" in log
        # صدا از میان ویدیوی قاطی خوانده می‌شود
        codec1, _, p1 = c.read_audio_frame()
        codec2, _, p2 = c.read_audio_frame()
        assert (codec1, codec2) == ("PCMA", "PCMA")
        assert p1 == b"\xAA" * 160
        assert p2 == b"\xBB" * 160
    finally:
        c.close()
    srv.join(timeout=5)


def test_session_aggregate_raises_original_461_when_rejected():
    srv = SessionAggregateServer(reject_session=True)
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/stream",
                        "", "", timeout=6.0, debug=True)
    try:
        with pytest.raises(RTSPError, match=r"کد 461"):
            c.connect()
        log = "\n".join(c._debug)
        assert "aggregate: video-first SETUP" in log
        assert "aggregate2: session-level SETUP" in log
        assert "session SETUP -> 461" in log
    finally:
        c.close()
    srv.join(timeout=5)


def test_demux_by_pt_reads_audio_on_any_channel():
    c = RTSPAudioClient("rtsp://127.0.0.1:554/x")
    c.sock, peer = socket.socketpair()
    c._mode = "tcp"
    c._demux_by_pt = True
    c.audio = {"pt": 8, "codec": "PCMA", "clock": 8000, "channels": 1}
    try:
        peer.sendall(_interleaved(0, _rtp(96, b"\x11" * 20)))   # ویدیو ch0
        peer.sendall(_interleaved(0, _rtp(8, b"\xCC" * 160)))   # صدا ch0
        peer.sendall(_interleaved(5, _rtp(8, b"\xDD" * 160)))   # صدا ch5
        codec, clock, p1 = c.read_audio_frame()
        _, _, p2 = c.read_audio_frame()
    finally:
        peer.close()
        c.sock.close()
    assert codec == "PCMA" and clock == 8000
    assert p1 == b"\xCC" * 160
    assert p2 == b"\xDD" * 160


def test_channel_filter_still_applies_when_demux_by_pt_off():
    c = RTSPAudioClient("rtsp://127.0.0.1:554/x")
    c.sock, peer = socket.socketpair()
    c._mode = "tcp"
    c._rtp_channel = 2
    c._demux_by_pt = False
    c.audio = {"pt": 8, "codec": "PCMA", "clock": 8000, "channels": 1}
    try:
        peer.sendall(_interleaved(0, _rtp(8, b"\xEE" * 160)))   # صدا ولی کانال اشتباه
        peer.sendall(_interleaved(2, _rtp(8, b"\xFF" * 160)))   # صدا کانال درست
        _, _, payload = c.read_audio_frame()
    finally:
        peer.close()
        c.sock.close()
    assert payload == b"\xFF" * 160

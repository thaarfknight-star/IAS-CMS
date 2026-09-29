# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.82-beta — «🎧 شنیدن صدای دوربین».

پوشش:
1. دیکد G.711 μ-law (وکتور استاندارد + رفت‌وبرگشت با انکدر camera_talk)
2. دیکد G.711 A-law (وکتورهای صفر مثبت/منفی)
3. پارس بسته‌ی RTP دریافتی
4. پارس SDP — پیداکردن ترک صوتی (PCMU/PCMA/بدون صدا/کنترل نسبی)
5. end-to-end با سرور RTSP جعلی روی localhost:
   چالش Digest ← DESCRIBE ← SETUP ← PLAY ← استریم RTP موج سینوسی
   440Hz با μ-law؛ تأیید دیکد صحیح (پیک فرکانسی نزدیک 440Hz)
6. سرور بدون ترک صوتی ← خطای no_audio_track
"""

import math
import socket
import struct
import threading

import numpy as np
import pytest

from camera_audio import (
    RTSPAudioClient,
    RTSPError,
    alaw_to_linear,
    decode_audio_payload,
    parse_rtp_packet,
    parse_sdp_audio,
    ulaw_to_linear,
)
from camera_talk import build_rtp_packet, linear_to_ulaw


# ---------------------------------------------------------------------------
# ۱) μ-law
# ---------------------------------------------------------------------------

def test_ulaw_decode_zero():
    assert ulaw_to_linear(0xFF) == 0


def test_ulaw_roundtrip():
    for s in (0, 100, -100, 1000, -1000, 8000, -8000, 16000, -16000,
              30000, -30000):
        b = linear_to_ulaw(s)
        d = ulaw_to_linear(b)
        assert abs(d - s) <= max(120, abs(s) * 0.04), (s, b, d)


# ---------------------------------------------------------------------------
# ۲) A-law
# ---------------------------------------------------------------------------

def test_alaw_decode_zeros():
    # در A-law دو صفر هست: 0xD5 ← ‎-8 و 0x55 ← ‎+8
    assert alaw_to_linear(0xD5) == -8
    assert alaw_to_linear(0x55) == 8


def test_alaw_sign():
    assert alaw_to_linear(0xD5) < 0 < alaw_to_linear(0x55)


# ---------------------------------------------------------------------------
# ۳) RTP
# ---------------------------------------------------------------------------

def test_parse_rtp_packet():
    payload = bytes(range(60))
    pkt = build_rtp_packet(7, 12345, 99, payload)
    parsed = parse_rtp_packet(pkt)
    assert parsed is not None
    pt, seq, ts, out = parsed
    assert pt == 0 and seq == 7 and ts == 12345 and out == payload


def test_parse_rtp_rejects_bad_version():
    pkt = bytearray(build_rtp_packet(1, 1, 1, b"xx"))
    pkt[0] = 0x00
    assert parse_rtp_packet(bytes(pkt)) is None


# ---------------------------------------------------------------------------
# ۴) SDP
# ---------------------------------------------------------------------------

SDP_AUDIO = """v=0\r
o=- 123 123 IN IP4 192.168.1.10\r
s=Live\r
m=video 0 RTP/AVP 96\r
a=rtpmap:96 H264/90000\r
a=control:track0\r
m=audio 0 RTP/AVP 0\r
a=rtpmap:0 PCMU/8000\r
a=control:track1\r
"""

SDP_AUDIO_PCMA = SDP_AUDIO.replace("RTP/AVP 0\r\na=rtpmap:0 PCMU/8000",
                                   "RTP/AVP 8\r\na=rtpmap:8 PCMA/8000/1")

SDP_NO_AUDIO = """v=0\r
o=- 123 123 IN IP4 192.168.1.10\r
s=Live\r
m=video 0 RTP/AVP 96\r
a=rtpmap:96 H264/90000\r
a=control:track0\r
"""


def test_sdp_finds_pcmu():
    info = parse_sdp_audio(SDP_AUDIO, "rtsp://192.168.1.10:554/live")
    assert info is not None
    assert info["codec"] == "PCMU" and info["clock"] == 8000
    assert info["control_url"] == "rtsp://192.168.1.10:554/track1"


def test_sdp_finds_pcma():
    info = parse_sdp_audio(SDP_AUDIO_PCMA, "rtsp://192.168.1.10:554/live")
    assert info is not None and info["codec"] == "PCMA"


def test_sdp_no_audio_returns_none():
    assert parse_sdp_audio(SDP_NO_AUDIO,
                           "rtsp://192.168.1.10:554/live") is None


# ---------------------------------------------------------------------------
# ۵/۶) سرور RTSP جعلی + end-to-end
# ---------------------------------------------------------------------------

def _sine_ulaw_frames(freq=440.0, rate=8000, n_frames=40, amp=10000):
    frames = []
    for f in range(n_frames):
        payload = bytes(
            linear_to_ulaw(
                int(amp * math.sin(2 * math.pi * freq * (f * 160 + i) / rate)))
            for i in range(160))
        frames.append(payload)
    return frames


class FakeRTSPAudioServer(threading.Thread):
    """سرور RTSP حداقلی: چالش Digest در DESCRIBE، بعد استریم صوتی.

    اگر tcp_reject_461=True باشد، SETUP با Transport روی TCP را با کد ۴۶۱
    رد می‌کند (شبیه دوربین طه) و فقط UDP را قبول می‌کند.
    """

    def __init__(self, with_audio=True, tcp_reject_461=False):
        super().__init__(daemon=True)
        self.with_audio = with_audio
        self.tcp_reject_461 = tcp_reject_461
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.frames = _sine_ulaw_frames()
        self.udp_client_addr = None  # (ip, rtp_port) برای حالت UDP

    def _read_request(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        head = data.decode("iso-8859-1")
        lines = head.split("\r\n")
        if not lines or " " not in lines[0]:
            return "", "", "1", False, {}
        method, target = lines[0].split(" ", 2)[:2]
        headers = {}
        cseq = "1"
        authed = False
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            if ln.lower().startswith("cseq:"):
                cseq = ln.split(":", 1)[1].strip()
            if ln.lower().startswith("authorization:"):
                authed = True
        return method, target, cseq, authed, headers

    def _respond(self, conn, cseq, code, msg, headers=None, body=b""):
        headers = headers or {}
        out = [f"RTSP/1.0 {code} {msg}", f"CSeq: {cseq}"]
        for k, v in headers.items():
            out.append(f"{k}: {v}")
        if body:
            out.append(f"Content-Length: {len(body)}")
        conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + body)

    def run(self):
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(10)
            session = "12345678"
            while True:
                try:
                    method, target, cseq, authed, headers = self._read_request(conn)
                except OSError:
                    break
                if not method:
                    break
                if method == "OPTIONS":
                    self._respond(conn, cseq, 200, "OK",
                                  {"Public": "DESCRIBE, SETUP, PLAY"})
                elif method == "DESCRIBE":
                    if not authed:
                        self._respond(
                            conn, cseq, 401, "Unauthorized",
                            {"WWW-Authenticate":
                             'Digest realm="testrealm", nonce="abc123", '
                             'qop="auth", algorithm="MD5"'})
                        continue
                    if self.with_audio:
                        sdp = SDP_AUDIO.replace("192.168.1.10", "127.0.0.1")
                    else:
                        sdp = SDP_NO_AUDIO.replace("192.168.1.10", "127.0.0.1")
                    self._respond(conn, cseq, 200, "OK",
                                  {"Content-Type": "application/sdp"},
                                  sdp.encode())
                elif method == "SETUP":
                    transport_req = headers.get("transport", "")
                    if self.tcp_reject_461 and "TCP" in transport_req.upper():
                        self._respond(conn, cseq, 461, "Unsupported Transport")
                        continue
                    if "client_port=" in transport_req:
                        import re as _re
                        m = _re.search(r"client_port=(\d+)-(\d+)", transport_req)
                        rtp_port = int(m.group(1))
                        peer_ip = conn.getpeername()[0]
                        self.udp_client_addr = (peer_ip, rtp_port)
                        self._respond(
                            conn, cseq, 200, "OK",
                            {"Session": session,
                             "Transport": transport_req +
                             f";server_port={rtp_port + 10}-"
                             f"{rtp_port + 11}"})
                    else:
                        self._respond(
                            conn, cseq, 200, "OK",
                            {"Session": session,
                             "Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
                elif method == "PLAY":
                    self._respond(conn, cseq, 200, "OK",
                                  {"Session": session, "Range": "npt=0-"})
                    seq, ts, ssrc = 100, 2000, 4242
                    udp_sock = None
                    if self.udp_client_addr:
                        udp_sock = socket.socket(socket.AF_INET,
                                                 socket.SOCK_DGRAM)
                    for payload in self.frames:
                        rtp = build_rtp_packet(seq, ts, ssrc, payload)
                        if udp_sock:
                            try:
                                udp_sock.sendto(rtp, self.udp_client_addr)
                            except OSError:
                                pass  # سندباکس ممکن است UDP را ببندد
                        else:
                            conn.sendall(b"$" + bytes((0,)) +
                                         struct.pack(">H", len(rtp)) + rtp)
                        seq = (seq + 1) & 0xFFFF
                        ts = (ts + 160) & 0xFFFFFFFF
                    if udp_sock:
                        udp_sock.close()
                    # اتصال را کمی باز نگه می‌داریم تا کلاینت بخواند
                    try:
                        self._read_request(conn)  # TEARDOWN
                    except OSError:
                        pass
                    break
                elif method == "TEARDOWN":
                    self._respond(conn, cseq, 200, "OK")
                    break
                else:
                    self._respond(conn, cseq, 400, "Bad Request")
                    break
        self.sock.close()


def test_e2e_audio_stream_with_digest():
    srv = FakeRTSPAudioServer(with_audio=True)
    srv.start()
    client = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                             "admin", "1234", timeout=10)
    try:
        info = client.connect()
        assert info["codec"] == "PCMU" and info["clock"] == 8000
        pcm_all = b""
        for _ in range(40):
            codec, clock, payload = client.read_audio_frame()
            assert codec == "PCMU" and clock == 8000
            assert len(payload) == 160
            pcm_all += decode_audio_payload(payload, codec)
    finally:
        client.close()
        srv.join(timeout=10)

    samples = np.frombuffer(pcm_all, dtype="<i2").astype("float64")
    assert samples.size == 40 * 160
    rms = float((samples ** 2).mean() ** 0.5)
    assert rms > 1000, f"صدای دریافتی ساکت است (rms={rms})"
    # پیک فرکانسی باید نزدیک 440Hz باشد ← یعنی «صدا از دوربین گرفته شد»
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    freqs = np.fft.rfftfreq(samples.size, 1 / 8000)
    peak = freqs[int(np.argmax(spectrum[1:])) + 1]
    assert 400 < peak < 480, f"پیک فرکانسی غیرمنتظره: {peak}"


def test_e2e_no_audio_track_raises():
    srv = FakeRTSPAudioServer(with_audio=False)
    srv.start()
    client = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                             "admin", "1234", timeout=10)
    try:
        with pytest.raises(RTSPError, match="no_audio_track"):
            client.connect()
    finally:
        client.close()
        srv.join(timeout=10)


def test_e2e_fallback_to_udp_after_461():
    """دوربین ۴۶۱ می‌دهد ← کلاینت باید به UDP سوییچ کند (هندشیک)."""
    srv = FakeRTSPAudioServer(with_audio=True, tcp_reject_461=True)
    srv.start()
    client = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                             "admin", "1234", timeout=10)
    try:
        info = client.connect()
        assert client._mode == "udp", f"حالت موردانتظار udp بود: {client._mode}"
        assert info["codec"] == "PCMU"
        # سرور باید SETUP با client_port دیده باشد
        assert srv.udp_client_addr is not None, \
            "کلاینت SETUP با client_port نفرستاد"
        assert srv.udp_client_addr[1] % 2 == 0, "پورت RTP باید زوج باشد"
        assert client.session_id, "Session تنظیم نشد"
    finally:
        client.close()
        srv.join(timeout=10)


class _FakeUDPSock:
    """استاب سوکت UDP برای تست مسیر خواندن بدون شبکه‌ی واقعی."""

    def __init__(self, datagrams):
        self._datagrams = list(datagrams)

    def recvfrom(self, _n):
        if not self._datagrams:
            raise TimeoutError("timed out")
        return self._datagrams.pop(0), ("127.0.0.1", 6000)

    def close(self):
        pass


def test_udp_read_path_parses_datagrams():
    """خواندن RTP از دیتاگرام UDP: رد PT نامرتبط و بسته‌ی خراب."""
    frames = _sine_ulaw_frames()
    wrong_pt = bytearray(build_rtp_packet(2, 3, 3, frames[1]))
    wrong_pt[1] = 0x80 | 96  # PT=96 به‌جای 0
    datagrams = [build_rtp_packet(1, 2, 3, frames[0]),  # معتبر
                 b"\x80",  # خراب — رد می‌شود
                 bytes(wrong_pt),  # PT اشتباه — رد می‌شود
                 build_rtp_packet(3, 4, 3, frames[2])]  # معتبر
    client = RTSPAudioClient("rtsp://127.0.0.1:9/x", "u", "p", timeout=5)
    client._mode = "udp"
    client.audio = {"codec": "PCMU", "clock": 8000, "pt": 0,
                    "control_url": "rtsp://127.0.0.1:9/x/audio"}
    client._udp_sock = _FakeUDPSock(datagrams)
    codec, clock, p1 = client.read_audio_frame()
    assert codec == "PCMU" and clock == 8000 and p1 == frames[0]
    codec, clock, p2 = client.read_audio_frame()
    assert p2 == frames[2]

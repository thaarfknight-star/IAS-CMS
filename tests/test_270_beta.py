# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.86-beta — مسیر aggregate برای «شنیدن صدای دوربین».

زمینه: دوربین IAS.NB-Q2501F طه (فریمور XM) در SDP ترک صوتی معرفی می‌کند
(PCMU/PCMA، a=control:trackID=1) ولی SETUP تکیِ ترک صوتی را با هر سه
Transport با ۴۶۱ رد می‌کند. بعضی فریمورها SETUP صدا را فقط داخل Session
ساخته‌شده با SETUP ویدیو قبول می‌کنند (رفتار VLC).

پوشش:
1. connect() وقتی SETUP تکی ۴۶۱ می‌خورد ولی ویدیو SETUP می‌شود ← مسیر
   aggregate موفق است و info صدا برمی‌گردد.
2. اگر SETUP ویدیو هم رد شود ← همان خطای ۴۶۱ مسیر قدیمی بالا می‌آید
   (رفتار قبلی حفظ می‌شود).
3. read_audio_frame فریم‌های interleaved ویدیو (کانال دیگر) را رد می‌کند.
4. parse_sdp_tracks روی SDP واقعی دوربین (با a=recvonly و fmtp) ویدیو و
   صدا را پیدا می‌کند؛ parse_sdp_audio همچنان سازگار است.
"""

import socket
import struct
import threading

import pytest

from camera_audio import (RTSPAudioClient, RTSPError, parse_sdp_audio,
                          parse_sdp_tracks)

# SDP شبیه دوربین طه (بعد از تغییر کدک به PCMA در لاگ دوم)
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


class AggregateServer(threading.Thread):
    """شبیه‌ساز فریمور XM: SETUP صدا بدون Session ← ۴۶۱.

    با reject_video=True حتی SETUP ویدیو هم ۴۶۱ می‌شود.
    """

    def __init__(self, reject_video=False):
        super().__init__(daemon=True)
        self.reject_video = reject_video
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
                if "trackID=0" in target:
                    if self.reject_video:
                        self._respond(conn, cseq, 461,
                                      "Unsupported Transport")
                    else:
                        self._respond(
                            conn, cseq, 200, "OK",
                            {"Session": "SES123",
                             "Transport": ("RTP/AVP/TCP;unicast;"
                                           "interleaved=0-1")})
                else:  # ترک صوتی
                    if headers.get("session") == "SES123":
                        self._respond(
                            conn, cseq, 200, "OK",
                            {"Session": "SES123",
                             "Transport": ("RTP/AVP/TCP;unicast;"
                                           "interleaved=2-3")})
                    else:
                        self._respond(conn, cseq, 461,
                                      "Unsupported Transport")
            elif method == "PLAY":
                self._respond(conn, cseq, 200, "OK",
                              {"Session": "SES123"})
                # از 2.0.90: connect() جریان صوت را راستی‌آزمایی می‌کند؛
                # صدا در aggregate روی کانال ۲ می‌آید (interleaved=2-3).
                try:
                    for _ in range(4):
                        conn.sendall(_interleaved(2, _rtp(8, b"\xAA" * 160)))
                except OSError:
                    pass
                try:
                    while True:
                        method, _, cseq, _ = self._read_request(conn)
                        if not method:
                            break
                        if method == "TEARDOWN":
                            self._respond(conn, cseq, 200, "OK")
                            break
                except OSError:
                    pass
            elif method == "TEARDOWN":
                self._respond(conn, cseq, 200, "OK")
                break

    def join(self, timeout=None):
        super().join(timeout)
        try:
            self.sock.close()
        except Exception:
            pass


def _rtp(pt: int, payload: bytes) -> bytes:
    hdr = struct.pack(">BBHII", 0x80, pt & 0x7F, 1, 2, 3)
    return hdr + payload


def _interleaved(channel: int, pkt: bytes) -> bytes:
    return b"$" + bytes([channel]) + struct.pack(">H", len(pkt)) + pkt


def test_aggregate_flow_connects_when_audio_setup_needs_session():
    srv = AggregateServer()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/stream",
                        "", "", timeout=6.0, debug=True)
    try:
        info = c.connect()
        mode, rtp_channel, session_id = c._mode, c._rtp_channel, c.session_id
        log = "\n".join(c._debug)
    finally:
        c.close()
    srv.join(timeout=5)
    assert info["codec"] == "PCMA"
    assert info["pt"] == 8
    assert mode == "tcp"
    assert rtp_channel == 2
    assert session_id == "SES123"
    assert "aggregate: video-first SETUP" in log
    assert "audio SETUP (aggregate) -> 200" in log


def test_aggregate_fallback_raises_original_461_when_video_rejected():
    srv = AggregateServer(reject_video=True)
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/stream",
                        "", "", timeout=6.0)
    try:
        with pytest.raises(RTSPError, match=r"کد 461"):
            c.connect()
    finally:
        c.close()
    srv.join(timeout=5)


def test_read_audio_frame_skips_video_interleaved_channel():
    c = RTSPAudioClient("rtsp://127.0.0.1:554/x")
    c.sock, peer = socket.socketpair()
    c._mode = "tcp"
    c._rtp_channel = 2
    c.audio = {"pt": 8, "codec": "PCMA", "clock": 8000, "channels": 1}
    try:
        peer.sendall(_interleaved(0, _rtp(96, b"\x11" * 20)))   # ویدیو
        peer.sendall(_interleaved(3, _rtp(8, b"\x22" * 10)))    # RTCP صدا
        peer.sendall(_interleaved(2, _rtp(8, b"\x33" * 160)))   # صدا
        codec, clock, payload = c.read_audio_frame()
    finally:
        peer.close()
        c.sock.close()
    assert codec == "PCMA"
    assert clock == 8000
    assert payload == b"\x33" * 160


def test_parse_sdp_tracks_finds_video_and_audio():
    tracks = parse_sdp_tracks(SDP_XM, "rtsp://127.0.0.1:554/stream")
    assert tracks["video"] is not None
    assert tracks["video"]["control_url"].endswith("trackID=0")
    assert tracks["audio"] is not None
    assert tracks["audio"]["pt"] == 8
    assert tracks["audio"]["codec"] == "PCMA"
    assert tracks["audio"]["control_url"].endswith("trackID=1")


def test_parse_sdp_audio_wrapper_still_works():
    info = parse_sdp_audio(SDP_XM, "rtsp://127.0.0.1:554/stream")
    assert info is not None
    assert info["codec"] == "PCMA"
    assert parse_sdp_audio("v=0\r\n", "rtsp://127.0.0.1:554/x") is None

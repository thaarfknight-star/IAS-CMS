# -*- coding: utf-8 -*-
"""تست 2.0.81-beta — قابلیت «🔊 صحبت با دوربین» (اتصال به بلندگوی دوربین).

پوشش:
1. انکد G.711 μ-law: وکتورهای شناخته‌شده‌ی ITU-T + نصف‌شدن حجم فریم.
2. بسته‌ی RTP: هدر (نسخه/PT/seq/timestamp/ssrc) + فریم interleaved روی TCP.
3. SDP بک‌چنل: محتوای درست (PCMU/8000/sendonly).
4. احرازهویت Digest: وکتور RFC 2617 (Mufasa).
5. هندشیک کامل RTSP (OPTIONS→ANNOUNCE→SETUP→RECORD→RTP→TEARDOWN)
   در برابر یک سرور RTSP جعلی روی localhost، شامل چالش Digest.
6. شناسایی پشتیبانی بلندگو با ماژول onvif شبیه‌سازی‌شده.
7. ساخت آدرس بک‌چنل (حذف یوزر/پس از URL).
"""
import os
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_talk
from camera_talk import (
    RTSPBackchannel,
    RTSPError,
    build_backchannel_sdp,
    build_backchannel_url,
    build_digest_auth,
    build_rtp_interleaved,
    build_rtp_packet,
    detect_talk_support,
    encode_ulaw_frame,
    linear_to_ulaw,
    ulaw_frame_rms,
)


# ---------------------------------------------------------------- 1. μ-law

def test_ulaw_known_vectors():
    # وکتورهای استاندارد G.711
    assert linear_to_ulaw(0) == 0xFF
    assert linear_to_ulaw(-1) == 0x7F
    assert linear_to_ulaw(32767) == 0x80
    assert linear_to_ulaw(-32768) == 0x00


def test_ulaw_frame_halves_size():
    import numpy as np
    pcm = (np.arange(160, dtype="<i2") * 100).tobytes()
    ulaw = encode_ulaw_frame(pcm)
    assert len(ulaw) == 160
    assert ulaw[0] == 0xFF  # نمونه‌ی صفر ← 0xFF


def test_ulaw_frame_rms_silence_and_tone():
    import numpy as np
    silence = (np.zeros(160, dtype="<i2")).tobytes()
    assert ulaw_frame_rms(silence) == 0.0
    tone = (np.ones(160, dtype="<i2") * 16384).tobytes()
    lvl = ulaw_frame_rms(tone)
    assert 0.49 < lvl < 0.51


# ---------------------------------------------------------------- 2. RTP

def test_rtp_packet_header():
    payload = bytes([0xFF] * 160)
    pkt = build_rtp_packet(seq=0x1234, timestamp=0xABCDEF01,
                           ssrc=0x11223344, payload=payload)
    assert len(pkt) == 12 + 160
    b0, b1, seq, ts, ssrc = struct.unpack(">BBHII", pkt[:12])
    assert b0 == 0x80 and (b1 & 0x7F) == 0  # نسخه‌ی ۲، PT=PCMU
    assert seq == 0x1234 and ts == 0xABCDEF01 and ssrc == 0x11223344


def test_rtp_interleaved_framing():
    payload = bytes([0xAB] * 160)
    frame = build_rtp_interleaved(1, 160, 99, payload)
    assert frame[0:1] == b"$" and frame[1] == 0
    (length,) = struct.unpack(">H", frame[2:4])
    assert length == 12 + 160
    assert len(frame) == 4 + length


# ---------------------------------------------------------------- 3. SDP

def test_backchannel_sdp():
    sdp = build_backchannel_sdp("192.168.1.50")
    assert "m=audio 0 RTP/AVP 0" in sdp
    assert "a=rtpmap:0 PCMU/8000" in sdp
    assert "a=sendonly" in sdp
    assert "192.168.1.50" in sdp


# ---------------------------------------------------------------- 4. Digest

def test_digest_rfc2617_vector():
    # وکتور RFC 2617: Mufasa / Circle Of Life
    challenge = {
        "realm": "testrealm@host.com",
        "nonce": "dcd98b7102dd2f0e8b11d0f600bfb0c093",
        "qop": "auth",
        "algorithm": "MD5",
        "opaque": "5ccc069c403ebaf9f0171e9517f40e41",
    }
    hdr = build_digest_auth("Mufasa", "Circle Of Life", "GET",
                            "/dir/index.html", challenge,
                            cnonce="0a4f113b")
    assert 'response=6629fae49393a05397450978507c4ef1' in hdr
    assert 'username="Mufasa"' in hdr


# ---------------------------------------------------------------- 5. سرور RTSP جعلی

class _FakeRTSPServer(threading.Thread):
    """سرور RTSP حداقلی: چالش Digest در OPTIONS اول، بعد قبول همه‌چیز."""

    REALM = "fake-realm"
    NONCE = "fake-nonce-123"

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.requests = []          # (method, has_auth)
        self.rtp_bytes = b""
        self.announce_body = b""

    def _read_request(self, conn):
        f = conn.makefile("rb")
        try:
            line = f.readline().decode("iso-8859-1").strip()
            if not line:
                return None
            method = line.split(" ", 1)[0]
            headers = {}
            while True:
                h = f.readline().decode("iso-8859-1")
                if h in ("\r\n", "\n", ""):
                    break
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            body = b""
            n = int(headers.get("content-length", "0") or 0)
            if n:
                body = f.read(n)
            return method, headers, body
        finally:
            f.close()

    def _respond(self, conn, code, headers=None, cseq="1"):
        headers = headers or {}
        lines = [f"RTSP/1.0 {code} {'OK' if code == 200 else 'Unauthorized'}",
                 f"CSeq: {cseq}"]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

    def run(self):
        conn, _ = self.sock.accept()
        authed = False
        cseq = "1"
        try:
            while True:
                req = self._read_request(conn)
                if req is None:
                    break
                method, headers, body = req
                cseq = headers.get("cseq", cseq)
                has_auth = "authorization" in headers
                self.requests.append((method, has_auth))
                if not authed and not has_auth:
                    self._respond(conn, 401, {
                        "WWW-Authenticate":
                            f'Digest realm="{self.REALM}", nonce="{self.NONCE}", '
                            'algorithm=MD5, qop="auth"',
                    }, cseq)
                    continue
                authed = True
                if method == "ANNOUNCE":
                    self.announce_body = body
                    # بک‌چنل ONVIF باید هدر Require را داشته باشد
                    assert "www.onvif.org/ver20/backchannel" in \
                        headers.get("require", ""), headers
                    self._respond(conn, 200, {}, cseq)
                elif method == "SETUP":
                    assert "interleaved=0-1" in headers.get("transport", "")
                    self._respond(conn, 200,
                                  {"Session": "FAKESESSION123"}, cseq)
                elif method in ("RECORD", "OPTIONS", "TEARDOWN"):
                    self._respond(conn, 200, {}, cseq)
                    if method == "TEARDOWN":
                        break
                else:
                    self._respond(conn, 200, {}, cseq)
                # خواندن فریم‌های interleaved احتمالی بعد از RECORD
                if method == "RECORD":
                    conn.settimeout(3.0)
                    try:
                        while True:
                            data = conn.recv(4096)
                            if not data:
                                break
                            self.rtp_bytes += data
                            if b"TEARDOWN" in self.rtp_bytes:
                                break
                    except socket.timeout:
                        pass
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def test_full_backchannel_handshake_with_digest():
    server = _FakeRTSPServer()
    server.start()
    url = f"rtsp://127.0.0.1:{server.port}/cam/realmonitor"
    bc = RTSPBackchannel(url, "admin", "12345", timeout=5.0)
    bc.connect()  # باید چالش Digest را پاس کند
    methods = [m for m, _ in server.requests]
    assert methods[0] == "OPTIONS"
    assert "ANNOUNCE" in methods and "SETUP" in methods and "RECORD" in methods
    # لااقل یک درخواست با Authorization ارسال شده (بعد از 401)
    assert any(has_auth for _, has_auth in server.requests)
    # SDP بک‌چنل واقعاً رسیده
    assert b"m=audio 0 RTP/AVP 0" in server.announce_body
    # ارسال یک فریم صوتی
    bc.send_audio_frame(bytes([0xFF] * 160))
    bc.close()
    server.join(timeout=10)
    assert server.rtp_bytes.startswith(b"$\x00")
    assert bc.session_id == ""  # بعد از close خالی می‌شود


def test_backchannel_connection_refused():
    # پورت بسته ← RTSPError تمیز، نه اکسپشن خام سوکت
    bc = RTSPBackchannel("rtsp://127.0.0.1:1/closed", timeout=1.0)
    try:
        bc.connect()
    except RTSPError as e:
        assert "اتصال" in str(e)
    else:
        raise AssertionError("باید RTSPError می‌داد")


# ---------------------------------------------------------------- 6. شناسایی

def _install_fake_onvif(has_audio_output: bool):
    import types

    class _FakeMedia:
        def __init__(self, ok):
            self._ok = ok

        def GetAudioOutputConfigurations(self):
            return ["cfg1"] if self._ok else []

        def GetProfiles(self):
            return []

    class _FakeCam:
        def __init__(self, ip, port, user, pwd, wsdl_dir=None):
            if port == 9999:
                raise ConnectionError("unreachable")

        def create_media_service(self):
            return _FakeMedia(has_audio_output)

    mod = types.ModuleType("onvif")
    mod.ONVIFCamera = _FakeCam
    sys.modules["onvif"] = mod


def test_detect_supported_and_unsupported():
    cam = {"ip": "192.168.1.10", "port": 554, "user": "a", "pass": "b"}
    _install_fake_onvif(True)
    try:
        info = detect_talk_support(cam)
        assert info["supported"] is True
        assert info["onvif_port"] == 554  # اول پورت RTSP خودش را می‌زند
    finally:
        sys.modules.pop("onvif", None)

    _install_fake_onvif(False)
    try:
        info = detect_talk_support(cam)
        assert info["supported"] is False
    finally:
        sys.modules.pop("onvif", None)


def test_detect_nvr_channel_blocked():
    cam = {"ip": "192.168.1.10", "port": 554, "nvr_id": "xxx"}
    info = detect_talk_support(cam)
    assert info["supported"] is False
    assert info["error"] == "nvr_channel"


# ---------------------------------------------------------------- 7. آدرس بک‌چنل

def test_backchannel_url_strips_credentials():
    import types
    # camera_store در سطح ماژول cv2 می‌خواهد (روی این VM نصب نیست)؛ stub سبک
    _cv2 = sys.modules.get("cv2") or types.ModuleType("cv2")
    _cv2.VideoCapture = getattr(_cv2, "VideoCapture", type("VideoCapture", (), {}))
    sys.modules["cv2"] = _cv2
    cam = {"ip": "192.168.1.10", "port": 554, "user": "admin", "pass": "p@ss",
           "path": "/h264/ch1"}
    url = build_backchannel_url(cam)
    assert url.startswith("rtsp://192.168.1.10:554/")
    assert "admin" not in url and "p@ss" not in url

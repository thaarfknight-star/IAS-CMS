# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.84-beta — فیکس‌های «شنیدن صدای دوربین».

پوشش:
1. پارس SDP سالم برای ترک صوتی (PCMU).
2. دوربین سخت‌گیر که با nonce تازه روی SETUP دوباره ۴۰۱ می‌دهد ←
   کلاینت باید Digest را با چلنج جدید بازسازی و ادامه دهد (نه گیرکردن).
3. رمز اشتباه ← RTSPError روشن و بدون حلقه‌ی بی‌نهایت.
4. لاگ عیب‌یابی handshake: متد/کد و Transport ثبت می‌شود، بدون رمز و
   بدون هدر Authorization.
"""

import socket
import threading

import pytest

from camera_audio import RTSPAudioClient, RTSPError, parse_sdp_audio

SDP_AUDIO_OK = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=Test
c=IN IP4 0.0.0.0
t=0 0
m=video 0 RTP/AVP 96
a=rtpmap:96 H264/90000
a=control:trackID=0
m=audio 5004 RTP/AVP 0
a=rtpmap:0 PCMU/8000
a=control:trackID=1
"""


def test_audio_normal_port_still_found():
    info = parse_sdp_audio(SDP_AUDIO_OK, "rtsp://127.0.0.1:554/stream")
    assert info is not None
    assert info["codec"] == "PCMU"


class StrictNonceServer(threading.Thread):
    """سرور سخت‌گیر: روی DESCRIBE و SETUP هر بار nonce تازه می‌دهد.

    اگر wrong_password=True باشد، هیچ هدر Authorizationای را قبول نمی‌کند.
    """

    def __init__(self, wrong_password=False):
        super().__init__(daemon=True)
        self.wrong_password = wrong_password
        self._setup_challenged = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.nonce_counter = 0

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
        headers, cseq, authed = {}, "1", False
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            if ln.lower().startswith("cseq:"):
                cseq = ln.split(":", 1)[1].strip()
            if ln.lower().startswith("authorization:"):
                authed = True
        return method, cseq, authed, headers

    def _respond(self, conn, cseq, code, msg, headers=None, body=b""):
        headers = headers or {}
        out = [f"RTSP/1.0 {code} {msg}", f"CSeq: {cseq}"]
        for k, v in headers.items():
            out.append(f"{k}: {v}")
        if body:
            out.append(f"Content-Length: {len(body)}")
        conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + body)

    def _challenge(self):
        self.nonce_counter += 1
        return ('Digest realm="strict", nonce="n%d", qop="auth", '
                'algorithm="MD5"') % self.nonce_counter

    def run(self):
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(10)
            authed_ok = False
            while True:
                try:
                    method, cseq, authed, headers = self._read_request(conn)
                except OSError:
                    break
                if not method:
                    break
                if method in ("OPTIONS",):
                    self._respond(conn, cseq, 200, "OK")
                elif method in ("DESCRIBE", "SETUP"):
                    # همیشه nonce تازه؛ فقط اگر Authorization با nonce آخر
                    # آمده و wrong_password نباشد قبول می‌کنیم. برای SETUP
                    # بار اول حتماً ۴۰۱ با nonce تازه می‌دهیم تا مسیر
                    # بازسازی Digest تست شود.
                    if method == "SETUP" and not self._setup_challenged:
                        self._setup_challenged = True
                        self._respond(conn, cseq, 401, "Unauthorized",
                                      {"WWW-Authenticate": self._challenge()})
                        continue
                    auth = headers.get("authorization", "")
                    last_nonce = "n%d" % self.nonce_counter
                    if (authed and not self.wrong_password
                            and f'nonce="{last_nonce}"' in auth):
                        authed_ok = True
                    if not authed_ok:
                        self._respond(conn, cseq, 401, "Unauthorized",
                                      {"WWW-Authenticate": self._challenge()})
                        continue
                    if method == "DESCRIBE":
                        sdp = SDP_AUDIO_OK.replace(
                            "127.0.0.1", "127.0.0.1")
                        self._respond(conn, cseq, 200, "OK",
                                      {"Content-Type": "application/sdp"},
                                      sdp.encode())
                    else:
                        self._respond(
                            conn, cseq, 200, "OK",
                            {"Session": "s1",
                             "Transport": ("RTP/AVP/TCP;unicast;"
                                           "interleaved=0-1")})
                elif method == "PLAY":
                    self._respond(conn, cseq, 200, "OK",
                                  {"Session": "s1"})
                    break
                else:
                    self._respond(conn, cseq, 200, "OK")
                    break


def _client_for(server):
    return RTSPAudioClient(f"rtsp://127.0.0.1:{server.port}/stream",
                           "admin", "1234", timeout=6.0)


def test_strict_camera_fresh_nonce_on_setup():
    srv = StrictNonceServer()
    srv.start()
    c = _client_for(srv)
    try:
        info = c.connect()
        assert info["codec"] == "PCMU"
    finally:
        c.close()
    srv.join(timeout=5)


def test_wrong_password_fails_cleanly_no_loop():
    srv = StrictNonceServer(wrong_password=True)
    srv.start()
    c = _client_for(srv)
    try:
        with pytest.raises(RTSPError) as exc:
            c.connect()
        assert "احرازهویت" in str(exc.value)
    finally:
        c.close()
    srv.join(timeout=5)


def test_debug_log_records_handshake_without_secrets():
    srv = StrictNonceServer()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/stream",
                        "admin", "s3cr3t", timeout=6.0, debug=True)
    try:
        c.connect()
        log = "\n".join(c._debug)
        assert "OPTIONS -> 200" in log
        assert "DESCRIBE -> 200" in log
        assert "SETUP -> 200" in log
        assert "transport sent: RTP/AVP/TCP;unicast;interleaved=0-1" in log
        assert "s3cr3t" not in log
        assert "Authorization" not in log
    finally:
        c.close()
    srv.join(timeout=5)


def test_debug_disabled_records_nothing():
    c = RTSPAudioClient("rtsp://127.0.0.1:554/stream", "u", "p")
    assert c._debug is None


def test_debug_log_includes_sdp_audio_section():
    srv = StrictNonceServer()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/stream",
                        "admin", "1234", timeout=6.0, debug=True)
    try:
        c.connect()
        log = "\n".join(c._debug)
        assert "sdp: m=audio 5004 RTP/AVP 0" in log
        assert "sdp: a=rtpmap:0 PCMU/8000" in log
        assert "sdp: a=control:trackID=1" in log
        # سکشن ویدیو نباید لاگ شود
        assert "sdp: m=video" not in log
    finally:
        c.close()
    srv.join(timeout=5)

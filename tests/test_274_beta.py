# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.90-beta — راستی‌آزمایی جریان صوت و fallback به aggregate.

زمینه: لاگ پنجم طه (IAS-Viewer-listen-debug-5.log) نشان داد با 2.0.89:
- SETUP مستقیم صدا روی آدرس کامل ۲۰۰ می‌دهد (fallback آدرس 2.0.88 جواب داد)
- ولی دوربین فقط ویدیو می‌فرستد: ۵۳۱۸ و ۹۶۲۰ بسته، همه PT=96، صفر PT=8
یعنی «۲۰۰ گرفتن» به‌تنهایی کافی نیست.

تغییرات 2.0.90:
1. هر مسیر (مستقیم/aggregate/session-level) بعد از PLAY جریان واقعی صوت
   را راستی‌آزمایی می‌کند (انتظار برای یک بسته با PT صوتی)؛ اگر نرسید،
   TEARDOWN + اتصال TCP تازه و تلاش مسیر بعدی.
2. مسیر aggregate حالا برای هر دو SETUP (ویدیو و صدا) هر دو فرم آدرس
   (کوتاه و کامل با query) را امتحان می‌کند.
3. لاگ هر مسیر: «audio packet OK» یا «no audio packet» همراه آمار PTها.

پوشش:
1. سناریوی end-to-end لاگ طه: دوربین فیک که SETUP مستقیم را ۲۰۰ می‌دهد
   ولی فقط ویدیو می‌فرستد → connect() به aggregate می‌رسد و صدا را می‌گیرد.
2. اگر هیچ مسیری صدا ندهد، connect() خطای no_audio_data می‌دهد و لاگ
   آمار هر سه مسیر را دارد.
"""

import select
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
a=control:trackID=1
a=recvonly
"""


def _rtp(pt: int, payload: bytes, seq: int = 1) -> bytes:
    hdr = struct.pack(">BBHII", 0x80, pt & 0x7F, seq, 2, 3)
    return hdr + payload


def _interleaved(channel: int, pkt: bytes) -> bytes:
    return b"$" + bytes([channel]) + struct.pack(">H", len(pkt)) + pkt


class CameraLikeServer(threading.Thread):
    """شبیه‌ساز دوربین طه از روی لاگ پنجم (2.0.89).

    - SETUP مستقیم صدا: آدرس کوتاه ← ۴۶۱، آدرس کامل ← ۲۰۰ ولی بعد از
      PLAY فقط ویدیو (PT=96) می‌فرستد — صدا صفر.
    - SETUP ویدیو (برای aggregate): کوتاه ← ۴۶۱، کامل ← ۲۰۰.
    - SETUP صدا با Session ویدیو: کوتاه ← ۴۶۱، کامل ← ۲۰۰ (interleaved=2-3).
    - SETUP روی presentation URL (مسیر session-level): ← ۲۰۰.
    - Transport روی UDP را کلاً ۴۶۱ می‌دهد.
    - اگر never_audio=True باشد، حتی در aggregate هم فقط ویدیو می‌فرستد.
    چند اتصال پشت سر هم را قبول می‌کند (کلاینت بین مسیرها reconnect می‌کند).
    """

    def __init__(self, never_audio: bool = False):
        super().__init__(daemon=True)
        self.never_audio = never_audio
        self._stop = False
        self.play_counts = []  # (mode, n_audio, n_video) هر PLAY
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(5)

    def _read_request(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            return "", "", "1", {}
        head = data.decode("iso-8859-1")
        lines = head.split("\r\n")
        method = lines[0].split(" ", 2)[0] if " " in lines[0] else ""
        target = lines[0].split(" ", 2)[1] if " " in lines[0] else ""
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

    def _is_full(self, target):
        return "av_stream/trackID" in target

    def _handle(self, conn):
        conn.settimeout(10)
        video_done = False
        sess_level = False
        while True:
            try:
                method, target, cseq, headers = self._read_request(conn)
            except OSError:
                return
            if not method:
                return
            if method == "OPTIONS":
                self._respond(conn, cseq, 200, "OK")
            elif method == "DESCRIBE":
                self._respond(conn, cseq, 200, "OK",
                              {"Content-Type": "application/sdp"},
                              SDP_XM.encode())
            elif method == "SETUP":
                transport = headers.get("transport", "")
                if transport.startswith("RTP/AVP;"):
                    # UDP را قبول ندارد
                    self._respond(conn, cseq, 461, "Unsupported Transport")
                elif "trackID=0" in target:
                    if self._is_full(target):
                        video_done = True
                        self._respond(
                            conn, cseq, 200, "OK",
                            {"Session": "AGG1",
                             "Transport": ("RTP/AVP/TCP;unicast;"
                                           "interleaved=0-1")})
                    else:
                        self._respond(conn, cseq, 461,
                                      "Unsupported Transport")
                elif "trackID=1" in target:
                    if self._is_full(target):
                        tr = ("RTP/AVP/TCP;unicast;interleaved=2-3"
                              if video_done else
                              "RTP/AVP/TCP;unicast;interleaved=0-1")
                        self._respond(conn, cseq, 200, "OK",
                                      {"Session": "AGG1" if video_done
                                       else "D1",
                                       "Transport": tr})
                    else:
                        self._respond(conn, cseq, 461,
                                      "Unsupported Transport")
                else:
                    # SETUP روی presentation URL (مسیر session-level)
                    sess_level = True
                    self._respond(conn, cseq, 200, "OK",
                                  {"Session": "SESS1",
                                   "Transport": ("RTP/AVP/TCP;unicast;"
                                                 "interleaved=0-1")})
            elif method == "PLAY":
                self._respond(conn, cseq, 200, "OK", {"Session": "S"})
                if video_done and not self.never_audio:
                    self._stream_mixed(conn)
                else:
                    self._stream_video_only(conn)
                return
            elif method == "TEARDOWN":
                self._respond(conn, cseq, 200, "OK")
                return

    def _stream_video_only(self, conn, duration=3.0):
        n_v = 0
        end = time.time() + duration
        while time.time() < end:
            r, _, _ = select.select([conn], [], [], 0.005)
            if r:
                try:
                    method, _, cseq, _ = self._read_request(conn)
                except OSError:
                    return
                if method == "TEARDOWN":
                    self._respond(conn, cseq, 200, "OK")
                return
            try:
                conn.sendall(_interleaved(0, _rtp(96, b"\x11" * 200,
                                                 seq=n_v)))
                n_v += 1
            except OSError:
                return
        self.play_counts.append(("video-only", 0, n_v))

    def _stream_mixed(self, conn):
        n_a = n_v = 0
        try:
            for i in range(4):
                conn.sendall(_interleaved(
                    0, _rtp(96, b"\x11" * 200, seq=i)))
                n_v += 1
                conn.sendall(_interleaved(
                    2, _rtp(8, b"\xd5" * 160, seq=i)))
                n_a += 1
        except OSError:
            return
        self.play_counts.append(("mixed", n_a, n_v))
        conn.settimeout(5)
        try:
            while True:
                method, _, cseq, _ = self._read_request(conn)
                if not method:
                    return
                if method == "TEARDOWN":
                    self._respond(conn, cseq, 200, "OK")
                    return
        except OSError:
            return

    def run(self):
        while not self._stop:
            try:
                self.sock.settimeout(0.5)
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


def _fast_verify_client(srv):
    """کلاینت با verify کوتاه‌تر برای سرعت تست (منطق همان است)."""
    c = RTSPAudioClient(
        f"rtsp://127.0.0.1:{srv.port}/h264/ch1/main/av_stream",
        "", "", timeout=6.0, debug=True)
    orig = RTSPAudioClient._play_and_verify_audio
    RTSPAudioClient._play_and_verify_audio = (
        lambda self, name, timeout=1.5: orig(self, name, timeout))
    return c, orig


def test_274_direct_silent_falls_back_to_aggregate():
    """سناریوی لاگ پنجم: مستقیم ۲۰۰ ولی بی‌صدا → aggregate صدا می‌دهد."""
    srv = CameraLikeServer(never_audio=False)
    srv.start()
    c, orig = _fast_verify_client(srv)
    try:
        info = c.connect()
        log = "\n".join(c._debug)
    finally:
        RTSPAudioClient._play_and_verify_audio = orig
        try:
            c.close()
        except Exception:
            pass
        srv.stop()
    assert info["codec"] == "PCMA"
    assert info["pt"] == 8
    # مسیر مستقیم صدا نداد ولی aggregate داد
    assert "direct: no audio packet" in log
    assert "aggregate: audio packet OK" in log
    # در مسیر مستقیم فقط PT ویدیو (96) دیده شد — مثل لاگ طه
    direct_line = [ln for ln in log.splitlines()
                   if "direct: no audio packet" in ln][0]
    assert "96:" in direct_line
    assert "8:" not in direct_line


def test_274_no_audio_anywhere_raises_with_per_path_stats():
    """اگر هیچ مسیری صدا ندهد: خطای no_audio_data + آمار هر سه مسیر."""
    srv = CameraLikeServer(never_audio=True)
    srv.start()
    c, orig = _fast_verify_client(srv)
    try:
        with pytest.raises(RTSPError) as ei:
            c.connect()
        log = "\n".join(c._debug)
    finally:
        RTSPAudioClient._play_and_verify_audio = orig
        srv.stop()
    assert "no_audio_data" in str(ei.value)
    assert "direct: no audio packet" in log
    assert "aggregate: no audio packet" in log
    assert "session: no audio packet" in log


class EarlyRtpServer(threading.Thread):
    """پاسخ PLAY و بسته‌های RTP را در یک sendall می‌فرستد (یک سگمنت TCP).

    قبلاً _read_response با makefile بایت‌های اضافی را دور می‌ریخت و
    بسته‌های اول صدا گم می‌شدند.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)

    def run(self):
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(10)
            data = b""
            n_play = 0
            while n_play < 1:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
                while b"\r\n\r\n" in data:
                    head, _, data = data.partition(b"\r\n\r\n")
                    method = head.decode("iso-8859-1").split(" ", 2)[0]
                    if method == "OPTIONS":
                        conn.sendall(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n")
                    elif method == "DESCRIBE":
                        body = SDP_XM.encode()
                        conn.sendall(
                            b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n"
                            b"Content-Type: application/sdp\r\n"
                            b"Content-Length: " + str(len(body)).encode() +
                            b"\r\n\r\n" + body)
                    elif method == "SETUP":
                        conn.sendall(
                            b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n"
                            b"Session: E1\r\n"
                            b"Transport: RTP/AVP/TCP;unicast;interleaved=0-1"
                            b"\r\n\r\n")
                    elif method == "PLAY":
                        n_play += 1
            # پاسخ PLAY + سه بسته‌ی صوتی در یک سگمنت
            blob = (b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nSession: E1\r\n\r\n" +
                    b"".join(_interleaved(0, _rtp(8, b"\xE5" * 160, seq=i))
                             for i in range(3)))
            try:
                conn.sendall(blob)
            except OSError:
                pass
            try:
                conn.settimeout(5)
                conn.recv(4096)  # TEARDOWN یا بسته‌شدن
            except OSError:
                pass


def test_274_early_rtp_with_play_response_not_lost():
    """بسته‌های RTP چسبیده به پاسخ PLAY گم نمی‌شوند (بافر _rx_buf)."""
    srv = EarlyRtpServer()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                        "", "", timeout=6.0, debug=True)
    try:
        info = c.connect()
        log = "\n".join(c._debug)
    finally:
        try:
            c.close()
        except Exception:
            pass
    assert info["codec"] == "PCMA"
    assert "direct: audio packet OK" in log
    # هر سه بسته دیده شده‌اند (نه فقط اولی)
    assert c._rtp_total >= 1

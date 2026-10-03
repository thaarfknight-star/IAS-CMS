# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.91-beta — اسکن ONVIF و فیدبک پیشرفت.

زمینه: طه گفت دیالوگ فقط «در حال اتصال به دوربین» می‌ماند و هیچ اتفاقی
نمی‌افتد، و پرسید نمی‌شود وب دوربین را اسکن کرد و مسیر صدا را پیدا کرد؟

تغییرات 2.0.91:
1. اسکن ONVIF (onvif_scan.py): GetCapabilities ← GetProfiles ←
   GetStreamUri با احرازهویت WS-Security؛ URIهای معرفی‌شده توسط خود
   دوربین در لاگ ثبت و (اگر با آدرس تنظیم‌شده فرق داشتند) امتحان می‌شوند.
2. کال‌بک on_progress: دیالوگ مسیر فعلی («مستقیم»، «aggregate» و...)
   را در متن «در حال اتصال» نشان می‌دهد.
3. تایم‌استمپ در لاگ عیب‌یابی ([+12.3s]) تا معلوم باشد زمان کجا رفته.

پوشش:
1. کشف ONVIF در برابر سرور فیک: URIها برگردند.
2. fallback به URI متفاوت ONVIF وقتی آدرس اصلی صدا نمی‌دهد.
3. on_progress در طول connect() صدا زده می‌شود.
4. خطاهای لاگ تایم‌استمپ دارند.
5. (2.0.92) RST وسط handshake (مثل WinError 10054): به RTSPError فارسی
   تبدیل می‌شود، کلاینت reconnect می‌کند و مسیر بعدی را امتحان می‌کند؛
   اگر همه RST بخورند، خطای فارسی + لاگ می‌دهد نه OSError خام.
6. (2.0.92) بین مسیرها TEARDOWN روی همان اتصال (بدون reconnect) —
   ملایم‌تر برای دوربین.
"""

import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import onvif_scan
from onvif_scan import onvif_get_stream_uris
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


def _rtp(pt, payload, seq=1):
    return struct.pack(">BBHII", 0x80, pt & 0x7F, seq, 2, 3) + payload


def _interleaved(channel, pkt):
    return b"$" + bytes([channel]) + struct.pack(">H", len(pkt)) + pkt


# -- سرور فیک ONVIF -----------------------------------------------------------

CAPABILITIES_RESP = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<tds:Capabilities><tds:Media>
<tt:XAddr xmlns:tt="http://www.onvif.org/ver10/schema">http://127.0.0.1:8899/onvif/media_service</tt:XAddr>
</tds:Media></tds:Capabilities></tds:GetCapabilitiesResponse></s:Body></s:Envelope>"""

PROFILES_RESP = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<trt:Profiles token="main"><trt:Name>main</trt:Name></trt:Profiles>
<trt:Profiles token="sub"><trt:Name>sub</trt:Name></trt:Profiles>
</trt:GetProfilesResponse></s:Body></s:Envelope>"""

STREAM_URI_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<trt:MediaUri><tt:Uri xmlns:tt="http://www.onvif.org/ver10/schema">{uri}</tt:Uri>
</trt:MediaUri></trt:GetStreamUriResponse></s:Body></s:Envelope>"""


class FakeOnvifHandler(BaseHTTPRequestHandler):
    rtsp_port = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8", errors="replace")
        if "GetCapabilities" in body:
            resp = CAPABILITIES_RESP
        elif "GetProfiles" in body:
            resp = PROFILES_RESP
        elif "GetStreamUri" in body:
            m = re.search(r"<trt:ProfileToken>(.*?)</trt:ProfileToken>", body)
            token = m.group(1) if m else "main"
            uri = (f"rtsp://127.0.0.1:{self.rtsp_port}/h264/ch1/"
                   f"{'main' if token == 'main' else 'sub'}/av_stream")
            resp = STREAM_URI_TMPL.format(uri=uri)
        else:
            resp = "<s:Envelope/>"
        data = resp.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_onvif_server(rtsp_port):
    FakeOnvifHandler.rtsp_port = rtsp_port
    srv = HTTPServer(("127.0.0.1", 8899), FakeOnvifHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def test_275_onvif_discovery_returns_uris():
    """سرور فیک ONVIF: دو URI (main/sub) کشف می‌شوند."""
    srv = _start_onvif_server(rtsp_port=554)
    try:
        uris = onvif_get_stream_uris("127.0.0.1", "admin", "12345",
                                     timeout=3.0)
    finally:
        srv.shutdown()
        srv.server_close()
    assert len(uris) == 2
    assert uris[0] == "rtsp://127.0.0.1:554/h264/ch1/main/av_stream"
    assert uris[1] == "rtsp://127.0.0.1:554/h264/ch1/sub/av_stream"


def test_275_onvif_no_service_returns_empty():
    """اگر ONVIF نباشد (پورت بسته)، لیست خالی و بدون اکسپشن."""
    uris = onvif_get_stream_uris("127.0.0.1", "admin", "x",
                                 timeout=1.0)
    # پورت 8899 بسته است (سرور بالا خاموش شده) و 80 هم ONVIF نیست
    assert uris == []


# -- سرور فیک RTSP برای fallback ------------------------------------------------

class OnvifFallbackRtspServer(threading.Thread):
    """آدرس اصلی فقط ویدیو می‌فرستد؛ مسیر /onvif-audio صدا هم می‌فرستد."""

    def __init__(self):
        super().__init__(daemon=True)
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
        head = data.decode("iso-8859-1")
        lines = head.split("\r\n")
        parts = lines[0].split(" ", 2)
        return parts[0], parts[1] if len(parts) > 1 else ""

    def _respond(self, conn, code, msg, headers=None, body=b""):
        out = [f"RTSP/1.0 {code} {msg}", "CSeq: 1"]
        for k, v in (headers or {}).items():
            out.append(f"{k}: {v}")
        if body:
            out.append(f"Content-Length: {len(body)}")
        conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + body)

    def _handle(self, conn):
        conn.settimeout(10)
        audio_path = False
        try:
            while True:
                method, target = self._read_request(conn)
                if not method:
                    return
                if method in ("OPTIONS", "ANNOUNCE"):
                    self._respond(conn, 200, "OK")
                elif method == "DESCRIBE":
                    self._respond(conn, 200, "OK", body=SDP_XM.encode())
                elif method == "SETUP":
                    audio_path = "onvif-audio" in target
                    self._respond(conn, 200, "OK", headers={
                        "Session": "S1",
                        "Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
                elif method == "PLAY":
                    self._respond(conn, 200, "OK",
                                  headers={"Session": "S1"})
                    for i in range(3):
                        conn.sendall(_interleaved(
                            0, _rtp(96, b"\x00" * 100, seq=i)))
                    if audio_path:
                        for i in range(3):
                            conn.sendall(_interleaved(
                                0, _rtp(8, b"\xE5" * 160, seq=10 + i)))
                    # بعد از PLAY هم حلقه ادامه دارد تا DESCRIBE بعدی
                    # (fallback به URI دیگر) یا TEARDOWN جواب داده شود
                    continue
                elif method == "TEARDOWN":
                    self._respond(conn, 200, "OK")
                    return
        except OSError:
            pass

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass


def test_275_connect_falls_back_to_onvif_uri(monkeypatch):
    """آدرس اصلی بی‌صدا ← connect() سراغ URI متفاوت ONVIF می‌رود و موفق می‌شود."""
    srv = OnvifFallbackRtspServer()
    srv.start()
    onvif_uri = f"rtsp://127.0.0.1:{srv.port}/onvif-audio"
    monkeypatch.setattr(onvif_scan, "onvif_get_stream_uris",
                        lambda *a, **k: [onvif_uri])

    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                        "", "", timeout=6.0, debug=True)
    progress_names = []
    c.on_progress = progress_names.append
    orig = RTSPAudioClient._play_and_verify_audio
    RTSPAudioClient._play_and_verify_audio = (
        lambda self, name, timeout=1.0: orig(self, name, timeout))
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
    assert f"ONVIF: {onvif_uri}" in log
    assert "audio packet OK" in log
    # پیشرفت واقعاً گزارش شده (نه فقط شروع)
    assert any("ONVIF" in p for p in progress_names)
    assert len(progress_names) > 3


def test_275_log_lines_have_timestamps():
    """خطاهای لاگ عیب‌یابی تایم‌استمپ [+Xs] دارند."""
    srv = OnvifFallbackRtspServer()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                        "", "", timeout=6.0, debug=True)
    orig = RTSPAudioClient._play_and_verify_audio
    RTSPAudioClient._play_and_verify_audio = (
        lambda self, name, timeout=0.5: orig(self, name, timeout))
    try:
        with pytest.raises(RTSPError):
            c.connect()
        lines = [l for l in c._debug if l.strip()]
    finally:
        RTSPAudioClient._play_and_verify_audio = orig
        srv.stop()
    assert lines, "لاگ خالی است"
    for l in lines:
        assert re.match(r"\[\s*\d+\.\d+s\] ", l), f"بدون تایم‌استمپ: {l[:60]}"


class RstServer(threading.Thread):
    """اتصال اول را وسط SETUP با RST می‌بندد (شبیه WinError 10054)؛
    اتصال بعدی عادی است و صدا می‌فرستد."""

    def __init__(self):
        super().__init__(daemon=True)
        self.n_conn = 0
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
        head = data.decode("iso-8859-1")
        return head.split(" ", 2)[0] if " " in head else ""

    def _respond(self, conn, code, msg, headers=None, body=b""):
        out = [f"RTSP/1.0 {code} {msg}", "CSeq: 1"]
        for k, v in (headers or {}).items():
            out.append(f"{k}: {v}")
        if body:
            out.append(f"Content-Length: {len(body)}")
        conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + body)

    def _rst_close(self, conn):
        import struct as _st
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                            _st.pack("ii", 1, 0))
        except OSError:
            pass
        conn.close()

    def _handle(self, conn, kill_on_setup):
        conn.settimeout(10)
        try:
            while True:
                method = self._read_request(conn)
                if not method:
                    return
                if method == "OPTIONS":
                    self._respond(conn, 200, "OK")
                elif method == "DESCRIBE":
                    self._respond(conn, 200, "OK", body=SDP_XM.encode())
                elif method == "SETUP":
                    if kill_on_setup:
                        self._rst_close(conn)
                        return
                    self._respond(conn, 200, "OK", headers={
                        "Session": "S1",
                        "Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
                elif method == "PLAY":
                    self._respond(conn, 200, "OK", headers={"Session": "S1"})
                    for i in range(2):
                        conn.sendall(_interleaved(
                            0, _rtp(8, b"\xE5" * 160, seq=i)))
                    return
                elif method == "TEARDOWN":
                    self._respond(conn, 200, "OK")
                    return
        except OSError:
            pass

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.n_conn += 1
            threading.Thread(target=self._handle,
                             args=(conn, self.n_conn == 1),
                             daemon=True).start()

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass


def test_275_rst_mid_handshake_reconnects_and_succeeds(monkeypatch):
    """RST وسط SETUP (مثل 10054 طه): خطا فارسی/لاگ‌دار می‌شود، کلاینت
    reconnect می‌کند و مسیر بعدی موفق می‌شود — نه abort."""
    monkeypatch.setattr(onvif_scan, "onvif_get_stream_uris",
                        lambda *a, **k: [])
    srv = RstServer()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                        "", "", timeout=6.0, debug=True)
    orig = RTSPAudioClient._play_and_verify_audio
    RTSPAudioClient._play_and_verify_audio = (
        lambda self, name, timeout=1.5: orig(self, name, timeout))
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
    # RST به RTSPError فارسی تبدیل شده (نه اکسپشن خام)
    assert "SETUP -> قطع ارتباط" in log
    assert "سوکت مرده، اتصال تازه" in log
    assert srv.n_conn >= 2  # واقعاً reconnect شده


def test_275_rst_everywhere_gives_rtsterror_with_log(monkeypatch):
    """اگر همه‌ی اتصال‌ها RST بخورند: RTSPError (نه OSError خام) + لاگ."""
    monkeypatch.setattr(onvif_scan, "onvif_get_stream_uris",
                        lambda *a, **k: [])

    class AlwaysRst(RstServer):
        def run(self):
            while True:
                try:
                    conn, _ = self.sock.accept()
                except OSError:
                    return
                # RST فوری روی هر اتصال
                self._rst_close(conn)

    srv = AlwaysRst()
    srv.start()
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                        "", "", timeout=4.0, debug=True)
    try:
        with pytest.raises(RTSPError) as ei:
            c.connect()
        log = "\n".join(c._debug)
    finally:
        srv.stop()
    # پیام فارسی و قابل‌فهم، نه OSError خام انگلیسی
    assert isinstance(ei.value, RTSPError)
    assert ("قطع ارتباط" in str(ei.value) or "نشد" in str(ei.value))
    assert "WinError" not in str(ei.value)
    assert "Errno" not in str(ei.value)
    assert "قطع ارتباط" in log  # نقطه‌ی دقیق شکست در لاگ هست

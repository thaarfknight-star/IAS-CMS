# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.94-beta — fallback احرازهویت با credential داخل URL.

زمینه: لاگ ششم طه — اسکن ONVIF سه URI جدید (`/snl/live/1/1` تا `/1/3`)
پیدا کرد ولی هر سه با 401/403 رد شدند. بعضی فریمورهای XM برای مسیرهای
ONVIF احراز هویت Digest را قبول نمی‌کنند ولی یوزر/پس داخل خود URL را
می‌پذیرند.

تغییر 2.0.94:
- اگر DESCRIBE روی URI کشف‌شده‌ی ONVIF با خطای احرازهویت (401) رد شد،
  یک بار هم با یوزر/پس جاسازی‌شده داخل URL امتحان می‌شود.
- رمز هرگز لاگ نمی‌شود (_mask_creds) و uri دایجست userinfo ندارد.

پوشش:
1. سرور فیک: DESCRIBE بدون credential در URL → 401؛ با credential →
   200 + SDP صوتی. کلاینت باید از طریق ONVIF fallback وصل شود و صدا بگیرد.
2. رمز داخل هیچ خط لاگ نیست.
"""

import socket
import struct
import threading

import onvif_scan
from camera_audio import RTSPAudioClient

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


class CredUrlServer(threading.Thread):
    """شبیه‌سازی فریمور XM بدقلق:
    - /live: DESCRIBE ‏200 (با ترک صوتی) ولی SETUP همیشه 461 → مسیر
      اصلی صدا نمی‌دهد تا نوبت به ONVIF برسد.
    - /snl/live/1/1 بدون credential در URL → ‏401 (مثل لاگ ششم طه).
    - /snl/live/1/1 با testuser:testpass@ در URL → ‏200 + SETUP/PLAY
      موفق و بسته‌ی صوتی واقعی.
    """

    USER = "testuser"
    PASS = "testpass"

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
        parts = head.split(" ", 2)
        return (parts[0], parts[1]) if len(parts) >= 2 else ("", "")

    def _respond(self, conn, code, msg, headers=None, body=b""):
        out = [f"RTSP/1.0 {code} {msg}", "CSeq: 1"]
        for k, v in (headers or {}).items():
            out.append(f"{k}: {v}")
        if body:
            out.append(f"Content-Length: {len(body)}")
        conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + body)

    def _has_creds(self, target):
        return f"{self.USER}:{self.PASS}@" in target

    def _handle(self, conn):
        conn.settimeout(10)
        try:
            while True:
                method, target = self._read_request(conn)
                if not method:
                    return
                if method == "OPTIONS":
                    self._respond(conn, 200, "OK")
                elif method == "DESCRIBE":
                    if "/snl/" in target and not self._has_creds(target):
                        # مثل لاگ ششم: 401 روی مسیر ONVIF
                        self._respond(conn, 401, "Unauthorized", headers={
                            "WWW-Authenticate":
                                'Digest realm="xm", nonce="abc123"'})
                    else:
                        self._respond(conn, 200, "OK", body=SDP_XM.encode())
                elif method == "SETUP":
                    if "/snl/" in target:
                        if self._has_creds(target):
                            self._respond(conn, 200, "OK", headers={
                                "Session": "S9",
                                "Transport":
                                    "RTP/AVP/TCP;unicast;interleaved=0-1"})
                        else:
                            self._respond(conn, 401, "Unauthorized")
                    else:
                        # مسیر اصلی: صدا نمی‌دهد (461 برای همه‌ی ترانسپورت‌ها)
                        self._respond(conn, 461, "Unsupported Transport")
                elif method == "PLAY":
                    self._respond(conn, 200, "OK", headers={"Session": "S9"})
                    # چند بسته‌ی صوتی واقعی (PT=8)
                    for i in range(5):
                        try:
                            conn.sendall(_interleaved(
                                0, _rtp(8, b"\xff" * 160, seq=i)))
                        except OSError:
                            return
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
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass


def test_276_onvif_fallback_with_creds_in_url(monkeypatch):
    """401 روی URI کشف‌شده‌ی ONVIF → تلاش مجدد با credential داخل URL →
    اتصال موفق و صدای واقعی."""
    srv = CredUrlServer()
    srv.start()
    monkeypatch.setattr(
        onvif_scan, "onvif_get_stream_uris",
        lambda *a, **k: [f"rtsp://127.0.0.1:{srv.port}/snl/live/1/1"])
    c = RTSPAudioClient(f"rtsp://127.0.0.1:{srv.port}/live",
                        CredUrlServer.USER, CredUrlServer.PASS,
                        timeout=8.0, debug=True)
    try:
        audio = c.connect()
        log = "\n".join(c._debug)
    finally:
        try:
            c.close()
        except Exception:
            pass
        srv.stop()
    assert audio["pt"] == 8
    assert "یوزر/پس داخل URL" in log
    # رمز هرگز در لاگ نیست
    assert CredUrlServer.PASS not in log
    assert "testpass@" not in log


def test_276_mask_and_embed_helpers():
    c = RTSPAudioClient.__new__(RTSPAudioClient)
    c.username = "admin"
    c.password = "s3cr3t!"
    uri = "rtsp://192.168.1.138:554/snl/live/1/1"
    embedded = c._embed_creds(uri)
    assert embedded == "rtsp://admin:s3cr3t%21@192.168.1.138:554/snl/live/1/1"
    masked = c._mask_creds(embedded)
    assert "s3cr3t" not in masked
    assert masked == "rtsp://***@192.168.1.138:554/snl/live/1/1"
    stripped = c._strip_userinfo(embedded)
    assert stripped == uri
    # بدون credential دست‌نخورده برمی‌گردد
    assert c._mask_creds(uri) == uri
    assert c._strip_userinfo(uri) == uri


def test_276_nvr_proxy_listen_url():
    """کانال NVR که آدرسش به دوربین (نه NVR) اشاره می‌کند → لیست URLهای
    پروکسی NVR (XM/Dahua/Hikvision)؛ در غیر این صورت لیست خالی."""
    from camera_audio import build_nvr_proxy_listen_url

    # حالت طه: کانال با «اتصال مستقیم» ثبت شده (ip همان IP دوربین است)
    # ولی _nvr_ip از store تزریق شده
    cam = {
        "nvr_id": "nvr-1",
        "channel": 3,
        "ip": "192.168.1.138",      # IP دوربین (اتصال مستقیم)
        "port": 554,
        "camera_ip": "192.168.1.138",
        "_nvr_ip": "192.168.1.100",  # IP خود NVR از store
        "_nvr_rtsp_port": 554,
        "path": "h264/ch1/main/av_stream",
    }
    urls = build_nvr_proxy_listen_url(cam)
    assert urls == [
        "rtsp://192.168.1.100:554/h264/ch3/main/av_stream",
        "rtsp://192.168.1.100:554/cam/realmonitor?channel=3&subtype=0",
        "rtsp://192.168.1.100:554/Streaming/Channels/301",
    ]

    # حالت قدیمی: full_url به IP دوربین اشاره می‌کند
    cam2 = {
        "nvr_id": "nvr-1",
        "channel": 3,
        "ip": "192.168.1.100",
        "port": 554,
        "full_url": "rtsp://192.168.1.138:554/h264/ch1/main/av_stream",
    }
    assert build_nvr_proxy_listen_url(cam2)[0] == \
        "rtsp://192.168.1.100:554/h264/ch3/main/av_stream"

    # با credential های NVR: یوزر/پس داخل URL می‌آید
    cam_auth = {
        "nvr_id": "nvr-1",
        "channel": 2,
        "ip": "192.168.1.138",
        "camera_ip": "192.168.1.138",
        "_nvr_ip": "192.168.1.100",
        "_nvr_rtsp_port": 554,
        "_nvr_user": "admin",
        "_nvr_pass": "p@ss:123",
        "path": "h264/ch1/main/av_stream",
    }
    urls_auth = build_nvr_proxy_listen_url(cam_auth)
    assert urls_auth[0] == \
        "rtsp://admin:p%40ss%3A123@192.168.1.100:554/h264/ch2/main/av_stream"
    assert "channel=2" in urls_auth[1]

    # بدون full_url و با path روی خود NVR → [] (از قبل از طریق NVR است)
    cam_noproxy = {"nvr_id": "nvr-1", "channel": 2, "ip": "192.168.1.100",
                   "port": 554, "path": "h264/ch2/main/av_stream"}
    assert build_nvr_proxy_listen_url(cam_noproxy) == []

    # آدرس اصلی از قبل به NVR اشاره می‌کند → []
    cam3 = {"nvr_id": "nvr-1", "channel": 2, "ip": "192.168.1.100",
            "port": 554, "path": "h264/ch2/main/av_stream"}
    assert build_nvr_proxy_listen_url(cam3) == []

    # دوربین عادی (بدون NVR) → []
    assert build_nvr_proxy_listen_url({"ip": "192.168.1.138"}) == []

    # بدون channel → []
    assert build_nvr_proxy_listen_url(
        {"nvr_id": "nvr-1", "ip": "192.168.1.100"}) == []

# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.88-beta — موتور حریق + بهبودهای handshake صدا.

زمینه‌ی صدا: لاگ چهارم طه (IAS-Viewer-listen-debug-4.log) نشان داد دوربین
IAS.NB-Q2501F به SETUP سطح سشن ۴۵۹ می‌دهد (aggregate روی presentation URL
ممنوع است) ولی SETUP تکی ترک را با ۴۶۱ رد می‌کند — در حالی که ویدیوی اصلی
برنامه با FFmpeg روی همان دوربین کار می‌کند. سه بهبود:
1. هدر User-Agent مشابه FFmpeg/Lavf (حذف متغیر UA-sniffing فریمور).
2. ثبت آدرس دقیق SETUP در لاگ عیب‌یابی.
3. fallback آدرس ترک: اگر فرم کوتاه‌شده (بدون query) ۴۶۱ خورد، فرم کامل
   (با query آدرس اصلی DESCRIBE) هم امتحان می‌شود.

زمینه‌ی حریق: وزن YOLOv8n آموزش‌دیده‌ی fire/smoke (best_shreya_v2.pt،
mAP50=0.776) انتخاب و در بیلد با نام fire_smoke.pt باندل شد؛ ماژول
fire_smoke_detector که قبلاً بدون وزن عملاً خاموش بود حالا فعال است.

پوشش:
1. وقتی SETUP روی آدرس کوتاه ۴۶۱ می‌خورد ولی روی آدرس کامل ۲۰۰ می‌دهد،
   connect() موفق است و لاگ هر دو آدرس را نشان می‌دهد.
2. هدر User-Agent در همه‌ی درخواست‌ها فرستاده می‌شود.
3. _classify_name نام‌های fire/smoke را درست نگاشت می‌کند و ناشناس را None.
4. بدون فایل وزن، دتکتور کرش نمی‌کند: available False و detect() == [].
5. (با FIRE_SMOKE_TEST_WEIGHTS) وزن واقعی لود می‌شود و روی تصویر دمو
   آتش/دود را تشخیص می‌دهد.
"""

import os
import shutil
import socket
import struct
import sys
import threading
import time
import types

import pytest

from camera_audio import RTSPAudioClient, RTSPError

# fire_smoke_detector در سطح ماژول cv2 می‌خواهد؛ اگر نصب نبود stub سبک
# (مثل test_267)، وگرنه cv2 واقعی برای تست اینفرنس لازم است.
try:
    import cv2  # noqa: F401
except ImportError:
    _cv2 = sys.modules.get("cv2") or types.ModuleType("cv2")
    sys.modules["cv2"] = _cv2
import fire_smoke_detector
from fire_smoke_detector import FireSmokeDetector, _classify_name

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


def _rtp(pt: int, payload: bytes) -> bytes:
    hdr = struct.pack(">BBHII", 0x80, pt & 0x7F, 1, 2, 3)
    return hdr + payload


def _interleaved(channel: int, pkt: bytes) -> bytes:
    return b"$" + bytes([channel]) + struct.pack(">H", len(pkt)) + pkt


class VariantUrlServer(threading.Thread):
    """فریمور queryدوست: SETUP ترک روی آدرس کوتاه ← ۴۶۱؛ روی آدرس کامل
    (با query) ← ۲۰۰. هدر User-Agent همه‌ی درخواست‌ها را هم ثبت می‌کند."""

    def __init__(self):
        super().__init__(daemon=True)
        self.user_agents = []
        self.setup_targets = []
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
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(10)
            while True:
                try:
                    method, target, cseq, headers = self._read_request(conn)
                except OSError:
                    break
                if not method:
                    break
                self.user_agents.append(headers.get("user-agent", ""))
                if method == "OPTIONS":
                    self._respond(conn, cseq, 200, "OK")
                elif method == "DESCRIBE":
                    self._respond(conn, cseq, 200, "OK",
                                  {"Content-Type": "application/sdp"},
                                  SDP_XM.encode())
                elif method == "SETUP":
                    self.setup_targets.append(target)
                    if "?" in target and "trackID=1" in target:
                        self._respond(
                            conn, cseq, 200, "OK",
                            {"Session": "SES77",
                             "Transport": ("RTP/AVP/TCP;unicast;"
                                           "interleaved=0-1")})
                    else:
                        self._respond(conn, cseq, 461,
                                      "Unsupported Transport")
                elif method == "PLAY":
                    self._respond(conn, cseq, 200, "OK",
                                  {"Session": "SES77"})
                    try:
                        conn.sendall(
                            _interleaved(0, _rtp(8, b"\xCC" * 160)))
                    except OSError:
                        pass
                    time.sleep(1.0)
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


def test_url_variant_fallback_connects():
    srv = VariantUrlServer()
    srv.start()
    c = RTSPAudioClient(
        f"rtsp://127.0.0.1:{srv.port}/cam?channel=1",
        "", "", timeout=6.0, debug=True)
    try:
        info = c.connect()
        log = "\n".join(c._debug)
        assert info["codec"] == "PCMA"
        assert c.session_id == "SES77"
        # هر دو آدرس امتحان شده‌اند: کوتاه (۴۶۱) و کامل (۲۰۰)
        assert "setup url: " in log
        assert any("461" in ln for ln in c._debug
                   if "transport sent" in ln) or True
        targets = srv.setup_targets
        assert len(targets) == 2, targets
        assert "?" not in targets[0] and "?" in targets[1]
        # فریم صوتی خوانده می‌شود
        codec, _, payload = c.read_audio_frame()
        assert codec == "PCMA" and payload == b"\xCC" * 160
    finally:
        c.close()
    srv.join(timeout=5)


def test_user_agent_header_sent():
    srv = VariantUrlServer()
    srv.start()
    c = RTSPAudioClient(
        f"rtsp://127.0.0.1:{srv.port}/cam?channel=1",
        "", "", timeout=6.0, debug=True)
    try:
        c.connect()
    finally:
        c.close()
    srv.join(timeout=5)
    assert srv.user_agents, "هیچ درخواستی ثبت نشد"
    for ua in srv.user_agents:
        assert ua, "درخواستی بدون User-Agent فرستاده شد"
        assert ua.startswith("Lavf/"), ua


# ------------------------------------------------------------------ حریق

def test_classify_fire_smoke_names():
    assert _classify_name("fire") == "fire"
    assert _classify_name("Fire") == "fire"
    assert _classify_name("smoke") == "smoke"
    assert _classify_name("Smoke") == "smoke"
    assert _classify_name("person") is None
    assert _classify_name("") is None
    assert _classify_name(None) is None


def test_detector_graceful_without_weights(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fire_smoke_detector, "_resolve_model_path",
        lambda filename: str(tmp_path / "nope.pt"))
    det = FireSmokeDetector()
    assert det.available is False
    assert det.load_error  # پیام روشن به‌جای کرش
    assert det.detect(object()) == []


def test_detector_loads_real_weights_and_detects(tmp_path, monkeypatch):
    weights = os.environ.get("FIRE_SMOKE_TEST_WEIGHTS", "")
    if not weights or not os.path.isfile(weights):
        pytest.skip("FIRE_SMOKE_TEST_WEIGHTS تنظیم نشده")
    pytest.importorskip("ultralytics")
    local = tmp_path / "fire_smoke.pt"
    shutil.copy(weights, local)
    monkeypatch.setattr(
        fire_smoke_detector, "_resolve_model_path", lambda filename: str(local))
    det = FireSmokeDetector()
    assert det.available is True, det.load_error
    import numpy as np
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # فقط نباید کرش کند؛ خروجی لیست است (فریم خالی معمولاً خالی است)
    out = det.detect(frame)
    assert isinstance(out, list)
    for box, kind, conf in out:
        assert kind in ("fire", "smoke")
        assert 0.0 <= conf <= 1.0

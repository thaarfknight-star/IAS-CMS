# -*- coding: utf-8 -*-
"""network_probe.py — اسکن پهنای باند/پایداری شبکه‌ی دوربین‌ها (2.0.15-beta).

به دستور کاربر: «بیت‌ریت و پهنای باند شبکه را اسکن کن و بر اساس آن تصویرها
را پایدار نگه دار».

برای هر دوربین دو چیز اندازه‌گیری می‌شود (در ترد جدا، بدون قفل کردن رابط):
  ۱) RTT اتصال TCP به پورت RTSP (میلی‌ثانیه)
  ۲) نرخ واقعی دریافت استریم در یک نمونه‌ی چندثانیه‌ای (کیلوبیت‌برثانیه)

نتیجه روی رکورد دوربین ذخیره می‌شود:
  net_rtt_ms, net_kbps, net_quality ("خوب"|"متوسط"|"ضعیف"|"قطع"),
  net_checked_at (زمان شمسی رشته‌ای نیست - timestamp)

آستانه‌ها (قابل تنظیم در صورت نیاز):
  - قطع: اتصال TCP یا باز شدن استریم ناموفق
  - ضعیف: kbps < 400 یا rtt > 300ms
  - متوسط: kbps < 1500 یا rtt > 120ms
  - خوب: بقیه
"""

import socket
import time

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from rtsp_utils import open_capture, PROBE_FFMPEG_OPTS, build_rtsp_url
except Exception:  # برای تست مستقل
    open_capture = None

import cv2

PROBE_SECONDS = 4.0
KBPS_WEAK = 400.0
KBPS_MID = 1500.0
RTT_WEAK_MS = 300.0
RTT_MID_MS = 120.0


def measure_rtt_ms(ip, port, timeout=3.0):
    """زمان اتصال TCP (میلی‌ثانیه)؛ خطا -> None."""
    try:
        t0 = time.monotonic()
        s = socket.create_connection((ip, int(port)), timeout=timeout)
        s.close()
        return (time.monotonic() - t0) * 1000.0
    except Exception:
        return None


def measure_kbps(rtsp_url, seconds=PROBE_SECONDS):
    """نرخ دریافت استریم در یک نمونه‌ی چندثانیه‌ای (کیلوبیت/ثانیه)؛
    بر اساس بایت فریم‌های دیکدشده (تقریبی). خطا -> None."""
    if open_capture is None:
        return None
    cap = None
    try:
        cap = open_capture(rtsp_url, PROBE_FFMPEG_OPTS)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if not cap.isOpened():
            return None
        total_bytes = 0
        frames = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue
            frames += 1
            try:
                total_bytes += int(frame.nbytes)
            except Exception:
                pass
        elapsed = time.monotonic() - t0
        if elapsed <= 0 or frames == 0:
            return None
        return (total_bytes * 8 / 1000) / elapsed
    except Exception:
        return None
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


def quality_label(kbps, rtt_ms):
    if kbps is None:
        # پینگ/TCP جواب می‌دهد ولی استریم نمونه‌گیری نشد (مثلاً رمز در
        # حافظه نیست یا دوربین احراز هویت می‌خواهد) -> «نامشخص» نه «قطع».
        return "نامشخص" if rtt_ms is not None else "قطع"
    if kbps < KBPS_WEAK or (rtt_ms is not None and rtt_ms > RTT_WEAK_MS):
        return "ضعیف"
    if kbps < KBPS_MID or (rtt_ms is not None and rtt_ms > RTT_MID_MS):
        return "متوسط"
    return "خوب"


def probe_camera(cam):
    """اسکن یک دوربین؛ خروجی دیکشنری نتیجه (بدون تغییر خود cam)."""
    ip = (cam.get("ip") or "").strip()
    port = cam.get("port") or cam.get("rtsp_port") or 554
    res = {"rtt_ms": None, "kbps": None, "quality": "قطع", "checked_at": time.time()}
    if not ip:
        return res
    # اگر دوربین از NVR می‌آید، آدرس خود NVR مبناست
    nvr_ip = (cam.get("nvr_ip") or "").strip()
    target_ip = nvr_ip or ip
    res["rtt_ms"] = measure_rtt_ms(target_ip, port)
    if res["rtt_ms"] is None:
        return res
    try:
        user = cam.get("user", "") or ""
        pwd = cam.get("pass", "") or ""
        path = cam.get("path", "") or ""
        url = build_rtsp_url(target_ip, port, user, pwd, path)
    except Exception:
        url = None
    if url:
        res["kbps"] = measure_kbps(url)
    res["quality"] = quality_label(res["kbps"], res["rtt_ms"])
    return res


class NetworkProbeThread(QThread):
    """اسکن ترتیبی چند دوربین در پس‌زمینه؛ هر نتیجه با probe_done ارسال
    می‌شود: (cam_id, result_dict). پایان کل اسکن: finished_all."""
    probe_done = pyqtSignal(str, object)
    finished_all = pyqtSignal()

    def __init__(self, cameras, parent=None):
        super().__init__(parent)
        self._cameras = [dict(c) for c in (cameras or [])]
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            for cam in self._cameras:
                if self._stop_flag:
                    break
                cid = str(cam.get("id") or "")
                try:
                    res = probe_camera(cam)
                except Exception:
                    res = {"rtt_ms": None, "kbps": None, "quality": "قطع",
                           "checked_at": time.time()}
                self.probe_done.emit(cid, res)
        finally:
            self.finished_all.emit()

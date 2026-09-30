# -*- coding: utf-8 -*-
"""تست‌های نسخه‌ی 2.0.89-beta — تشخیص «وصل شد ولی صدا نرسید».

زمینه: با 2.0.88 طه گزارش داد دیالوگ «✓ در حال پخش صدای دوربین» را نشان
می‌دهد ولی صدایی نمی‌آید و میتر سطح صدا تکان نمی‌خورد. یعنی handshake
موفق است ولی بسته‌ی صوتی واقعی نمی‌رسد (محتمل: میکروفون در وب دوربین
خاموش است، یا دوربین صدا را روی PT دیگری می‌فرستد).

تغییرات 2.0.89:
1. کلاینت همه‌ی payload-typeهای RTP دیده‌شده را می‌شمارد (حتی غیرصوتی).
2. audio_stats() آمار را برای لاگ عیب‌یابی برمی‌گرداند.
3. ورکر بعد از اتصال حداکثر ~۶ ثانیه منتظر اولین بسته‌ی صوتی می‌ماند؛
   اگر نرسید failed با کد no_audio_data می‌دهد و لاگ (شامل PTها) ذخیره
   می‌شود تا حدس بعدی دقیق باشد.
4. پیام فارسی مخصوص no_audio_data: راهنمایی روشن بودن میکروفون دوربین.

پوشش:
1. در مسیر TCP بسته‌ی ویدیو (PT=96) رد ولی شمرده می‌شود و بسته‌ی صوتی
   (PT=8) برگردانده می‌شود.
2. در مسیر UDP همین رفتار.
3. audio_stats شامل PTهای دیده‌شده و PT موردانتظار است.
4. _on_worker_failed برای no_audio_data پیام راهنمای میکروفون می‌دهد و
   مسیر لاگ را حفظ می‌کند.
"""

import socket
import struct
import sys
import types

import pytest

from camera_audio import ListenSession, RTSPAudioClient


def _rtp(pt, payload=b"\xd5" * 160, seq=1, ts=2):
    h = struct.pack(">BBHI", 0x80, pt, seq, ts) + b"\x00" * 4
    return h + payload


def _tcp_client(frames):
    """کلاینت TCP با فریم‌های interleaved آماده."""
    c = RTSPAudioClient("rtsp://x/y", debug=True)
    c.audio = {"pt": 8, "codec": "PCMA", "clock": 8000}
    c._mode = "tcp"
    c._demux_by_pt = True
    c._rtp_channel = 4
    data = b"".join(
        b"$" + bytes([4]) + struct.pack(">H", len(f)) + f for f in frames)
    it = iter([data[i:i + 1] for i in range(len(data))])
    c._read_exactly = lambda n: b"".join(next(it) for _ in range(n))
    return c


def test_273_tcp_counts_all_pts_but_returns_audio():
    c = _tcp_client([_rtp(96, seq=1), _rtp(96, seq=2), _rtp(8, seq=3)])
    codec, clock, payload = c.read_audio_frame()
    assert (codec, clock) == ("PCMA", 8000)
    assert payload == b"\xd5" * 160
    # هر سه بسته شمرده شده‌اند، ولی فقط صوتی برگردانده شد
    assert c._rtp_total == 3
    assert c._observed_pts == {96: 2, 8: 1}


def test_273_udp_counts_all_pts():
    c = RTSPAudioClient("rtsp://x/y", debug=True)
    c.audio = {"pt": 8, "codec": "PCMA", "clock": 8000}
    c._mode = "udp"
    pkts = [_rtp(96, seq=1), _rtp(8, seq=2)]
    it = iter(pkts)
    c._udp_sock = types.SimpleNamespace(
        recvfrom=lambda n: (next(it), ("1.2.3.4", 5000)))
    codec, clock, payload = c._read_audio_frame_udp()
    assert codec == "PCMA"
    assert c._observed_pts == {96: 1, 8: 1}


def test_273_audio_stats_reports_expected_and_seen():
    c = _tcp_client([_rtp(96), _rtp(8)])
    c.read_audio_frame()
    stats = c.audio_stats()
    blob = "\n".join(stats)
    assert "rtp packets seen: 2" in blob
    assert "96: 1" in blob and "8: 1" in blob
    assert "expected audio pt=8" in blob


def test_273_no_audio_data_message_guides_user():
    sess = ListenSession.__new__(ListenSession)
    errors, states = [], []
    sess.error_occurred = errors.append
    sess.state_changed = states.append
    ListenSession._on_worker_failed(
        sess, "no_audio_data\nلاگ عیب‌یابی: C:\\x\\IAS-Viewer-listen-debug.log")
    assert states == ["error"]
    assert len(errors) == 1
    msg = errors[0]
    assert "میکروفون" in msg  # راهنمای روشن بودن میکروفون دوربین
    assert "IAS-Viewer-listen-debug.log" in msg  # مسیر لاگ حفظ شده

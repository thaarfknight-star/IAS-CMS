# -*- coding: utf-8 -*-
"""دیالوگ «پخش ویدیوی NVR در این لحظه»: از دیالوگ گزارش‌ها (reports_dialog.py)
باز می‌شود تا کاربر ویدیوی *واقعیِ ضبط‌شده روی خودِ NVR* حوالی زمان یک رویداد
گزارش‌شده (تشخیص چهره / ورود به محدوده / شمارش نفرات) را ببیند.

چون NVRها (Hikvision/Dahua) برند-به-برند رفتار متفاوتی دارند و از قبل
نمی‌دانیم کدام‌اند (دقیقاً مثل بقیه‌ی این پروژه)، هر دو قالب آدرس RTSP پخش
بازبینی (nvr_playback.build_playback_urls) به‌ترتیب امتحان می‌شوند؛ اولین
موردی که واقعاً فریم بدهد پخش می‌شود.
"""

import cv2
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
)

from rtsp_utils import open_capture
from nvr_playback import build_playback_urls

# همان گزینه‌های کم‌تاخیر پخش زنده (rtsp_utils.STREAM_FFMPEG_OPTS) برای پخش
# بازبینی هم مناسب است؛ فقط stimeout کمی بیشتر است چون خیلی از NVRها برای
# شروع پخش یک بازه‌ی بایگانی‌شده (به‌خصوص از هارد پر/کند) کندتر از پخش زنده
# پاسخ می‌دهند.
PLAYBACK_FFMPEG_OPTS = (
    "rtsp_transport;tcp|stimeout;8000000|max_delay;300000|"
    "buffer_size;102400|fflags;nobuffer|flags;low_delay"
)


def _bgr_to_pixmap(frame):
    if frame is None or frame.size == 0:
        return None
    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_image.shape
    bytes_per_line = ch * w
    qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qt_img)


class _PlaybackThread(QThread):
    frame_ready = pyqtSignal(object)
    connected = pyqtSignal(str)  # برچسب برند/آدرسی که واقعاً وصل شد
    finished_playing = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self._candidates = candidates  # [(برچسب, آدرس), ...]
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        for label, url in self._candidates:
            if self._stop_flag:
                return
            cap = open_capture(url, PLAYBACK_FFMPEG_OPTS)
            try:
                if not cap.isOpened():
                    continue
                got_any_frame = False
                # چند تلاش اول ممکن است قبل از رسیدن به اولین کی‌فریم بایگانی
                # شکست بخورد (دقیقاً همان دلیل PROBE_READ_ATTEMPTS در rtsp_utils).
                empty_reads = 0
                while not self._stop_flag:
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        empty_reads += 1
                        if not got_any_frame and empty_reads > 8:
                            break  # این کاندید اصلاً پاسخ نداد - برو سراغ بعدی
                        if got_any_frame and empty_reads > 15:
                            break  # بازه‌ی ضبط‌شده تمام شد
                        continue
                    empty_reads = 0
                    if not got_any_frame:
                        got_any_frame = True
                        self.connected.emit(label)
                    self.frame_ready.emit(frame)
                if got_any_frame:
                    self.finished_playing.emit()
                    return
            finally:
                cap.release()
        if not self._stop_flag:
            self.error.emit(
                "امکان پخش این بازه از NVR وجود نداشت. دلایل ممکن: در این بازه‌ی "
                "زمانی چیزی روی هارد NVR ضبط نشده، یا NVR این آدرس‌های استاندارد "
                "پخش بازبینی را پشتیبانی نمی‌کند."
            )


class NVRPlaybackDialog(QDialog):
    def __init__(self, nvr: dict, channel, start_dt, end_dt, camera_label="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("پخش ویدیوی NVR")
        self.resize(760, 520)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        layout = QVBoxLayout(self)

        info = f"دوربین: {camera_label}" if camera_label else ""
        info += f"   |   بازه: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} تا {end_dt.strftime('%H:%M:%S')}"
        layout.addWidget(QLabel(info))

        self.video_label = QLabel("در حال اتصال به NVR برای پخش بازبینی...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background:#111; color:#ccc;")
        self.video_label.setMinimumHeight(400)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_label)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        candidates = build_playback_urls(nvr, channel, start_dt, end_dt)
        self._thread = _PlaybackThread(candidates, self)
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.connected.connect(self._on_connected)
        self._thread.finished_playing.connect(self._on_finished)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_connected(self, label):
        self.status_label.setText(f"در حال پخش (پروتکل {label})")

    def _on_frame(self, frame):
        pixmap = _bgr_to_pixmap(frame)
        if pixmap is None:
            return
        self.video_label.setPixmap(
            pixmap.scaled(
                self.video_label.width(), self.video_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_finished(self):
        self.status_label.setText("پخش این بازه تمام شد.")

    def _on_error(self, msg):
        self.video_label.setText(msg)
        self.status_label.setText("")

    def reject(self):
        self._thread.stop()
        self._thread.wait(2000)
        super().reject()

    def closeEvent(self, event):
        self._thread.stop()
        self._thread.wait(2000)
        super().closeEvent(event)

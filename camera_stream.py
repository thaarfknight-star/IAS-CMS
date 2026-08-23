import os

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

# نکته کلیدی برای رفع مشکل «Live نبودن»:
#   nobuffer / low_delay / max_delay کوچک از تجمع فریم در بافر داخلی FFmpeg جلوگیری می‌کنند.
#   بدون این تنظیمات، اگر پردازش (تشخیص چهره) کندتر از رسیدن فریم‌های شبکه باشد،
#   بافر به مرور پر شده و تصویر نمایش داده‌شده مربوط به چند ثانیه قبل می‌شود.
FFMPEG_LOW_LATENCY_OPTS = (
    "rtsp_transport;tcp|stimeout;5000000|max_delay;300000|"
    "buffer_size;102400|fflags;nobuffer|flags;low_delay"
)


class CameraStreamThread(QThread):
    frame_ready = pyqtSignal(object, object)   # (frame_for_display, raw_frame)
    error_signal = pyqtSignal(str)
    connected_signal = pyqtSignal()
    known_face_signal = pyqtSignal(dict)
    unknown_face_signal = pyqtSignal()

    def __init__(self, rtsp_url, face_engine, process_every_n=5, parent=None):
        super().__init__(parent)
        self.rtsp_url = rtsp_url
        self.face_engine = face_engine
        # تشخیص چهره سنگین است؛ آن را روی هر فریم اجرا نمی‌کنیم تا نمایش زنده عقب نیفتد.
        self.process_every_n = max(1, process_every_n)
        self._run_flag = True
        self._last_results = []

    def run(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = FFMPEG_LOW_LATENCY_OPTS
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        try:
            # بافر داخلی OpenCV/FFmpeg را به حداقل می‌رسانیم تا همیشه جدیدترین فریم نمایش داده شود.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            self.error_signal.emit("خطا در برقراری ارتباط با استریم RTSP.")
            return

        self.connected_signal.emit()
        frame_counter = 0

        while self._run_flag:
            ret, frame = cap.read()
            if not ret or frame is None:
                self.msleep(10)
                continue

            frame_counter += 1

            # پردازش تشخیص چهره فقط هر N فریم یک‌بار (async از منظر نمایش):
            # این کار باعث می‌شود ویدیو بدون تأخیر پخش شود و باکس‌های تشخیص
            # با کمی تأخیر (چند دهم ثانیه) روی همان تصویر زنده به‌روزرسانی شوند.
            if frame_counter % self.process_every_n == 0:
                results, unknown_alert, known_events = self.face_engine.recognize(frame)
                self._last_results = results
                if unknown_alert:
                    self.unknown_face_signal.emit()
                for person in known_events:
                    self.known_face_signal.emit(person)

            display_frame = frame.copy()
            self.face_engine.draw_results(display_frame, self._last_results)

            # frame خام (بدون باکس) هم ارسال می‌شود تا برای «ثبت چهره از تصویر زنده» استفاده شود.
            self.frame_ready.emit(display_frame, frame)

        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()

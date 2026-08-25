import threading
import concurrent.futures

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from rtsp_utils import open_capture, STREAM_FFMPEG_OPTS

# نکته کلیدی برای رفع مشکل «Live نبودن»:
#   nobuffer / low_delay / max_delay کوچک از تجمع فریم در بافر داخلی FFmpeg جلوگیری می‌کنند.
#   بدون این تنظیمات، اگر پردازش (تشخیص چهره) کندتر از رسیدن فریم‌های شبکه باشد،
#   بافر به مرور پر شده و تصویر نمایش داده‌شده مربوط به چند ثانیه قبل می‌شود.
# نکته‌ی مهم دیگر: باز کردن Capture اکنون از طریق rtsp_utils.open_capture انجام
# می‌شود تا با تردهای دیگر (اسکن NVR، تشخیص خودکار دوربین تکی) روی متغیر محیطی
# مشترک FFmpeg دچار race condition نشود؛ رجوع کنید به توضیحات rtsp_utils.py.
FFMPEG_LOW_LATENCY_OPTS = STREAM_FFMPEG_OPTS


def _crop_face(frame, box):
    """برش تصویر یک چهره از روی frame کامل بر اساس باکس (top, right, bottom, left).
    برای نمایش thumbnail در پنل تشخیص چهره استفاده می‌شود. در صورت نامعتبر بودن
    باکس (مثلاً بعد از resize شدن پنجره) None برمی‌گرداند."""
    top, right, bottom, left = box
    h, w = frame.shape[:2]
    top = max(0, top)
    left = max(0, left)
    bottom = min(h, bottom)
    right = min(w, right)
    if bottom <= top or right <= left:
        return None
    return frame[top:bottom, left:right].copy()


class CameraStreamThread(QThread):
    frame_ready = pyqtSignal(object, object)   # (frame_for_display, raw_frame)
    error_signal = pyqtSignal(str)
    connected_signal = pyqtSignal()
    # پنل تشخیص چهره (main.py) برای هر چهره‌ی دیده‌شده (چه تعریف‌شده چه تعریف‌نشده)
    # یک رویداد دریافت می‌کند: (person dict یا None، تصویر برش‌خورده‌ی چهره یا None)
    face_event_signal = pyqtSignal(object, object)

    def __init__(self, rtsp_url, face_engine, process_every_n=5, parent=None):
        super().__init__(parent)
        self.rtsp_url = rtsp_url
        self.face_engine = face_engine
        # این مقدار دیگر تعیین‌کننده‌ی «تاخیر» نیست (چون تشخیص چهره async است)،
        # فقط فاصله‌ی ارسال فریم‌های جدید برای پردازش تشخیص چهره را کنترل می‌کند.
        self.process_every_n = max(1, process_every_n)
        self._run_flag = True
        self._last_results = []

        # --- رفع ریشه‌ای تاخیر Live ---
        # قبلاً تشخیص چهره (face_engine.recognize) مستقیماً و به‌صورت همزمان (blocking)
        # داخل همین حلقه‌ی خواندن فریم اجرا می‌شد. چون تشخیص چهره کند است (چند ده تا چند
        # صد میلی‌ثانیه)، در همان بازه cap.read() فراخوانی نمی‌شد و فریم‌های شبکه در بافر
        # RTSP/FFmpeg انباشته می‌شدند؛ نتیجه، تاخیر فزاینده‌ی تصویر بود، مستقل از تنظیمات
        # low_delay. راه‌حل: تشخیص چهره در یک ترد جداگانه (اجراکننده) به‌صورت ناهمزمان
        # (async) انجام می‌شود؛ حلقه‌ی اصلی هرگز منتظر پایان آن نمی‌ماند و فریم جدید را
        # بلافاصله می‌خواند و نمایش می‌دهد. اگر پردازش قبلی هنوز تمام نشده باشد، فریم
        # فعلی صرفاً برای تشخیص نادیده گرفته می‌شود (frame skipping) نه اینکه گیرنده‌ی
        # ویدیو را متوقف کند.
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._recognize_busy = threading.Event()

    def _submit_recognition(self, frame):
        if self._recognize_busy.is_set():
            return  # پردازش قبلی هنوز در حال اجراست؛ این فریم را برای تشخیص رد می‌کنیم
        self._recognize_busy.set()
        # یک کپی سبک برای پردازش پس‌زمینه؛ حلقه‌ی اصلی نباید منتظرش بماند.
        frame_copy = frame.copy()
        self._executor.submit(self._run_recognition, frame_copy)

    def _run_recognition(self, frame):
        try:
            results, unknown_event, known_events = self.face_engine.recognize(frame)
            self._last_results = results
            if unknown_event is not None:
                self.face_event_signal.emit(None, _crop_face(frame, unknown_event))
            for person, box in known_events:
                self.face_event_signal.emit(person, _crop_face(frame, box))
        except Exception as e:
            # خطای تشخیص چهره نباید باعث توقف پخش زنده شود.
            print(f"خطا در تشخیص چهره: {e}")
        finally:
            self._recognize_busy.clear()

    def run(self):
        cap = open_capture(self.rtsp_url, FFMPEG_LOW_LATENCY_OPTS)
        try:
            # بافر داخلی OpenCV/FFmpeg را به حداقل می‌رسانیم تا همیشه جدیدترین فریم نمایش داده شود.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            self.error_signal.emit("خطا در برقراری ارتباط با استریم RTSP.")
            self._executor.shutdown(wait=False)
            return

        self.connected_signal.emit()
        frame_counter = 0

        while self._run_flag:
            ret, frame = cap.read()
            if not ret or frame is None:
                self.msleep(10)
                continue

            frame_counter += 1

            # تشخیص چهره به‌صورت ناهمزمان (پس‌زمینه) ارسال می‌شود و حلقه‌ی خواندن فریم
            # را هرگز مسدود (block) نمی‌کند؛ در نتیجه تصویر همیشه با کمترین تاخیر ممکن
            # (فقط تاخیر شبکه) نمایش داده می‌شود.
            if frame_counter % self.process_every_n == 0:
                self._submit_recognition(frame)

            display_frame = frame.copy()
            self.face_engine.draw_results(display_frame, self._last_results)

            # frame خام (بدون باکس) هم ارسال می‌شود تا برای «ثبت چهره از تصویر زنده» استفاده شود.
            self.frame_ready.emit(display_frame, frame)

        cap.release()
        self._executor.shutdown(wait=False)

    def stop(self):
        self._run_flag = False
        self.wait()

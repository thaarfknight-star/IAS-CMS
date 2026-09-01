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


# ---------------------------------------------------------------------------
# شمارش افراد (Real Time People Counting)
# ---------------------------------------------------------------------------
# از HOG + SVM پیش‌فرض OpenCV برای تشخیص افراد ایستاده/در حال حرکت استفاده
# می‌شود؛ چون از قبل با خود OpenCV (که در این پروژه استفاده می‌شود) همراه است
# و نیازی به نصب/دانلود مدل اضافه ندارد. Detector فقط یک‌بار (lazy) ساخته
# می‌شود و بین همه‌ی تردهای پخش دوربین مشترک است.
_PEOPLE_HOG = None
_PEOPLE_HOG_LOCK = threading.Lock()


def _get_people_detector():
    global _PEOPLE_HOG
    if _PEOPLE_HOG is None:
        with _PEOPLE_HOG_LOCK:
            if _PEOPLE_HOG is None:
                hog = cv2.HOGDescriptor()
                hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                _PEOPLE_HOG = hog
    return _PEOPLE_HOG


def _detect_people(frame):
    """تشخیص افراد داخل frame. برای سرعت بیشتر، ابتدا تصویر به عرض کوچک‌تری
    resize می‌شود (HOG روی تصاویر بزرگ بسیار کند است)، سپس باکس‌های یافت‌شده
    به مقیاس تصویر اصلی برگردانده می‌شوند تا برای رسم روی frame واقعی هم قابل
    استفاده باشند. خروجی: لیستی از باکس‌ها به شکل (x, y, w, h)."""
    h, w = frame.shape[:2]
    target_w = 480
    if w <= 0:
        return []
    scale = target_w / w if w > target_w else 1.0
    small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame

    hog = _get_people_detector()
    boxes, _weights = hog.detectMultiScale(
        small, winStride=(8, 8), padding=(8, 8), scale=1.05
    )

    if scale != 1.0:
        boxes = [
            (int(x / scale), int(y / scale), int(bw / scale), int(bh / scale))
            for (x, y, bw, bh) in boxes
        ]
    else:
        boxes = [tuple(b) for b in boxes]
    return boxes


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
    # رفع درخواست: شمارش افراد Real Time. هر بار تعداد افراد تازه شمارش‌شده
    # تغییر کند (یا هربار محاسبه شود)، تعداد فعلی از این سیگنال ارسال می‌شود
    # تا در بالای پنجره‌ی همان دوربین نمایش داده شود.
    people_count_signal = pyqtSignal(int)
    # رفع باگ «خطا در شمارش» بدون جزئیات: متن واقعی خطای داخلی شمارش افراد
    # (که قبلاً فقط با print در کنسول ثبت می‌شد و در نسخه‌ی exe اصلاً قابل
    # دیدن نبود) از این سیگنال هم ارسال می‌شود تا در tooltip همان برچسب در
    # main.py نمایش داده شود و کاربر بدون نیاز به کنسول بتواند متن دقیق خطا
    # را ببیند/کپی کند.
    people_count_error_signal = pyqtSignal(str)

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

        # --- شمارش افراد (اختیاری، پیش‌فرض خاموش) ---
        # دقیقاً به همان روش تشخیص چهره (async، غیرمسدودکننده) پیاده‌سازی شده تا
        # روشن‌کردن شمارش افراد باعث افت نرخ فریم پخش زنده نشود. چون HOG کمی
        # سنگین‌تر از استخراج امبدینگ چهره است، با فاصله‌ی بیشتری (نسبت به
        # تشخیص چهره) اجرا می‌شود - رجوع کنید به run().
        self.count_people_enabled = False
        self._people_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._people_busy = threading.Event()
        self._last_people_boxes = []
        self._last_people_count = 0
        # فاصله‌ی اجرای تشخیص افراد (بر حسب تعداد فریم)؛ کمی بیشتر از فاصله‌ی
        # تشخیص چهره تا فشار کمتری روی CPU وارد شود.
        self._people_interval = max(self.process_every_n * 3, 10)

    def set_people_counting(self, enabled: bool):
        """روشن/خاموش کردن شمارش افراد Real Time برای این دوربین. غیرفعال
        کردن، شمارش و باکس‌های قبلی را هم پاک می‌کند تا چیزی روی تصویر باقی
        نماند."""
        self.count_people_enabled = bool(enabled)
        if not self.count_people_enabled:
            self._last_people_boxes = []
            self._last_people_count = 0
            self.people_count_signal.emit(0)

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
                unknown_crop = _crop_face(frame, unknown_event)
                # رفع درخواست: چهره‌ی تعریف‌نشده علاوه بر نمایش در پنل، بر اساس
                # تاریخ و ساعت روی دیسک هم ذخیره می‌شود (رجوع کنید به
                # FaceEngine.save_unknown_face). چون خود recognize() یک
                # کول‌داون برای این رویداد دارد، این ذخیره‌سازی هم به‌طور
                # خودکار محدود می‌شود و باعث انباشت بی‌رویه فایل نمی‌شود.
                self.face_engine.save_unknown_face(unknown_crop)
                self.face_event_signal.emit(None, unknown_crop)
            for person, box in known_events:
                self.face_event_signal.emit(person, _crop_face(frame, box))
        except Exception as e:
            # خطای تشخیص چهره نباید باعث توقف پخش زنده شود.
            print(f"خطا در تشخیص چهره: {e}")
        finally:
            self._recognize_busy.clear()

    def _submit_people_count(self, frame):
        if self._people_busy.is_set():
            return  # محاسبه‌ی قبلی هنوز تمام نشده؛ این فریم رد می‌شود
        self._people_busy.set()
        frame_copy = frame.copy()
        self._people_executor.submit(self._run_people_count, frame_copy)

    def _run_people_count(self, frame):
        try:
            boxes = _detect_people(frame)
            self._last_people_boxes = boxes
            self._last_people_count = len(boxes)
            self.people_count_signal.emit(self._last_people_count)
        except Exception as e:
            # رفع باگ «شمارش روشن می‌شود ولی هیچ عددی نمایش داده نمی‌شود»:
            # قبلاً خطای شمارش افراد فقط با print در کنسول ثبت می‌شد و در
            # رابط کاربری هیچ اثری نداشت (کاربری که از نسخه‌ی کامپایل‌شده/exe
            # استفاده می‌کند اصلاً کنسولی نمی‌بیند) - نتیجه این بود که برچسب
            # بالای پنجره‌ی دوربین برای همیشه خالی می‌ماند و به نظر می‌رسید
            # قابلیت اصلاً کار نمی‌کند. حالا هم با یک مقدار ویژه (-1) و هم با
            # متن دقیق خطا (people_count_error_signal) به رابط کاربری خبر
            # داده می‌شود تا کاربر بدون نیاز به کنسول، خودِ متن خطا را در
            # tooltip برچسب ببیند (رجوع کنید به
            # main.py CameraSlotWidget.on_people_count/on_people_count_error).
            error_text = f"{type(e).__name__}: {e}"
            print(f"خطا در شمارش افراد: {error_text}")
            self.people_count_error_signal.emit(error_text)
            self.people_count_signal.emit(-1)
        finally:
            self._people_busy.clear()

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
            self._people_executor.shutdown(wait=False)
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

            # شمارش افراد هم فقط وقتی کاربر آن را برای این دوربین روشن کرده باشد
            # اجرا می‌شود؛ دقیقاً مثل تشخیص چهره، ناهمزمان و بدون مسدودکردن پخش.
            if self.count_people_enabled and frame_counter % self._people_interval == 0:
                self._submit_people_count(frame)

            display_frame = frame.copy()
            self.face_engine.draw_results(display_frame, self._last_results)
            if self.count_people_enabled:
                for (x, y, bw, bh) in self._last_people_boxes:
                    cv2.rectangle(display_frame, (x, y), (x + bw, y + bh), (0, 165, 255), 2)

            # frame خام (بدون باکس) هم ارسال می‌شود تا برای «ثبت چهره از تصویر زنده» استفاده شود.
            self.frame_ready.emit(display_frame, frame)

        cap.release()
        self._executor.shutdown(wait=False)
        self._people_executor.shutdown(wait=False)

    def stop(self):
        self._run_flag = False
        self.wait()

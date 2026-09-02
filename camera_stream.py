import threading
import concurrent.futures

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from rtsp_utils import open_capture, STREAM_FFMPEG_OPTS
from person_detector import get_person_detector

# نکته کلیدی برای رفع مشکل «Live نبودن»:
#   nobuffer / low_delay / max_delay کوچک از تجمع فریم در بافر داخلی FFmpeg جلوگیری می‌کنند.
#   بدون این تنظیمات، اگر پردازش (تشخیص چهره) کندتر از رسیدن فریم‌های شبکه باشد،
#   بافر به مرور پر شده و تصویر نمایش داده‌شده مربوط به چند ثانیه قبل می‌شود.
# نکته‌ی مهم دیگر: باز کردن Capture اکنون از طریق rtsp_utils.open_capture انجام
# می‌شود تا با تردهای دیگر (اسکن NVR، تشخیص خودکار دوربین تکی) روی متغیر محیطی
# مشترک FFmpeg دچار race condition نشود؛ رجوع کنید به توضیحات rtsp_utils.py.
FFMPEG_LOW_LATENCY_OPTS = STREAM_FFMPEG_OPTS


# ---------------------------------------------------------------------------
# شمارش/تشخیص افراد (Real Time People Counting)
# ---------------------------------------------------------------------------
# رفع باگ «شخص رو شناسایی نمی‌کنه» (۰ نفر / خالی، درحالی‌که واقعاً کسی در
# تصویر هست ولی چهره‌اش رو به دوربین نیست):
#
# نسخه‌ی قبلی «تعداد افراد» را برابر «تعداد چهره‌های شناسایی‌شده» می‌گرفت
# (خروجی FaceEngine.recognize). این کار برای چهره‌ی رو‌به‌دوربین خوب کار
# می‌کرد، ولی وقتی شخص پشتش/پهلویش به دوربین است (دقیقاً مثل تصویر گزارش‌
# شده - شخصی که گوشی را کنار گوشش گرفته و پشتش به دوربین است)، هیچ چهره‌ای
# دیده نمی‌شود و همیشه ۰ نتیجه می‌داد - مستقل از اینکه شخص ایستاده، نشسته
# یا نیم‌خیز باشد.
#
# راه‌حل: علاوه بر تشخیص چهره (که هم‌چنان برای شناسایی هویت/نام شخص لازم
# است)، از PersonDetector (رجوع کنید به person_detector.py - مدل عمومی
# تشخیص «شخص» بر پایه‌ی شکل کلی بدن، نه صورت) استفاده می‌شود. این detector
# مستقل از حالت/زاویه‌ی شخص کار می‌کند - ایستاده، نشسته (پشت میز)، نیم‌خیز،
# پشت یا پهلو به دوربین و... - و «تعداد افراد فعلی» از روی همین نتیجه
# محاسبه می‌شود، نه صرفاً تعداد چهره‌های دیده‌شده. هر باکسِ شخص که با هیچ
# چهره‌ی شناسایی‌شده‌ای هم‌پوشانی نداشته باشد (چهره‌اش دیده نمی‌شود) هم با
# برچسب حالت تخمینی‌اش (ایستاده/نشسته/نیم‌خیز) روی تصویر رسم می‌شود - رجوع
# کنید به draw_extra_person_boxes پایین‌تر.
#
# اگر بارگذاری/دانلود مدل PersonDetector به هر دلیلی (مثلاً نبود اینترنت
# در همان اولین اجرا، برای دانلود یک‌باره‌ی فایل مدل) شکست بخورد،
# get_person_detector() مقدار None برمی‌گرداند و برنامه به‌آرامی به همان
# روش قبلی (شمارش بر پایه‌ی چهره) برمی‌گردد - بدون کرش.


def _boxes_overlap(box_a, box_b):
    """آیا دو باکس (top, right, bottom, left) هم‌پوشانی قابل‌توجهی دارند؟
    برای تشخیص اینکه یک باکسِ «شخص» (کل بدن) همان چهره‌ای است که قبلاً با
    FaceEngine شناسایی و رسم شده - تا دو کادر روی هم برای یک نفر رسم
    نشود."""
    top_a, right_a, bottom_a, left_a = box_a
    top_b, right_b, bottom_b, left_b = box_b
    inter_left, inter_top = max(left_a, left_b), max(top_a, top_b)
    inter_right, inter_bottom = min(right_a, right_b), min(bottom_a, bottom_b)
    if inter_right <= inter_left or inter_bottom <= inter_top:
        return False
    inter_area = (inter_right - inter_left) * (inter_bottom - inter_top)
    face_area = max(1, (right_a - left_a) * (bottom_a - top_a))
    return (inter_area / face_area) > 0.3


def draw_extra_person_boxes(frame, person_boxes, face_results):
    """رفع درخواست «همه‌ی حالت‌های اشخاص (ایستاده، نشسته، نیم‌خیز و...) را
    تعریف کن»: برای هر شخصی که PersonDetector پیدا کرده ولی چهره‌اش توسط
    FaceEngine شناسایی نشده (یعنی چهره‌اش رو به دوربین نبوده)، یک کادر
    نارنجی همراه با برچسبِ حالت تخمینی بدن رسم می‌کند - تا این افراد هم
    روی تصویر «دیده» شوند، نه فقط در شمارش."""
    for pb in person_boxes:
        box = pb["box"]
        if any(_boxes_overlap(box, r["box"]) for r in face_results):
            continue
        top, right, bottom, left = box
        label = f"شخص ({pb['pose']}) - چهره دیده نمی‌شود"
        color = (0, 165, 255)  # نارنجی: شخص شناسایی‌شده، هویت نامشخص
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.rectangle(frame, (left, max(top, bottom - 24)), (right, bottom), color, cv2.FILLED)
        cv2.putText(frame, label, (left + 6, bottom - 6), cv2.FONT_HERSHEY_DUPLEX, 0.5, (255, 255, 255), 1)
    return frame


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


def _box_center(box):
    top, right, bottom, left = box
    return ((left + right) / 2.0, (top + bottom) / 2.0)


def _box_avg_size(box):
    top, right, bottom, left = box
    return max(1.0, ((right - left) + (bottom - top)) / 2.0)


class _FaceTracker:
    """رفع باگ «برچسب/رنگ کادر ناپایدار - سبز/قرمز عوض می‌شه»: تشخیص چهره
    روی تک‌تک فریم‌ها مستقل اجرا می‌شود؛ وقتی فاصله‌ی (distance) چهره‌ی یک
    نفر با نزدیک‌ترین چهره‌ی تعریف‌شده در بانک دقیقاً نزدیک آستانه‌ی tolerance
    باشد (مثلاً به‌خاطر زاویه‌ی کمی متفاوت سر در هر فریم)، ممکن است یک دور
    «شناخته‌شده» و دور بعد «تعریف‌نشده» تشخیص داده شود - نتیجه، چشمک‌زدن رنگ/
    برچسب است، بدون اینکه واقعاً کسی عوض شده باشد.

    این کلاس هر چهره‌ی تازه‌تشخیص‌داده‌شده را (بر اساس نزدیکی موقعیت کادر، نه
    هویت) به نزدیک‌ترین «ردِ» چهره‌ی همان دوربین در فریم‌های قبلی وصل می‌کند و
    یک هیستررزیس ساده اعمال می‌کند: برچسبِ نمایش‌داده‌شده فقط وقتی عوض می‌شود
    که هویت جدید حداقل ۲ دور پیاپی تکرار شود؛ در غیر این صورت همان برچسب قبلی
    (که معمولاً درست است) نگه داشته می‌شود. موقعیت کادر همیشه فوری به‌روز
    می‌شود تا دنبال‌کردن حرکت شخص تاخیر نداشته باشد - فقط «برچسب» است که
    پایدارتر می‌شود.

    هر ردِ گم‌شده (چهره‌ای که این دور تشخیص داده نشد) هم بلافاصله حذف نمی‌شود؛
    فقط بعد از چند دور پیاپیِ گم‌بودن پاک می‌شود (رفع چشمک‌زدن ظاهر/محو کادر)."""

    SWITCH_STREAK = 2   # چند دور پیاپی برای پذیرفتن تعویض برچسب
    MISS_LIMIT = 2       # چند دور پیاپی برای پاک‌کردن یک ردِ گم‌شده

    def __init__(self):
        self.tracks = []  # هر رد: box, displayed_person, candidate_person, candidate_streak, miss_streak

    def update(self, results):
        """results: خروجی خام FaceEngine.recognize (لیستی از {"box","person"}).
        خروجی: همان شکل، ولی با برچسب پایدارشده و شامل ردهای اخیراً گم‌شده هم
        (تا محو کادر هم با کمی تاخیر انجام شود)."""
        unmatched_tracks = list(self.tracks)
        for r in results:
            box, person = r["box"], r["person"]
            center = _box_center(box)
            size = _box_avg_size(box)

            best_track, best_dist = None, None
            for t in unmatched_tracks:
                d = ((center[0] - _box_center(t["box"])[0]) ** 2 +
                     (center[1] - _box_center(t["box"])[1]) ** 2) ** 0.5
                if d < size * 0.7 and (best_dist is None or d < best_dist):
                    best_track, best_dist = t, d

            if best_track is not None:
                unmatched_tracks.remove(best_track)
                best_track["box"] = box
                best_track["miss_streak"] = 0
                new_id = person["id"] if person else None
                displayed_id = best_track["displayed_person"]["id"] if best_track["displayed_person"] else None
                if new_id == displayed_id:
                    best_track["candidate_person"] = None
                    best_track["candidate_streak"] = 0
                else:
                    cand_id = best_track["candidate_person"]["id"] if best_track["candidate_person"] else None
                    if cand_id != new_id:
                        best_track["candidate_person"] = person
                        best_track["candidate_streak"] = 1
                    else:
                        best_track["candidate_streak"] += 1
                    if best_track["candidate_streak"] >= self.SWITCH_STREAK:
                        best_track["displayed_person"] = best_track["candidate_person"]
                        best_track["candidate_person"] = None
                        best_track["candidate_streak"] = 0
            else:
                # چهره‌ی کاملاً تازه - بدون تاخیر با همان برچسب اولش نمایش داده می‌شود.
                self.tracks.append({
                    "box": box,
                    "displayed_person": person,
                    "candidate_person": None,
                    "candidate_streak": 0,
                    "miss_streak": 0,
                })

        for t in unmatched_tracks:
            t["miss_streak"] += 1
        self.tracks = [t for t in self.tracks if t["miss_streak"] < self.MISS_LIMIT]

        return [{"box": t["box"], "person": t["displayed_person"]} for t in self.tracks]


class CameraStreamThread(QThread):
    frame_ready = pyqtSignal(object, object)   # (frame_for_display, raw_frame)
    error_signal = pyqtSignal(str)
    connected_signal = pyqtSignal()
    # پنل تشخیص چهره (main.py) برای هر چهره‌ی دیده‌شده (چه تعریف‌شده چه تعریف‌نشده)
    # یک رویداد دریافت می‌کند: (person dict یا None، تصویر برش‌خورده‌ی چهره یا None)
    face_event_signal = pyqtSignal(object, object)
    # رفع درخواست: شمارش افراد Real Time. هر بار تعداد افراد تازه شمارش‌شده
    # تغییر کند (یا هربار محاسبه شود)، تعداد فعلی از این سیگنال ارسال می‌شود
    # تا در بالای پنجره‌ی همان دوربین نمایش داده شود. (از نسخه‌ی فعلی به بعد
    # این عدد از روی تعداد چهره‌های شناسایی‌شده محاسبه می‌شود - رجوع کنید به
    # توضیح بالای فایل - و دیگر هرگز با خطا مواجه نمی‌شود.)
    people_count_signal = pyqtSignal(int)

    def __init__(self, rtsp_url, face_engine, process_every_n=5, parent=None):
        super().__init__(parent)
        self.rtsp_url = rtsp_url
        self.face_engine = face_engine
        # این مقدار دیگر تعیین‌کننده‌ی «تاخیر» نیست (چون تشخیص چهره async است)،
        # فقط فاصله‌ی ارسال فریم‌های جدید برای پردازش تشخیص چهره را کنترل می‌کند.
        self.process_every_n = max(1, process_every_n)
        self._run_flag = True
        self._last_results = []
        # رفع باگ چشمک‌زدن کادر/برچسب: به‌جای جایگزینی مستقیم نتیجه‌ی خام هر
        # دور تشخیص، از _FaceTracker (تعریف بالای فایل) برای پایدارسازی
        # موقعیت و برچسب استفاده می‌شود.
        self._face_tracker = _FaceTracker()

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

        # تشخیص «شخص» بر پایه‌ی کل بدن (مستقل از حالت/چهره) - رجوع کنید به
        # person_detector.py و توضیح بالای فایل. نمونه‌ی مشترک بین همه‌ی
        # دوربین‌هاست، پس فقط یک‌بار (برای اولین دوربین) بارگذاری می‌شود.
        self._person_detector = get_person_detector()
        self._last_person_boxes = []  # خروجی خام PersonDetector.detect برای آخرین فریم پردازش‌شده

        # --- شمارش افراد (اختیاری، پیش‌فرض خاموش) ---
        # اگر PersonDetector با موفقیت بارگذاری شده باشد، «تعداد افراد» از
        # روی تعداد باکس‌های بدنِ شناسایی‌شده (self._last_person_boxes)
        # محاسبه می‌شود که مستقل از حالت (ایستاده/نشسته/نیم‌خیز) و دیده‌شدن
        # چهره کار می‌کند. در غیر این صورت (مثلاً شکست دانلود مدل)، به همان
        # روش قبلی - تعداد چهره‌های شناسایی‌شده - برمی‌گردیم تا شمارش کاملاً
        # از کار نیفتد.
        self.count_people_enabled = False
        self._last_people_count = -1  # برای فرستادن سیگنال فقط وقتی عدد واقعاً عوض شود

    def set_people_counting(self, enabled: bool):
        """روشن/خاموش کردن شمارش افراد Real Time برای این دوربین."""
        self.count_people_enabled = bool(enabled)
        self._last_people_count = -1
        if not self.count_people_enabled:
            self.people_count_signal.emit(0)

    def _submit_recognition(self, frame):
        if self._recognize_busy.is_set():
            return  # پردازش قبلی هنوز در حال اجراست؛ این فریم را برای تشخیص رد می‌کنیم
        self._recognize_busy.set()
        # یک کپی سبک برای پردازش پس‌زمینه؛ حلقه‌ی اصلی نباید منتظرش بماند.
        frame_copy = frame.copy()
        self._executor.submit(self._run_recognition, frame_copy)

    def _run_recognition(self, frame):
        if self._person_detector is not None:
            try:
                self._last_person_boxes = self._person_detector.detect(frame)
            except Exception as e:
                # خطای تشخیص شخص هم نباید باعث توقف پخش زنده یا تشخیص چهره شود.
                print(f"خطا در تشخیص شخص (PersonDetector): {e}")
        try:
            results, unknown_event, known_events = self.face_engine.recognize(frame)
            # رفع باگ «کادر چشمک می‌زنه» و «برچسب/رنگ ناپایدار (سبز/قرمز عوض
            # می‌شه)»: نتیجه‌ی خام هر دور تشخیص مستقیماً نمایش داده نمی‌شود؛
            # از _FaceTracker (تعریف بالای فایل) عبور می‌کند که هم ظاهر/محو
            # ناگهانی کادر را (با نگه‌داشتن چند دور) میرا می‌کند، هم برچسب هر
            # چهره را فقط بعد از تکرار پیاپی یک هویت جدید عوض می‌کند - نه با
            # اولین نوسان لحظه‌ای تشخیص.
            self._last_results = self._face_tracker.update(results)
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

            # رفع باگ «کسی تو اتاقه ولی عدد صفر قفل شده / شخص شناسایی نمی‌شه»:
            # به‌جای تکیه‌ی صرف بر تعداد چهره‌های دیده‌شده، تعداد باکس‌های
            # PersonDetector (تشخیص کل بدن، مستقل از حالت/زاویه‌ی شخص) ملاک
            # قرار می‌گیرد؛ فقط اگر آن مدل در دسترس نباشد، به شمارش بر پایه‌ی
            # چهره برمی‌گردیم. چون این فقط یک len() است (نه پردازش تصویر)،
            # بدون هیچ هزینه‌ی اضافه‌ای هر فریم قابل به‌روزرسانی است.
            if self.count_people_enabled:
                if self._person_detector is not None:
                    current_count = len(self._last_person_boxes)
                else:
                    current_count = len(self._last_results)
                if current_count != self._last_people_count:
                    self._last_people_count = current_count
                    self.people_count_signal.emit(current_count)

            display_frame = frame.copy()
            self.face_engine.draw_results(display_frame, self._last_results)
            if self._person_detector is not None:
                draw_extra_person_boxes(display_frame, self._last_person_boxes, self._last_results)

            # frame خام (بدون باکس) هم ارسال می‌شود تا برای «ثبت چهره از تصویر زنده» استفاده شود.
            self.frame_ready.emit(display_frame, frame)

        cap.release()
        self._executor.shutdown(wait=False)

    def stop(self):
        self._run_flag = False
        self.wait()

import threading

import cv2

# ---------------------------------------------------------------------------
# تشخیص «شخص» (Person Detection) — مستقل از حالت بدن و بدون نیاز به دیدن چهره
# ---------------------------------------------------------------------------
# رفع درخواست: «شخص رو شناسایی نمی‌کنه؛ همه‌ی حالت‌های اشخاص (ایستاده، نشسته،
# نیم‌خیز، پشت به دوربین و ...) را براش تعریف کن» + «به‌جای machine learning
# از OpenCV استفاده کن».
#
# نسخه‌ی قبلی این فایل از یک مدل یادگیری ماشین (YOLOv8n از کتابخانه‌ی
# ultralytics/torch) استفاده می‌کرد. طبق درخواست، این مدل کاملاً حذف شده و
# به‌جایش فقط از قابلیت‌های خودِ OpenCV استفاده می‌شود - بدون هیچ مدل
# یادگیری‌ماشینیِ خارجی، بدون torch/ultralytics و بدون دانلود هیچ فایل وزنی.
# هرچه این فایل لازم دارد همان چیزی است که با نصب عادی «opencv-python» از
# قبل روی دیسک وجود دارد (فایل‌های Haar Cascade داخل خودِ بسته‌ی cv2) یا
# داخل خودِ کتابخانه پیاده‌سازی شده (HOGDescriptor، createBackgroundSubtractorMOG2).
#
# چون هیچ مدلِ از‌قبل‌آموزش‌دیده‌ی «شیء-کلاس شخص» با پوشش کامل تمام حالت‌های
# بدن در دسترس نیست (این دقیقاً همان چیزی است که YOLO آن را از داده یاد گرفته
# بود)، به‌جای تکیه بر یک روش، سه روش کلاسیک OpenCV با هم ترکیب می‌شوند تا
# هرکدام نقطه‌ضعف دیگری را پوشش دهد:
#
#   ۱) تفریق پس‌زمینه (Background Subtraction / MOG2) — کاملاً مستقل از شکل
#      یا حالت بدن است؛ فقط تشخیص می‌دهد «این ناحیه با پس‌زمینه‌ی یادگرفته‌شده
#      فرق دارد». یعنی فردی که نشسته، خم شده، پشتش به دوربین است یا نیمه‌پنهان
#      پشت میز/قفسه، تا وقتی جایی متفاوت از پس‌زمینه‌ی ثابت اتاق باشد (چه با
#      حرکت خودش، چه چون از ابتدا جزو پس‌زمینه‌ی یادگرفته‌شده نبوده) به‌عنوان
#      یک ناحیه‌ی پیش‌زمینه شناسایی می‌شود. این روش اصلی‌ترین پوشش‌دهنده‌ی
#      خواسته‌ی «در هر حالتی قابل تشخیص باشه» است، چون اصلاً به شکل ظاهری
#      شخص کاری ندارد.
#      محدودیت صادقانه: اگر شخص برای مدت طولانی کاملاً بی‌حرکت بماند، مدل
#      پس‌زمینه به‌مرور او را هم جزو پس‌زمینه یاد می‌گیرد (drift طبیعی هر
#      Background Subtractor) و دیگر به‌تنهایی کافی نیست - دقیقاً همان‌جایی
#      که دو روش زیر وارد عمل می‌شوند.
#
#   ۲) HOGDescriptor + SVM پیش‌فرض OpenCV (تشخیص عابر پیاده/کل بدن) — برای
#      شخصِ ایستاده/کاملاً بی‌حرکت (که تفریق پس‌زمینه ممکن است رفته‌رفته از
#      دست بدهد) از روی ظاهر کلی بدن او را پیدا می‌کند؛ مستقل از رنگ لباس یا
#      اینکه چهره دیده شود یا نه.
#
#   ۳) Haar Cascade های آماده‌ی خودِ OpenCV برای «کل بدن»، «نیم‌تنه‌ی بالا»
#      و «نیم‌تنه‌ی پایین» (haarcascade_fullbody / upperbody / lowerbody).
#      این سه با هم چند زاویه‌ی دید و چند سطح از دیده‌شدن بدن را پوشش
#      می‌دهند - مثلاً وقتی فرد پشت میز نشسته و فقط نیم‌تنه‌ی بالایش دیده
#      می‌شود (upperbody)، یا وقتی فقط از کمر به پایین در قاب است.
#
# نتیجه‌ی هر سه روش با هم ادغام (merge) می‌شود: باکس‌هایی که همپوشانی زیادی
# دارند یکی حساب می‌شوند تا یک نفر چند بار شمرده نشود. یعنی برخلاف یک مدل
# یادگیری‌ماشینی که یک تصمیم نهایی می‌دهد، اینجا چند «رأی» مستقل با هم جمع
# می‌شوند - اگر حتی یکی از سه روش شخص را در یک ناحیه ببیند، آن ناحیه به
# لیست نهایی اضافه می‌شود.
#
# نکته‌ی مهم درباره‌ی حالت (state): برخلاف نسخه‌ی YOLO که کاملاً stateless
# بود (هر فریم مستقل از فریم قبلی تحلیل می‌شد)، تفریق پس‌زمینه به‌طور ذاتی
# «حافظه» دارد (باید چند فریم پیاپی از همان دوربین را ببیند تا پس‌زمینه را
# یاد بگیرد). به همین دلیل دیگر یک نمونه‌ی مشترک (singleton) بین همه‌ی
# دوربین‌ها معنا ندارد - هر دوربین باید نمونه‌ی PersonDetector مستقل خودش را
# داشته باشد، وگرنه فریم‌های چند دوربین قاطی مدل پس‌زمینه‌ی یکدیگر می‌شوند و
# نتیجه بی‌معنی می‌شود. به همین خاطر camera_stream.py دیگر یک singleton
# سراسری وارد نمی‌کند؛ به‌جایش برای هر CameraStreamThread یک نمونه‌ی تازه‌ی
# PersonDetector ساخته می‌شود (رجوع کنید به camera_stream.py).
#
# نکته‌ی نصب/اجرا: چون این پیاده‌سازی فقط از opencv-python (که همین الان هم
# وابستگی اجباری پروژه است) استفاده می‌کند، دیگر هیچ بسته‌ی اختیاری جداگانه
# (ultralytics/torch) لازم نیست و هیچ فایل وزن مدلی دانلود نمی‌شود؛ در نتیجه
# .github/workflows/build.yml هم ساده‌تر و سریع‌تر شده (رجوع کنید به همان
# فایل).


def _merge_boxes(boxes, overlap_thresh=0.3):
    """چند باکس هم‌پوشان (که سه روش تشخیص مختلف روی یک نفر تولید کرده‌اند)
    را در یک باکس واحد ادغام می‌کند تا یک نفر چند بار شمرده نشود.

    ورودی/خروجی: لیستی از (x, y, w, h). الگوریتم ساده و سریع greedy است
    (نه NMS استاندارد مبتنی بر امتیاز، چون اینجا امتیاز اطمینانِ قابل‌مقایسه
    بین سه روش مختلف نداریم) - باکس‌ها را یکی‌یکی برمی‌دارد، هر باکس دیگری
    که همپوشانی‌اش (نسبت به مساحت کوچک‌تر) از overlap_thresh بیشتر باشد را
    با آن ادغام (اتحاد دو مستطیل) می‌کند، و این کار را تا رسیدن به حالت
    پایدار تکرار می‌کند.
    """
    if not boxes:
        return []

    def to_xyxy(b):
        x, y, w, h = b
        return x, y, x + w, y + h

    def area(b):
        x, y, w, h = b
        return max(0, w) * max(0, h)

    def overlap_ratio(a, b):
        ax1, ay1, ax2, ay2 = to_xyxy(a)
        bx1, by1, bx2, by2 = to_xyxy(b)
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        smaller = min(area(a), area(b))
        if smaller <= 0:
            return 0.0
        return inter / smaller

    def union(a, b):
        ax1, ay1, ax2, ay2 = to_xyxy(a)
        bx1, by1, bx2, by2 = to_xyxy(b)
        x1, y1 = min(ax1, bx1), min(ay1, by1)
        x2, y2 = max(ax2, bx2), max(ay2, by2)
        return x1, y1, x2 - x1, y2 - y1

    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        result = []
        used = [False] * len(merged)
        for i in range(len(merged)):
            if used[i]:
                continue
            current = merged[i]
            used[i] = True
            for j in range(i + 1, len(merged)):
                if used[j]:
                    continue
                if overlap_ratio(current, merged[j]) >= overlap_thresh:
                    current = union(current, merged[j])
                    used[j] = True
                    changed = True
            result.append(current)
        merged = result
    return merged


class PersonDetector:
    # نسبت مساحت مستطیلِ پیش‌زمینه‌ی حاصل از تفریق پس‌زمینه به مساحت کل
    # فریم که کمتر از آن، به‌عنوان نویز (نه یک شخص واقعی) نادیده گرفته
    # می‌شود. مقدار کوچک نگه داشته شده تا شخصی که فقط تا حدی از پشت
    # اثاثیه پیداست هم رد نشود.
    MIN_MOTION_AREA_RATIO = 0.012
    # عرض کاری داخلی برای پردازش (تصویر قبل از پردازش به این عرض تغییر
    # اندازه داده می‌شود، شبیه imgsz در نسخه‌ی قبلی) - فقط برای سرعت؛
    # باکس‌های نهایی دوباره به مقیاس فریم اصلی برگردانده می‌شوند.
    PROCESS_WIDTH = 480

    def __init__(self, hog_hit_threshold=0.0, haar_scale_factor=1.05, haar_min_neighbors=3):
        self._lock = threading.RLock()

        # --- روش ۱: تفریق پس‌زمینه --- (رجوع کنید به توضیح بالای فایل)
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=40, detectShadows=True
        )
        # هسته‌ی مورفولوژیک برای پاک‌کردن نویز ریز و پرکردن حفره‌های کوچک
        # داخل ناحیه‌ی تشخیص‌داده‌شده (مثلاً بین دست و بدن).
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

        # --- روش ۲: HOG + SVM پیش‌فرض OpenCV ---
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._hog_hit_threshold = hog_hit_threshold

        # --- روش ۳: Haar Cascade های آماده‌ی خودِ OpenCV ---
        cascade_dir = cv2.data.haarcascades
        cascade_files = [
            "haarcascade_fullbody.xml",
            "haarcascade_upperbody.xml",
            "haarcascade_lowerbody.xml",
        ]
        self._cascades = []
        self._load_error = None
        for name in cascade_files:
            try:
                clf = cv2.CascadeClassifier(cascade_dir + name)
                if not clf.empty():
                    self._cascades.append(clf)
            except Exception as e:
                self._load_error = str(e)
        self._haar_scale_factor = haar_scale_factor
        self._haar_min_neighbors = haar_min_neighbors

        # برخلاف نسخه‌ی قبلی (YOLO که ممکن بود نصب/دانلودش شکست بخورد)،
        # همه‌ی ابزارهای این نسخه بخشی از خودِ opencv-python هستند که
        # هم‌اکنون هم وابستگی اجباری پروژه است؛ پس عملاً همیشه در دسترس
        # است. این پرچم فقط برای حالت نادر خرابی نصب OpenCV (فایل‌های
        # cascade پیدا نشوند) نگه داشته شده تا camera_stream.py بتواند به
        # همان شکل قبلی (بدون کرش) به شمارش بر پایه‌ی چهره برگردد.
        self._available = len(self._cascades) > 0
        if not self._available:
            print(
                "تشخیص شخص (OpenCV) در دسترس نیست - شمارش صرفاً بر پایه‌ی "
                f"چهره ادامه می‌یابد. خطا: {self._load_error}"
            )

    @property
    def available(self):
        return self._available

    def _detect_motion_boxes(self, small_frame, scale_x, scale_y):
        """باکس‌های ناحیه‌ی پیش‌زمینه (متفاوت از پس‌زمینه‌ی یادگرفته‌شده) را
        برمی‌گرداند - مستقل از حالت/زاویه‌ی بدن (رجوع کنید به روش ۱ در
        توضیح بالای فایل)."""
        fg_mask = self._bg_subtractor.apply(small_frame)
        # مقدار ۱۲۷ در MOG2 یعنی «سایه» (نه پیش‌زمینه‌ی واقعی)؛ فقط ۲۵۵
        # (پیش‌زمینه‌ی قطعی) نگه داشته می‌شود تا سایه‌ی فرد به‌اشتباه بخشی
        # از باکس او حساب نشود یا باکس جداگانه‌ای برای خودش نسازد.
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self._morph_kernel)
        fg_mask = cv2.dilate(fg_mask, self._morph_kernel, iterations=2)

        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = small_frame.shape[0] * small_frame.shape[1]
        min_area = frame_area * self.MIN_MOTION_AREA_RATIO

        boxes = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            boxes.append((int(x * scale_x), int(y * scale_y), int(w * scale_x), int(h * scale_y)))
        return boxes

    def _detect_hog_boxes(self, small_frame, scale_x, scale_y):
        """تشخیص افراد ایستاده/کاملاً پیدا بر اساس ظاهر کلی بدن (رجوع کنید
        به روش ۲ در توضیح بالای فایل) - مکمل تفریق پس‌زمینه برای شخصی که
        مدتی است بی‌حرکت مانده."""
        try:
            rects, _weights = self._hog.detectMultiScale(
                small_frame,
                winStride=(8, 8),
                padding=(8, 8),
                scale=1.05,
                hitThreshold=self._hog_hit_threshold,
            )
        except Exception:
            return []
        boxes = []
        for (x, y, w, h) in rects:
            boxes.append((int(x * scale_x), int(y * scale_y), int(w * scale_x), int(h * scale_y)))
        return boxes

    def _detect_haar_boxes(self, gray_small, scale_x, scale_y):
        """تشخیص با Cascade های کل‌بدن/نیم‌تنه‌ی بالا/نیم‌تنه‌ی پایین (رجوع
        کنید به روش ۳ در توضیح بالای فایل) - چند زاویه/میزان دیده‌شدن بدن
        را پوشش می‌دهد، از جمله فردی که فقط نیم‌تنه‌اش (مثلاً پشت میز
        نشسته) در قاب است."""
        boxes = []
        min_size = (
            max(20, gray_small.shape[1] // 12),
            max(40, gray_small.shape[0] // 6),
        )
        for cascade in self._cascades:
            try:
                rects = cascade.detectMultiScale(
                    gray_small,
                    scaleFactor=self._haar_scale_factor,
                    minNeighbors=self._haar_min_neighbors,
                    minSize=min_size,
                )
            except Exception:
                continue
            for (x, y, w, h) in rects:
                boxes.append((int(x * scale_x), int(y * scale_y), int(w * scale_x), int(h * scale_y)))
        return boxes

    def detect(self, frame):
        """لیستی از باکس‌های افراد در frame را برمی‌گرداند - با همان قالب
        (top, right, bottom, left) که در بقیه‌ی پروژه (FaceEngine) استفاده
        می‌شود، تا camera_stream.py بتواند این نتایج را کنار نتایج چهره،
        بدون تغییر قالب، رسم و شمارش کند. اگر به هر دلیل هیچ‌کدام از
        ابزارهای OpenCV در دسترس نباشد، لیست خالی برمی‌گرداند (بدون خطا).
        """
        if not self._available or frame is None or frame.size == 0:
            return []

        with self._lock:
            h, w = frame.shape[:2]
            if w <= 0 or h <= 0:
                return []

            # تغییر اندازه به یک عرض کاری ثابت، فقط برای سرعت (شبیه imgsz
            # در نسخه‌ی قبلی مبتنی بر YOLO)؛ باکس‌های نهایی با scale_x/scale_y
            # به مقیاس فریم اصلی برگردانده می‌شوند.
            if w > self.PROCESS_WIDTH:
                scale = self.PROCESS_WIDTH / w
                small = cv2.resize(frame, (int(w * scale), int(h * scale)))
            else:
                small = frame
            scale_x = w / small.shape[1]
            scale_y = h / small.shape[0]

            try:
                gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                gray_small = cv2.equalizeHist(gray_small)
            except Exception:
                gray_small = None

            candidate_boxes = []
            candidate_boxes.extend(self._detect_motion_boxes(small, scale_x, scale_y))
            candidate_boxes.extend(self._detect_hog_boxes(small, scale_x, scale_y))
            if gray_small is not None:
                candidate_boxes.extend(self._detect_haar_boxes(gray_small, scale_x, scale_y))

        merged = _merge_boxes(candidate_boxes, overlap_thresh=0.3)

        boxes = []
        for (x, y, box_w, box_h) in merged:
            left, top = max(0, x), max(0, y)
            right, bottom = min(w, x + box_w), min(h, y + box_h)
            if right > left and bottom > top:
                boxes.append((top, right, bottom, left))
        return boxes

    def draw_boxes(self, frame, boxes):
        """کادر آبی‌روشن دور *کل بدن* هر فرد شناسایی‌شده (فارغ از حالت بدن یا
        اینکه چهره‌اش دیده می‌شود یا نه) - جدا و قابل‌تشخیص از کادر سبز/قرمز
        چهره که FaceEngine.draw_results رسم می‌کند."""
        for (top, right, bottom, left) in boxes:
            cv2.rectangle(frame, (left, top), (right, bottom), (255, 200, 0), 2)
            label = "شخص"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 0.5, 1)
            label_top = max(0, top - th - 8)
            cv2.rectangle(frame, (left, label_top), (left + tw + 8, label_top + th + 6), (255, 200, 0), cv2.FILLED)
            cv2.putText(frame, label, (left + 4, label_top + th + 1), cv2.FONT_HERSHEY_DUPLEX, 0.5, (30, 30, 30), 1)
        return frame

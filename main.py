import os
import sys
import time
import uuid
import threading

import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QMenu, QTreeWidget, QTreeWidgetItem, QInputDialog, QDialog,
    QGridLayout, QComboBox, QScrollArea, QSizePolicy, QSplitter, QStackedWidget
)
from PyQt6.QtGui import QImage, QPixmap, QAction, QIcon, QDrag, QFontMetrics, QPainter, QPen, QColor, QPolygonF
from PyQt6.QtCore import Qt, QSize, QMimeData, QPointF, QRectF, QTimer, QEvent, pyqtSignal

from face_engine import FaceEngine
from scanner import NetworkScanThread
from camera_store import CameraStore
from camera_stream import CameraStreamThread, region_to_polygon, is_low_spec_mode
# نکته: floor_detector عمداً در بالای فایل import نمی‌شود؛ transformers و
# SegFormer چند صد مگابایت رم می‌گیرند. فقط وقتی کاربر واقعاً دکمه‌ی «تشخیص
# هوشمند زمین» را بزند، در همان لحظه بارگذاری می‌شود - رجوع کنید به
# _get_floor_detect_thread پایین‌تر.
_FloorDetectThread = None


def _get_floor_detect_thread():
    """دسترسی تنبل به FloorDetectThread (بارگذاری سنگین transformers فقط
    در لحظه‌ی استفاده، نه در زمان بالا آمدن برنامه)."""
    global _FloorDetectThread
    if _FloorDetectThread is None:
        from floor_detector import FloorDetectThread as _FT
        _FloorDetectThread = _FT
    return _FloorDetectThread
from add_camera_dialog import AddCameraDialog
from add_nvr_dialog import AddNVRDialog
from nvr_scanner import DirectCameraProbeThread
try:
    from nvr_webview_dialog import NVRWebViewDialog, _WEBENGINE_AVAILABLE
except ImportError:
    NVRWebViewDialog, _WEBENGINE_AVAILABLE = None, False
from face_library_dialog import FaceLibraryPage
from report_store import report_store
from reports_dialog import ReportsPage
# رفع درخواست «سیستم پلاک‌خوان»: صفحه‌ی جدا (مثل Face Library) با دو تب
# «تعریف پلاک‌ها» و «گزارش عبور» + دیتابیس SQLite پلاک‌ها/رویدادها.
from plate_store import plate_store
from plate_library_dialog import PlateLibraryPage
# رفع درخواست «ردیابی اشخاص بین دوربین‌ها»: صفحه‌ی جدا (مثل پلاک‌خوان) با دو
# تب «اشخاص ردیابی‌شده» و «گزارش مسیر حرکت» + دیتابیس SQLite اشخاص/حضورها +
# تطبیق ظاهری بدون چهره (person_reid).
from person_store import person_store
from person_track_dialog import PersonTrackPage
from person_reid import GlobalPersonMatcher
# رفع درخواست «نقشه‌ی تعاملی ساختمان»: صفحه‌ی جدا با نقشه‌ی هر طبقه (DXF
# اتوکد/تصویر)، جای‌گذاری دوربین/NVR/رک/سوئیچ، پخش زنده با کلیک روی دوربین
# نقشه و نمایش مسیر تردد شخص روی نقشه‌ها.
try:
    from building_map_dialog import BuildingMapPage
    _MAP_PAGE_AVAILABLE = True
except Exception:
    BuildingMapPage = None
    _MAP_PAGE_AVAILABLE = False
from nvr_storage_dialog import NVRStorageDialog
from device_detect import DeviceDetectThread
from fire_alarm_store import FireAlarmStore
from fire_alarm_io import FireAlarmMonitorThread
from fire_alarm_dialog import FireAlarmPage
try:
    # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
    # این سه فایل در پکیج ias-cms-fire-building کنار main.py قرار می‌گیرند.
    # نبودشان باعث کرش نمی‌شود؛ فقط این قابلیت غیرفعال می‌ماند.
    from building_fire_output import BuildingFireOutput
    from alarm_sound import AlarmSoundPlayer
    from building_fire_settings_dialog import BuildingFireSettingsDialog
    _BUILDING_FIRE_AVAILABLE = True
except ImportError:
    BuildingFireOutput = None
    AlarmSoundPlayer = None
    BuildingFireSettingsDialog = None
    _BUILDING_FIRE_AVAILABLE = False
from image_settings_dialog import ImageSettingsDialog
from theme import (
    apply_theme, LOGO_SHIELD, APP_NAME_FA, APP_NAME_EN, LOGO_BLUE, TEXT_MUTED,
)

# بهینه‌سازی برای سیستم‌های ضعیف (رم کم / بدون کارت گرافیک):
# OpenCV به‌صورت پیش‌فرض برای عملیات داخلی (resize، cvtColor و ...) روی *تمام*
# هسته‌های CPU ترد باز می‌کند. وقتی چند دوربین هم‌زمان پخش می‌شوند (هر کدام با
# ترد پخش + ترد تشخیص چهره‌ی خودشان)، این تردهای داخلی OpenCV با تردهای خود
# برنامه بر سر CPU رقابت می‌کنند و روی سیستم‌های 2 تا 4 هسته‌ای (بدون GPU) کل
# رابط کاربری کند/تکه‌تکه می‌شود. محدود کردن آن به نصف هسته‌ها این رقابت را
# کم می‌کند بدون افت محسوس در سرعت پردازش هر فریم.
cv2.setNumThreads(max(1, (os.cpu_count() or 4) // 2))

# روی سیستم‌های کم‌هسته، تشخیص چهره روی هر ۵ فریم هنوز نسبتاً سنگین است؛ فاصله
# را کمی بیشتر می‌کنیم تا CPU بیشتری برای خود پخش زنده (decode ویدیو) بماند.
# حالت سبک (۴ گیگ رم / بدون GPU): خودکار از روی رم سیستم تشخیص داده می‌شود
# (کمتر از ۶ گیگ = سبک)؛ با IAS_LITE_MODE=1 اجباری و با IAS_LITE_MODE=0
# غیرفعال می‌شود. در حالت سبک فاصله‌ی تشخیص بیشتر می‌شود تا CPU و رم برای
# پخش زنده بماند.
_LOW_SPEC_MODE = is_low_spec_mode()
if _LOW_SPEC_MODE:
    print("حالت سبک فعال شد (رم کم / بدون GPU): تشخیص‌ها با فاصله‌ی بیشتر و حداکثر یک تشخیص هم‌زمان.")
_PROCESS_EVERY_N = 12 if _LOW_SPEC_MODE else (5 if (os.cpu_count() or 4) >= 6 else 8)

# نگاشت تعداد نمایش هم‌زمان دوربین‌ها به چیدمان (ردیف, ستون) شبکه‌ی نمایش.
# اعداد دقیقاً همان مقادیر درخواستی هستند: 1، 4، 9، 16، 32، 64.
GRID_LAYOUTS = {
    1: (1, 1),
    4: (2, 2),
    9: (3, 3),
    16: (4, 4),
    32: (4, 8),
    64: (8, 8),
}


def _bgr_to_pixmap(frame):
    """تبدیل یک فریم OpenCV (BGR، numpy) به QPixmap برای نمایش در UI."""
    if frame is None or frame.size == 0:
        return None
    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_image.shape
    bytes_per_line = ch * w
    # .copy() الزامی است؛ بدون آن QImage به بافر موقت numpy اشاره می‌کند که ممکن
    # است پیش از رندر شدن، توسط پایتون آزاد/بازچرخانی شود.
    qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qt_img)


def _play_alarm_beep():
    """رفع درخواست: پخش صدای آلارم هنگام عبور شخص از خط فرضی. در یک ترد
    جداگانه اجرا می‌شود تا رابط کاربری هرگز قفل نشود. روی ویندوز (پلتفرم
    اصلی این برنامه) از winsound.Beep استفاده می‌شود؛ اگر در دسترس نبود
    (مثلاً روی لینوکس/مک برای توسعه)، به بیپ ساده‌ی Qt برمی‌گردد."""
    def _run():
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(1500, 220)
                time.sleep(0.08)
        except Exception:
            try:
                QApplication.beep()
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()


class VideoDisplayLabel(QLabel):
    """رفع درخواست: امکان رسم «محدوده‌ی هشدار» به‌شکل یک چندضلعیِ دلخواه
    (Polygon) روی زمین/تصویر زنده‌ی هر دوربین - جایگزین نسخه‌ی قبلی که فقط
    یک مستطیل با کشیدن (drag) ماوس رسم می‌کرد. کاربر با کلیک‌های متوالی روی
    نقاط دلخواه (مثلاً گوشه‌های واقعی یک اتاق یا راهرو، که لزوماً مستطیل
    نیستند) محدوده را می‌سازد؛ برنامه نقاط را به‌ترتیب به هم وصل می‌کند.
    برای بستن محدوده: یا روی همان نقطه‌ی اول (با یک دایره‌ی متمایز مشخص
    شده) کلیک کنید، یا دابل‌کلیک کنید (حداقل به ۳ نقطه نیاز است). با کلیک
    راست، رسمِ در حال انجام (نقاط هنوز بسته‌نشده) بدون تاثیر روی
    محدوده‌های قبلاً تایید‌شده لغو می‌شود.

    رفع درخواست «قابلیت ادیت‌کردن»: هر محدوده‌ی «در انتظار» - چه تازه با
    کلیک‌ها بسته شده و هنوز نام‌گذاری نشده، چه یک محدوده‌ی قبلاً تایید‌شده
    که کاربر از دیالوگ «مدیریت محدوده‌ها» برای ویرایش شکلش را باز کرده، چه
    محدوده‌ی خودکارِ «کل تصویر» (رجوع کنید به
    CameraSlotWidget.start_auto_full_frame_region) - همیشه با ماوس قابل
    تغییر است: کلیک-و-درگ روی هر گوشه جابه‌جایش می‌کند، کلیک روی وسط یک
    یال یک گوشه‌ی تازه اضافه می‌کند و کلیک راست روی یک گوشه حذفش می‌کند
    (حداقل ۳ گوشه لازم است).

    منطق مختصات: چون setPixmap با KeepAspectRatio یک pixmap کوچک‌تر یا
    مساوی اندازه‌ی خودِ لیبل تولید می‌کند و QLabel آن را وسط‌چین (AlignCenter)
    نمایش می‌دهد، مستطیل واقعیِ تصویر داخل لیبل همیشه یک مستطیل هم‌مرکز به
    اندازه‌ی pixmap فعلی است (_frame_rect). نقاط رسم‌شده با ماوس (پیکسل
    لیبل) با این مستطیل به مختصات نرمال 0..1 (نسبت به خودِ فریم دوربین، نه
    اندازه‌ی لیبل) تبدیل و نگه‌داشته می‌شوند تا با تغییر اندازه‌ی پنجره/شبکه
    هم موقعیت محدوده‌ها درست بماند."""

    region_drawn = pyqtSignal(list)  # لیستی از (x,y) نرمال‌شده‌ی 0..1، حداقل ۳ نقطه

    # فاصله‌ی (به پیکسلِ لیبل) که کلیک نزدیک نقطه‌ی اول را «بستن محدوده»
    # حساب می‌کنیم - نه یک نقطه‌ی تازه.
    _CLOSE_THRESHOLD_PX = 14.0

    # رفع درخواست «بتونه اندازه و شکل محدوده رو تغییر بده»: فاصله‌ی (به
    # پیکسلِ لیبل) که کلیک/درگ نزدیک یکی از گوشه‌های محدوده‌ی در انتظار
    # (pending - چه تازه رسم‌شده و هنوز نام‌گذاری‌نشده، چه یک محدوده‌ی
    # قبلاً تایید‌شده که برای ویرایش شکل بارگذاری شده) را «گرفتنِ همان
    # گوشه برای جابه‌جایی/حذف» حساب می‌کنیم؛ همین آستانه برای «کلیک روی
    # نزدیک‌ترین یال» هم استفاده می‌شود که یک نقطه‌ی تازه به آن یال اضافه
    # می‌کند (برای ریزتر کردن شکل).
    _VERTEX_HIT_PX = 10.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.draw_mode = False
        # نقاطِ نرمال (0..1) محدوده‌ای که کاربر همین الان دارد با کلیک‌های
        # متوالی می‌سازد و هنوز نبسته (تایید نکرده) است.
        self._draw_points = []
        # موقعیت فعلی ماوس (نرمال) - فقط برای رسم خط‌چینِ پیش‌نمایش از آخرین
        # نقطه‌ی کلیک‌شده تا زیر نشانگر ماوس.
        self._hover_norm = None
        # رفع درخواست: برخلاف خط فرضیِ قدیمی (که بعد از تایید دیگر روی
        # تصویر دیده نمی‌شد)، محدوده‌های تایید‌شده همیشه با یک قاب نازک و
        # برچسبِ شماره/نام‌شان روی تصویر نمایش داده می‌شوند - چون می‌توانند
        # چندتایی و نام‌دار باشند و کاربر باید همیشه مرزشان را ببیند.
        self._confirmed_regions = []  # لیستی از دیکشنری {"number","name","points"}
        # محدوده‌ای که تازه بسته شده ولی هنوز کاربر نامش را تایید نکرده، یا
        # یک محدوده‌ی قبلاً تایید‌شده که همین الان برای ویرایش شکل/اندازه
        # بارگذاری شده - برای پیش‌نمایش (رجوع کنید به set_pending_points_norm).
        # برخلاف نقاطِ در حال رسم (_draw_points)، این نقاط همیشه با ماوس
        # قابل کشیدن/افزودن/حذف‌کردن هستند (رجوع کنید به mousePressEvent).
        self._pending_points = None
        # True یعنی این پیش‌نمایش، ویرایشِ یک محدوده‌ی از قبل تایید‌شده است
        # (رنگ سبز) - False یعنی یک محدوده‌ی تازه که هنوز تایید نشده (رنگ
        # زرد، رفتار قبلی). فقط برای تمایز بصری استفاده می‌شود.
        self._editing_existing = False
        # اندیس گوشه‌ای که همین الان با درگ ماوس در حال جابه‌جایی است؛ None
        # یعنی هیچ گوشه‌ای در حال کشیده‌شدن نیست.
        self._drag_vertex_idx = None
        self.setMouseTracking(True)

    def set_draw_mode(self, enabled: bool):
        self.draw_mode = bool(enabled)
        self.setCursor(Qt.CursorShape.CrossCursor if self.draw_mode else Qt.CursorShape.ArrowCursor)
        self._draw_points = []
        self._hover_norm = None
        self.update()

    def set_pending_points_norm(self, points, editing_existing=False):
        """محدوده‌ی «بسته‌شده ولی هنوز تایید/نام‌گذاری نشده» یا «در حال
        ویرایش» را برای پیش‌نمایش تنظیم می‌کند؛ None یعنی هیچ محدوده‌ای در
        انتظار نیست. ``editing_existing=True`` یعنی این نقاط متعلق به یک
        محدوده‌ی قبلاً تایید‌شده است که کاربر دارد شکلش را ویرایش می‌کند
        (رنگ سبز به‌جای زرد - رجوع کنید به CameraSlotWidget.start_edit_region)."""
        self._pending_points = [tuple(p) for p in points] if points else None
        self._editing_existing = bool(editing_existing) if self._pending_points else False
        self._drag_vertex_idx = None
        self.update()

    def pending_points_norm(self):
        """نقاطِ نرمال‌شده‌ی فعلیِ محدوده‌ی در انتظار/در حال ویرایش (شامل
        هر جابه‌جایی/افزودن/حذف گوشه‌ای که کاربر تا همین لحظه با ماوس انجام
        داده) را برمی‌گرداند؛ None اگر چیزی در انتظار نیست."""
        return list(self._pending_points) if self._pending_points else None

    def set_confirmed_regions(self, regions):
        """لیست محدوده‌های نهایی/فعال این دوربین را برای رسم دائمی روی
        تصویر تنظیم می‌کند."""
        self._confirmed_regions = list(regions or [])
        self.update()

    def _frame_rect(self):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        pm_w, pm_h = pixmap.width(), pixmap.height()
        if pm_w <= 0 or pm_h <= 0:
            return None
        x0 = (self.width() - pm_w) / 2.0
        y0 = (self.height() - pm_h) / 2.0
        return QRectF(x0, y0, pm_w, pm_h)

    def _widget_to_norm(self, point):
        rect = self._frame_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        x = (point.x() - rect.x()) / rect.width()
        y = (point.y() - rect.y()) / rect.height()
        return (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))

    def _norm_to_widget(self, norm_point):
        rect = self._frame_rect()
        if rect is None:
            return None
        return QPointF(rect.x() + norm_point[0] * rect.width(), rect.y() + norm_point[1] * rect.height())

    def _pending_vertex_at(self, widget_pos):
        """اندیسِ نزدیک‌ترین گوشه‌ی محدوده‌ی در انتظار به widget_pos را
        برمی‌گرداند - اگر در فاصله‌ی _VERTEX_HIT_PX بود؛ وگرنه None."""
        if not self._pending_points:
            return None
        for i, norm_p in enumerate(self._pending_points):
            wp = self._norm_to_widget(norm_p)
            if wp is None:
                continue
            if (widget_pos - wp).manhattanLength() <= self._VERTEX_HIT_PX:
                return i
        return None

    @staticmethod
    def _closest_point_on_segment(p, a, b):
        """نزدیک‌ترین نقطه روی پاره‌خط a-b به نقطه‌ی p و فاصله‌اش تا آن."""
        ax, ay, bx, by, px, py = a.x(), a.y(), b.x(), b.y(), p.x(), p.y()
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq < 1e-9 else min(max(((px - ax) * dx + (py - ay) * dy) / length_sq, 0.0), 1.0)
        cx, cy = ax + t * dx, ay + t * dy
        return QPointF(cx, cy), ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

    def _pending_edge_insert(self, widget_pos):
        """رفع درخواست «تغییر شکل محدوده»: اگر widget_pos به یکی از یال‌های
        محدوده‌ی در انتظار نزدیک باشد، اندیسی که باید یک گوشه‌ی تازه در آن
        درج شود + مختصات نرمال همان نقطه را برمی‌گرداند - وگرنه None. این
        اجازه می‌دهد کاربر با کلیک روی وسط یک ضلع، آن را به دو ضلع تبدیل
        کند و شکل را دقیق‌تر کند (مثلاً دور زدن یک مانع)."""
        pts = self._pending_points
        if not pts or len(pts) < 2:
            return None
        n = len(pts)
        best = None
        for i in range(n):
            a = self._norm_to_widget(pts[i])
            b = self._norm_to_widget(pts[(i + 1) % n])
            if a is None or b is None:
                continue
            _, dist = self._closest_point_on_segment(widget_pos, a, b)
            if dist <= self._VERTEX_HIT_PX and (best is None or dist < best[1]):
                best = (i, dist)
        if best is None:
            return None
        norm_point = self._widget_to_norm(widget_pos)
        if norm_point is None:
            return None
        return best[0] + 1, norm_point

    def _finish_polygon(self):
        """رفع درخواست: بستن محدوده‌ی در حال رسم (حداقل ۳ نقطه لازم است) و
        ارسال سیگنال region_drawn با همان نقاط - یک‌بار، چه از راه کلیک
        نزدیک نقطه‌ی اول چه از راه دابل‌کلیک."""
        if len(self._draw_points) >= 3:
            points = list(self._draw_points)
            self._draw_points = []
            self._hover_norm = None
            self.update()
            self.region_drawn.emit(points)

    def mousePressEvent(self, event):
        if self.draw_mode and self._frame_rect() is not None:
            if event.button() == Qt.MouseButton.RightButton:
                # لغو رسمِ در حال انجام (نقاط هنوز بسته‌نشده)؛ محدوده‌های
                # قبلاً تایید‌شده دست‌نخورده می‌مانند.
                self._draw_points = []
                self._hover_norm = None
                self.update()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                norm = self._widget_to_norm(event.position())
                if norm is None:
                    return
                # اگر با شروع یک محدوده‌ی تازه، پیش‌نمایش محدوده‌ی «بسته‌شده
                # ولی هنوز تاییدنشده‌»ی قبلی روی تصویر معلق مانده بود، همین
                # الان پاکش می‌کنیم - وگرنه دو پیش‌نمایش هم‌زمان گیج‌کننده
                # می‌شود (دقیقاً همان رفتار نسخه‌ی قبلیِ مستطیلی: شروع یک drag
                # تازه، پیش‌نمایش مستطیل معلقِ قبلی را از اولویت رسم می‌انداخت).
                if not self._draw_points:
                    self._pending_points = None
                # اگر حداقل ۳ نقطه داریم و کلیک نزدیک نقطه‌ی اول است، محدوده
                # بسته می‌شود؛ در غیر این صورت یک نقطه‌ی تازه اضافه می‌شود.
                if len(self._draw_points) >= 3:
                    first_widget = self._norm_to_widget(self._draw_points[0])
                    if first_widget is not None and (event.position() - first_widget).manhattanLength() <= self._CLOSE_THRESHOLD_PX:
                        self._finish_polygon()
                        return
                self._draw_points.append(norm)
                self.update()
                return
        # رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: وقتی در حالت
        # رسمِ فعال (کلیک‌های متوالی) نیستیم ولی یک محدوده‌ی در انتظار/در
        # حال ویرایش روی تصویر هست، همان محدوده همیشه با ماوس قابل تغییر
        # است - کلیک چپ روی یک گوشه = گرفتنِ آن برای جابه‌جایی (درگ)، کلیک
        # چپ روی وسط یک یال = افزودن گوشه‌ی تازه در همان‌جا، کلیک راست روی
        # یک گوشه = حذف همان گوشه (تا وقتی حداقل ۳ گوشه باقی بماند).
        if not self.draw_mode and self._pending_points and self._frame_rect() is not None:
            pos = event.position()
            if event.button() == Qt.MouseButton.RightButton:
                idx = self._pending_vertex_at(pos)
                if idx is not None and len(self._pending_points) > 3:
                    del self._pending_points[idx]
                    self.update()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                idx = self._pending_vertex_at(pos)
                if idx is not None:
                    self._drag_vertex_idx = idx
                    return
                inserted = self._pending_edge_insert(pos)
                if inserted is not None:
                    insert_at, norm_point = inserted
                    self._pending_points.insert(insert_at, norm_point)
                    self._drag_vertex_idx = insert_at
                    self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_vertex_idx is not None and self._pending_points:
            norm = self._widget_to_norm(event.position())
            if norm is not None:
                self._pending_points[self._drag_vertex_idx] = norm
                self.update()
            return
        if self.draw_mode and self._draw_points:
            self._hover_norm = self._widget_to_norm(event.position())
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_vertex_idx is not None:
            self._drag_vertex_idx = None
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.draw_mode and event.button() == Qt.MouseButton.LeftButton and len(self._draw_points) >= 3:
            self._finish_polygon()
            return
        super().mouseDoubleClickEvent(event)

    def _region_polygon_widget(self, region):
        """نقاط یک محدوده (چه جدید با \"points\" چه قدیمیِ \"rect\") را به
        مختصات پیکسلِ لیبل (برای رسم) تبدیل می‌کند."""
        polygon_norm = region_to_polygon(region) if isinstance(region, dict) else list(region)
        pts = []
        for p in polygon_norm:
            wp = self._norm_to_widget(p)
            if wp is None:
                return None
            pts.append(wp)
        return pts if len(pts) >= 3 else None

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)

        # محدوده‌های تایید‌شده - همیشه با قاب آبی نازک و برچسب شماره/نام‌شان
        # رسم می‌شوند (رجوع کنید به توضیح بالای کلاس).
        for region in self._confirmed_regions:
            pts = self._region_polygon_widget(region)
            if pts is None:
                continue
            pen = QPen(QColor("#3498db"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF(pts))
            label = f"{region.get('number', '')}"
            if region.get("name"):
                label += f" / {region['name']}"
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(pts[0] + QPointF(4, 14), label)

        # اولویت با «رسمِ در حال انجام» (نقاطی که همین الان کاربر دارد
        # کلیک می‌کند) است؛ اگر خالی بود، پیش‌نمایش محدوده‌ی تازه‌بسته‌شده
        # ولی هنوز تاییدنشده نشان داده می‌شود - دقیقاً همان اولویت نسخه‌ی
        # قبلیِ مستطیلی (drag در حال انجام روی پیش‌نمایشِ معلق اولویت داشت).
        if self._draw_points:
            pen = QPen(QColor("#f1c40f"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            widget_pts = [self._norm_to_widget(p) for p in self._draw_points]
            # یال‌های تاکنون کلیک‌شده (باز - هنوز بسته نشده)
            for i in range(len(widget_pts) - 1):
                painter.drawLine(widget_pts[i], widget_pts[i + 1])
            # خط‌چینِ پیش‌نمایش از آخرین نقطه تا زیر نشانگر ماوس
            if self._hover_norm is not None:
                hover_pt = self._norm_to_widget(self._hover_norm)
                if hover_pt is not None:
                    dash_pen = QPen(QColor("#f1c40f"))
                    dash_pen.setWidth(1)
                    dash_pen.setStyle(Qt.PenStyle.DashLine)
                    painter.setPen(dash_pen)
                    painter.drawLine(widget_pts[-1], hover_pt)
                    painter.setPen(pen)
            # دایره‌ی روی هر نقطه؛ نقطه‌ی اول بزرگ‌تر و متمایز - همان‌جایی
            # که کلیک روی آن محدوده را می‌بندد.
            for i, wp in enumerate(widget_pts):
                r = 5.0 if i == 0 else 3.0
                painter.setBrush(QColor("#f1c40f") if i == 0 else QColor("#1e1e1e"))
                painter.drawEllipse(wp, r, r)
        elif self._pending_points is not None:
            pts = self._region_polygon_widget({"points": self._pending_points})
            if pts is not None:
                # رفع درخواست: رنگ سبز = این پیش‌نمایش، ویرایشِ یک محدوده‌ی
                # قبلاً تایید‌شده است؛ رنگ زرد = یک محدوده‌ی تازه (چه با
                # کلیک‌های متوالی رسم شده، چه با «تشخیص خودکار محدوده»
                # ساخته شده) که هنوز تایید/نام‌گذاری نشده.
                color = QColor("#2ecc71") if self._editing_existing else QColor("#f1c40f")
                pen = QPen(color)
                pen.setWidth(2)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolygon(QPolygonF(pts))
                # رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: یک
                # دسته‌ی توپُر روی هر گوشه - نشانه‌ی این‌که این گوشه با ماوس
                # قابل کشیدن (تغییر شکل/اندازه) است (رجوع کنید به
                # mousePressEvent/mouseMoveEvent بالا).
                handle_pen = QPen(QColor("#1e1e1e"))
                handle_pen.setWidth(1)
                painter.setPen(handle_pen)
                painter.setBrush(color)
                for wp in pts:
                    painter.drawEllipse(wp, 5.0, 5.0)
        painter.end()


class CameraSlotWidget(QWidget):
    """یک خانه (slot) در شبکه‌ی نمایش هم‌زمان دوربین‌ها. می‌تواند خالی باشد یا
    یک دوربین را پخش کند. با کلیک انتخاب (highlight) می‌شود تا فریم زنده‌اش
    برای «ثبت چهره از تصویر زنده» در دسترس باشد."""

    # رفع باگ: دکمه‌های نوار ابزار محدوده‌ی هشدار در MainWindow قبلاً فقط
    # هنگام عوض‌شدن خانه‌ی انتخاب‌شده (selection_changed) به‌روزرسانی
    # می‌شدند - نه وقتی خودِ محدوده داخل همین خانه رسم/تایید/حذف می‌شد.
    # نتیجه این بود که بعد از رسم محدوده، دکمه‌ی «تایید» غیرفعال (خاکستری)
    # باقی می‌ماند و کلیک روی آن هیچ اثری نداشت - و چون تایید هرگز واقعاً
    # اجرا نمی‌شد، محدوده هم هرگز روی ترد پخش برای تشخیص ورود فعال نمی‌شد
    # (پس آلارم هم هرگز رخ نمی‌داد). این سیگنال با هر تغییر وضعیت محدوده‌ها
    # (رسم/تایید/حذف/بارگذاری از دیسک/بستن دوربین) ارسال می‌شود تا
    # MainWindow._refresh_line_buttons همیشه با وضعیت واقعی هم‌گام بماند.
    tripwire_changed = pyqtSignal()
    # تغییر وضعیت موتور تشخیص شخص (برای بنر صفحه‌ی «ردیابی اشخاص»).
    detector_status_changed = pyqtSignal()

    # هشدارهای وضعیت پلاک‌خوان فقط یک‌بار در کل برنامه نمایش داده می‌شوند
    # (کلید: متن پیام) تا با چند دوربین، دیالوگ تکراری باز نشود.
    _plate_status_warned = set()

    def __init__(self, on_clicked, on_close_requested, on_double_clicked=None,
                 on_slot_drag_swap=None, on_camera_drag_drop=None, on_region_alert=None,
                 on_fire_event=None, on_plate_event=None, on_person_event=None,
                 parent=None):
        super().__init__(parent)
        self.cam = None
        self.stream_thread = None
        self.latest_raw_frame = None
        # رفع باگ «یک فریم از دوربین قبلی در کادر می‌ماند» هنگام جابه‌جایی با
        # درگ‌انددراپ: سیگنال‌های ترد پخش با اتصال صف‌شده (queued) به ترد GUI
        # می‌رسند؛ بعد از stop() (که ترد را کاملاً متوقف می‌کند) ممکن است
        # هنوز چند رویداد قدیمی در صف رویداد GUI مانده باشد و بعد از start()
        # دوربین جدید، دیرتر تحویل شوند - مثلاً آخرین فریم دوربین قبلی روی
        # خانه‌ای که حالا دوربین دیگری را نشان می‌دهد نقاشی می‌شد. هر start/
        # stop یک شماره‌ی نسل (generation) تازه می‌سازد و همه‌ی اتصال‌های
        # سیگنال فقط رویدادهای هم‌نسل با استریم جاری را قبول می‌کنند.
        self._stream_seq = 0
        # رفع درخواست «هر کادر قابلیت Zoom in/out داشته باشه»: بزرگ‌نمایی
        # دیجیتالِ مستقلِ هر خانه.
        #   _zoom: ضریب بزرگ‌نمایی (1.0 = کل فریم)؛ سقف 8 برابر
        #   _zoom_cx/_zoom_cy: مرکز ناحیه‌ی نمایشی، نرمال 0..1 نسبت به فریم اصلی
        #   _last_zoom_crop: آخرین کراپ اعمال‌شده (x0,y0,cw,ch) به پیکسل فریم -
        #       برای نگاشت موقعیت نشانگر ماوس به مختصات فریم (زوم حول نشانگر + پن)
        self._zoom = 1.0
        self._zoom_cx = 0.5
        self._zoom_cy = 0.5
        self._last_zoom_crop = None
        # بهینه‌سازی سرعت: زمان آخرین به‌روزرسانی تصویر این تایل (برای
        # محدود کردن نرخ نمایش به ~۱۵ فریم‌برثانیه - رجوع کنید به
        # on_frame_ready).
        self._last_display_ts = 0.0
        self._panning = False
        self._pan_button = None
        self._pan_start_label_pos = None
        self._pan_start_center = None
        self._selected = False
        self._on_clicked = on_clicked
        self._on_close_requested = on_close_requested
        # رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار این خانه، این
        # callback (در MainWindow) صدا زده می‌شود تا رویداد در پنل تشخیص
        # چهره هم به‌صورت متنی ثبت شود.
        self._on_region_alert = on_region_alert
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: با هر تشخیص تصویری
        # آتش/دود روی این خانه، این callback (در MainWindow) صدا زده می‌شود
        # تا رویداد در پنل «هشدارهای حریق و دود» ثبت و گزارش شود. نکته: نام
        # این ویژگی عمداً با متد واکنشیِ سیگنال (_on_fire_event پایین‌تر)
        # متفاوت است تا با آن تداخل (override) نکند - دقیقاً مثل تمایز
        # self._on_region_alert (callback) از _on_region_entered (متد).
        self._fire_event_cb = on_fire_event
        # رفع درخواست «سیستم پلاک‌خوان»: با هر پلاک تازه‌تأییدشده روی این خانه،
        # این callback (در MainWindow) صدا زده می‌شود تا عبور (تعریف‌شده/
        # تعریف‌نشده) ثبت و در گزارش عبور نمایش داده شود. نام عمداً با متد
        # واکنشی سیگنال (_on_plate_event پایین‌تر) متفاوت است تا override نشود.
        self._plate_event_cb = on_plate_event
        # رفع درخواست «ردیابی اشخاص»: با هر رد تازه‌تأییدشده/تمام‌شده روی این
        # خانه، این callback (در MainWindow) صدا زده می‌شود تا تطبیق بین
        # دوربینی و ثبت «حضور» در person_store انجام شود.
        self._person_event_cb = on_person_event
        # رفع درخواست: وضعیت روشن/خاموش بودن «شمارش افراد Real Time» برای این
        # خانه؛ چون خانه‌ها هنگام عوض شدن تعداد شبکه (set_grid_size) از نو
        # ساخته می‌شوند، این وضعیت فقط تا وقتی همین خانه/دوربین برقرار است
        # حفظ می‌شود.
        self._people_counting_enabled = False
        # رفع درخواست: با دابل‌کلیک روی تصویر دوربین، این خانه بزرگ‌نمایی
        # می‌شود و با دابل‌کلیک دوباره به اندازه‌ی قبل (چیدمان شبکه‌ای) برمی‌گردد.
        self._on_double_clicked = on_double_clicked
        # رفع درخواست: امکان جابه‌جایی محل نمایش دوربین‌ها با درگ (Drag & Drop) -
        # هم بین دو خانه‌ی شبکه (جابه‌جایی) و هم از لیست دوربین‌ها روی یک خانه
        # (افزودن/جایگزینی). index این خانه در CameraGridWidget.set_grid_size
        # مقداردهی می‌شود.
        self._on_slot_drag_swap = on_slot_drag_swap
        self._on_camera_drag_drop = on_camera_drag_drop
        self.slot_index = None
        self._drag_start_pos = None
        self.setAcceptDrops(True)

        # رفع درخواست: محدوده‌های هشدار (Zone) برای این خانه - جایگزین خط
        # فرضی عبور قبلی. کاربر می‌تواند به تعداد دلخواه محدوده‌ی چندضلعی
        # (نقاط دلخواه روی زمین، نه فقط مستطیل) رسم و نام‌گذاری کند (مثلاً
        # «محدوده ۱ / اتاق سرور»)؛ با ورود هرکسی به هرکدام، کادر این خانه
        # قرمز می‌شود و آلارم صوتی پخش می‌شود.
        #   pending_points: نقاطِ محدوده‌ای که تازه با کلیک‌های متوالی بسته
        #       شده ولی هنوز کاربر نامش را تایید نکرده - با خط‌چین زرد روی
        #       تصویر دیده می‌شود. None یعنی چیزی در انتظار تایید نیست.
        #   regions: لیست محدوده‌های نهایی/فعال؛ هر کدام
        #       {"id","number","name","points"} - همیشه روی تصویر دیده می‌شوند.
        self.pending_points = None
        self.regions = []
        # رفع درخواست: وقتی کاربر از دیالوگ «مدیریت محدوده‌ها» یکی از
        # محدوده‌های قبلاً تایید‌شده را برای «ویرایش شکل/اندازه» باز کرده،
        # id همان محدوده اینجا نگه داشته می‌شود تا با «ذخیره ویرایش» روی
        # همان محدوده به‌جای ساختن یک محدوده‌ی تازه اعمال شود (رجوع کنید به
        # start_edit_region/save_region_edit/cancel_region_edit پایین‌تر).
        # None یعنی الان در حال ویرایش هیچ محدوده‌ی از قبل تایید‌شده‌ای
        # نیستیم (حالت عادی رسم محدوده‌ی تازه).
        self._editing_region_id = None
        self._alarm_active = False
        self._alarm_timer = QTimer(self)
        self._alarm_timer.setSingleShot(True)
        self._alarm_timer.timeout.connect(self._clear_alarm)
        # رفع درخواست: «محدوده رسم می‌شود ولی هشدار نمی‌دهد» بی‌هیچ توضیحی.
        # علتش این بود که کل زنجیره‌ی هشدار به بارگذاری موفق مدل تشخیص شخص
        # (YOLOv8، در person_detector.py) وابسته است و قبلاً وقتی آن مدل
        # بارگذاری نمی‌شد، هیچ نشانه‌ای روی UI دیده نمی‌شد (فقط یک print()
        # که در exe نهایی اصلاً قابل‌دیدن نیست - رجوع کنید به
        # CameraStreamThread.person_detector_status_signal). None یعنی
        # هنوز وضعیت واقعی مشخص نیست (اولین تلاش بارگذاری هنوز انجام
        # نشده)، True/False یعنی نتیجه‌ی همان اولین تلاش.
        self._detector_available = None
        self._detector_error = ""

        # رفع درخواست: ثبت دائمیِ تاریخچه‌ی شمارش نفرات (report_store.py).
        # فقط وقتی عدد نسبت به آخرین باری که ثبت شد تغییر کند لاگ می‌شود
        # (نه هر فریم/هر چند فریم که people_count_signal شلیک می‌شود) تا
        # حجم گزارش منطقی بماند؛ None یعنی هنوز هیچ عددی برای این خانه لاگ
        # نشده (بعد از هر start() تازه دوباره None می‌شود - رجوع کنید به
        # start() پایین‌تر).
        self._last_logged_count = None

        self.setMinimumSize(140, 110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.setSpacing(2)

        header = QHBoxLayout()
        self.name_label = QLabel("خالی")
        self.name_label.setStyleSheet("color:#dddddd; font-size:11px; font-weight:bold;")
        # رفع باگ: وقتی اسم دوربین طولانی است (مثلاً اسم + IP)، در خانه‌های
        # کوچک (چیدمان‌های شلوغ مثل 9/16/32 دوربین) sizeHint کامل متن باعث
        # می‌شد کل عرض هدر را اشغال کند و برچسب تعداد نفرات/دکمه‌ی بستن را از
        # فضای قابل‌مشاهده بیرون براند (بدون خطا، فقط دیده نمی‌شدند). با
        # Ignored از layout می‌خواهیم sizeHint این لیبل را نادیده بگیرد و
        # فضای باقی‌مانده (بعد از رزرو فضای ثابت برای بقیه‌ی ویجت‌های هدر) را
        # به آن بدهد؛ متن هم با «...» بر اساس همان فضای واقعی کوتاه می‌شود
        # (رجوع کنید به _refresh_name_label/resizeEvent) تا چیزی از هدر گم
        # نشود.
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._name_full_text = "خالی"
        # رفع درخواست: نمایش Real Time تعداد افراد شناسایی‌شده، بالای همان
        # پنجره‌ی دوربین (کنار نام دوربین). فقط وقتی شمارش افراد به‌صورت
        # سراسری (یک دکمه‌ی واحد بالای همه‌ی پنجره‌های دوربین، در نوار ابزار
        # وسط - رجوع کنید به MainWindow.people_toggle_btn) روشن باشد مقداری
        # دارد (رجوع کنید به set_people_counting/on_people_count).
        self.people_count_label = QLabel("")
        self.people_count_label.setStyleSheet("color:#f39c12; font-size:11px; font-weight:bold;")
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setStyleSheet("QPushButton{color:#ccc; background:#333; border-radius:9px; padding:0px;}")
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(lambda: self._on_close_requested(self))
        # رفع درخواست «هر کادر قابلیت Zoom in/out داشته باشه»: دکمه‌های
        # بزرگ‌نمایی/کوچک‌نمایی/بازنشانی هر خانه (علاوه بر زوم با اسکرول ماوس
        # روی تصویر و پن با Shift+درگ یا درگ با دکمه‌ی وسط ماوس).
        _zoom_style = ("QPushButton{color:#ccc; background:#333; border-radius:9px; font-size:11px; padding:0px;}"
                       "QPushButton:disabled{color:#666; background:#2a2a2a;}")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedSize(18, 18)
        self.zoom_in_btn.setStyleSheet(_zoom_style)
        self.zoom_in_btn.setToolTip("بزرگ‌نمایی تصویر (می‌توانید با اسکرول ماوس روی تصویر هم زوم کنید)")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedSize(18, 18)
        self.zoom_out_btn.setStyleSheet(_zoom_style)
        self.zoom_out_btn.setToolTip("کوچک‌نمایی تصویر")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.zoom_reset_btn = QPushButton("1:1")
        self.zoom_reset_btn.setFixedSize(26, 18)
        self.zoom_reset_btn.setStyleSheet(_zoom_style)
        self.zoom_reset_btn.setToolTip("بازنشانی بزرگ‌نمایی (نمایش کل فریم)")
        self.zoom_reset_btn.clicked.connect(lambda: self._reset_zoom())
        header.addWidget(self.name_label, 1)
        header.addWidget(self.people_count_label)
        header.addWidget(self.zoom_in_btn)
        header.addWidget(self.zoom_out_btn)
        header.addWidget(self.zoom_reset_btn)
        header.addWidget(self.close_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#888888; font-size:9px;")

        # رفع درخواست: وقتی محدوده‌ی هشدار تعریف شده ولی موتور تشخیص شخص
        # (YOLOv8) بارگذاری نشده - پس هیچ هشداری هرگز صادر نخواهد شد - این
        # پیام به‌جای سکوت کامل، همین‌جا زیر نام دوربین نشان داده می‌شود.
        # رجوع کنید به _refresh_detector_warning.
        self.detector_warn_label = QLabel("")
        self.detector_warn_label.setStyleSheet("color:#e67e22; font-size:9px; font-weight:bold;")
        self.detector_warn_label.setWordWrap(True)
        self.detector_warn_label.setVisible(False)

        self.video_label = VideoDisplayLabel("خالی — برای افزودن دوربین،\nدر لیست سمت چپ دابل‌کلیک کنید")
        self.video_label.setWordWrap(True)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color:#1e1e1e; color:#888888; border-radius:6px; font-size:10px;")
        self.video_label.setMinimumSize(100, 80)
        # رفع باگ: وقتی یک QLabel با setPixmap() یک فریم بزرگ (مثلاً حالت
        # بزرگ‌نمایی‌شده با دابل‌کلیک) نمایش می‌دهد، sizeHint/minimumSizeHint
        # داخلی آن برابر همان اندازه‌ی بزرگ باقی می‌ماند و چیدمان (QGridLayout)
        # دیگر اجازه نمی‌دهد این خانه پس از بازگشت به حالت شبکه‌ای، کوچک شود -
        # همان مشکل «تصویر دوربین بعد از دابل‌کلیک دوم به اندازه‌ی قبل برنمی‌گردد».
        # با Ignored، چیدمان این sizeHint را نادیده می‌گیرد و صرفاً فضای واقعی
        # داده‌شده به خانه را ملاک قرار می‌دهد.
        self.video_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video_label.region_drawn.connect(self._on_region_drawn)
        # برای زوم با اسکرول و پن (Shift+درگ / درگ با دکمه‌ی وسط) روی تصویر،
        # رویدادهای لیبل نمایش از همین‌جا رهگیری می‌شوند - رجوع کنید به eventFilter.
        self.video_label.installEventFilter(self)
        self._refresh_zoom_buttons()

        outer.addLayout(header)
        outer.addWidget(self.status_label)
        outer.addWidget(self.detector_warn_label)
        outer.addWidget(self.video_label, 1)
        self._apply_frame_style()

    # ---------------------------------------------------------- selection --

    def _apply_frame_style(self):
        # رفع درخواست: وقتی آلارم عبور از خط فرضی فعال است، کادر دور خانه‌ی
        # دوربین قرمز و ضخیم‌تر می‌شود - با اولویت بالاتر از رنگ انتخاب‌شدن.
        if self._alarm_active:
            border = "3px solid #e74c3c"
        elif self._selected:
            border = "2px solid #3498db"
        else:
            border = "1px solid #3a3a3a"
        self.setStyleSheet(f"CameraSlotWidget {{ border: {border}; border-radius: 8px; background-color: #262626; }}")

    # ----------------------------------------------------- محدوده‌ی هشدار --

    def set_draw_mode(self, enabled: bool):
        # مختصات رسم محدوده نرمالِ کل فریم است؛ اگر زوم فعال باشد ابتدا به
        # حالت عادی برمی‌گردیم تا محدوده سر جای درستش رسم شود.
        if enabled:
            self._reset_zoom()
        self.video_label.set_draw_mode(enabled)
        self._refresh_zoom_buttons()

    def is_draw_mode(self) -> bool:
        return self.video_label.draw_mode

    def has_pending_region(self) -> bool:
        return self.pending_points is not None

    def has_any_region(self) -> bool:
        return bool(self.regions) or self.pending_points is not None

    def _on_region_drawn(self, norm_points):
        self.pending_points = list(norm_points)
        self.video_label.set_pending_points_norm(self.pending_points)
        self.tripwire_changed.emit()

    def confirm_region(self, name: str):
        """رفع درخواست: تایید و نام‌گذاری محدوده‌ی تازه‌بسته‌شده. محدوده به
        لیست محدوده‌های فعال این خانه اضافه و بلافاصله روی ترد پخش برای
        تشخیص ورود فعال می‌شود. دیکشنری محدوده‌ی تازه (برای ذخیره در
        camera_store) یا None برمی‌گرداند اگر نقاطی در انتظار تایید نبود."""
        if self.pending_points is None:
            return None
        region = {
            "id": str(uuid.uuid4()),
            "number": len(self.regions) + 1,
            "name": (name or "").strip(),
            "points": list(self.pending_points),
        }
        self.regions.append(region)
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions(self.regions)
        self.video_label.set_draw_mode(False)
        if self.stream_thread is not None:
            self.stream_thread.set_regions(self.regions)
        self._refresh_detector_warning()
        self.tripwire_changed.emit()
        return region

    def cancel_pending_region(self):
        """رفع درخواست: لغو نقاط در حال رسم/در انتظار نام‌گذاری، بدون هیچ
        تاثیری روی محدوده‌های قبلاً تایید‌شده."""
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()

    def is_editing_region(self) -> bool:
        """آیا همین الان در حال ویرایش شکل/اندازه‌ی یک محدوده‌ی قبلاً
        تایید‌شده هستیم؟ (رجوع کنید به start_edit_region)."""
        return self._editing_region_id is not None

    def start_edit_region(self, region_id) -> bool:
        """رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: محدوده‌ی
        تایید‌شده با شناسه‌ی region_id را به‌عنوان پیش‌نمایشِ سبزِ قابل‌کشیدن
        روی تصویر می‌گذارد (موقتاً از فهرست محدوده‌های ثابت/آبی بیرون
        می‌آید تا دوبار دیده نشود) تا کاربر گوشه‌هایش را با ماوس جابه‌جا/
        اضافه/حذف کند؛ نتیجه با save_region_edit ذخیره یا با
        cancel_region_edit لغو می‌شود. اگر چیزی در انتظار رسم/ویرایشِ
        دیگری باشد یا محدوده پیدا نشود، False برمی‌گرداند."""
        if self.pending_points is not None:
            return False
        region = next((r for r in self.regions if r["id"] == region_id), None)
        if region is None:
            return False
        self._editing_region_id = region_id
        self.pending_points = list(region["points"])
        visible = [r for r in self.regions if r["id"] != region_id]
        self.video_label.set_confirmed_regions(visible)
        self.video_label.set_pending_points_norm(self.pending_points, editing_existing=True)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()
        return True

    def save_region_edit(self):
        """رفع درخواست: شکل/اندازه‌ی ویرایش‌شده (نقاطِ فعلیِ روی video_label،
        شاملِ هر جابه‌جایی/افزودن/حذف گوشه‌ای که کاربر انجام داده) را روی
        همان محدوده‌ی قبلاً تایید‌شده می‌نویسد و بلافاصله روی ترد پخش هم
        برای تشخیص ورود به‌روز می‌کند. دیکشنری محدوده‌ی به‌روزشده (برای
        ذخیره در camera_store) یا None برمی‌گرداند (اگر در حال ویرایش
        نبودیم یا کمتر از ۳ گوشه باقی مانده بود - عملاً غیرممکن چون خودِ
        VideoDisplayLabel اجازه‌ی حذف گوشه‌ی چهارم به بعد را فقط تا حداقل ۳
        گوشه می‌دهد)."""
        if self._editing_region_id is None:
            return None
        points = self.video_label.pending_points_norm()
        if not points or len(points) < 3:
            return None
        region = None
        for r in self.regions:
            if r["id"] == self._editing_region_id:
                r["points"] = list(points)
                region = r
                break
        self._editing_region_id = None
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions(self.regions)
        if self.stream_thread is not None:
            self.stream_thread.set_regions(self.regions)
        self.tripwire_changed.emit()
        return region

    def cancel_region_edit(self):
        """رفع درخواست: انصراف از ویرایشِ در حال انجام - محدوده به شکل/
        اندازه‌ی قبلی (قبل از شروع ویرایش) دست‌نخورده برمی‌گردد، چون
        start_edit_region هیچ تغییری روی خودِ self.regions اعمال نکرده
        بود (فقط یک کپی برای پیش‌نمایش ساخته بود)."""
        self._editing_region_id = None
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions(self.regions)
        self.tripwire_changed.emit()

    def start_auto_full_frame_region(self) -> bool:
        """رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش
        رو محدوده محسوب کنه»: چون این برنامه به مدلی برای تشخیص دقیقِ کفِ
        زمین (Ground/Floor Segmentation - جدا از تشخیص شخص) دسترسی ندارد،
        این حالت کل کادر تصویر زنده‌ی همین دوربین را - که در عمل تقریباً
        معادل «کل زمینی است که دوربین می‌بیند» - به‌عنوان یک محدوده‌ی تازه و
        در انتظار تایید می‌گذارد؛ دقیقاً مثل یک محدوده‌ی دستی‌رسم‌شده، کاملاً
        قابل ویرایش است (کاربر می‌تواند مثلاً گوشه‌ای را عقب بکشد تا یک در
        ورودی یا راهرو را از محدوده کنار بگذارد) و در پایان با همان دکمه‌ی
        «تایید و نام‌گذاری» یا «لغو رسم» ذخیره/لغو می‌شود. اگر چیزی در حال
        رسم/ویرایش دیگری باشد، False برمی‌گرداند."""
        if self.pending_points is not None or self._editing_region_id is not None:
            return False
        # کمی فاصله از لبه‌ی دقیق کادر (۰ و ۱) تا دسته‌های چهارگوشه کاملاً
        # داخل تصویر و به‌راحتی با ماوس قابل‌گرفتن باشند.
        inset = 0.015
        points = [
            (inset, inset), (1 - inset, inset),
            (1 - inset, 1 - inset), (inset, 1 - inset),
        ]
        self.pending_points = points
        self.video_label.set_pending_points_norm(points, editing_existing=False)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()
        return True

    def start_ai_floor_region(self, points_norm) -> bool:
        """رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش
        رو محدوده محسوب کنه ولی بازم قابل ادیت باشه، دقیق‌تر با مدل هوش
        مصنوعی (Segmentation)»: برخلاف start_auto_full_frame_region (که
        همیشه ۴ گوشه‌ی ثابتِ کل کادر را می‌گذاشت چون مدلی در دسترس نبود)،
        اینجا points_norm همان چندضلعیِ واقعاً تشخیص‌داده‌شده‌ی سطح زمین است
        (رجوع کنید به floor_detector.FloorDetector.detect_polygon، صدا
        زده‌شده از MainWindow._on_ai_floor_detect_finished). دقیقاً مثل هر
        محدوده‌ی دیگر - چه دستی‌رسم‌شده چه خودکارِ کل تصویر - قبل از تایید
        کاملاً با ماوس قابل ویرایش (جابه‌جایی/افزودن/حذف گوشه) است؛ کاربر
        می‌تواند مثلاً گوشه‌ای را که اشتباهی روی یک فرش/سایه رفته عقب بکشد.
        اگر چیزی در حال رسم/ویرایش دیگری باشد یا کمتر از ۳ نقطه داده شده
        باشد، False برمی‌گرداند."""
        if self.pending_points is not None or self._editing_region_id is not None:
            return False
        if not points_norm or len(points_norm) < 3:
            return False
        points = [tuple(p) for p in points_norm]
        self.pending_points = points
        self.video_label.set_pending_points_norm(points, editing_existing=False)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()
        return True

    def remove_region(self, region_id):
        """حذف یک محدوده‌ی مشخص با شناسه‌اش (از پنل مدیریت محدوده‌ها) و
        شماره‌گذاری مجدد بقیه. لیست محدوده‌های باقی‌مانده را برمی‌گرداند."""
        self.regions = [r for r in self.regions if r["id"] != region_id]
        for i, r in enumerate(self.regions, start=1):
            r["number"] = i
        self.video_label.set_confirmed_regions(self.regions)
        if self.stream_thread is not None:
            self.stream_thread.set_regions(self.regions)
        self._refresh_detector_warning()
        self.tripwire_changed.emit()
        return list(self.regions)

    def set_regions_silent(self, regions):
        """بارگذاری محدوده‌های از قبل ذخیره‌شده (هنگام باز شدن دوربین) - از
        همان لحظه هم روی تصویر دیده می‌شوند و هم روی ترد پخش برای تشخیص
        ورود فعال می‌شوند."""
        self.regions = [dict(r) for r in (regions or [])]
        self.video_label.set_confirmed_regions(self.regions)
        if self.stream_thread is not None and self.regions:
            self.stream_thread.set_regions(self.regions)
        self._refresh_detector_warning()
        self.tripwire_changed.emit()

    def _on_region_entered(self, number, name):
        # رفع درخواست: با هر ورود، کادر قرمز می‌شود، آلارم صوتی پخش می‌شود
        # و پیام «ورود به محدوده شماره N / نام» زیر نام دوربین نمایش داده
        # می‌شود؛ تایمر با هر ورود تازه ریست می‌شود تا قرمزی/پیام حداقل چند
        # ثانیه بماند (نه فقط یک لحظه‌ی محو).
        label = f"شماره {number}" + (f" / {name}" if name else "")
        self._alarm_active = True
        self._apply_frame_style()
        self.status_label.setText(f"⚠ ورود به محدوده {label}")
        _play_alarm_beep()
        self._alarm_timer.start(4000)
        if self._on_region_alert is not None and self.cam is not None:
            # رفع درخواست «گزارش‌ها روی NVR ضبط بشه»: کل cam پاس داده می‌شود
            # تا nvr_id/channel هم در on_region_alert در دسترس باشد.
            self._on_region_alert(self.cam, number, name)

    def _on_fire_event(self, kind: str, crop_frame, confidence: float):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: دقیقاً همان الگوی
        _on_region_entered بالا - کادر خانه قرمز و ضخیم می‌شود، آلارم صوتی
        پخش می‌شود و پیام روی همین خانه نمایش داده می‌شود؛ به‌علاوه رویداد به
        MainWindow (پنل «هشدارهای حریق و دود» + ثبت دائمی در گزارش‌ها) هم
        اطلاع داده می‌شود."""
        label = "🔥 آتش" if kind == "fire" else "💨 دود"
        self._alarm_active = True
        self._apply_frame_style()
        self.status_label.setText(f"⚠ تشخیص {label} ({confidence * 100:.0f}%)")
        _play_alarm_beep()
        self._alarm_timer.start(4000)
        if self._fire_event_cb is not None and self.cam is not None:
            self._fire_event_cb(self.cam, kind, crop_frame, confidence)

    def _on_detector_status(self, available: bool, error_msg: str):
        """رفع درخواست: بعد از اولین تلاش (موفق یا ناموفق) برای بارگذاری
        مدل تشخیص شخص، این متد صدا زده می‌شود (رجوع کنید به
        CameraStreamThread.person_detector_status_signal). فقط وقتی این
        خانه محدوده‌ی هشدار هم داشته باشد پیام نشان داده می‌شود - چون تا
        وقتی محدوده‌ای تعریف نشده، در دسترس نبودن تشخیص شخص برای کاربر
        این خانه بی‌اهمیت است."""
        self._detector_available = bool(available)
        self._detector_error = error_msg or ""
        self._refresh_detector_warning()
        # بنر صفحه‌ی «ردیابی اشخاص» هم باید از این وضعیت باخبر شود.
        try:
            self.detector_status_changed.emit()
        except Exception:
            pass

    def _refresh_detector_warning(self):
        if self._detector_available is False and self.regions:
            msg = "⚠ تشخیص شخص غیرفعال است؛ هشدار ورود به محدوده کار نمی‌کند"
            if self._detector_error:
                msg += f" ({self._detector_error})"
            self.detector_warn_label.setText(msg)
            self.detector_warn_label.setToolTip(
                "برای رفع این مشکل: مطمئن شوید کتابخانه‌ی ultralytics نصب است و فایل "
                "وزن مدل (yolov8n.pt) در دسترس است (کنار برنامه یا با اتصال اینترنت "
                "برای دانلود یک‌بار). اگر از نسخه‌ی exe پرتابل استفاده می‌کنید، بررسی "
                "کنید مرحله‌ی دانلود/بسته‌بندی این فایل در بیلد گیت‌هاب موفق بوده است."
            )
            self.detector_warn_label.setVisible(True)
        else:
            self.detector_warn_label.setVisible(False)

    def _clear_alarm(self):
        self._alarm_active = False
        self._apply_frame_style()
        # بعد از پایان قرمزی، اگر وضعیت هنوز پیام هشدار را نشان می‌دهد،
        # دوباره به متن وضعیت عادی (متصل/خالی) برمی‌گردد.
        if self.status_label.text().startswith("⚠"):
            if self.stream_thread is not None and self.stream_thread.isRunning():
                self.status_label.setText("متصل - پخش زنده")
            else:
                self.status_label.setText("")

    def _set_name_text(self, full_text: str):
        """متن کامل اسم دوربین را نگه می‌دارد و نسخه‌ی کوتاه‌شده (بر اساس
        عرض واقعی فعلیِ لیبل) را نمایش می‌دهد. تولتیپ همیشه متن کامل را
        نشان می‌دهد تا حتی وقتی کوتاه شده، اطلاعات کامل (اسم + IP) در دسترس
        بماند."""
        self._name_full_text = full_text
        self.name_label.setToolTip(full_text)
        self._refresh_name_label()

    def _refresh_name_label(self):
        fm = QFontMetrics(self.name_label.font())
        avail_w = max(20, self.name_label.width())
        elided = fm.elidedText(self._name_full_text, Qt.TextElideMode.ElideRight, avail_w)
        self.name_label.setText(elided)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_name_label()

    def set_selected(self, value: bool):
        self._selected = value
        self._apply_frame_style()

    # ------------------------------------------------------ people count --

    def set_people_counting(self, enabled: bool):
        """رفع درخواست: شمارش افراد دیگر دکمه‌ی جداگانه برای هر خانه ندارد؛
        این متد از بیرون (CameraGridWidget، بر اساس همان یک دکمه‌ی سراسری
        بالای شبکه‌ی دوربین‌ها) صدا زده می‌شود تا شمارش برای این خانه هم
        روشن/خاموش شود."""
        self._people_counting_enabled = enabled
        if self.stream_thread is not None:
            self.stream_thread.set_people_counting(enabled)
        if enabled:
            # رفع باگ «روشن کردم ولی چیزی نمایش داده نشد»: قبلاً بعد از روشن
            # کردن، تا رسیدن اولین چرخه‌ی واقعی شمارش برچسب کاملاً خالی
            # می‌ماند. حالا بلافاصله یک وضعیت موقت نمایش داده می‌شود تا
            # مشخص باشد شمارش واقعاً فعال شده و فقط منتظر اولین نتیجه هستیم
            # (که چون از همان تشخیص چهره‌ی همیشه‌فعال می‌آید، معمولاً خیلی
            # سریع می‌رسد).
            self.people_count_label.setText("👤 در حال شمارش...")
        else:
            self.people_count_label.setText("")

    def set_plate_detection(self, enabled: bool):
        """رفع درخواست «سیستم پلاک‌خوان»: روشن/خاموش کردن زنده‌ی پلاک‌خوان
        برای دوربینِ همین خانه (از صفحه‌ی «پلاک‌خوان» تب «تعریف پلاک‌ها»)؛
        روی ترد پخشِ جاری اعمال می‌شود و در cam ذخیره نمی‌شود (ذخیره با
        camera_store.update_camera در همان صفحه انجام شده)."""
        if self.stream_thread is not None:
            self.stream_thread.set_plate_detection(enabled)

    def set_person_tracking(self, enabled: bool):
        """رفع درخواست «ردیابی اشخاص»: روشن/خاموش کردن زنده‌ی ردیابی اشخاص
        برای دوربینِ همین خانه (از صفحه‌ی «ردیابی اشخاص» تب «اشخاص»)؛
        روی ترد پخشِ جاری اعمال می‌شود و در cam ذخیره نمی‌شود (ذخیره با
        camera_store.update_camera در همان صفحه انجام شده)."""
        if self.stream_thread is not None:
            self.stream_thread.set_person_tracking(enabled)

    def on_people_count(self, count):
        # رفع باگ «کسی تو اتاقه ولی صفر نشون می‌داد»: این عدد از تشخیص‌دهنده‌ی
        # شخص/بدن کامل (PersonDetector در person_detector.py) می‌آید که همه‌ی
        # حالت‌های بدن (ایستاده، نشسته، نیم‌خیز، پشت به دوربین و ...) را
        # پوشش می‌دهد و نیازی به دیدن چهره ندارد؛ فقط اگر آن تشخیص‌دهنده در
        # دسترس نباشد، به شمارش بر پایه‌ی چهره برمی‌گردد - رجوع کنید به
        # CameraStreamThread.run().
        self.people_count_label.setText(f"👤 {count} نفر")

        # رفع درخواست: ثبت دائمی تاریخچه‌ی شمارش نفرات با ساعت/تاریخ روی
        # سیستمی که برنامه رویش اجراست (report_store.py) - فقط وقتی عدد
        # واقعاً نسبت به آخرین ثبت تغییر کرده باشد (وگرنه با نرخ فریم/چند
        # فریمِ people_count_signal، دیتابیس بی‌دلیل غرق می‌شد).
        if self.cam is not None and count != self._last_logged_count:
            self._last_logged_count = count
            report_store.log_person_count(
                self.cam.get("name", ""), count,
                nvr_id=self.cam.get("nvr_id"), channel=self.cam.get("channel"),
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        self._on_clicked(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # رفع درخواست: با گرفتن و کشیدن (درگ) یک خانه‌ی دارای دوربین، محل
        # نمایش آن با خانه‌ی مقصد جابه‌جا می‌شود (رجوع کنید به dropEvent و
        # CameraGridWidget.swap_slots).
        if (
            self.cam is not None
            and self._drag_start_pos is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData("application/x-ias-slot-index", str(self.slot_index).encode("utf-8"))
            drag.setMimeData(mime)
            pixmap = self.video_label.pixmap()
            if pixmap is not None and not pixmap.isNull():
                drag.setPixmap(pixmap.scaled(96, 72, Qt.AspectRatioMode.KeepAspectRatio))
            self._drag_start_pos = None
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        # رفع درخواست: دابل‌کلیک روی تصویر دوربین، بزرگ/کوچک‌نمایی (toggle) را
        # فعال می‌کند - منطق واقعیِ چیدمان در CameraGridWidget.toggle_maximize است.
        if self._on_double_clicked is not None:
            self._on_double_clicked(self)
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------- drag & drop --

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("application/x-ias-slot-index") or md.hasFormat("application/x-ias-camera-id"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("application/x-ias-slot-index"):
            try:
                src_index = int(bytes(md.data("application/x-ias-slot-index")).decode("utf-8"))
            except (TypeError, ValueError):
                return
            if self._on_slot_drag_swap is not None:
                self._on_slot_drag_swap(src_index, self.slot_index)
            event.acceptProposedAction()
        elif md.hasFormat("application/x-ias-camera-id"):
            cam_id = bytes(md.data("application/x-ias-camera-id")).decode("utf-8")
            if self._on_camera_drag_drop is not None:
                self._on_camera_drag_drop(cam_id, self.slot_index)
            event.acceptProposedAction()

    # --------------------------------------------------------------- start -

    def _guarded_connect(self, seq):
        """سازنده‌ی اتصال سیگنالِ محافظت‌شده با شماره‌ی نسل استریم: هندلر فقط
        وقتی صدا زده می‌شود که seq برابر نسل جاری همین خانه باشد؛ رویدادهای
        جامانده‌ی استریم قبلی (که در صف GUI گیر کرده‌اند) بی‌صدا نادیده گرفته
        می‌شوند - رجوع کنید به توضیح self._stream_seq در __init__."""
        def connect(signal, handler):
            signal.connect(
                lambda *args, _s=seq, _h=handler: _h(*args) if _s == self._stream_seq else None
            )
        return connect

    def start(self, cam: dict, rtsp_url: str, face_engine: FaceEngine, face_event_cb,
              plate_event_cb=None, person_event_cb=None):
        self._stream_seq += 1
        _seq = self._stream_seq
        _conn = self._guarded_connect(_seq)
        self._reset_zoom()
        self.cam = cam
        # نمایش اسم دوربین همراه با IP (کنار هم، جلوی شمارش افراد در همین
        # هدر). اگر کاربر برای دوربین اسمی وارد نکرده باشد، cam["name"] از
        # قبل برابر همان IP است (رجوع کنید به camera_store.py) که در این حالت
        # از تکرار IP در پرانتز جلوگیری می‌شود.
        cam_name = cam.get("name") or cam.get("ip", "")
        cam_ip = cam.get("ip", "")
        if cam_ip and cam_ip != cam_name:
            self._set_name_text(f"{cam_name} ({cam_ip})")
        else:
            self._set_name_text(cam_name)
        self.close_btn.setVisible(True)
        self.status_label.setText("در حال اتصال...")
        self.video_label.setText("در انتظار تصویر...")

        self.stream_thread = CameraStreamThread(rtsp_url, face_engine, process_every_n=_PROCESS_EVERY_N)
        # ناحیه‌ی زوم فعلی (اگر هست) به پلاک‌خوان ترد تازه اعلام می‌شود
        self._sync_plate_roi()
        # همه‌ی اتصال‌ها محافظت‌شده با نسل استریم‌اند تا رویدادهای جامانده‌ی
        # استریم قبلی همین خانه (در صف GUI) به اشتباه روی دوربین جدید اعمال
        # نشوند - رجوع کنید به _guarded_connect/_stream_seq.
        _conn(self.stream_thread.frame_ready, self.on_frame_ready)
        _conn(self.stream_thread.error_signal, self.on_error)
        _conn(self.stream_thread.connected_signal, self.on_connected)
        _conn(self.stream_thread.people_count_signal, self.on_people_count)
        _conn(self.stream_thread.region_entered, self._on_region_entered)
        _conn(self.stream_thread.person_detector_status_signal, self._on_detector_status)
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»
        _conn(self.stream_thread.fire_event_signal, self._on_fire_event)
        self._last_logged_count = None
        # با هر بازِ جدید (دوربین تازه در همین خانه)، وضعیت تشخیص شخص قبلی
        # (اگر مربوط به دوربین قبلی این خانه بوده) پاک می‌شود تا وضعیت
        # واقعی دوربین جدید (بعد از اولین دور تشخیصش) دوباره از صفر تعیین
        # شود - نه اینکه هشدار نادرست از پخش قبلی روی صفحه بماند.
        self._detector_available = None
        self._detector_error = ""
        self._refresh_detector_warning()
        # رفع درخواست «گزارش‌ها روی NVR ضبط بشه»: cam (کل دیکشنری دوربین، نه
        # فقط اسمش) پاس داده می‌شود تا on_face_event بتواند nvr_id/channel را
        # هم برای لینک «پخش ویدیوی NVR» در دیالوگ گزارش‌ها ثبت کند.
        # مثل بقیه‌ی سیگنال‌ها با محافظ نسل وصل می‌شود تا رویداد چهره‌ی ترد
        # قبلی بعد از جابه‌جایی دوربین به کادر جدید نرسد.
        _conn(
            self.stream_thread.face_event_signal,
            lambda person, crop: face_event_cb(cam, person, crop),
        )
        # رفع درخواست «سیستم پلاک‌خوان»: مثل face_event با محافظ نسل وصل
        # می‌شود تا رویداد پلاکِ ترد قبلی به دوربین جدید نرسد؛ cam (کل
        # دیکشنری دوربین، نه فقط اسمش) پاس داده می‌شود تا نام دوربین و
        # nvr_id/channel هم در گزارش عبور ثبت شود.
        if plate_event_cb is not None:
            _conn(
                self.stream_thread.plate_event_signal,
                lambda data: plate_event_cb(cam, data),
            )
        # وضعیت پلاک‌خوان (مثلاً «موتور OCR نصب نیست»): یک‌بار به کاربر
        # اطلاع داده می‌شود تا باکس خالیِ بی‌صدا نماند.
        _conn(
            self.stream_thread.plate_detector_status_signal,
            lambda available, msg: self._on_plate_detector_status(cam, available, msg),
        )
        # پلاک‌خوان این دوربین از همان تنظیم ذخیره‌شده (cam["plate_detection"])
        # فعال می‌شود - رجوع کنید به صفحه‌ی «پلاک‌خوان» تب «تعریف پلاک‌ها».
        self.stream_thread.set_plate_detection(bool(cam.get("plate_detection")))
        # رفع درخواست «ردیابی اشخاص»: مثل plate_event با محافظ نسل وصل
        # می‌شود تا رویداد ردِ ترد قبلی به دوربین جدید نرسد؛ cam (کل
        # دیکشنری دوربین) پاس داده می‌شود تا نام دوربین و nvr_id/channel در
        # گزارش مسیر حرکت ثبت شود.
        if person_event_cb is not None:
            _conn(
                self.stream_thread.person_event_signal,
                lambda data: person_event_cb(cam, data),
            )
        # ردیابی اشخاص این دوربین از همان تنظیم ذخیره‌شده
        # (cam["person_tracking"]) فعال می‌شود - رجوع کنید به صفحه‌ی
        # «ردیابی اشخاص» تب «اشخاص ردیابی‌شده».
        self.stream_thread.set_person_tracking(bool(cam.get("person_tracking")))
        self.stream_thread.start()
        # بنر صفحه‌ی «ردیابی اشخاص» را هم تازه کن (می‌شود «در حال آماده‌سازی»).
        try:
            self.detector_status_changed.emit()
        except Exception:
            pass
        # رفع درخواست: اگر برای این دوربین قبلاً محدوده‌های هشدار رسم و
        # ذخیره شده باشند (cameras.json)، همان لحظه‌ی اتصال دوباره روی ترد
        # پخش تازه فعال می‌شوند.
        saved_regions = cam.get("regions")
        if saved_regions:
            self.set_regions_silent(saved_regions)
        # اگر شمارش افراد به‌صورت سراسری (دکمه‌ی بالای شبکه‌ی دوربین‌ها) از قبل
        # روشن بوده، روی ترد پخش جدید هم بلافاصله اعمال می‌شود (رجوع کنید به
        # CameraGridWidget که بلافاصله بعد از start() هم set_people_counting
        # را صدا می‌زند؛ این خط فقط برای اطمینان از سازگاری با وضعیت فعلیِ
        # همین خانه است).
        if self._people_counting_enabled:
            self.stream_thread.set_people_counting(True)
        # پروفایل تصویر ذخیره‌شده‌ی این دوربین (اگر قبلاً تنظیم شده) روی ترد
        # تازه اعمال می‌شود - رجوع کنید به image_profile.py.
        self._apply_saved_image_profile()
        self._refresh_zoom_buttons()

    def on_connected(self):
        self.status_label.setText("متصل - پخش زنده")

    def on_error(self, msg):
        self.status_label.setText(f"خطا: {msg}")

    def _on_plate_detector_status(self, cam, available, msg):
        """نمایش یک‌باره‌ی هشدار وضعیت پلاک‌خوان (مثلاً «موتور OCR نصب نیست»)."""
        if not msg or msg in CameraSlotWidget._plate_status_warned:
            return
        CameraSlotWidget._plate_status_warned.add(msg)
        cam_name = ""
        if isinstance(cam, dict):
            cam_name = cam.get("name") or cam.get("ip", "")
        QMessageBox.warning(
            self, "پلاک‌خوان",
            f"دوربین «{cam_name}»: {msg}" if cam_name else msg)

    def on_frame_ready(self, display_frame, raw_frame):
        self.latest_raw_frame = raw_frame
        # بهینه‌سازی سرعت (۱): هر تایل حداکثر ~۱۵ فریم‌برثانیه به‌روز می‌شود.
        # چشم انسان در کاشی‌های کوچک نظارتی فرق ۱۵ با ۲۵ فریم را حس نمی‌کند،
        # ولی تبدیل/مقیاس هر فریم در ترد UI پرهزینه است.
        now = time.monotonic()
        if now - self._last_display_ts < 1.0 / 15.0:
            return
        self._last_display_ts = now
        show_frame = display_frame
        if self._zoom > 1.0:
            show_frame = self._zoom_crop(display_frame)
        # بهینه‌سازی سرعت (۲): به‌جای تبدیل BGR→RGB روی فریم کامل ۱۰۸۰p و
        # بعد مقیاس نرم‌افزاری پرهزینه در Qt، اول با OpenCV (سریع) به اندازه‌ی
        # خودِ لیبل کوچک می‌کنیم و بعد تبدیل می‌کنیم - حدود ۱۰ برابر ارزان‌تر.
        try:
            lw, lh = self.video_label.width(), self.video_label.height()
        except Exception:
            lw, lh = 0, 0
        if lw > 0 and lh > 0:
            h, w = show_frame.shape[:2]
            _scale = min(lw / w, lh / h)
            if _scale < 1.0:
                _nw, _nh = max(1, int(w * _scale)), max(1, int(h * _scale))
                show_frame = cv2.resize(show_frame, (_nw, _nh),
                                        interpolation=cv2.INTER_LINEAR)
        pixmap = _bgr_to_pixmap(show_frame)
        if pixmap is None:
            return
        # چون فریم از قبل به اندازه‌ی لیبل (با حفظ نسبت) کوچک شده، دیگر
        # scaled() لازم نیست - مستقیم نمایش داده می‌شود.
        self.video_label.setPixmap(pixmap)

    # ------------------------------------------------- zoom in/out (هر کادر) --
    _ZOOM_MIN = 1.0
    _ZOOM_MAX = 8.0
    _ZOOM_STEP = 1.25

    def _reset_zoom(self):
        self._zoom = 1.0
        self._zoom_cx = 0.5
        self._zoom_cy = 0.5
        self._last_zoom_crop = None
        self._panning = False
        self._pan_button = None
        self._refresh_zoom_buttons()
        self._sync_plate_roi()

    def _clamp_zoom_center(self):
        half = 0.5 / self._zoom
        self._zoom_cx = min(max(self._zoom_cx, half), 1.0 - half)
        self._zoom_cy = min(max(self._zoom_cy, half), 1.0 - half)

    def _zoom_crop(self, frame):
        """کراپ ناحیه‌ی نمایشی از فریم بر اساس ضریب و مرکز زوم؛ ابعاد کراپ هم
        در _last_zoom_crop نگه داشته می‌شود تا نگاشت ماوس→فریم دقیق باشد."""
        h, w = frame.shape[:2]
        cw, ch = w / self._zoom, h / self._zoom
        cx = min(max(self._zoom_cx * w, cw / 2.0), w - cw / 2.0)
        cy = min(max(self._zoom_cy * h, ch / 2.0), h - ch / 2.0)
        x0, y0 = int(cx - cw / 2.0), int(cy - ch / 2.0)
        cw_i, ch_i = max(1, int(cw)), max(1, int(ch))
        self._last_zoom_crop = (x0, y0, cw_i, ch_i)
        return frame[y0:y0 + ch_i, x0:x0 + cw_i]

    def _label_pos_to_frame_norm(self, label_pos):
        """موقعیت نشانگر (پیکسل لیبل) به مختصات نرمال 0..1 فریم اصلی - با در
        نظر گرفتن کراپِ زومِ جاری."""
        rect = self.video_label._frame_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        if self.latest_raw_frame is None:
            return None
        h, w = self.latest_raw_frame.shape[:2]
        px = (label_pos.x() - rect.x()) / rect.width()
        py = (label_pos.y() - rect.y()) / rect.height()
        if self._last_zoom_crop is None:
            return (min(max(px, 0.0), 1.0), min(max(py, 0.0), 1.0))
        x0, y0, cw, ch = self._last_zoom_crop
        fx = (x0 + px * cw) / w
        fy = (y0 + py * ch) / h
        return (min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0))

    def _zoom_at(self, focus_norm, factor):
        """زوم حول یک نقطه‌ی ثابت از فریم (نرمال 0..1) - نقطه‌ی زیر نشانگر
        سر جایش می‌ماند."""
        if self.cam is None or self.is_draw_mode():
            return
        new_zoom = min(self._ZOOM_MAX, max(self._ZOOM_MIN, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        fx, fy = focus_norm
        r = self._zoom / new_zoom
        self._zoom_cx = fx - (fx - self._zoom_cx) * r
        self._zoom_cy = fy - (fy - self._zoom_cy) * r
        self._zoom = new_zoom
        self._clamp_zoom_center()
        self._refresh_zoom_buttons()
        self._sync_plate_roi()

    def zoom_in(self):
        self._zoom_at((self._zoom_cx, self._zoom_cy), self._ZOOM_STEP)

    def zoom_out(self):
        self._zoom_at((self._zoom_cx, self._zoom_cy), 1.0 / self._ZOOM_STEP)

    def _plate_roi_norm(self):
        """ناحیه‌ی نمایشیِ زوم به‌صورت نرمال 0..1 نسبت به فریم کامل؛
        None یعنی کل فریم (بدون زوم). ریاضیات دقیقاً آینه‌ی _zoom_crop است."""
        if self._zoom <= self._ZOOM_MIN + 1e-9:
            return None
        cw_n = ch_n = 1.0 / self._zoom
        cx = min(max(self._zoom_cx, cw_n / 2.0), 1.0 - cw_n / 2.0)
        cy = min(max(self._zoom_cy, ch_n / 2.0), 1.0 - ch_n / 2.0)
        return (cx - cw_n / 2.0, cy - ch_n / 2.0,
                cx + cw_n / 2.0, cy + ch_n / 2.0)

    def _sync_plate_roi(self):
        """اعلام ناحیه‌ی زوم به ترد استریم تا پلاک‌خوان همان ناحیه را با
        رزولوشن کامل سنسور تشخیص/OCR بدهد (نه فقط نمایش بزرگ‌شده)."""
        try:
            th = getattr(self, "stream_thread", None)
            if th is not None and hasattr(th, "set_plate_roi"):
                th.set_plate_roi(self._plate_roi_norm())
        except Exception:
            pass

    def _refresh_zoom_buttons(self):
        has_cam = self.cam is not None
        drawing = self.is_draw_mode()
        self.zoom_in_btn.setEnabled(has_cam and not drawing and self._zoom < self._ZOOM_MAX)
        self.zoom_out_btn.setEnabled(has_cam and not drawing and self._zoom > self._ZOOM_MIN)
        self.zoom_reset_btn.setEnabled(has_cam and not drawing and self._zoom > self._ZOOM_MIN)
        self.zoom_reset_btn.setToolTip(
            f"بازنشانی بزرگ‌نمایی (فعلی: {self._zoom:.1f}×)"
            if self._zoom > self._ZOOM_MIN else "بازنشانی بزرگ‌نمایی (نمایش کل فریم)"
        )

    def _pan_to(self, label_pos):
        """جابه‌جایی مرکز زوم بر اساس درگ ماوس (پن) - محتوا دنبال نشانگر می‌آید."""
        rect = self.video_label._frame_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return
        if self.latest_raw_frame is None or self._last_zoom_crop is None:
            return
        if self._pan_start_label_pos is None or self._pan_start_center is None:
            return
        h, w = self.latest_raw_frame.shape[:2]
        _, _, cw, ch = self._last_zoom_crop
        dx_px = label_pos.x() - self._pan_start_label_pos.x()
        dy_px = label_pos.y() - self._pan_start_label_pos.y()
        dnx = dx_px / rect.width() * (cw / w)
        dny = dy_px / rect.height() * (ch / h)
        sx, sy = self._pan_start_center
        self._zoom_cx, self._zoom_cy = sx - dnx, sy - dny
        self._clamp_zoom_center()

    def eventFilter(self, obj, event):
        # زوم با اسکرول + پن (Shift+درگ چپ یا درگ با دکمه‌ی وسط) روی تصویر هر
        # خانه - بدون تداخل با رسم محدوده (draw_mode) و درگ‌انددراپ جابه‌جایی
        # خانه‌ها (درگ چپِ ساده همچنان مال جابه‌جایی خانه است).
        if obj is self.video_label:
            etype = event.type()
            if etype == QEvent.Type.Wheel:
                if self.cam is not None and not self.is_draw_mode():
                    delta = event.angleDelta().y()
                    if delta:
                        focus = self._label_pos_to_frame_norm(event.position())
                        self._zoom_at(focus or (0.5, 0.5),
                                      self._ZOOM_STEP if delta > 0 else 1.0 / self._ZOOM_STEP)
                        return True
            elif etype == QEvent.Type.MouseButtonPress:
                btn = event.button()
                if (self._zoom > 1.0 and not self.is_draw_mode() and not self._panning
                        and (btn == Qt.MouseButton.MiddleButton
                             or (btn == Qt.MouseButton.LeftButton
                                 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier))):
                    self._panning = True
                    self._pan_button = btn
                    self._pan_start_label_pos = event.position()
                    self._pan_start_center = (self._zoom_cx, self._zoom_cy)
                    self.video_label.setCursor(Qt.CursorShape.ClosedHandCursor)
                    return True
            elif etype == QEvent.Type.MouseMove:
                if self._panning:
                    self._pan_to(event.position())
                    return True
            elif etype == QEvent.Type.MouseButtonRelease:
                if self._panning and event.button() == self._pan_button:
                    self._panning = False
                    self._pan_button = None
                    self._pan_start_label_pos = None
                    self._pan_start_center = None
                    self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
                    # پایان پن: ناحیه‌ی نهایی به پلاک‌خوان اعلام می‌شود
                    # (نه حین درگ، تا ترکر مدام بازنشانی نشود)
                    self._sync_plate_roi()
                    return True
        return super().eventFilter(obj, event)

    # --------------------------------------- تنظیمات تصویر (هر دوربین جدا) --
    def apply_image_profile(self, profile):
        """اعمال زنده‌ی پروفایل تنظیمات تصویر روی ترد پخش جاری (پیش‌نمایش
        دیالوگ «تنظیمات تصویر») - رجوع کنید به image_profile.py."""
        if self.stream_thread is not None:
            self.stream_thread.set_image_profile(profile)

    def _apply_saved_image_profile(self):
        """اعمال پروفایل ذخیره‌شده‌ی این دوربین (cameras.json) روی ترد تازه."""
        if self.stream_thread is None or self.cam is None:
            return
        try:
            from image_profile import get_camera_profile
            self.stream_thread.set_image_profile(get_camera_profile(self.cam))
        except Exception:
            pass

    def stop(self):
        # رفع درخواست «ردیابی اشخاص»: قبل از بالا بردن نسل استریم (که
        # رویدادهای بعدی را با _guarded_connect نادیده می‌گیرد)، ردهای باز
        # را می‌بندیم تا «حضور»هایشان در دیتابیس بسته شوند.
        if self.stream_thread is not None:
            try:
                self.stream_thread.flush_person_tracks()
            except Exception:
                pass
        # نسل استریم را همین‌جا بالا می‌بریم تا هر رویداد جامانده‌ی ترد قبلی
        # که هنوز در صف GUI است، با _guarded_connect نادیده گرفته شود (رفع
        # باگ «یک فریم از دوربین قبلی در کادر می‌ماند»).
        self._stream_seq += 1
        # با توقف پخش، بزرگ‌نمایی هم به حالت عادی برمی‌گردد.
        self._reset_zoom()
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
        self.stream_thread = None
        self.cam = None
        self.latest_raw_frame = None
        self._set_name_text("خالی")
        # بنر صفحه‌ی «ردیابی اشخاص» را هم تازه کن.
        try:
            self.detector_status_changed.emit()
        except Exception:
            pass
        self.close_btn.setVisible(False)
        self.status_label.setText("")
        self.video_label.clear()
        self.video_label.setText("خالی — برای افزودن دوربین،\nدر لیست سمت چپ دابل‌کلیک کنید")
        self.set_selected(False)
        # رفع درخواست: با بسته‌شدن/خالی‌شدن خانه، شمارش افراد هم خاموش و
        # برچسب تعداد پاک می‌شود.
        self._people_counting_enabled = False
        self.people_count_label.setText("")
        # پاک‌کردن کامل وضعیت محدوده‌های هشدار/آلارم این خانه (دوربین بعدی
        # که در این خانه باز شود، محدوده‌های خودش را - اگر داشته باشد -
        # جداگانه از cameras.json بارگذاری می‌کند).
        self.pending_points = None
        self.regions = []
        self._editing_region_id = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions([])
        self.video_label.set_draw_mode(False)
        self._clear_alarm()
        self._detector_available = None
        self._detector_error = ""
        self._refresh_detector_warning()
        self._refresh_zoom_buttons()
        self.tripwire_changed.emit()


class RegionManagerDialog(QDialog):
    """رفع درخواست: مدیریت (مشاهده/حذف) محدوده‌های هشدار تعریف‌شده برای
    دوربین انتخاب‌شده‌ی فعلی - چون هر دوربین می‌تواند هم‌زمان چند محدوده‌ی
    نام‌دار داشته باشد و راهی برای حذف تک‌تک آن‌ها لازم است."""

    def __init__(self, slot, on_changed, parent=None):
        super().__init__(parent)
        self.setWindowTitle("مدیریت محدوده‌های هشدار")
        self.resize(340, 320)
        self.slot = slot
        self.on_changed = on_changed

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("محدوده‌های تعریف‌شده برای این دوربین:"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        # رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: این دکمه همین
        # دیالوگ را می‌بندد و روی تصویر زنده‌ی همان دوربین، محدوده‌ی
        # انتخاب‌شده را به‌شکل یک پیش‌نمایش سبزِ قابل‌کشیدن درمی‌آورد
        # (رجوع کنید به CameraSlotWidget.start_edit_region) - از همان
        # دکمه‌های «✅ تایید و نام‌گذاری»/«❌ لغو رسم» نوار ابزار (که در حالت
        # ویرایش به «💾 ذخیره ویرایش شکل»/«↩ لغو ویرایش» تغییر متن می‌دهند)
        # برای ذخیره یا انصراف استفاده می‌شود.
        self.edit_btn = QPushButton("✏ ویرایش شکل/اندازه")
        self.edit_btn.setToolTip(
            "این دیالوگ را می‌بندد و امکان کشیدن گوشه‌های این محدوده را روی تصویر دوربین فعال می‌کند."
        )
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        layout.addWidget(self.edit_btn)

        self.remove_btn = QPushButton("🗑 حذف محدوده‌ی انتخاب‌شده")
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.remove_btn)

        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._reload()

    def _reload(self):
        self.list_widget.clear()
        for region in self.slot.regions:
            label = f"شماره {region['number']}"
            if region.get("name"):
                label += f" / {region['name']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, region["id"])
            self.list_widget.addItem(item)

    def _on_remove_clicked(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        region_id = item.data(Qt.ItemDataRole.UserRole)
        self.slot.remove_region(region_id)
        if self.on_changed is not None:
            self.on_changed()
        self._reload()

    def _on_edit_clicked(self):
        item = self.list_widget.currentItem()
        if item is None:
            QMessageBox.information(self, "ویرایش شکل/اندازه", "ابتدا یک محدوده را از فهرست انتخاب کنید.")
            return
        region_id = item.data(Qt.ItemDataRole.UserRole)
        if not self.slot.start_edit_region(region_id):
            QMessageBox.information(
                self, "ویرایش شکل/اندازه",
                "الان امکان شروع ویرایش نیست (یک رسم/ویرایش دیگر در حال انجام است)."
            )
            return
        QMessageBox.information(
            self, "ویرایش شکل/اندازه",
            "گوشه‌های سبزِ روی تصویر را با کلیک-و-درگ جابه‌جا کنید. برای افزودن گوشه‌ی "
            "تازه روی وسط یک ضلع کلیک کنید؛ برای حذف یک گوشه، روی آن کلیک راست کنید "
            "(حداقل ۳ گوشه لازم است). در پایان از نوار ابزار «💾 ذخیره ویرایش شکل» یا "
            "«↩ لغو ویرایش» را بزنید."
        )
        self.accept()


class CameraPreviewDialog(QDialog):
    """رفع درخواست: با «دابل‌کلیک» روی دوربین در صفحه‌ی «نقشه ساختمان»،
    به‌جای رفتن به صفحه‌ی اصلی، همین پنجره‌ی شناور باز می‌شود و پخش زنده‌ی
    همان دوربین را نشان می‌دهد (بدون ترک نقشه). کلیک ساده فقط دوربین را
    انتخاب می‌کند (پنل تنظیم زاویه/پهنای دید). بستن پنجره، استریم را متوقف
    می‌کند. دکمه‌ی «نمایش در صفحه اصلی» همان رفتار قبلی (باز کردن در
    شبکه‌ی نمایش) را انجام می‌دهد."""

    def __init__(self, cam: dict, rtsp_url: str, face_engine, parent=None):
        super().__init__(parent)
        self.cam = cam
        self._last_display_ts = 0.0
        cam_name = cam.get("name") or cam.get("ip", "")
        self.setWindowTitle(f"🎥 پخش زنده — {cam_name}")
        self.resize(680, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        self.video_label = QLabel("در حال اتصال...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(480, 320)
        self.video_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_label.setStyleSheet(
            "background:#111; color:#aaa; border-radius:6px;")
        layout.addWidget(self.video_label, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel("در حال اتصال...")
        self.status_label.setStyleSheet("color:#9b978c;")
        bottom.addWidget(self.status_label, 1)
        self.grid_btn = QPushButton("📌 نمایش در صفحه اصلی")
        self.grid_btn.clicked.connect(self._open_in_grid)
        bottom.addWidget(self.grid_btn)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self.stream_thread = CameraStreamThread(
            rtsp_url, face_engine, process_every_n=_PROCESS_EVERY_N)
        self.stream_thread.frame_ready.connect(self._on_frame_ready)
        self.stream_thread.error_signal.connect(self._on_error)
        self.stream_thread.connected_signal.connect(self._on_connected)
        self.stream_thread.start()

    def _open_in_grid(self):
        parent = self.parent()
        open_fn = getattr(parent, "open_live_view", None)
        self.close()
        if callable(open_fn):
            open_fn(self.cam)

    def _on_frame_ready(self, display_frame, raw_frame):
        # مثل CameraSlotWidget.on_frame_ready: حداکثر ~۱۵ فریم‌برثانیه و
        # کوچک‌سازی اولیه با OpenCV برای سبکی روی CPU.
        now = time.monotonic()
        if now - self._last_display_ts < 1.0 / 15.0:
            return
        self._last_display_ts = now
        try:
            lw, lh = self.video_label.width(), self.video_label.height()
        except Exception:
            lw, lh = 0, 0
        show_frame = display_frame
        if lw > 0 and lh > 0:
            h, w = show_frame.shape[:2]
            _scale = min(lw / w, lh / h)
            if _scale < 1.0:
                _nw, _nh = max(1, int(w * _scale)), max(1, int(h * _scale))
                show_frame = cv2.resize(show_frame, (_nw, _nh),
                                        interpolation=cv2.INTER_LINEAR)
        pixmap = _bgr_to_pixmap(show_frame)
        if pixmap is not None:
            self.video_label.setPixmap(pixmap)

    def _on_connected(self):
        self.status_label.setText("متصل شد ✓")

    def _on_error(self, msg):
        self.status_label.setText(f"خطا: {msg}")
        if "در انتظار تصویر" in self.video_label.text() or \
                "در حال اتصال" in self.video_label.text():
            self.video_label.setText(f"عدم اتصال\n{msg}")

    def closeEvent(self, event):
        try:
            if self.stream_thread is not None:
                self.stream_thread.stop()
        except Exception:
            pass
        super().closeEvent(event)


class CameraGridWidget(QWidget):
    """شبکه‌ی نمایش هم‌زمان دوربین‌ها با تعداد خانه‌ی قابل انتخاب
    (1، 4، 9، 16، 32 یا 64). با تغییر تعداد، دوربین‌های از قبل باز تا حد
    امکان در چیدمان جدید حفظ می‌شوند."""

    # رفع درخواست: هر بار خانه‌ی انتخاب‌شده عوض شود (یا با -1 خالی شود)،
    # این سیگنال ارسال می‌شود تا نوار ابزار «محدوده‌ی هشدار» در MainWindow
    # وضعیت دکمه‌های تایید/لغو/مدیریت را برای همان خانه به‌روزرسانی کند.
    selection_changed = pyqtSignal(int)
    # رفع باگ: علاوه بر عوض‌شدن انتخاب، با هر تغییر واقعی در وضعیت
    # محدوده‌های هر خانه (رسم/تایید/حذف - رجوع کنید به
    # CameraSlotWidget.tripwire_changed) هم باید نوار ابزار به‌روز شود،
    # وگرنه دکمه‌ها با وضعیت واقعی هم‌گام نمی‌مانند.
    tripwire_changed = pyqtSignal()
    # تغییر وضعیت موتور تشخیص شخص در هر خانه (CameraSlotWidget.
    # detector_status_changed)؛ به صورت تجمیعی به MainWindow می‌رسد تا بنر
    # صفحه‌ی «ردیابی اشخاص» با وضعیت واقعی به‌روز شود.
    detector_status_changed = pyqtSignal()

    def __init__(self, face_engine: FaceEngine, on_face_event, on_external_camera_drop=None,
                 on_region_alert=None, on_fire_event=None, on_plate_event=None,
                 on_person_event=None, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.on_face_event = on_face_event
        # رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار هر خانه، این
        # callback (در MainWindow) به هر خانه‌ی تازه‌ساخته‌شده هم پاس داده
        # می‌شود تا رویداد در پنل تشخیص چهره هم ثبت شود.
        self.on_region_alert = on_region_alert
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: مشابه on_region_alert،
        # به هر خانه‌ی تازه‌ساخته‌شده پاس داده می‌شود.
        self.on_fire_event = on_fire_event
        # رفع درخواست «سیستم پلاک‌خوان»: callback رویداد پلاک (در MainWindow)
        # به هر خانه‌ی تازه‌ساخته‌شده پاس داده می‌شود تا عبور پلاک‌ها
        # (تعریف‌شده/تعریف‌نشده) ثبت و گزارش شود.
        self.on_plate_event = on_plate_event
        # رفع درخواست «ردیابی اشخاص»: callback رویداد رد (در MainWindow) به
        # هر خانه‌ی تازه‌ساخته‌شده پاس داده می‌شود تا تطبیق بین دوربینی و
        # ثبت «حضور» در person_store انجام شود.
        self.on_person_event = on_person_event
        # رفع درخواست: وقتی موردی از لیست دوربین‌ها (خارج از شبکه‌ی نمایش) روی
        # یک خانه رها (drop) شود، این callback (در MainWindow) صدا زده می‌شود
        # تا رمز عبور را در صورت نیاز بپرسد و آدرس RTSP را بسازد.
        self.on_external_camera_drop = on_external_camera_drop
        self.slots = []
        self.selected_index = None
        # رفع درخواست: با دابل‌کلیک روی یک خانه، آن خانه تمام فضای شبکه را
        # اشغال می‌کند (بزرگ‌نمایی) و بقیه‌ی خانه‌ها مخفی می‌شوند؛ برای بازگشت
        # به حالت قبل، موقعیت اصلی (ردیف/ستون) هر خانه را نگه می‌داریم.
        self._slot_positions = []  # index -> (row, col)
        self._rows = 0
        self._cols = 0
        self._maximized_index = None
        # رفع درخواست: به‌جای یک دکمه‌ی جداگانه‌ی شمارش افراد برای هر خانه،
        # فقط یک دکمه‌ی سراسری بالای کل شبکه‌ی دوربین‌ها وجود دارد (رجوع کنید
        # به MainWindow.people_toggle_btn و set_people_counting_all). این
        # فلگ وضعیت همان دکمه‌ی سراسری را نگه می‌دارد تا خانه‌های تازه‌ساز
        # (تغییر چیدمان) یا دوربین‌های تازه‌باز هم همان وضعیت را بگیرند.
        self._people_counting_enabled = False

        self._layout = QGridLayout(self)
        self._layout.setSpacing(4)
        self._layout.setContentsMargins(2, 2, 2, 2)

        self.set_grid_size(4)

    # ------------------------------------------------------------- layout --

    def set_grid_size(self, count: int):
        rows, cols = GRID_LAYOUTS.get(count, (2, 2))

        # حفظ دوربین‌های در حال پخش (تا حد ظرفیت چیدمان جدید).
        previous = [(slot.cam, slot.stream_thread.rtsp_url if slot.stream_thread else None)
                    for slot in self.slots if slot.cam is not None]

        for slot in self.slots:
            slot.stop()
            self._layout.removeWidget(slot)
            slot.setParent(None)
            slot.deleteLater()
        self.slots = []
        self.selected_index = None
        self.selection_changed.emit(-1)
        self._slot_positions = []
        self._rows = rows
        self._cols = cols
        self._maximized_index = None

        total = rows * cols
        for r in range(rows):
            for c in range(cols):
                slot = CameraSlotWidget(
                    self._on_slot_clicked, self._on_slot_close_requested, self._on_slot_double_clicked,
                    on_slot_drag_swap=self._on_slot_drag_swap,
                    on_camera_drag_drop=self._on_camera_drag_drop,
                    on_region_alert=self.on_region_alert,
                    on_fire_event=self.on_fire_event,
                    on_plate_event=self.on_plate_event,
                    on_person_event=self.on_person_event,
                )
                slot.slot_index = len(self.slots)
                slot.tripwire_changed.connect(self.tripwire_changed.emit)
                slot.detector_status_changed.connect(
                    self.detector_status_changed.emit)
                self._layout.addWidget(slot, r, c)
                self.slots.append(slot)
                self._slot_positions.append((r, c))

        for cam, rtsp_url in previous[:total]:
            if rtsp_url:
                self.assign_camera(cam, rtsp_url)

    # ------------------------------------------------------ people count --

    def set_people_counting_all(self, enabled: bool):
        """رفع درخواست: یک گزینه‌ی واحد بالای تمام پنجره‌های دوربین‌ها که
        شمارش افراد Real Time را برای همه‌ی دوربین‌های باز، هم‌زمان روشن/
        خاموش می‌کند. تعداد نفرات هم‌چنان جداگانه، بالای پنجره‌ی هر دوربین
        نمایش داده می‌شود (رجوع کنید به CameraSlotWidget.on_people_count)."""
        self._people_counting_enabled = bool(enabled)
        for slot in self.slots:
            if slot.cam is not None:
                slot.set_people_counting(self._people_counting_enabled)

    # -------------------------------------------------------- assignment --

    def assign_camera(self, cam: dict, rtsp_url: str) -> bool:
        """دوربین را در اولین خانه‌ی خالی باز می‌کند. اگر همان دوربین از قبل
        باز است، فقط آن خانه را انتخاب می‌کند. اگر خانه‌ی خالی نباشد، False
        برمی‌گرداند تا پیام مناسب به کاربر نمایش داده شود."""
        for i, slot in enumerate(self.slots):
            if slot.cam is not None and slot.cam["id"] == cam["id"]:
                self._select_index(i)
                return True

        for i, slot in enumerate(self.slots):
            if slot.cam is None:
                slot.start(cam, rtsp_url, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
                # اعمال وضعیت فعلیِ دکمه‌ی سراسریِ شمارش افراد روی دوربین
                # تازه‌باز.
                slot.set_people_counting(self._people_counting_enabled)
                self._select_index(i)
                return True

        return False

    def is_camera_open(self, cam_id) -> bool:
        return any(slot.cam is not None and slot.cam["id"] == cam_id for slot in self.slots)

    def assign_camera_to_slot(self, cam: dict, rtsp_url: str, slot_index: int):
        """رفع درخواست: دوربین را دقیقاً در خانه‌ی مشخص‌شده (مثلاً همان خانه‌ای
        که کاربر آیتم را روی آن رها/drop کرده) باز می‌کند. اگر همان دوربین از
        قبل در خانه‌ی دیگری باز است، به‌جای باز کردن یک اتصال تکراری، فقط
        محل نمایش آن به خانه‌ی مقصد منتقل (جابه‌جا) می‌شود."""
        if not (0 <= slot_index < len(self.slots)):
            return
        for i, slot in enumerate(self.slots):
            if slot.cam is not None and slot.cam["id"] == cam["id"]:
                if i != slot_index:
                    self.swap_slots(i, slot_index)
                self._select_index(slot_index)
                return
        target = self.slots[slot_index]
        target.stop()
        target.start(cam, rtsp_url, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
        target.set_people_counting(self._people_counting_enabled)
        self._select_index(slot_index)

    def swap_slots(self, idx_a: int, idx_b: int):
        """رفع درخواست: جابه‌جا کردن محل نمایش دو خانه با درگ‌کردن. چون هر ترد
        پخش (CameraStreamThread) مستقیماً به سیگنال‌های همان خانه وصل است، به
        جای جابه‌جایی فیزیکی ویجت‌ها در چیدمان (که منطق بزرگ‌نمایی/بازگشت را
        پیچیده می‌کرد)، محتوای دو خانه (دوربین + اتصال) با هم جابه‌جا می‌شود -
        از دید کاربر دقیقاً همان «عوض شدن جای پنجره‌ی نمایش» است."""
        if idx_a == idx_b or not (0 <= idx_a < len(self.slots)) or not (0 <= idx_b < len(self.slots)):
            return
        slot_a, slot_b = self.slots[idx_a], self.slots[idx_b]
        if slot_a.cam is None and slot_b.cam is None:
            return

        cam_a = slot_a.cam
        url_a = slot_a.stream_thread.rtsp_url if slot_a.stream_thread else None
        cam_b = slot_b.cam
        url_b = slot_b.stream_thread.rtsp_url if slot_b.stream_thread else None

        slot_a.stop()
        slot_b.stop()
        if cam_b is not None:
            slot_a.start(cam_b, url_b, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
            slot_a.set_people_counting(self._people_counting_enabled)
        if cam_a is not None:
            slot_b.start(cam_a, url_a, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
            slot_b.set_people_counting(self._people_counting_enabled)

        if self.selected_index == idx_a:
            self._select_index(idx_b)
        elif self.selected_index == idx_b:
            self._select_index(idx_a)

    def _on_slot_drag_swap(self, src_index, dst_index):
        self.swap_slots(src_index, dst_index)

    def _on_camera_drag_drop(self, cam_id, dst_index):
        if self.on_external_camera_drop is not None:
            self.on_external_camera_drop(cam_id, dst_index)

    # --------------------------------------------------------- selection --

    def _on_slot_clicked(self, slot):
        self._select_index(self.slots.index(slot))

    def _select_index(self, idx):
        if self.selected_index is not None and 0 <= self.selected_index < len(self.slots):
            self.slots[self.selected_index].set_selected(False)
        self.selected_index = idx
        self.slots[idx].set_selected(True)
        self.selection_changed.emit(idx)

    def _on_slot_close_requested(self, slot):
        idx = self.slots.index(slot)
        slot.stop()
        if idx == self.selected_index:
            self.selected_index = None
            self.selection_changed.emit(-1)
        if idx == self._maximized_index:
            self.toggle_maximize(idx)

    # ----------------------------------------------------------- maximize --

    def _on_slot_double_clicked(self, slot):
        self.toggle_maximize(self.slots.index(slot))

    def toggle_maximize(self, idx):
        """رفع درخواست: با دابل‌کلیک روی تصویر یک دوربین، آن خانه بزرگ می‌شود
        (کل فضای شبکه را می‌گیرد و بقیه‌ی خانه‌ها مخفی می‌شوند) و با دابل‌کلیک
        دوباره روی همان خانه، به اندازه و چیدمان قبلی (شبکه‌ای) برمی‌گردد."""
        if self._maximized_index == idx:
            # بازگشت به چیدمان عادی شبکه‌ای.
            for i, s in enumerate(self.slots):
                self._layout.removeWidget(s)
                r, c = self._slot_positions[i]
                self._layout.addWidget(s, r, c)
                s.setVisible(True)
            self._maximized_index = None
        else:
            for i, s in enumerate(self.slots):
                self._layout.removeWidget(s)
                if i == idx:
                    self._layout.addWidget(s, 0, 0, self._rows, self._cols)
                    s.setVisible(True)
                else:
                    s.setVisible(False)
            self._maximized_index = idx
        self._select_index(idx)

    def get_selected_frame(self):
        if self.selected_index is not None:
            return self.slots[self.selected_index].latest_raw_frame
        return None

    def stop_all(self):
        for slot in self.slots:
            slot.stop()


class CameraTreeWidget(QTreeWidget):
    """درخت «دوربین‌ها و NVRهای من» با پشتیبانی از Drag: رفع درخواست - کاربر
    می‌تواند یک دوربین را از این لیست گرفته و روی خانه‌ی موردنظر در شبکه‌ی
    نمایش رها (drop) کند تا همان‌جا باز شود. فقط آیتم‌های «دوربین» قابل درگ
    هستند (نه گره‌های NVR که خودشان قابل پخش مستقیم نیستند)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "camera":
            return
        mime = QMimeData()
        mime.setData("application/x-ias-camera-id", str(data["id"]).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME_FA} | {APP_NAME_EN}")
        # آیکون پنجره: سپر لوگوی شرکت (هم در اجرای عادی، هم داخل exe).
        _logo_icon = QIcon(LOGO_SHIELD)
        if not _logo_icon.isNull():
            self.setWindowIcon(_logo_icon)
        self.setGeometry(100, 100, 1500, 780)

        self.face_engine = FaceEngine()
        self.camera_store = CameraStore()
        # رفع درخواست «ردیابی اشخاص بین دوربین‌ها»: تطبیق‌دهنده‌ی سراسری
        # ظاهری (یک نمونه برای کل برنامه، در ترد اصلی) + نگاشت ردهای فعال
        # هر دوربین: (cam_id, local_id) -> {person_id, sighting_id, slot}
        # تا «حضور»ها با ورود/خروج دقیق ثبت و کد شخص روی تصویر به‌روز شود.
        self._person_matcher = GlobalPersonMatcher(
            threshold=person_store.match_threshold,
            window_s=person_store.link_window_min * 60.0)
        self._active_person_tracks = {}
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: لیست پنل‌ها/سنسورهای
        # فیزیکی اعلام حریق کاربر + تردهای پس‌زمینه‌ی مانیتور هرکدام
        # (panel_id -> FireAlarmMonitorThread) - رجوع کنید به
        # fire_alarm_io.py/fire_alarm_store.py.
        self.fire_alarm_store = FireAlarmStore()
        self._fire_alarm_threads = {}
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # وقتی آتش/دود تشخیص داده شود، آژیر ممتد پخش و سیگنال به پنل
        # ساختمان ارسال می‌شود (تنظیمات در building_fire_config.json).
        if _BUILDING_FIRE_AVAILABLE:
            self.building_fire = BuildingFireOutput(logger=self._on_building_fire_log)
            self.alarm_player = AlarmSoundPlayer()
        else:
            self.building_fire = None
            self.alarm_player = None
        self.network_scan_thread = None
        self.detect_thread = None
        self._scan_ports_by_ip = {}  # ip -> [ports...] از آخرین اسکن شبکه
        self._detect_queue = []  # صف IPهایی که با انتخاب چندتایی باید پشت‌سرهم تشخیص داده شوند

        self.init_ui()
        self.reload_camera_list()
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: پنل‌های ذخیره‌شده از
        # اجراهای قبلی، مانیتور پس‌زمینه‌ی هرکدام همین لحظه شروع می‌شود -
        # دقیقاً مثل بارگذاری خودکار دوربین‌ها/NVRها. نمایش خودِ لیست حالا در
        # صفحه‌ی جداگانه‌ی FireAlarmPage انجام می‌شود (هنگام باز شدن آن از
        # هدر، متد refresh صفحه صدا زده می‌شود).
        for panel in self.fire_alarm_store.panels:
            self._start_fire_alarm_monitor(panel)

    # ---------------------------------------------------------------- UI ---

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        left_panel = QVBoxLayout()

        # رفع درخواست: کادر یوزرنیم/پسورد بالای پنل اسکن شبکه. این مقادیر فقط
        # در حافظه نگه‌داشته می‌شوند (هیچ‌جا روی دیسک ذخیره نمی‌شوند) و برای
        # اتصال به دستگاه‌های یافت‌شده در اسکن شبکه (چه برای تشخیص خودکار نوع
        # دستگاه، چه برای پرشدن خودکار فیلد یوزرنیم/پسورد دیالوگ افزودن
        # دوربین/NVR) استفاده می‌شوند. با بستن برنامه (closeEvent) پاک می‌شوند.
        scan_cred_group = QGroupBox("اطلاعات ورود برای اتصال به دوربین‌ها")
        scan_cred_layout = QVBoxLayout()
        self.scan_user_input = QLineEdit("admin")
        self.scan_user_input.setPlaceholderText("نام کاربری")
        self.scan_pass_input = QLineEdit()
        self.scan_pass_input.setPlaceholderText("رمز عبور")
        self.scan_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        scan_cred_layout.addWidget(self.scan_user_input)
        scan_cred_layout.addWidget(self.scan_pass_input)
        scan_cred_group.setLayout(scan_cred_layout)
        left_panel.addWidget(scan_cred_group)

        # بخش اسکن شبکه
        # نکته: قبلاً این بخش «اسکن دستگاه‌های مداربسته» نام داشت و صرفاً پورت‌های
        # باز را نشان می‌داد؛ چون این اسکن صرفاً یک اسکن عمومی شبکه (پورت‌های باز
        # روی هر IP) است - نه اسکن اختصاصی دوربین - عنوان و متن دکمه به «اسکن
        # شبکه» تغییر کرد تا با واقعیت عملکرد آن هم‌خوانی داشته باشد. همچنین حالا
        # با دابل‌کلیک روی هر نتیجه، کاربر مشخص می‌کند دستگاه یک دوربین تکی است یا
        # یک NVR؛ در صورت انتخاب NVR، مستقیماً دیالوگ افزودن NVR با IP از پیش
        # پرشده باز می‌شود و کانال‌ها/دوربین‌های متصل به آن پس از افزودن، به‌صورت
        # زیرمنو (زیرشاخه‌ی درختی) زیر همان NVR در پنل «دوربین‌ها و NVRهای من»
        # نمایش داده می‌شوند (رجوع کنید به reload_camera_list).
        scan_group = QGroupBox("اسکن شبکه (Network Scan)")
        scan_layout = QVBoxLayout()
        self.subnet_input = QLineEdit("192.168.1")
        self.subnet_input.setPlaceholderText("پیشوند ساب‌نت (مثلاً 192.168.1)")
        self.scan_btn = QPushButton("اسکن شبکه")
        self.scan_btn.clicked.connect(self.run_network_scan)
        self.scan_result_list = QListWidget()
        # رفع درخواست: به‌جای نگه‌داشتن Ctrl/Shift هنگام کلیک برای انتخاب چندتایی،
        # کنار هر دستگاه یک چک‌باکس نمایش داده می‌شود (هنگام افزودن آیتم‌ها در
        # _on_network_scan_finished تنظیم می‌شود) و کاربر با تیک زدن آن‌ها،
        # دستگاه‌های موردنظر برای اتصال را مشخص می‌کند. دابل‌کلیک همچنان برای
        # افزودن سریع یک دستگاه تکی کار می‌کند.
        self.scan_result_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.scan_result_list.itemDoubleClicked.connect(self.on_scan_result_selected)
        self.add_selected_scan_btn = QPushButton("+ افزودن دستگاه‌های انتخاب‌شده")
        self.add_selected_scan_btn.clicked.connect(self.on_add_selected_scan_results)
        self.detect_status_label = QLabel("")
        self.detect_status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        # نکته: دیگر از کاربر پرسیده نمی‌شود دستگاه دوربین تکی است یا NVR؛ با
        # دابل‌کلیک، نوع دستگاه به‌صورت خودکار تشخیص داده می‌شود (device_detect.py).
        scan_layout.addWidget(self.scan_result_list)
        scan_layout.addWidget(self.add_selected_scan_btn)
        scan_layout.addWidget(self.detect_status_label)
        scan_group.setLayout(scan_layout)
        left_panel.addWidget(scan_group)

        # بخش لیست دوربین‌ها و NVRهای من (با نام دلخواه)
        cam_group = QGroupBox("دوربین‌ها و NVRهای من")
        cam_layout = QVBoxLayout()
        add_btn_row = QHBoxLayout()
        self.add_camera_btn = QPushButton("+ افزودن دوربین تکی")
        self.add_camera_btn.clicked.connect(lambda: self.open_add_camera_dialog())
        self.add_nvr_btn = QPushButton("+ افزودن NVR")
        # نکته: چون open_add_nvr_dialog اکنون یک آرگومان اختیاری (prefill_ip)
        # دارد، باید مثل add_camera_btn از طریق lambda وصل شود؛ در غیر این
        # صورت PyQt مقدار bool سیگنال clicked(checked) را به‌جای None به
        # prefill_ip پاس می‌دهد و IP به‌اشتباه با True/False پر می‌شود.
        self.add_nvr_btn.clicked.connect(lambda: self.open_add_nvr_dialog())
        add_btn_row.addWidget(self.add_camera_btn)
        add_btn_row.addWidget(self.add_nvr_btn)

        # دوربین‌های متصل به یک NVR به‌صورت زیرمجموعه‌ی همان NVR نمایش داده می‌شوند.
        self.camera_list = CameraTreeWidget()
        self.camera_list.setHeaderHidden(True)
        self.camera_list.itemDoubleClicked.connect(self.on_camera_item_activated)
        self.camera_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.camera_list.customContextMenuRequested.connect(self.show_camera_context_menu)
        connect_hint = QLabel("برای پخش زنده روی یک دوربین/کانال دابل‌کلیک کنید. کلیک راست: ویرایش/حذف/بازخوانی کانال‌ها")
        connect_hint.setStyleSheet("color: #888; font-size: 10px;")
        cam_layout.addLayout(add_btn_row)
        cam_layout.addWidget(self.camera_list)
        cam_layout.addWidget(connect_hint)
        cam_group.setLayout(cam_layout)
        left_panel.addWidget(cam_group)

        # «🔥 اعلام حریق»، «👤 چهره‌ها» و «📊 گزارش‌ها» دیگر در پنل چپ
        # نیستند؛ هرکدام صفحه‌ی جداگانه‌ی خودشان را دارند و از هدر بالای
        # برنامه (دکمه‌های ناوبری) قابل دسترسی‌اند - رجوع کنید به انتهای
        # init_ui (QStackedWidget) و متد show_page.

        # ------------------------------------------------ ستون میانی: شبکه‌ی
        # نمایش هم‌زمان دوربین‌ها با تعداد خانه‌ی قابل انتخاب.
        grid_column = QVBoxLayout()
        grid_toolbar = QHBoxLayout()

        # دکمه‌ی نمایش/مخفی‌کردن پنل کناری سمت چپ (اسکن شبکه، دوربین‌ها و
        # NVRهای من). با کلیک روی این دکمه، عرض پنل چپ صفر یا
        # به اندازه‌ی قبلی‌اش برمی‌گردد تا کاربر در صورت نیاز فضای بیشتری
        # برای تصویر دوربین‌ها در وسط داشته باشد.
        self.sidebar_toggle_btn = QPushButton("☰")
        self.sidebar_toggle_btn.setFixedWidth(32)
        self.sidebar_toggle_btn.setToolTip("نمایش / مخفی کردن پنل کناری")
        self.sidebar_toggle_btn.clicked.connect(self.toggle_sidebar)
        grid_toolbar.addWidget(self.sidebar_toggle_btn)

        grid_toolbar_label = QLabel("تعداد نمایش هم‌زمان دوربین‌ها:")
        grid_toolbar_label.setStyleSheet("font-size: 11px;")
        self.grid_size_combo = QComboBox()
        for n in (1, 4, 9, 16, 32, 64):
            self.grid_size_combo.addItem(str(n), n)
        self.grid_size_combo.setCurrentIndex(1)  # پیش‌فرض: 4
        self.grid_size_combo.currentIndexChanged.connect(self._on_grid_size_changed)
        grid_toolbar.addWidget(grid_toolbar_label)
        grid_toolbar.addWidget(self.grid_size_combo)

        # رفع درخواست: به‌جای یک دکمه‌ی روشن/خاموش شمارش افراد برای هر
        # دوربین جداگانه، فقط یک دکمه‌ی واحد بالای تمام پنجره‌های دوربین‌ها
        # قرار دارد که شمارش را برای همه‌ی دوربین‌های باز هم‌زمان روشن/خاموش
        # می‌کند. تعداد نفرات هم‌چنان جداگانه بالای پنجره‌ی هر دوربین نوشته
        # می‌شود (رجوع کنید به CameraSlotWidget.people_count_label).
        self.people_toggle_btn = QPushButton("👥 شمارش افراد (همه دوربین‌ها)")
        self.people_toggle_btn.setCheckable(True)
        self.people_toggle_btn.setToolTip("روشن/خاموش کردن شمارش افراد Real Time برای تمام دوربین‌های باز")
        self.people_toggle_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#e67e22; color:#fff;}"
        )
        self.people_toggle_btn.toggled.connect(self._on_people_toggle_all)
        grid_toolbar.addWidget(self.people_toggle_btn)

        # رفع درخواست: دکمه‌های انتخاب نوع رسم محدوده (رسم دستی/کل کادر/
        # تشخیص با AI) فقط بعد از زدن این دکمه‌ی اصلی ظاهر می‌شوند - قبلاً
        # همیشه هر سه کنار هم روی نوار ابزار دیده می‌شدند و شلوغ بود.
        self.region_menu_btn = QPushButton("🖊 رسم محدوده")
        self.region_menu_btn.setCheckable(True)
        self.region_menu_btn.setToolTip("نمایش/مخفی‌کردن گزینه‌های رسم محدوده‌ی هشدار (رسم دستی، کل کادر، تشخیص با AI)")
        self.region_menu_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#9b59b6; color:#fff;}"
        )
        self.region_menu_btn.toggled.connect(self._on_region_menu_toggled)
        grid_toolbar.addWidget(self.region_menu_btn)

        # ------ کانتینر سه دکمه‌ی «نوع رسم» - فقط با زدن دکمه‌ی بالا نمایان می‌شود ------
        self.region_type_row = QWidget()
        region_type_layout = QHBoxLayout()
        region_type_layout.setContentsMargins(0, 0, 0, 0)
        self.region_type_row.setLayout(region_type_layout)
        self.region_type_row.setVisible(False)
        grid_toolbar.addWidget(self.region_type_row)

        # رفع درخواست: محدوده‌ی هشدار (Zone) - جایگزین خط فرضی عبور قبلی.
        # کاربر ابتدا یک دوربین را از شبکه انتخاب می‌کند (کلیک روی خانه‌اش)،
        # سپس این دکمه را می‌زند تا بتواند با کلیک‌های متوالی روی نقاط دلخواه
        # زمین/تصویر همان دوربین یک محدوده‌ی چندضلعی (نه لزوماً مستطیل) بسازد.
        # «تایید و نام‌گذاری» یک نام (مثلاً اسم اتاق) از کاربر می‌پرسد و
        # محدوده را به لیست محدوده‌های فعال آن دوربین اضافه می‌کند (از آن پس
        # با ورود هرکسی به آن، کادر دوربین قرمز و آلارم پخش می‌شود)؛ «لغو
        # رسم» فقط نقاط در انتظار نام‌گذاری را پاک می‌کند و «مدیریت
        # محدوده‌ها» امکان مشاهده/حذف محدوده‌های از قبل تایید‌شده را می‌دهد.
        # هر دوربین می‌تواند هم‌زمان چند محدوده‌ی نام‌دار داشته باشد.
        self.draw_line_btn = QPushButton("✏ رسم دستی")
        self.draw_line_btn.setCheckable(True)
        self.draw_line_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید\n"
            "۳) روی تصویر همان دوربین، به‌ترتیب روی نقاط زمین/اتاق کلیک کنید "
            "(برنامه نقاط را به هم وصل می‌کند)\n"
            "۴) برای بستن محدوده: روی نقطه‌ی اول (دایره‌ی بزرگ‌تر) کلیک کنید، "
            "یا دابل‌کلیک کنید (حداقل ۳ نقطه لازم است)\n"
            "کلیک راست: لغو رسمِ در حال انجام"
        )
        self.draw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#9b59b6; color:#fff;}"
        )
        self.draw_line_btn.toggled.connect(self._on_draw_line_toggled)
        region_type_layout.addWidget(self.draw_line_btn)

        # رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش رو
        # محدوده محسوب کنه»: به‌جای کلیک‌های متوالی دستی، این دکمه بلافاصله
        # کل کادر تصویر زنده‌ی دوربین انتخاب‌شده را - در عمل معادل «کل زمینی
        # که دوربین می‌بیند» - به‌عنوان یک محدوده‌ی تازه و در انتظار تایید
        # می‌گذارد (دقیقاً مثل رسم دستی، با همان دکمه‌های تایید/لغوِ پایین).
        # این محدوده هم مثل هر محدوده‌ی دیگری کاملاً قابل ویرایش است - قبل
        # از تایید (با کشیدن گوشه‌ها) یا بعداً از «مدیریت محدوده‌ها».
        self.auto_region_btn = QPushButton("🌐 کل کادر")
        self.auto_region_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید - کل تصویر دوربین به‌عنوان محدوده در نظر گرفته می‌شود\n"
            "۳) در صورت نیاز، گوشه‌های آن را با ماوس بکشید تا شکل/اندازه‌اش را دقیق‌تر کنید\n"
            "   (مثلاً گوشه‌ای را عقب بکشید تا یک در ورودی از محدوده کنار برود)\n"
            "۴) «✅ تایید و نام‌گذاری» را بزنید\n"
            "توجه: این برنامه مدل جداگانه‌ای برای تشخیص دقیقِ کفِ زمین ندارد؛ «تشخیص خودکار» "
            "یعنی کل کادر تصویر دوربین به‌عنوان محدوده گرفته می‌شود، نه شناسایی هوشمند مرز زمین."
        )
        self.auto_region_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.auto_region_btn.clicked.connect(self._on_auto_region_clicked)
        region_type_layout.addWidget(self.auto_region_btn)

        # رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش رو
        # محدوده محسوب کنه، ولی بازم قابل ادیت باشه، دقیق‌تر با مدل هوش
        # مصنوعی (Segmentation)»: برخلاف دکمه‌ی بالا (که فقط کل کادر تصویر
        # را می‌گذارد چون مدل جداگانه‌ای نداشت)، این دکمه واقعاً از یک مدل
        # Segmentation (SegFormer/ADE20K - رجوع کنید به floor_detector.py)
        # روی آخرین فریمِ زنده‌ی همین دوربین استفاده می‌کند تا فقط سطح
        # زمین/کفِ واقعی را (نه کل کادر شامل دیوار/سقف/اثاثیه) به‌عنوان
        # محدوده‌ی پیشنهادی بگذارد. نتیجه، دقیقاً مثل هر محدوده‌ی دیگر،
        # کاملاً با ماوس قابل ویرایش است. چون بارگذاری/اجرای مدل چند ثانیه
        # طول می‌کشد، در یک ترد جدا (FloorDetectThread) اجرا می‌شود تا UI
        # فریز نشود - رجوع کنید به _on_ai_floor_region_clicked.
        self.ai_floor_btn = QPushButton("🧭 تشخیص با AI")
        self.ai_floor_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید - با مدل هوش مصنوعی (Segmentation)، فقط "
            "سطح زمین/کفِ واقعیِ تصویر تشخیص داده و به‌عنوان محدوده پیشنهاد "
            "می‌شود (نه کل کادر تصویر)\n"
            "۳) در صورت نیاز، گوشه‌ها را با ماوس بکشید/اضافه/حذف کنید تا "
            "دقیق‌تر شود\n"
            "۴) «✅ تایید و نام‌گذاری» را بزنید\n"
            "نکته: اولین استفاده ممکن است به‌خاطر دانلود یک‌بارِ مدل (حدود "
            "۱۴ مگابایت) چند ثانیه بیشتر طول بکشد. اگر این قابلیت روی سیستم "
            "شما در دسترس نباشد، به‌جایش می‌توانید از «تشخیص خودکار محدوده "
            "(کل تصویر)» یا رسم دستی استفاده کنید."
        )
        self.ai_floor_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.ai_floor_btn.clicked.connect(self._on_ai_floor_region_clicked)
        region_type_layout.addWidget(self.ai_floor_btn)
        # نگه‌داشتن ارجاع به تردِ در حال اجرا (اگر باشد) - هم برای جلوگیری
        # از garbage-collect شدنِ زودهنگام QThread در حال اجرا، هم برای
        # اینکه بدانیم همین الان یک تشخیص در جریان است (رجوع کنید به
        # _on_ai_floor_region_clicked).
        self._floor_detect_thread = None

        # رفع درخواست: دکمه‌های «تایید»/«لغو رسم» فقط بعد از انتخاب یکی از
        # سه نوع رسم بالا (و تا وقتی یک محدوده‌ی در-انتظار/در-حال-ویرایش
        # وجود دارد) نمایان می‌شوند - رجوع کنید به _refresh_line_buttons که
        # visibility این کانتینر را هم به‌روز می‌کند.
        self.region_action_row = QWidget()
        region_action_layout = QHBoxLayout()
        region_action_layout.setContentsMargins(0, 0, 0, 0)
        self.region_action_row.setLayout(region_action_layout)
        self.region_action_row.setVisible(False)
        grid_toolbar.addWidget(self.region_action_row)

        self.confirm_line_btn = QPushButton("✅ تایید و نام‌گذاری")
        self.confirm_line_btn.setEnabled(False)
        self.confirm_line_btn.setToolTip(
            "محدوده‌ی رسم‌شده را با یک نام (مثلاً نام اتاق) نهایی می‌کند؛ از این پس با ورود هرکسی به آن، کادر دوربین قرمز و آلارم پخش می‌شود."
        )
        self.confirm_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#27ae60; color:#fff;}"
        )
        self.confirm_line_btn.clicked.connect(self._on_confirm_line_clicked)
        region_action_layout.addWidget(self.confirm_line_btn)

        self.redraw_line_btn = QPushButton("❌ لغو رسم")
        self.redraw_line_btn.setEnabled(False)
        self.redraw_line_btn.setToolTip("نقاط در حال رسم/در انتظار نام‌گذاری را لغو می‌کند؛ محدوده‌های قبلاً تایید‌شده حذف نمی‌شوند.")
        self.redraw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.redraw_line_btn.clicked.connect(self._on_redraw_line_clicked)
        region_action_layout.addWidget(self.redraw_line_btn)

        self.manage_regions_btn = QPushButton("📋 مدیریت محدوده‌ها")
        self.manage_regions_btn.setEnabled(False)
        self.manage_regions_btn.setToolTip("مشاهده و حذف محدوده‌های هشدار تعریف‌شده برای دوربین انتخاب‌شده.")
        self.manage_regions_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#2980b9; color:#fff;}"
        )
        self.manage_regions_btn.clicked.connect(self._on_manage_regions_clicked)
        grid_toolbar.addWidget(self.manage_regions_btn)

        # رفع درخواست «سیستم تنظیمات تصویر (مثل WDR، Anti Fogging و...)»:
        # دکمه برای دوربینِ خانه‌ی انتخاب‌شده؛ هر دوربین پروفایل مستقل خودش
        # را دارد (در cameras.json ذخیره می‌شود) و چند پیش‌فرض آماده هم داخل
        # دیالوگ هست - رجوع کنید به image_settings_dialog.py و image_profile.py.
        self.image_settings_btn = QPushButton("🎨 تنظیمات تصویر")
        self.image_settings_btn.setEnabled(False)
        self.image_settings_btn.setToolTip(
            "تنظیمات تصویر (روشنایی، کنتراست، WDR، ضد مه و...) برای دوربین "
            "انتخاب‌شده.\nهر دوربین تنظیم مستقل خودش را دارد؛ چند پیش‌فرض آماده "
            "هم داخل دیالوگ هست."
        )
        self.image_settings_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#8e44ad; color:#fff;}"
        )
        self.image_settings_btn.clicked.connect(self._on_image_settings_clicked)
        grid_toolbar.addWidget(self.image_settings_btn)

        grid_toolbar.addStretch()

        self.camera_grid = CameraGridWidget(
            self.face_engine, self.on_face_event, on_external_camera_drop=self.on_camera_dropped_on_grid,
            on_region_alert=self.on_region_alert,
            on_fire_event=self.on_fire_event,
            on_plate_event=self.on_plate_event,
            on_person_event=self.on_person_event,
        )
        self.camera_grid.selection_changed.connect(lambda _idx: self._refresh_line_buttons())
        self.camera_grid.tripwire_changed.connect(self._refresh_line_buttons)
        self.camera_grid.detector_status_changed.connect(
            self._refresh_person_detector_status)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.camera_grid)

        grid_column.addLayout(grid_toolbar)
        grid_column.addWidget(grid_scroll, 1)

        # ------------------------------------------------ ستون راست: پنل
        # تشخیص چهره. رفع درخواست: پنل قبلی «رویدادهای شناسایی چهره» که در
        # پایین پنجره و به‌صورت یک لیست متنی ساده بود حذف شد؛ به‌جای آن این
        # پنل، سمت راست تصویر دوربین‌ها قرار گرفته و برای هر چهره‌ای که هر
        # کدام از دوربین‌ها می‌بیند (چه شناخته‌شده چه تعریف‌نشده)، یک ردیف با
        # تصویر برش‌خورده‌ی همان چهره و برچسب «تعریف شده» یا «تعریف نشده»
        # ثبت می‌کند.
        face_panel_group = QGroupBox("پنل تشخیص چهره")
        face_panel_layout = QVBoxLayout()
        self.face_panel_list = QListWidget()
        self.face_panel_list.setIconSize(QSize(64, 64))
        self.face_panel_list.setWordWrap(True)
        face_panel_layout.addWidget(self.face_panel_list)
        face_panel_group.setLayout(face_panel_layout)

        # پنل «هشدارهای حریق و دود» - رفع درخواست «سیستم تشخیص دود و اعلام
        # حریق»: هم رویدادهای تشخیص تصویری (fire_smoke_detector.py روی هر
        # دوربین) و هم هشدارهای دریافتی از پنل‌های فیزیکی (fire_alarm_io.py)
        # در همین‌جا (و هم‌زمان در «گزارش‌ها») ثبت می‌شوند - جدا از پنل
        # تشخیص چهره‌ی بالا تا با آن قاطی نشود.
        fire_panel_group = QGroupBox("🔥 هشدارهای حریق و دود")
        fire_panel_layout = QVBoxLayout()
        self.fire_panel_list = QListWidget()
        self.fire_panel_list.setIconSize(QSize(64, 64))
        self.fire_panel_list.setWordWrap(True)
        fire_panel_layout.addWidget(self.fire_panel_list)
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # دکمه‌ی قطع صدای آژیر + دکمه‌ی تنظیمات اتصال ساختمان زیر لیست حریق.
        fire_btn_row = QHBoxLayout()
        self.btn_stop_alarm = QPushButton("🔇 قطع صدای هشدار")
        self.btn_stop_alarm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_alarm.clicked.connect(self.stop_fire_alarm_sound)
        self.btn_building_fire = QPushButton("🏢 اتصال به سیستم حریق ساختمان")
        self.btn_building_fire.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_building_fire.clicked.connect(self.open_building_fire_settings)
        fire_btn_row.addWidget(self.btn_stop_alarm)
        fire_btn_row.addWidget(self.btn_building_fire)
        fire_panel_layout.addLayout(fire_btn_row)
        fire_panel_group.setLayout(fire_panel_layout)

        # رفع درخواست «تنظیم اندازه پنل‌های سمت راست»: به‌جای QVBoxLayout با
        # ضریب کشش ثابت (که با توجه به حداقل‌اندازه‌ی متفاوت دو QGroupBox
        # همیشه دقیقاً به همان نسبت ۳:۲ در نمی‌آمد و کاربر هم راهی برای
        # تغییرش نداشت)، از یک QSplitter عمودی استفاده می‌شود: هم نسبت
        # اولیه‌ی مشخص (۶۰٪/۴۰٪) دارد، هم کاربر می‌تواند با کشیدن لبه‌ی بین
        # دو پنل، اندازه‌ی هرکدام را دستی تنظیم کند، و هم حداقل ارتفاعی
        # (setMinimumHeight) دارند که هیچ‌کدام کاملاً جمع نشوند.
        face_panel_group.setMinimumHeight(120)
        fire_panel_group.setMinimumHeight(120)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(face_panel_group)
        self.right_splitter.addWidget(fire_panel_group)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 2)

        # رفع درخواست: عرض پنل سمت راست (پنل تشخیص چهره) باید دقیقاً هم‌اندازه‌ی
        # پنل سمت چپ باشد تا فضای بیشتری به تصویر دوربین‌ها در وسط برسد. قبلاً
        # با addLayout/addWidget + ضریب کشش (stretch factor) این کار انجام
        # می‌شد، اما چون QVBoxLayout سمت چپ و QGroupBox سمت راست حداقل‌اندازه‌ی
        # (minimumSizeHint) متفاوتی داشتند، ضریب کشش یکسان همیشه به عرض واقعاً
        # یکسان منجر نمی‌شد. با QSplitter و تنظیم صریح اندازه‌ی اولیه، عرض دو
        # ستون کناری همیشه یکسان شروع می‌شود (کاربر همچنان می‌تواند با کشیدن
        # لبه‌ی splitter عرض را دستی تغییر دهد).
        # رفع درخواست: پنل سمت چپ کوچکتر شده (عرض ثابت و محدودتر به‌جای سهم
        # کشیدنی از splitter) و یک دکمه‌ی sidebar اضافه شده که با کلیک روی آن
        # کل پنل چپ باز/بسته می‌شود (toggle_sidebar).
        self.left_widget = QWidget()
        self.left_widget.setLayout(left_panel)
        self.left_widget.setMaximumWidth(230)
        grid_widget = QWidget()
        grid_widget.setLayout(grid_column)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.left_widget)
        self.splitter.addWidget(grid_widget)
        self.splitter.addWidget(self.right_splitter)
        # پنل چپ اکنون کوچکتر و با عرض محدود (ثابت‌تر) است، پنل وسط (شبکه‌ی
        # دوربین‌ها) بیشترین سهم را می‌گیرد و پنل راست (تشخیص چهره) بدون تغییر
        # باقی می‌ماند.
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 7)
        self.splitter.setStretchFactor(2, 2)
        total_w = max(self.width(), 1500)
        right_w = total_w * 2 // 9        # عرض قبلی/بدون‌تغییرِ پنل راست
        left_w = 200                       # عرض کوچک و ثابت پنل چپ
        self.splitter.setSizes([left_w, total_w - left_w - right_w, right_w])
        # اندازه‌ی اولیه‌ی صریح برای دو پنل داخل ستون راست (۶۰٪ تشخیص چهره،
        # ۴۰٪ هشدار حریق/دود) - کاربر همچنان می‌تواند با کشیدن لبه‌ی بینشان
        # این نسبت را تغییر دهد.
        right_h = max(self.height(), 780)
        self.right_splitter.setSizes([right_h * 6 // 10, right_h * 4 // 10])
        # عرضی که پنل چپ قبل از مخفی‌شدن داشت، برای بازگرداندن آن هنگام کلیک
        # مجدد روی دکمه‌ی sidebar نگه‌داشته می‌شود.
        self._left_panel_width = left_w

        # -------------------------------------------- صفحه‌ها + هدر بالای برنامه --
        # محتوای قبلی پنجره (پنل چپ + شبکه‌ی دوربین‌ها + پنل راست) حالا «صفحه‌ی
        # اصلی» است؛ «اعلام حریق»، «چهره‌ها» و «گزارش‌ها» هرکدام صفحه‌ی
        # جداگانه‌ی خودشان را دارند و از هدر بالای برنامه (دکمه‌های ناوبری)
        # قابل دسترسی‌اند.
        home_widget = QWidget()
        home_layout = QHBoxLayout(home_widget)
        home_layout.setContentsMargins(0, 0, 0, 0)
        home_layout.addWidget(self.splitter)

        self.pages = QStackedWidget()
        self.pages.addWidget(home_widget)
        self.fire_page = FireAlarmPage(
            self.fire_alarm_store, self._start_fire_alarm_monitor,
            self._stop_fire_alarm_monitor,
        )
        self.pages.addWidget(self.fire_page)
        self.face_page = FaceLibraryPage(self.face_engine, self.get_active_camera_frame)
        self.pages.addWidget(self.face_page)
        # camera_store به صفحه‌ی گزارش‌ها پاس داده می‌شود تا برای دکمه‌ی
        # «پخش ویدیوی NVR»، اطلاعات اتصال NVR مربوط به هر رویداد را پیدا کند.
        self.reports_page = ReportsPage(report_store, self.camera_store)
        self.pages.addWidget(self.reports_page)
        # رفع درخواست «سیستم پلاک‌خوان»: صفحه‌ی جدا (مثل Face Library) با دو
        # تب «تعریف پلاک‌ها» و «گزارش عبور»؛ on_plate_toggle برای اعمال زنده‌ی
        # فعال/غیرفعال شدن پلاک‌خوان روی دوربینی که همین حالا باز است.
        self.plate_page = PlateLibraryPage(
            self.get_active_camera_frame, self.camera_store,
            on_plate_toggle=self._on_camera_plate_toggle,
            get_plate_diag_callback=self._get_plate_live_diag)
        self.pages.addWidget(self.plate_page)
        # رفع درخواست «ردیابی اشخاص بین دوربین‌ها»: صفحه‌ی جدا (مثل
        # پلاک‌خوان) با دو تب «اشخاص ردیابی‌شده» و «گزارش مسیر حرکت»؛
        # on_person_toggle برای اعمال زنده‌ی فعال/غیرفعال شدن ردیابی روی
        # دوربینی که همین حالا باز است.
        self.person_page = PersonTrackPage(
            self.camera_store,
            on_person_toggle=self._on_camera_person_toggle,
            on_show_on_map=self._on_person_show_on_map)
        self.pages.addWidget(self.person_page)
        # رفع درخواست «نقشه‌ی تعاملی ساختمان»: صفحه‌ی هفتم؛ اگر فایل
        # building_map_dialog.py کنار برنامه نباشد، بدون کرش رد می‌شود.
        self.map_page = None
        if _MAP_PAGE_AVAILABLE:
            self.map_page = BuildingMapPage(
                self.camera_store,
                on_camera_click=self._on_map_camera_click)
            self.pages.addWidget(self.map_page)

        main_layout.addWidget(self._build_header())
        main_layout.addWidget(self.pages, 1)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        self.show_page("home")

    def toggle_sidebar(self):
        """رفع درخواست: نمایش/مخفی‌کردن پنل کناری سمت چپ با کلیک روی دکمه‌ی
        sidebar. وقتی پنل باز است با کلیک بسته می‌شود (عرض صفر) و وقتی بسته
        است با کلیک، به آخرین عرضی که داشت باز می‌گردد."""
        sizes = self.splitter.sizes()
        if sizes[0] > 0:
            self._left_panel_width = sizes[0]
            sizes[1] += sizes[0]
            sizes[0] = 0
        else:
            restore_w = getattr(self, "_left_panel_width", 200) or 200
            sizes[1] = max(0, sizes[1] - restore_w)
            sizes[0] = restore_w
        self.splitter.setSizes(sizes)

    def _on_grid_size_changed(self, _index):
        count = self.grid_size_combo.currentData()
        self.camera_grid.set_grid_size(count)

    def _on_people_toggle_all(self, checked):
        """رفع درخواست: با این یک دکمه (بالای کل شبکه‌ی دوربین‌ها)، شمارش
        افراد Real Time برای همه‌ی دوربین‌های باز هم‌زمان روشن/خاموش می‌شود؛
        وضعیت روی دوربین‌هایی که بعداً باز شوند هم اعمال می‌ماند (رجوع کنید
        به CameraGridWidget.set_people_counting_all)."""
        self.camera_grid.set_people_counting_all(checked)

    # ----------------------------------------------------- محدوده‌ی هشدار --

    def _on_region_menu_toggled(self, checked):
        """رفع درخواست: دکمه‌های «رسم دستی»/«کل کادر»/«تشخیص با AI» فقط با
        زدن دکمه‌ی اصلیِ «🖊 رسم محدوده» نمایان/مخفی می‌شوند."""
        self.region_type_row.setVisible(checked)

    def _selected_slot(self):
        idx = self.camera_grid.selected_index
        if idx is None or not (0 <= idx < len(self.camera_grid.slots)):
            return None
        return self.camera_grid.slots[idx]

    def _on_draw_line_toggled(self, checked):
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            if checked:
                self.draw_line_btn.blockSignals(True)
                self.draw_line_btn.setChecked(False)
                self.draw_line_btn.blockSignals(False)
                QMessageBox.information(
                    self, "رسم محدوده هشدار",
                    "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)، سپس دوباره این دکمه را بزنید."
                )
            return
        slot.set_draw_mode(checked)
        self._refresh_line_buttons()

    def _on_auto_region_clicked(self):
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            QMessageBox.information(
                self, "تشخیص خودکار محدوده",
                "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)."
            )
            return
        if slot.is_draw_mode():
            self.draw_line_btn.setChecked(False)
        if not slot.start_auto_full_frame_region():
            QMessageBox.information(
                self, "تشخیص خودکار محدوده",
                "ابتدا محدوده‌ی در انتظار/در حال ویرایشِ فعلی را با «✅ تایید» یا «❌ لغو» تمام کنید."
            )
            return
        self._refresh_line_buttons()

    def _on_ai_floor_region_clicked(self):
        """رفع درخواست «حالت جدید که خودش سطح زمین رو تشخیص بده»: برخلاف
        _on_auto_region_clicked (که فوری و همزمان کل کادر را می‌گذارد)،
        اینجا باید روی آخرین فریمِ زنده‌ی دوربین یک مدل واقعی اجرا شود -
        این کار در یک ترد جدا (FloorDetectThread) انجام می‌شود تا در طول
        چند ثانیه‌ی پردازش (به‌خصوص بار اول، وقتی مدل هنوز دانلود/بارگذاری
        نشده)، بقیه‌ی برنامه (از جمله پخش زنده‌ی سایر دوربین‌های باز) فریز
        نشود."""
        if self._floor_detect_thread is not None:
            return  # یک تشخیص از قبل در حال اجراست؛ از کلیک تکراری صرف‌نظر می‌شود
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)."
            )
            return
        if slot.has_pending_region() or slot.is_editing_region():
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "ابتدا محدوده‌ی در انتظار/در حال ویرایشِ فعلی را با «✅ تایید» یا «❌ لغو» تمام کنید."
            )
            return
        frame = slot.latest_raw_frame
        if frame is None:
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "هنوز هیچ فریمی از این دوربین دریافت نشده - چند لحظه صبر کنید تا تصویر زنده بیاید و دوباره امتحان کنید."
            )
            return
        if slot.is_draw_mode():
            self.draw_line_btn.setChecked(False)
        self.ai_floor_btn.setEnabled(False)
        self.ai_floor_btn.setText("⏳ در حال تحلیل تصویر...")
        thread = _get_floor_detect_thread()(frame.copy(), parent=self)
        thread.finished_signal.connect(
            lambda points, message, _slot=slot: self._on_ai_floor_detect_finished(_slot, points, message)
        )
        thread.finished.connect(self._on_ai_floor_detect_thread_done)
        self._floor_detect_thread = thread
        thread.start()

    def _on_ai_floor_detect_thread_done(self):
        self._floor_detect_thread = None
        self.ai_floor_btn.setText("🧭 تشخیص هوشمند زمین (AI)")
        self._refresh_line_buttons()

    def _on_ai_floor_detect_finished(self, slot, points, message):
        """رفع درخواست: نتیجه‌ی FloorDetectThread را روی تصویر همان دوربین
        (slot) به‌عنوان یک محدوده‌ی «در انتظار» و کاملاً قابل‌ویرایش قرار
        می‌دهد - دقیقاً مثل خروجی رسم دستی یا تشخیص خودکار کل تصویر. اگر
        مدل چیزی پیدا نکرد یا اصلاً در دسترس نبود، به‌جای گذاشتن یک محدوده‌ی
        نادرست/حدسی، فقط دلیل را روشن به کاربر می‌گوید و پیشنهاد می‌دهد از
        «تشخیص خودکار محدوده (کل تصویر)» یا رسم دستی استفاده کند."""
        if points is None:
            QMessageBox.warning(self, "تشخیص هوشمند زمین ناموفق بود", message)
            return
        # ممکن است در طول چند ثانیه‌ی پردازش، کاربر دوربین دیگری انتخاب
        # کرده یا محدوده‌ی دیگری روی همین دوربین شروع کرده باشد؛ در این
        # حالت نتیجه‌ی دیرکرد‌شده را بی‌سروصدا نادیده می‌گیریم تا چیزی
        # غیرمنتظره روی تصویر ظاهر نشود.
        if slot.has_pending_region() or slot.is_editing_region():
            return
        if not slot.start_ai_floor_region(points):
            return
        if self._selected_slot() is slot:
            self._refresh_line_buttons()

    def _on_confirm_line_clicked(self):
        slot = self._selected_slot()
        if slot is None or not slot.has_pending_region():
            return
        # رفع درخواست «قابلیت ادیت‌کردن»: اگر در حال ویرایشِ شکل/اندازه‌ی یک
        # محدوده‌ی از قبل تایید‌شده هستیم (رجوع کنید به
        # CameraSlotWidget.start_edit_region)، همین دکمه به‌جای پرسیدن یک
        # نام تازه، فقط نقاط جدید را روی همان محدوده ذخیره می‌کند - نامش
        # دست‌نخورده می‌ماند.
        if slot.is_editing_region():
            region = slot.save_region_edit()
            if region is not None and slot.cam is not None:
                self.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
            self._refresh_line_buttons()
            return
        name, ok = QInputDialog.getText(
            self, "نام‌گذاری محدوده",
            "نام این محدوده را وارد کنید (مثلاً اسم اتاق) - اختیاری:"
        )
        if not ok:
            return
        region = slot.confirm_region(name)
        if region is not None and slot.cam is not None:
            self.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
        # رفع درخواست: قبلاً اگر تشخیص شخص (YOLOv8) بارگذاری نشده بود، کاربر
        # محدوده را تایید می‌کرد و فکر می‌کرد همه‌چیز فعال شده، در حالی که
        # هشدار هرگز صادر نمی‌شد و هیچ توضیحی هم نمی‌دید. اگر همین الان
        # می‌دانیم وضعیت تشخیص شخص False است، بلافاصله (نه فقط با برچسب
        # کوچک زیر تصویر) به کاربر اطلاع می‌دهیم.
        if region is not None and slot._detector_available is False:
            QMessageBox.warning(
                self, "محدوده ذخیره شد، ولی هشدار فعلاً کار نمی‌کند",
                "این محدوده ذخیره شد و روی تصویر دیده می‌شود، اما تشخیص شخص (YOLOv8) "
                "روی این برنامه بارگذاری نشده، پس ورود کسی به این محدوده هنوز هشدار "
                "(کادر قرمز/بوق) صادر نمی‌کند.\n\n"
                "برای رفع: مطمئن شوید کتابخانه‌ی ultralytics نصب است و فایل وزن مدل "
                "(yolov8n.pt) در دسترس است، یا نسخه‌ی exe را با بسته‌بندی درستِ این "
                "فایل دوباره بسازید."
            )
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(False)
        self.draw_line_btn.blockSignals(False)
        self._refresh_line_buttons()

    def _on_redraw_line_clicked(self):
        slot = self._selected_slot()
        if slot is None:
            return
        # رفع درخواست «قابلیت ادیت‌کردن»: در حالت ویرایشِ یک محدوده‌ی
        # قبلاً تایید‌شده، این دکمه فقط ویرایش را لغو می‌کند (خودِ محدوده با
        # شکل/اندازه‌ی قبلی دست‌نخورده می‌ماند) - نه اینکه محدوده حذف شود.
        if slot.is_editing_region():
            slot.cancel_region_edit()
        else:
            slot.cancel_pending_region()
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(False)
        self.draw_line_btn.blockSignals(False)
        self._refresh_line_buttons()

    def _on_manage_regions_clicked(self):
        slot = self._selected_slot()
        if slot is None:
            return

        def _on_changed():
            if slot.cam is not None:
                self.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
            self._refresh_line_buttons()

        dialog = RegionManagerDialog(slot, _on_changed, self)
        dialog.exec()

    def _on_image_settings_clicked(self):
        """باز کردن دیالوگ «تنظیمات تصویر» برای دوربینِ خانه‌ی انتخاب‌شده -
        هر دوربین پروفایل مستقل خودش را دارد (رجوع کنید به
        image_settings_dialog.py)."""
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            return
        dialog = ImageSettingsDialog(slot, self.camera_store, self)
        dialog.exec()

    def on_region_alert(self, cam, number, name):
        """رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار هر دوربین
        (از CameraSlotWidget._on_region_entered)، یک ردیف متنی قرمز هم در
        پنل تشخیص چهره (سمت راست) ثبت می‌شود تا سابقه‌ی هشدارها هم در دسترس
        باشد. ``cam``: کل دیکشنری دوربین (نه فقط اسم) تا nvr_id/channel هم
        برای لینک «پخش ویدیوی NVR» در دیالوگ گزارش‌ها ذخیره شود."""
        camera_name = cam.get("name", "")
        timestamp = time.strftime("%H:%M:%S")
        label = f"شماره {number}" + (f" / {name}" if name else "")
        text = f"[{timestamp}] {camera_name}\n⚠ ورود به محدوده {label}"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#e74c3c"))
        self.face_panel_list.insertItem(0, item)
        # رفع درخواست: علاوه بر نمایش موقت در همین پنل، ثبت دائمی روی سیستم
        # (report_store.py) - قابل جست‌وجو/خروجی از دیالوگ «گزارش‌ها».
        report_store.log_region_alert(camera_name, number, name,
                                       nvr_id=cam.get("nvr_id"), channel=cam.get("channel"))
        while self.face_panel_list.count() > 300:
            self.face_panel_list.takeItem(self.face_panel_list.count() - 1)

    def on_fire_event(self, cam, kind: str, crop_frame, confidence: float):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: با هر تشخیص تصویری
        آتش/دود روی یکی از دوربین‌ها (از CameraSlotWidget._on_fire_event)،
        یک ردیف با تصویر برش‌خورده در پنل «هشدارهای حریق و دود» ثبت و در
        گزارش‌ها ذخیره می‌شود - دقیقاً همان الگوی on_region_alert بالا."""
        camera_name = cam.get("name", "")
        timestamp = time.strftime("%H:%M:%S")
        label = "🔥 آتش" if kind == "fire" else "💨 دود"
        text = f"[{timestamp}] {camera_name}\n⚠ {label} شناسایی شد ({confidence * 100:.0f}%)"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#e74c3c"))
        pixmap = _bgr_to_pixmap(crop_frame) if crop_frame is not None else None
        if pixmap is not None:
            item.setIcon(QIcon(pixmap))
        self.fire_panel_list.insertItem(0, item)
        report_store.log_fire_smoke_visual(camera_name, kind, crop_frame=crop_frame,
                                            confidence=confidence,
                                            nvr_id=cam.get("nvr_id"), channel=cam.get("channel"))
        while self.fire_panel_list.count() > 300:
            self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # با هر تشخیص آتش/دود: آژیر ممتد + ارسال سیگنال به پنل ساختمان.
        if self.alarm_player is not None:
            self.alarm_player.start()
        if self.building_fire is not None:
            self.building_fire.trigger(camera_name, kind, confidence)

    # ---------------------------------------------- پنل‌های فیزیکی اعلام حریق -

    def _start_fire_alarm_monitor(self, panel):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: برای این پنل یک ترد
        مانیتور پس‌زمینه (رجوع کنید به fire_alarm_io.FireAlarmMonitorThread)
        شروع می‌کند که با تغییر واقعی وضعیت، سیگنال می‌فرستد."""
        self._stop_fire_alarm_monitor(panel["id"])  # جلوگیری از دو ترد هم‌زمان روی یک پنل
        thread = FireAlarmMonitorThread(panel, self)
        thread.alarm_triggered.connect(lambda p=panel: self.on_fire_alarm_panel_triggered(p))
        thread.alarm_cleared.connect(lambda p=panel: self.on_fire_alarm_panel_cleared(p))
        thread.start()
        self._fire_alarm_threads[panel["id"]] = thread

    def _stop_fire_alarm_monitor(self, panel_id):
        thread = self._fire_alarm_threads.pop(panel_id, None)
        if thread is not None:
            thread.stop()

    def on_fire_alarm_panel_triggered(self, panel):
        """آلارم یک پنل/سنسور فیزیکی اعلام حریق همین الان فعال شده - رفع
        درخواست «سیستم تشخیص دود و اعلام حریق»: بوق هشدار + ردیف قرمز در
        پنل + ثبت دائمی در گزارش‌ها، دقیقاً مثل تشخیص تصویری."""
        timestamp = time.strftime("%H:%M:%S")
        text = f"[{timestamp}] {panel['name']}\n🔥 آلارم پنل اعلام حریق فعال شد!"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#e74c3c"))
        self.fire_panel_list.insertItem(0, item)
        _play_alarm_beep()
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # آلارم پنل فیزیکی هم آژیر ممتد + سیگنال به پنل ساختمان را فعال می‌کند.
        if self.alarm_player is not None:
            self.alarm_player.start()
        if self.building_fire is not None:
            self.building_fire.trigger(panel["name"], "fire", 1.0)
        report_store.log_fire_alarm_panel(panel["name"], active=True)
        while self.fire_panel_list.count() > 300:
            self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)

    def on_fire_alarm_panel_cleared(self, panel):
        timestamp = time.strftime("%H:%M:%S")
        text = f"[{timestamp}] {panel['name']}\n✅ وضعیت پنل اعلام حریق به حالت عادی برگشت"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#2ecc71"))
        self.fire_panel_list.insertItem(0, item)
        # با عادی‌شدن پنل، آژیر ممتد هم قطع می‌شود.
        self.stop_fire_alarm_sound()
        report_store.log_fire_alarm_panel(panel["name"], active=False)
        while self.fire_panel_list.count() > 300:
            self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)

    def _on_building_fire_log(self, msg):
        """نمایش پیام فارسی نتیجه‌ی ارسال سیگنال به پنل ساختمان
        (از ترد پس‌زمینه‌ی BuildingFireOutput) در لیست رویدادهای حریق."""
        try:
            item = QListWidgetItem(f"🏢 {msg}")
            item.setWordWrap(True)
            self.fire_panel_list.insertItem(0, item)
            while self.fire_panel_list.count() > 300:
                self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)
        except Exception:
            pass

    def stop_fire_alarm_sound(self):
        """قطع صدای آژیر حریق (دکمه‌ی 🔇 قطع صدای هشدار)."""
        if self.alarm_player is not None:
            try:
                self.alarm_player.stop()
            except Exception:
                pass

    def open_building_fire_settings(self):
        """باز کردن دیالوگ تنظیمات اتصال به سیستم حریق ساختمان + صدای هشدار."""
        if not _BUILDING_FIRE_AVAILABLE or BuildingFireSettingsDialog is None:
            QMessageBox.warning(
                self, "اتصال به سیستم حریق ساختمان",
                "فایل‌های اتصال ساختمان (building_fire_output.py، alarm_sound.py و\n"
                "building_fire_settings_dialog.py) کنار main.py پیدا نشد.\n"
                "آن‌ها را از پکیج ias-cms-fire-building کپی کنید.")
            return
        dlg = BuildingFireSettingsDialog(self, log_callback=self._on_building_fire_log)
        if dlg.exec():
            self.building_fire.reload()
            self.alarm_player.reload()

    def _refresh_line_buttons(self):
        """دکمه‌های «تایید و نام‌گذاری»/«لغو رسم»/«مدیریت محدوده‌ها» و
        وضعیت تیک‌خورده‌ی «رسم محدوده هشدار» را بر اساس خانه‌ی فعلاً
        انتخاب‌شده به‌روز می‌کند - چون هر خانه محدوده‌های مستقل خودش را
        دارد."""
        slot = self._selected_slot()
        has_pending = slot.has_pending_region() if slot is not None else False
        has_confirmed = bool(slot.regions) if slot is not None else False
        is_editing = slot.is_editing_region() if slot is not None else False
        self.confirm_line_btn.setEnabled(bool(has_pending))
        self.redraw_line_btn.setEnabled(bool(has_pending))
        self.manage_regions_btn.setEnabled(bool(has_confirmed))
        # دکمه‌ی تنظیمات تصویر فقط وقتی یک خانه‌ی دارای دوربین انتخاب شده.
        self.image_settings_btn.setEnabled(slot is not None and slot.cam is not None)
        # رفع درخواست: دکمه‌های «تایید»/«لغو رسم» فقط وقتی یک محدوده‌ی
        # در-انتظار یا در-حال-ویرایش وجود دارد نمایان می‌شوند (نه همیشه).
        self.region_action_row.setVisible(bool(has_pending or is_editing))
        # رفع درخواست «قابلیت ادیت‌کردن»: وقتی یک محدوده‌ی «در انتظار» (چه
        # تازه رسم‌شده، چه محدوده‌ی خودکارِ کل تصویر، چه در حال ویرایش شکلِ
        # یک محدوده‌ی قبلی) روی تصویر هست، رسم/تشخیص خودکارِ تازه غیرفعال
        # می‌شود تا دو جریان هم‌زمان با هم قاطی نشوند؛ متن دکمه‌های
        # تایید/لغو هم بسته به این‌که «ویرایش یک محدوده‌ی موجود» است یا
        # «محدوده‌ی تازه»، عوض می‌شود.
        self.draw_line_btn.setEnabled(not has_pending)
        self.auto_region_btn.setEnabled(not has_pending)
        # رفع درخواست: دکمه‌ی «تشخیص هوشمند زمین» هم مثل «تشخیص خودکار
        # (کل تصویر)» وقتی محدوده‌ای در انتظار/در حال ویرایش است غیرفعال
        # می‌شود؛ علاوه بر آن، اگر همین الان یک تشخیص در حال اجرا باشد
        # (self._floor_detect_thread) هم غیرفعال می‌ماند تا این تابع (که از
        # چند رویداد دیگر - مثل تغییر انتخاب دوربین - هم صدا زده می‌شود)
        # وسط پردازش دوباره فعالش نکند.
        self.ai_floor_btn.setEnabled(not has_pending and self._floor_detect_thread is None)
        if is_editing:
            self.confirm_line_btn.setText("💾 ذخیره ویرایش شکل")
            self.redraw_line_btn.setText("↩ لغو ویرایش")
        else:
            self.confirm_line_btn.setText("✅ تایید و نام‌گذاری")
            self.redraw_line_btn.setText("❌ لغو رسم")
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(bool(slot.is_draw_mode()) if slot is not None else False)
        self.draw_line_btn.blockSignals(False)

    # ------------------------------------------------------- camera list ---

    def reload_camera_list(self):
        self.camera_list.clear()

        # NVRها به‌صورت گره‌های والد و کانال‌های آن‌ها به‌صورت فرزند نمایش داده می‌شوند.
        for nvr in self.camera_store.nvrs:
            channel_count = len(self.camera_store.cameras_for_nvr(nvr["id"]))
            nvr_item = QTreeWidgetItem([f"🖥 {nvr['name']}  ({nvr['ip']}) — {channel_count} کانال"])
            nvr_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "nvr", "id": nvr["id"]})
            self.camera_list.addTopLevelItem(nvr_item)
            for cam in self.camera_store.cameras_for_nvr(nvr["id"]):
                # رفع درخواست: در صورت شناسایی IP واقعی دوربین شبکه‌ای پشت این
                # کانال (متفاوت از IP خود NVR)، جلوی نام کانال هم نمایش داده می‌شود.
                cam_label = cam["name"]
                if cam.get("camera_ip"):
                    cam_label += f"  ({cam['camera_ip']})"
                cam_item = QTreeWidgetItem([cam_label])
                cam_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "camera", "id": cam["id"]})
                nvr_item.addChild(cam_item)
            nvr_item.setExpanded(True)

        # دوربین‌های مستقل (بدون NVR)
        for cam in self.camera_store.standalone_cameras():
            cam_item = QTreeWidgetItem([cam["name"]])
            cam_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "camera", "id": cam["id"]})
            self.camera_list.addTopLevelItem(cam_item)

    def _scan_credentials(self):
        """رفع درخواست: نام‌کاربری/رمز کادر بالای پنل اسکن شبکه را برمی‌گرداند
        (این مقادیر هرگز روی دیسک ذخیره نمی‌شوند - فقط در حافظه‌ی همین کادر)."""
        user = self.scan_user_input.text().strip() or "admin"
        pwd = self.scan_pass_input.text()
        return user, pwd

    def _auto_display_camera(self, cam) -> bool:
        """رفع درخواست: بعد از افزوده‌شدن هر دوربین، به‌صورت خودکار در اولین
        خانه‌ی خالی شبکه‌ی نمایش باز می‌شود. اگر رمز عبور لازم و نامعلوم باشد
        از کاربر پرسیده می‌شود؛ صرف‌نظر کردن از رمز به‌معنای «جای خالی نبود»
        نیست، پس True برمی‌گرداند (خطای واقعی فقط پر بودن شبکه‌ی نمایش است)."""
        if not self._ensure_password(cam):
            return True
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        return self.camera_grid.assign_camera(cam, rtsp_url)

    def open_add_camera_dialog(self, prefill_ip=None, detected_path=None, detected_full_url=None,
                                prefill_user=None, prefill_pass=None):
        dialog = AddCameraDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user is not None:
            dialog.user_input.setText(prefill_user)
        if prefill_pass is not None:
            dialog.pass_input.setText(prefill_pass)
        if detected_path is not None or detected_full_url is not None:
            dialog.set_detected_stream(path=detected_path, full_url=detected_full_url)
        if dialog.exec():
            data = dialog.get_camera_data()
            cam = self.camera_store.add_camera(
                data["name"], data["ip"], data["port"], data["user"], data["pass"], data["path"],
                full_url=data.get("full_url"),
            )
            self.reload_camera_list()
            # رفع درخواست: دوربین تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
            self.open_live_view(cam)

    def open_add_nvr_dialog(self, prefill_ip=None, detected_brand=None, detected_onvif_port=None,
                             prefill_user=None, prefill_pass=None):
        dialog = AddNVRDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user is not None:
            dialog.user_input.setText(prefill_user)
        if prefill_pass is not None:
            dialog.pass_input.setText(prefill_pass)
        if detected_brand:
            dialog.set_detected_brand(brand=detected_brand, onvif_port=detected_onvif_port)
        if dialog.exec():
            data = dialog.get_nvr_data()
            nvr = self.camera_store.add_nvr(
                name=data["name"], ip=data["ip"], rtsp_port=data["rtsp_port"],
                onvif_port=data["onvif_port"], user=data["user"],
                pwd=data["pass"], brand=data["brand"],
                camera_brand=data["camera_brand"],
            )
            added_cams = [
                self._add_channel_from_entry(nvr, entry, default_name)
                for entry, default_name in dialog.get_selected_channels()
            ]
            self.reload_camera_list()
            # رفع درخواست: هر کانال تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
            not_shown = sum(1 for cam in added_cams if not self._auto_display_camera(cam))
            msg = f"NVR «{nvr['name']}» با {len(added_cams)} کانال اضافه شد."
            if not_shown:
                msg += (
                    f"\n{not_shown} کانال به دلیل پر بودن شبکه‌ی نمایش به‌صورت خودکار باز "
                    "نشدند؛ برای باز کردن آن‌ها، تعداد نمایش هم‌زمان را افزایش دهید یا "
                    "روی آن‌ها در لیست دابل‌کلیک کنید."
                )
            QMessageBox.information(self, "NVR اضافه شد", msg)

    def _add_channel_from_entry(self, nvr, entry, default_name):
        camera_ip = entry.get("camera_ip") or ""
        if entry["is_full_url"]:
            # ONVIF: خودِ URL کشف‌شده معمولاً از قبل مستقیماً به IP دوربین
            # اشاره می‌کند (رجوع کنید به nvr_scanner._extract_camera_ip_from_uri).
            return self.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path="", full_url=entry["path_or_url"],
                camera_ip=camera_ip,
            )
        if camera_ip and entry.get("direct"):
            # رفع درخواست: این کانال با یک مسیر واقعاً تست‌شده روی خودِ
            # دوربین تایید شده (نه پروکسی NVR که خطا می‌داد)؛ دقیقاً مثل
            # افزودن یک دوربین تکی، مستقیماً به همان IP/پورت ۵۵۴ دوربین وصل
            # می‌شویم - در حالی که کانال همچنان زیر همین NVR در لیست می‌ماند.
            return self.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path=entry["path_or_url"],
                camera_ip=camera_ip, connect_ip=camera_ip, connect_port="554",
            )
        return self.camera_store.add_channel_camera(
            nvr, entry["channel"], default_name, path=entry["path_or_url"], camera_ip=camera_ip,
        )

    def rescan_nvr(self, nvr_id):
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        dialog = AddNVRDialog(self)
        dialog.setWindowTitle(f"بازخوانی کانال‌های «{nvr['name']}»")
        dialog.name_input.setText(nvr["name"])
        dialog.ip_input.setText(nvr["ip"])
        dialog.rtsp_port_input.setText(str(nvr.get("rtsp_port", "554")))
        dialog.onvif_port_input.setText(str(nvr.get("onvif_port", "") or ""))
        dialog.user_input.setText(nvr.get("user", ""))
        dialog.pass_input.setText(nvr.get("pass", ""))
        idx = dialog.brand_combo.findData(nvr.get("brand", "auto"))
        if idx >= 0:
            dialog.brand_combo.setCurrentIndex(idx)
        cam_brand_idx = dialog.camera_brand_combo.findData(nvr.get("camera_brand", "auto"))
        if cam_brand_idx >= 0:
            dialog.camera_brand_combo.setCurrentIndex(cam_brand_idx)

        if dialog.exec():
            data = dialog.get_nvr_data()
            self.camera_store.update_nvr(nvr_id, **data)
            existing_channels = {c.get("channel") for c in self.camera_store.cameras_for_nvr(nvr_id)}
            added_cams = []
            for entry, default_name in dialog.get_selected_channels():
                if entry["channel"] in existing_channels:
                    continue  # این کانال قبلاً اضافه شده است
                added_cams.append(self._add_channel_from_entry(nvr, entry, default_name))
            self.reload_camera_list()
            # رفع درخواست: کانال‌های تازه‌کشف‌شده هم اتوماتیک به پنجره‌ی نمایش اضافه شوند.
            for cam in added_cams:
                self._auto_display_camera(cam)
            QMessageBox.information(self, "بازخوانی کامل شد", f"{len(added_cams)} کانال جدید اضافه شد.")

    def delete_nvr(self, nvr_id):
        confirm = QMessageBox.question(
            self, "تأیید حذف", "آیا از حذف این NVR و همه‌ی کانال‌های ثبت‌شده‌ی آن مطمئن هستید؟"
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.camera_store.remove_nvr(nvr_id, cascade=True)
            self.reload_camera_list()

    def open_nvr_storage(self, nvr_id):
        """رفع درخواست: وضعیت هارددیسک NVR و جست‌وجوی بازه‌های واقعاً
        ضبط‌شده روی آن را نشان می‌دهد (رجوع کنید به nvr_storage_dialog.py /
        nvr_storage_api.py). قبل از باز کردن دیالوگ، رمز NVR باید در حافظه
        موجود باشد (دقیقاً همان جریان _ensure_password که برای پخش زنده
        استفاده می‌شود، چون این قابلیت هم به یوزرنیم/رمز NVR نیاز دارد)."""
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        if not nvr.get("pass"):
            pwd, ok = QInputDialog.getText(
                self, "رمز عبور مورد نیاز",
                f"برای بررسی هارد/ضبط‌های NVR «{nvr['name']}»، رمز عبور آن را وارد کنید:",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            nvr["pass"] = pwd
            for sibling in self.camera_store.cameras_for_nvr(nvr["id"]):
                sibling["pass"] = pwd

        channels = sorted(
            ((c.get("channel"), c.get("name", "")) for c in self.camera_store.cameras_for_nvr(nvr_id)
             if c.get("channel") is not None),
            key=lambda x: x[0],
        )
        dialog = NVRStorageDialog(nvr, channels, self)
        dialog.exec()

    def open_nvr_webview(self, nvr_id):
        """رفع درخواست: باز کردن پنل وب واقعی NVR داخل برنامه (با موتور
        Chromium از طریق PyQt6-WebEngine)، برای دستگاه‌هایی که سرویس RTSP
        آن‌ها به‌دلیل باگ فریمور با کلاینت‌های استاندارد (این برنامه، VLC،
        live555) کار نمی‌کند. این پنل هم پخش زنده‌ی سالم را ممکن می‌کند و
        هم لیست واقعی کانال‌های متصل به NVR را (با دکمه‌ی «دریافت لیست
        کانال‌ها») مستقیماً از خودِ NVR می‌گیرد."""
        if not _WEBENGINE_AVAILABLE:
            QMessageBox.warning(
                self, "بسته‌ی موردنیاز نصب نیست",
                "برای این قابلیت باید بسته‌ی PyQt6-WebEngine نصب باشد:\n\n"
                "pip install PyQt6-WebEngine\n\n"
                "بعد از نصب، برنامه را دوباره اجرا کنید."
            )
            return
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        dialog = NVRWebViewDialog(nvr, self)
        dialog.channels_fetched.connect(lambda ch_list: self._on_nvr_channels_fetched(nvr, ch_list))
        dialog.exec()

    def _on_nvr_channels_fetched(self, nvr, ch_list):
        """کانال‌هایی که از پنل وب NVR دریافت شده‌اند (via g_deviceList) را،
        در صورت تأیید کاربر، به لیست دوربین‌های زیر همین NVR اضافه می‌کند.

        رفع درخواست: هر کانالی که IP واقعی دوربین شبکه‌ای پشت آن هم از پنل وب
        NVR شناسایی شده باشد (نه فقط شماره کانال)، دقیقاً مثل افزودن یک
        دوربین تکی مستقیماً به همان IP دوربین (نه IP خودِ NVR) و با همان
        یوزرنیم/رمزی که برای این NVR در برنامه ثبت شده وصل می‌شود؛ کانال
        همچنان زیر همین NVR در لیست گروه‌بندی می‌ماند. برای کانال‌هایی که
        IP دوربینشان مشخص نشده (مثلاً کانال آنالوگ)، مثل قبل از طریق خودِ
        NVR اضافه می‌شوند.
        """
        if not ch_list:
            return
        existing_channels = {c.get("channel") for c in self.camera_store.cameras_for_nvr(nvr["id"])}
        new_entries = []
        for i, dev in enumerate(ch_list):
            # ساختار دقیق آیتم‌های g_deviceList ممکن است بسته به مدل کمی
            # فرق کند؛ چند نام کلید رایج را امتحان می‌کنیم.
            chn = dev.get("chn") or dev.get("channel") or (i + 1)
            if chn in existing_channels:
                continue
            name = dev.get("name") or dev.get("dev_name") or f"کانال {chn}"
            # رفع درخواست: g_deviceList معمولاً IP واقعی دوربین شبکه‌ای متصل
            # به این کانال را هم دارد (کلیدهای رایج: ip / IP / ipAddress)؛
            # اگر موجود باشد همراه با نام/شماره کانال ثبت می‌شود.
            cam_ip = dev.get("ip") or dev.get("IP") or dev.get("ipAddress") or ""
            new_entries.append((chn, name, cam_ip))

        if not new_entries:
            QMessageBox.information(self, "چیزی برای افزودن نیست",
                                     "همه‌ی این کانال‌ها قبلاً به لیست شما اضافه شده‌اند.")
            return

        names = "\n".join(
            f"- کانال {c} ({n})" + (f"  —  IP دوربین: {ip} (اتصال مستقیم)" if ip else "  —  بدون IP دوربین (اتصال از طریق NVR)")
            for c, n, ip in new_entries
        )
        confirm = QMessageBox.question(
            self, "افزودن کانال‌ها",
            f"{len(new_entries)} کانال جدید از این NVR پیدا شد:\n\n{names}\n\n"
            "کانال‌هایی که IP دوربینشان مشخص است، مستقیماً به همان IP وصل "
            "می‌شوند (با یوزرنیم/رمز همین NVR)؛ در حال بررسی مسیر اتصال هر "
            "کدام هستیم که ممکن است چند ثانیه طول بکشد.\n\n"
            "آیا به لیست «دوربین‌ها و NVRهای من» اضافه شوند؟"
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage("در حال بررسی مسیر اتصال مستقیم دوربین‌ها...")
        self._direct_probe_thread = DirectCameraProbeThread(
            new_entries, nvr.get("user", ""), nvr.get("pass", ""), nvr.get("rtsp_port", "554"),
        )
        self._direct_probe_thread.progress_signal.connect(self.statusBar().showMessage)
        self._direct_probe_thread.finished_signal.connect(
            lambda results: self._on_direct_probe_finished(nvr, results)
        )
        self._direct_probe_thread.start()

    def _on_direct_probe_finished(self, nvr, results):
        """رفع درخواست: بعد از پیدا شدن (یا نشدن) مسیر مستقیم RTSP هر دوربین
        (در ترد جدا - نگاه کنید به DirectCameraProbeThread)، کانال‌ها ثبت
        می‌شوند: کانال‌های دارای IP دوربین با اتصال مستقیم به آن IP (پورت
        ۵۵۴ خودِ دوربین + یوزرنیم/رمز همین NVR)، و بقیه مثل قبل از طریق NVR."""
        self.statusBar().clearMessage()
        added_cams = []
        for r in results:
            if r["cam_ip"]:
                cam = self.camera_store.add_channel_camera(
                    nvr, r["chn"], r["name"], path=r["path"] or "",
                    camera_ip=r["cam_ip"], connect_ip=r["cam_ip"], connect_port="554",
                )
            else:
                cam = self.camera_store.add_channel_camera(nvr, r["chn"], r["name"], path="")
            added_cams.append(cam)

        self.reload_camera_list()
        # رفع درخواست: هر کانال تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
        not_shown = sum(1 for cam in added_cams if not self._auto_display_camera(cam))
        no_path_found = sum(1 for r in results if r["cam_ip"] and not r["path"])
        msg = f"{len(added_cams)} کانال اضافه شد."
        if no_path_found:
            msg += (
                f"\nبرای {no_path_found} دوربین، مسیر RTSP به‌طور خودکار پیدا نشد؛ "
                "برای این‌ها روی «ویرایش» کلیک کنید و مسیر را دستی وارد یا با "
                "«تشخیص خودکار» پیدا کنید."
            )
        if not_shown:
            msg += (
                f"\n{not_shown} کانال به دلیل پر بودن شبکه‌ی نمایش به‌صورت خودکار باز "
                "نشدند؛ برای باز کردن آن‌ها، تعداد نمایش هم‌زمان را افزایش دهید یا "
                "روی آن‌ها در لیست دابل‌کلیک کنید."
            )
        QMessageBox.information(self, "افزودن کامل شد", msg)

    def show_camera_context_menu(self, pos):
        item = self.camera_list.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        menu = QMenu(self)
        if data["type"] == "camera":
            edit_action = QAction("ویرایش", self)
            edit_action.triggered.connect(lambda: self.edit_camera(data["id"]))
            delete_action = QAction("حذف", self)
            delete_action.triggered.connect(lambda: self.delete_camera(data["id"]))
            menu.addAction(edit_action)
            menu.addAction(delete_action)
        else:  # nvr
            rescan_action = QAction("بازخوانی کانال‌ها", self)
            rescan_action.triggered.connect(lambda: self.rescan_nvr(data["id"]))
            # رفع درخواست: برای NVRهایی که سرویس RTSP‌شان مشکل فریمور دارد
            # (پخش زنده و بازخوانی کانال از طریق RTSP/ONVIF جواب نمی‌دهد)،
            # یک راه جایگزین: باز کردن پنل وب واقعی خود NVR داخل برنامه (با
            # موتور Chromium) که هم پخش زنده در آن سالم کار می‌کند و هم
            # می‌توان لیست کانال‌های واقعی را از همان‌جا گرفت.
            webview_action = QAction("باز کردن پنل وب NVR (برای دستگاه‌های با RTSP خراب)", self)
            webview_action.triggered.connect(lambda: self.open_nvr_webview(data["id"]))
            # رفع درخواست: بررسی وضعیت هارددیسک NVR و جست‌وجوی بازه‌های
            # زمانی‌ای که واقعاً روی هارد خودِ NVR ضبط شده - از طریق همان
            # API وب رسمی سازنده (رجوع کنید به nvr_storage_api.py).
            storage_action = QAction("🗄 هارد و ضبط‌های NVR", self)
            storage_action.triggered.connect(lambda: self.open_nvr_storage(data["id"]))
            delete_action = QAction("حذف NVR و همه کانال‌ها", self)
            delete_action.triggered.connect(lambda: self.delete_nvr(data["id"]))
            menu.addAction(rescan_action)
            menu.addAction(webview_action)
            menu.addAction(storage_action)
            menu.addAction(delete_action)
        menu.exec(self.camera_list.mapToGlobal(pos))

    def edit_camera(self, cam_id):
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        dialog = AddCameraDialog(self, existing_cam=cam)
        if dialog.exec():
            data = dialog.get_camera_data()
            self.camera_store.update_camera(cam_id, **data)
            self.reload_camera_list()

    def delete_camera(self, cam_id):
        confirm = QMessageBox.question(self, "تأیید حذف", "آیا از حذف این دوربین از لیست مطمئن هستید؟")
        if confirm == QMessageBox.StandardButton.Yes:
            self.camera_store.remove_camera(cam_id)
            self.reload_camera_list()

    def on_camera_item_activated(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data["type"] != "camera":
            return
        cam = self.camera_store.get_camera(data["id"])
        if cam:
            self.open_live_view(cam)

    def on_scan_result_selected(self, item):
        text = item.text()
        if " " not in text:
            return
        ip = text.split(" ")[0]

        if self.detect_thread is not None and self.detect_thread.isRunning():
            return  # یک تشخیص در حال اجراست؛ منتظر پایان آن بمانیم.

        # دابل‌کلیک یعنی فقط همین یک دستگاه (صف خالی است).
        self._detect_queue = []
        self._start_device_detect(ip)

    def on_add_selected_scan_results(self):
        """رفع درخواست: چند نتیجه‌ی اسکن هم‌زمان انتخاب و پشت‌سرهم اضافه شوند.
        چون هر تشخیص (و در ادامه‌ی آن، دیالوگ افزودن دوربین/NVR) نیاز به
        تعامل کاربر دارد، دستگاه‌ها یکی‌یکی (نه هم‌زمان) پردازش می‌شوند: بعد
        از بسته‌شدن دیالوگ مربوط به هر دستگاه، خودکار سراغ دستگاه بعدی در صف
        می‌رود."""
        if self.detect_thread is not None and self.detect_thread.isRunning():
            return

        ips = []
        # رفع درخواست: انتخاب چندتایی دیگر با Ctrl/Shift نیست؛ آیتم‌هایی که
        # چک‌باکس‌شان تیک خورده (Qt.CheckState.Checked) به‌عنوان انتخاب‌شده
        # در نظر گرفته می‌شوند.
        for i in range(self.scan_result_list.count()):
            item = self.scan_result_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            text = item.text()
            if " " not in text:
                continue
            ip = text.split(" ")[0]
            if ip not in ips:
                ips.append(ip)

        if not ips:
            QMessageBox.information(
                self, "موردی انتخاب نشده",
                "ابتدا چک‌باکس کنار یک یا چند دستگاه را از لیست نتایج اسکن تیک بزنید."
            )
            return

        self._detect_queue = ips[1:]
        self._start_device_detect(ips[0])

    def _start_device_detect(self, ip):
        # رفع درخواست: دیگر از کاربر «دوربین تکی یا NVR؟» پرسیده نمی‌شود؛
        # DeviceDetectThread با یک اتصال آزمایشی (ONVIF یا تست کانال‌ها)
        # خودش نوع دستگاه را تشخیص می‌دهد - رجوع کنید به device_detect.py.
        self._detect_ip = ip
        self.scan_result_list.setEnabled(False)
        self.add_selected_scan_btn.setEnabled(False)
        self.detect_status_label.setText(f"در حال تشخیص نوع دستگاه {ip}...")

        # رفع درخواست: تشخیص نوع دستگاه (و در ادامه، اتصال به دوربین/NVR) با
        # نام‌کاربری/رمز کادر بالای پنل اسکن شبکه انجام می‌شود، نه مقدار ثابت
        # admin/بدون‌رمز.
        scan_user, scan_pass = self._scan_credentials()
        self.detect_thread = DeviceDetectThread(
            ip=ip,
            open_ports=self._scan_ports_by_ip.get(ip, []),
            rtsp_port="554",
            user=scan_user,
            pwd=scan_pass,
            parent=self,
        )
        self.detect_thread.progress_signal.connect(self.detect_status_label.setText)
        self.detect_thread.detected_signal.connect(self._on_device_detected)
        self.detect_thread.failed_signal.connect(self._on_device_detect_failed)
        self.detect_thread.start()

    def _reset_detect_ui(self):
        self.scan_result_list.setEnabled(True)
        self.detect_status_label.setText("")

    def _advance_detect_queue(self):
        """بعد از بسته‌شدن دیالوگ دستگاه فعلی، اگر مورد دیگری در صفِ انتخاب
        چندتایی باقی مانده، تشخیص آن را شروع می‌کند."""
        if self._detect_queue:
            next_ip = self._detect_queue.pop(0)
            self._start_device_detect(next_ip)
        else:
            self.add_selected_scan_btn.setEnabled(True)

    def _on_device_detected(self, kind, payload):
        ip = self._detect_ip
        self._reset_detect_ui()
        scan_user, scan_pass = self._scan_credentials()
        if kind == "nvr":
            self.open_add_nvr_dialog(
                prefill_ip=ip,
                detected_brand=payload.get("brand"),
                detected_onvif_port=payload.get("onvif_port"),
                prefill_user=scan_user,
                prefill_pass=scan_pass,
            )
        else:
            self.open_add_camera_dialog(
                prefill_ip=ip,
                detected_path=payload.get("path"),
                detected_full_url=payload.get("full_url"),
                prefill_user=scan_user,
                prefill_pass=scan_pass,
            )
        self._advance_detect_queue()

    def _on_device_detect_failed(self, msg):
        ip = self._detect_ip
        self._reset_detect_ui()

        # تشخیص خودکار با نام کاربری/رمز پیش‌فرض (admin/بدون رمز) ممکن است روی
        # دستگاه‌هایی با اطلاعات ورود سفارشی شکست بخورد؛ در این حالت کاربر
        # می‌تواند به‌صورت دستی و با وارد کردن رمز درست، نوع دستگاه را انتخاب کند.
        box = QMessageBox(self)
        box.setWindowTitle("تشخیص خودکار ناموفق بود")
        box.setText(f"{msg}\n\nنوع دستگاه {ip} را به‌صورت دستی مشخص کنید:")
        camera_btn = box.addButton("دوربین تکی", QMessageBox.ButtonRole.AcceptRole)
        nvr_btn = box.addButton("NVR (چند کاناله)", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("انصراف", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        scan_user, scan_pass = self._scan_credentials()
        if box.clickedButton() == camera_btn:
            self.open_add_camera_dialog(prefill_ip=ip, prefill_user=scan_user, prefill_pass=scan_pass)
        elif box.clickedButton() == nvr_btn:
            self.open_add_nvr_dialog(prefill_ip=ip, prefill_user=scan_user, prefill_pass=scan_pass)
        self._advance_detect_queue()

    # ------------------------------------------------------ live view -----

    def open_live_view(self, cam: dict):
        """دوربین انتخاب‌شده را در اولین خانه‌ی خالی شبکه‌ی نمایش باز می‌کند.
        اگر همان دوربین از قبل باز باشد، فقط همان خانه انتخاب (highlight)
        می‌شود. اگر هیچ خانه‌ی خالی نباشد، از کاربر می‌خواهد یکی را ببندد یا
        تعداد نمایش هم‌زمان را افزایش دهد."""
        if not self._ensure_password(cam):
            return  # کاربر از وارد کردن رمز صرف‌نظر کرد

        rtsp_url = self.camera_store.build_rtsp_url(cam)
        ok = self.camera_grid.assign_camera(cam, rtsp_url)
        if not ok:
            QMessageBox.information(
                self, "جایی خالی نیست",
                "همه‌ی خانه‌های شبکه‌ی نمایش پر است. ابتدا یکی را ببندید یا "
                "تعداد نمایش هم‌زمان را از بالای شبکه افزایش دهید."
            )

    def _ensure_password(self, cam: dict) -> bool:
        """رفع درخواست امنیتی: رمزهای عبور دیگر روی دیسک ذخیره نمی‌شوند
        (camera_store.py)، پس با هر بار اجرای برنامه خالی بارگذاری می‌شوند.
        قبل از شروع پخش زنده، اگر رمز دوربین (یا در صورت متصل بودن به یک NVR،
        رمز خود آن NVR) در حافظه موجود نباشد، اینجا از کاربر پرسیده می‌شود.
        رمز واردشده فقط در حافظه (تا زمان بستن برنامه) نگه‌داشته می‌شود تا
        برای بقیه‌ی کانال‌های همان NVR در همین نشست دوباره پرسیده نشود."""
        nvr = self.camera_store.get_nvr(cam.get("nvr_id")) if cam.get("nvr_id") else None
        source = nvr if nvr is not None else cam

        if source.get("pass"):
            if nvr is not None and not cam.get("pass"):
                cam["pass"] = nvr["pass"]
            return True

        label = f"NVR «{source['name']}»" if nvr is not None else f"دوربین «{source['name']}»"
        pwd, ok = QInputDialog.getText(
            self, "رمز عبور مورد نیاز",
            f"برای اتصال، رمز عبور {label} را وارد کنید:",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return False

        source["pass"] = pwd
        if nvr is not None:
            # رمز بین همه‌ی کانال‌های همین NVR مشترک است؛ برای جلوگیری از
            # پرسیدن دوباره در همین نشست، روی همه‌ی آن‌ها هم اعمال می‌شود.
            for sibling in self.camera_store.cameras_for_nvr(nvr["id"]):
                sibling["pass"] = pwd
        return True

    def get_active_camera_frame(self):
        return self.camera_grid.get_selected_frame()

    def on_camera_dropped_on_grid(self, cam_id, slot_index):
        """رفع درخواست: وقتی کاربر یک دوربین را از لیست «دوربین‌ها و NVRهای
        من» گرفته و روی یک خانه از شبکه‌ی نمایش رها کند، همان‌جا باز می‌شود."""
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        if not self._ensure_password(cam):
            return
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        self.camera_grid.assign_camera_to_slot(cam, rtsp_url, slot_index)

    # ------------------------------------------------------ face library ---

    # --------------------------------------------------- ناوبری هدر/صفحه‌ها --

    def _build_header(self):
        """هدر بالای برنامه: دکمه‌های ناوبری بین صفحه‌ی اصلی (دوربین‌ها) و
        شش صفحه‌ی جداگانه‌ی «اعلام حریق»، «چهره‌ها»، «گزارش‌ها»،
        «پلاک‌خوان»، «ردیابی اشخاص» و «نقشه ساختمان»."""
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        # لوگوی شرکت در هدر: سپر + نام فارسی/انگلیسی «ایمن آرا سورنا».
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        logo_label = QLabel()
        _logo_pix = QPixmap(LOGO_SHIELD)
        if not _logo_pix.isNull():
            logo_label.setPixmap(
                _logo_pix.scaledToHeight(42, Qt.TransformationMode.SmoothTransformation)
            )
        brand_row.addWidget(logo_label)
        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        name_col.setContentsMargins(0, 0, 0, 0)
        fa_name = QLabel(APP_NAME_FA)
        fa_name.setStyleSheet("font-size: 16px; font-weight: bold;")
        en_name = QLabel(APP_NAME_EN)
        en_name.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; letter-spacing: 3px;"
        )
        name_col.addWidget(fa_name)
        name_col.addWidget(en_name)
        brand_row.addLayout(name_col)
        header_layout.addLayout(brand_row)
        header_layout.addStretch()

        self.nav_buttons = {}
        for key, label in (
            ("home", "🏠 صفحه اصلی"),
            ("fire", "🔥 اعلام حریق"),
            ("face", "👤 چهره‌ها"),
            ("reports", "📊 گزارش‌ها"),
            ("plate", "🚗 پلاک‌خوان"),
            ("person", "👥 ردیابی اشخاص"),
            ("map", "🗺 نقشه ساختمان"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{padding: 6px 14px; border-radius: 6px; font-size: 12px;}"
                f"QPushButton:checked{{background: {LOGO_BLUE}; color: white; font-weight: bold;}}"
            )
            btn.clicked.connect(lambda _checked=False, _key=key: self.show_page(_key))
            header_layout.addWidget(btn)
            self.nav_buttons[key] = btn
        return header

    def show_page(self, key):
        """تغییر صفحه‌ی فعال از طریق هدر؛ هر صفحه هنگام نمایش، داده‌هایش را
        با متد refresh خودش تازه می‌کند."""
        index = {"home": 0, "fire": 1, "face": 2, "reports": 3, "plate": 4,
                 "person": 5, "map": 6}[key]
        if key == "map" and self.map_page is None:
            QMessageBox.warning(
                self, "صفحه‌ی نقشه در دسترس نیست",
                "فایل building_map_dialog.py (و building_map.py) کنار برنامه "
                "پیدا نشد؛ آن‌ها را کنار main.py بگذارید و دوباره اجرا کنید.")
            return
        page = self.pages.widget(index)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        if key == "person":
            # بنر وضعیت موتور تشخیص شخص را هم تازه کن (رجوع کنید به
            # _refresh_person_detector_status).
            try:
                self._refresh_person_detector_status()
            except Exception:
                pass
        self.pages.setCurrentIndex(index)
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)

    def _on_map_camera_click(self, cam):
        """دابل‌کلیک روی دوربین در صفحه‌ی «نقشه ساختمان»: باز شدن پنجره‌ی
        شناور پخش زنده‌ی همان دوربین (بدون ترک صفحه‌ی نقشه)."""
        if not self._ensure_password(cam):
            return  # کاربر از وارد کردن رمز صرف‌نظر کرد
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        dlg = CameraPreviewDialog(cam, rtsp_url, self.face_engine, parent=self)
        dlg.show()

    def _on_person_show_on_map(self, person_id):
        """دکمه‌ی «🗺 نمایش روی نقشه» در تب گزارش مسیر حرکت: باز کردن صفحه‌ی
        نقشه و رسم مسیر تردد روی نقشه‌ی طبقات. person_id=None یعنی «همه‌ی
        اشخاص» — هر شخص با خط‌چینِ رنگ مخصوص خودش."""
        if self.map_page is None:
            QMessageBox.warning(
                self, "صفحه‌ی نقشه در دسترس نیست",
                "فایل building_map_dialog.py کنار برنامه پیدا نشد.")
            return
        self.show_page("map")
        if person_id:
            self.map_page.show_person_path(person_id)
        else:
            self.map_page.show_all_person_paths()

    def on_face_event(self, cam, person, crop_frame):
        """برای هر چهره‌ای که هر یک از دوربین‌ها ببیند (شناخته‌شده یا
        تعریف‌نشده) فراخوانی می‌شود و یک ردیف جدید - با تصویر برش‌خورده‌ی
        همان چهره - در بالای پنل تشخیص چهره (سمت راست تصویر دوربین‌ها) اضافه
        می‌کند. ``cam``: کل دیکشنری دوربین (نه فقط اسم) تا nvr_id/channel هم
        برای لینک «پخش ویدیوی NVR» در دیالوگ گزارش‌ها ذخیره شود."""
        camera_name = cam.get("name", "")
        timestamp = time.strftime("%H:%M:%S")
        if person:
            text = f"[{timestamp}] {camera_name}\n{person.get('name', '')} — تعریف شده ✅"
        else:
            text = f"[{timestamp}] {camera_name}\n⚠ تعریف نشده"

        item = QListWidgetItem(text)
        pixmap = _bgr_to_pixmap(crop_frame) if crop_frame is not None else None
        if pixmap is not None:
            item.setIcon(QIcon(pixmap))
        item.setForeground(Qt.GlobalColor.green if person else Qt.GlobalColor.red)

        # رفع درخواست: علاوه بر نمایش موقت در همین پنل، ثبت دائمی (با
        # ساعت/تاریخ کامل و همین تصویر برش‌خورده) روی سیستم (report_store.py).
        report_store.log_face_event(camera_name, person, crop_frame,
                                     nvr_id=cam.get("nvr_id"), channel=cam.get("channel"))

        self.face_panel_list.insertItem(0, item)
        # جلوگیری از رشد بی‌حد پنل در نشست‌های طولانی.
        while self.face_panel_list.count() > 300:
            self.face_panel_list.takeItem(self.face_panel_list.count() - 1)

    # ------------------------------------------------------ plate reader ---

    def on_plate_event(self, cam, data):
        """برای هر پلاک تازه‌تأییدشده‌ای که یکی از دوربین‌های فعالِ پلاک‌خوان
        ببیند فراخوانی می‌شود. data دیکشنری {"plate_text", "plate_display",
        "conf", "crop", "box"} است (رجوع کنید به camera_stream.py).
        تطبیق با پلاک‌های تعریف‌شده + ثبت دائمی رویداد (با تصویر برش‌خورده)
        همین‌جا انجام می‌شود؛ اگر صفحه‌ی «پلاک‌خوان» باز باشد، گزارشش هم
        تازه می‌شود."""
        try:
            camera_name = cam.get("name", "")
            event = plate_store.log_event(
                camera_name,
                data.get("plate_text", ""),
                confidence=float(data.get("conf", 0.0) or 0.0),
                crop_bgr=data.get("crop"),
                nvr_id=cam.get("nvr_id") or "",
                channel=cam.get("channel"),
                plate_display=data.get("plate_display", ""),
            )
            # اگر کاربر همین حالا صفحه‌ی پلاک‌خوان (تب گزارش) را می‌بیند،
            # جدول را زنده تازه کن.
            try:
                if (hasattr(self, "plate_page") and self.plate_page is not None
                        and self.pages.currentWidget() is self.plate_page):
                    self.plate_page.run_report_search()
                    self.plate_page._update_stats()
            except Exception:
                pass
        except Exception as e:
            print(f"خطا در ثبت رویداد پلاک: {e}")

    def _get_plate_live_diag(self):
        """جمع‌آوری شمارنده‌های تشخیصی پلاک‌خوان همه‌ی دوربین‌های باز برای
        دیالوگ «وضعیت زنده‌ی پلاک‌خوان (تشخیصی)» صفحه‌ی پلاک‌خوان."""
        out = {}
        try:
            for slot in self.camera_grid.slots:
                try:
                    cam = getattr(slot, "cam", None)
                    name = (cam.get("name") or cam.get("ip") or "") if isinstance(cam, dict) else ""
                    th = getattr(slot, "stream_thread", None)
                    if th is not None and hasattr(th, "get_plate_diag"):
                        out[name or "دوربین"] = th.get_plate_diag()
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _on_camera_plate_toggle(self, cam_id, enabled):
        """اعمال زنده‌ی تیک «پلاک‌خوان» صفحه‌ی پلاک‌خوان روی دوربینی که همین
        حالا در شبکه‌ی نمایش باز است (بدون نیاز به بستن/باز کردن دوباره)."""
        try:
            for slot in self.camera_grid.slots:
                if slot.cam is not None and slot.cam.get("id") == cam_id:
                    slot.cam["plate_detection"] = bool(enabled)
                    slot.set_plate_detection(enabled)
        except Exception as e:
            print(f"خطا در اعمال پلاک‌خوان روی دوربین باز: {e}")

    def on_person_event(self, cam, data):
        """برای هر رویداد ردیابی اشخاص که یکی از دوربین‌های فعالِ ردیابی
        ببیند فراخوانی می‌شود. data دیکشنری {"type": "candidate" /
        "candidate_ended" / "confirmed" / "ended", "local_id", ...} است
        (رجوع کنید به camera_stream.py).

        - candidate: نشان زرد «در حال شناسایی» روی نقشه — حس «در لحظه»،
          بدون ثبت در دیتابیس.
        - confirmed: تطبیق بین دوربینی (با کمک چهره: اگر چهره در بانک
          چهره‌ها شناخته‌شده باشد، همان هویت قطعی است حتی با لباس متفاوت)
          + ثبت «حضور» (ورود/خروج با ساعت دقیق) در person_store — در ترد
          اصلی انجام می‌شود؛ اگر صفحه‌ی «ردیابی اشخاص» باز باشد، جدول‌هایش
          هم زنده تازه می‌شوند."""
        try:
            etype = (data or {}).get("type")
            cam_id = cam.get("id") if isinstance(cam, dict) else None
            camera_name = (cam.get("name") or cam.get("ip") or "") \
                if isinstance(cam, dict) else ""
            key = (cam_id, data.get("local_id"))
            mp = getattr(self, "map_page", None)

            if etype == "candidate":
                # «در لحظه»: به‌محض دومین دیده‌شدن پیاپی، نشان زرد روی نقشه
                try:
                    if not hasattr(self, "_person_candidates"):
                        self._person_candidates = set()
                    self._person_candidates.add(key)
                    if mp is not None:
                        mp.set_live_person(cam_id, f"~{data.get('local_id')}",
                                           "…", camera_name, True,
                                           kind="candidate")
                except Exception:
                    pass
                return

            if etype == "candidate_ended":
                try:
                    if hasattr(self, "_person_candidates"):
                        self._person_candidates.discard(key)
                    if mp is not None:
                        mp.set_live_person(cam_id, f"~{data.get('local_id')}",
                                           "", "", False)
                except Exception:
                    pass
                return

            if etype == "confirmed":
                desc = data.get("descriptor") or {}
                vector = desc.get("vector")
                if vector is None:
                    return
                ts = time.time()
                face_pid = str(desc.get("face_person_id") or "")
                face_name = str(desc.get("face_name") or "")
                face_vector = desc.get("face_vector")
                # کاندیدای همین رد (اگر بود) را بردار؛ نشان سبز جایش می‌نشیند
                try:
                    if hasattr(self, "_person_candidates"):
                        self._person_candidates.discard(key)
                    if mp is not None:
                        mp.set_live_person(cam_id, f"~{data.get('local_id')}",
                                           "", "", False)
                except Exception:
                    pass
                person_id = None
                # ۱) کمک چهره: اگر چهره در بانک چهره‌ها شناخته‌شده است، اول
                # در دیتابیس دنبال همان هویت بگرد (قطعی، حتی با لباس عوض‌شده)
                if face_pid:
                    try:
                        person_id = person_store.find_by_face(face_pid)
                    except Exception:
                        person_id = None
                # ۲) تطبیق سراسری (ظاهر + امضای چهره)
                if person_id is None:
                    person_id, _dist = self._person_matcher.find_match(
                        vector, camera_name, ts,
                        face_vector=face_vector,
                        face_person_id=face_pid or None)
                is_new = person_id is None
                if is_new:
                    person_id = person_store.add_person(
                        desc, thumb_bgr=data.get("crop"))
                elif face_pid:
                    # شخص از مسیر دیگری (ظاهری) پیدا شد ولی چهره‌اش شناخته‌شده
                    # است: هویت چهره را به او بچسبان تا دفعه‌ی بعد قطعی شود
                    try:
                        person_store.set_person_face(
                            person_id, face_pid, face_name)
                    except Exception:
                        pass
                self._person_matcher.register(
                    person_id, vector, camera_name, ts,
                    face_vector=face_vector,
                    face_person_id=face_pid or None)
                sighting_id = person_store.start_sighting(
                    person_id, camera_name,
                    nvr_id=(cam.get("nvr_id") or "") if isinstance(cam, dict) else "",
                    channel=(cam.get("channel") if isinstance(cam, dict) else None),
                    snapshot_bgr=data.get("crop"))
                # کد یکتای شخص را روی باکس تصویر همان دوربین بنویس
                # (لاتین می‌ماند چون cv2.putText فارسی رسم نمی‌کند)
                for slot in self.camera_grid.slots:
                    if slot.cam is not None and slot.cam.get("id") == cam_id \
                            and slot.stream_thread is not None:
                        slot.stream_thread.set_person_track_label(
                            data.get("local_id"), person_id)
                        break
                self._active_person_tracks[key] = {
                    "person_id": person_id, "sighting_id": sighting_id}
                # ردیابی «در لحظه»: همان ثانیه‌ی شناسایی، نشان زنده‌ی شخص
                # روی دوربینِ نقشه‌ی ساختمان می‌نشیند و اگر مسیر همین شخص
                # روی نقشه باز است، توقف جدید بی‌درنگ به آن اضافه می‌شود.
                # اگر چهره شناخته‌شده باشد، نامش هم روی نشان نقشه می‌آید.
                try:
                    if mp is not None:
                        code = (f"{person_id} · {face_name}"
                                if face_name else person_id)
                        mp.set_live_person(cam_id, person_id, code,
                                           camera_name, True, kind="live")
                        mp.append_live_stop(person_id)
                except Exception:
                    pass
            elif etype == "ended":
                info = self._active_person_tracks.pop(key, None)
                if info is not None:
                    person_store.end_sighting(info["sighting_id"])
                    try:
                        if mp is not None:
                            mp.set_live_person(cam_id, info["person_id"],
                                               info["person_id"], "", False)
                            # احتیاط: کاندیدای باقی‌مانده‌ی همین رد
                            mp.set_live_person(
                                cam_id, f"~{data.get('local_id')}",
                                "", "", False)
                    except Exception:
                        pass
                if hasattr(self, "_person_candidates"):
                    self._person_candidates.discard(key)
            else:
                return
            # اگر کاربر همین حالا صفحه‌ی «ردیابی اشخاص» را می‌بیند،
            # جدول‌ها را زنده تازه کن.
            try:
                if (hasattr(self, "person_page") and self.person_page is not None
                        and self.pages.currentWidget() is self.person_page):
                    self.person_page.refresh_persons_table()
                    self.person_page._reload_path_person_combo()
                    self.person_page.run_path_search()
            except Exception:
                pass
        except Exception as e:
            print(f"خطا در ثبت رویداد ردیابی اشخاص: {e}")

    def _on_camera_person_toggle(self, cam_id, enabled):
        """اعمال زنده‌ی تیک «ردیابی اشخاص» صفحه‌ی ردیابی اشخاص روی دوربینی
        که همین حالا در شبکه‌ی نمایش باز است (بدون نیاز به بستن/باز کردن
        دوباره). با خاموش شدن، ردهای باز آن دوربین بسته می‌شوند."""
        try:
            for slot in self.camera_grid.slots:
                if slot.cam is not None and slot.cam.get("id") == cam_id:
                    slot.cam["person_tracking"] = bool(enabled)
                    slot.set_person_tracking(enabled)
                    if not enabled:
                        # «حضور»های باز این دوربین را ببند
                        for key in [k for k in self._active_person_tracks
                                    if k[0] == cam_id]:
                            info = self._active_person_tracks.pop(key)
                            person_store.end_sighting(info["sighting_id"])
                            try:
                                mp = getattr(self, "map_page", None)
                                if mp is not None:
                                    mp.set_live_person(
                                        cam_id, info["person_id"],
                                        info["person_id"], "", False)
                            except Exception:
                                pass
                        # نشان‌های زرد «در حال شناسایی» این دوربین را هم بردار
                        try:
                            if hasattr(self, "_person_candidates"):
                                for key in [k for k in self._person_candidates
                                            if k[0] == cam_id]:
                                    self._person_candidates.discard(key)
                                    mp = getattr(self, "map_page", None)
                                    if mp is not None:
                                        mp.set_live_person(
                                            cam_id, f"~{key[1]}", "", "", False)
                        except Exception:
                            pass
        except Exception as e:
            print(f"خطا در اعمال ردیابی اشخاص روی دوربین باز: {e}")

    def _refresh_person_detector_status(self):
        """به‌روزرسانی بنر وضعیت موتور تشخیص شخص در صفحه‌ی «ردیابی اشخاص».

        رفع درخواست «هیچ گزارشی از افراد ثبت نمیشه» بی‌هیچ توضیحی: علت
        معمولاً یکی از این‌هاست و حالا هر کدام با پیام مشخص روی همان
        صفحه دیده می‌شود:
        ۱) هیچ دوربینی در صفحه‌ی اصلی باز/در حال پخش نیست؛
        ۲) مدل تشخیص شخص (YOLOv8 در person_detector.py) بارگذاری نشده؛
        ۳) همه‌چیز سالم است و گزارش‌ها در حال ثبت‌اند.
        """
        page = getattr(self, "person_page", None)
        if page is None:
            return
        try:
            open_slots = [s for s in self.camera_grid.slots
                          if getattr(s, "cam", None) is not None
                          and getattr(s, "stream_thread", None) is not None]
        except Exception:
            open_slots = []
        if not open_slots:
            page.set_detector_status("no_camera")
            return
        try:
            states = [getattr(s, "_detector_available", None) for s in open_slots]
        except Exception:
            states = []
        if any(s is True for s in states):
            page.set_detector_status("ok")
            return
        if states and all(s is False for s in states):
            err = ""
            try:
                for s in open_slots:
                    if getattr(s, "_detector_error", ""):
                        err = s._detector_error
                        break
            except Exception:
                pass
            page.set_detector_status("error", err)
            return
        page.set_detector_status("loading")


    # ------------------------------------------------------------- scan ---

    def run_network_scan(self):
        # رفع باگ: قبلاً scan_subnet مستقیماً روی ترد UI اجرا می‌شد و کل برنامه
        # را برای طول مدت اسکن (چند ثانیه تا چند ده ثانیه) کاملاً فریز می‌کرد؛
        # حالا در یک QThread جداگانه (NetworkScanThread) اجرا می‌شود.
        if self.network_scan_thread is not None and self.network_scan_thread.isRunning():
            return

        subnet = self.subnet_input.text().strip()
        self.scan_result_list.clear()
        self.scan_result_list.addItem("در حال اسکن شبکه...")
        self.scan_btn.setEnabled(False)

        self.network_scan_thread = NetworkScanThread(subnet, self)
        self.network_scan_thread.finished_signal.connect(self._on_network_scan_finished)
        self.network_scan_thread.start()

    def _on_network_scan_finished(self, devices):
        self.scan_btn.setEnabled(True)
        self.scan_result_list.clear()
        self._scan_ports_by_ip = {}
        if not devices:
            self.scan_result_list.addItem("هیچ دستگاهی یافت نشد.")
            return

        for dev in devices:
            self._scan_ports_by_ip[dev["ip"]] = dev["ports"]
            ports_str = ",".join(map(str, dev["ports"]))
            item = QListWidgetItem(f"{dev['ip']} (پورت‌ها: {ports_str})")
            # رفع درخواست: به‌جای انتخاب با Ctrl/Shift، کنار هر دستگاه یک
            # چک‌باکس قرار می‌گیرد تا کاربر با تیک زدن، دستگاه‌های موردنظر
            # برای اتصال هم‌زمان را مشخص کند.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.scan_result_list.addItem(item)

    # ------------------------------------------------------------ close ---

    def closeEvent(self, event):
        self.camera_grid.stop_all()
        # جلوگیری از کرش هنگام بستن برنامه در حین اسکن شبکه/تشخیص نوع دستگاه:
        # Qt هنگام تخریب یک QThread که هنوز در حال اجراست، کرش می‌کند.
        if self.network_scan_thread is not None and self.network_scan_thread.isRunning():
            self.network_scan_thread.wait(3000)
        if self.detect_thread is not None and self.detect_thread.isRunning():
            self.detect_thread.cancel()
            self.detect_thread.wait(3000)
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: همان دلیل بالا - همه‌ی
        # تردهای مانیتور پنل‌های فیزیکی اعلام حریق باید قبل از بسته‌شدن
        # برنامه صریحاً متوقف شوند.
        for panel_id in list(self._fire_alarm_threads.keys()):
            self._stop_fire_alarm_monitor(panel_id)
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # قطع آژیر ممتد هنگام خروج از برنامه تا در پس‌زمینه ادامه پیدا نکند.
        if getattr(self, "alarm_player", None) is not None:
            try:
                self.alarm_player.stop()
            except Exception:
                pass

        # رفع درخواست: هنگام خروج از برنامه، تمام رمزهای عبوری که فقط در
        # حافظه نگه‌داشته شده بودند (هیچ‌وقت روی دیسک ذخیره نمی‌شوند - رجوع
        # کنید به camera_store.py) پاک می‌شوند؛ در اجرای بعدی دوباره پرسیده
        # خواهند شد.
        self.camera_store.clear_all_passwords()
        # همان نکته برای پنل‌های اعلام حریق فیزیکی (fire_alarm_store.py).
        self.fire_alarm_store.clear_all_passwords()
        # رفع درخواست: کادر یوزرنیم/پسوورد بالای پنل اسکن شبکه هم هرگز روی
        # دیسک ذخیره نشده (فقط QLineEdit در حافظه بود) و هنگام خروج از
        # برنامه صراحتاً پاک می‌شود.
        self.scan_user_input.clear()
        self.scan_pass_input.clear()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_theme(app)  # تم تیره‌ی سازگار با لوگوی ایمن آرا سورنا
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

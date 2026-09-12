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
    QGridLayout, QComboBox, QScrollArea, QSizePolicy, QSplitter
)
from PyQt6.QtGui import QImage, QPixmap, QAction, QIcon, QDrag, QFontMetrics, QPainter, QPen, QColor, QPolygonF
from PyQt6.QtCore import Qt, QSize, QMimeData, QPointF, QRectF, QTimer, pyqtSignal

from face_engine import FaceEngine
from scanner import NetworkScanThread
from camera_store import CameraStore
from camera_stream import CameraStreamThread, region_to_polygon
from floor_detector import FloorDetectThread
from add_camera_dialog import AddCameraDialog
from add_nvr_dialog import AddNVRDialog
from nvr_scanner import DirectCameraProbeThread
try:
    from nvr_webview_dialog import NVRWebViewDialog, _WEBENGINE_AVAILABLE
except ImportError:
    NVRWebViewDialog, _WEBENGINE_AVAILABLE = None, False
from face_library_dialog import FaceLibraryDialog
from report_store import report_store
from reports_dialog import ReportsDialog
from nvr_storage_dialog import NVRStorageDialog
from device_detect import DeviceDetectThread
from fire_alarm_store import FireAlarmStore
from fire_alarm_io import FireAlarmMonitorThread
from add_fire_alarm_dialog import AddFireAlarmDialog

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
_PROCESS_EVERY_N = 5 if (os.cpu_count() or 4) >= 6 else 8

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

    def __init__(self, on_clicked, on_close_requested, on_double_clicked=None,
                 on_slot_drag_swap=None, on_camera_drag_drop=None, on_region_alert=None, parent=None):
        super().__init__(parent)
        self.cam = None
        self.stream_thread = None
        self.latest_raw_frame = None
        self._selected = False
        self._on_clicked = on_clicked
        self._on_close_requested = on_close_requested
        # رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار این خانه، این
        # callback (در MainWindow) صدا زده می‌شود تا رویداد در پنل تشخیص
        # چهره هم به‌صورت متنی ثبت شود.
        self._on_region_alert = on_region_alert
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
        self.close_btn.setStyleSheet("QPushButton{color:#ccc; background:#333; border-radius:9px;}")
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(lambda: self._on_close_requested(self))
        header.addWidget(self.name_label, 1)
        header.addWidget(self.people_count_label)
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
        self.video_label.set_draw_mode(enabled)

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

    def start(self, cam: dict, rtsp_url: str, face_engine: FaceEngine, face_event_cb, fire_event_cb=None):
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
        self.stream_thread.frame_ready.connect(self.on_frame_ready)
        self.stream_thread.error_signal.connect(self.on_error)
        self.stream_thread.connected_signal.connect(self.on_connected)
        self.stream_thread.people_count_signal.connect(self.on_people_count)
        self.stream_thread.region_entered.connect(self._on_region_entered)
        self.stream_thread.person_detector_status_signal.connect(self._on_detector_status)
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
        self.stream_thread.face_event_signal.connect(
            lambda person, crop: face_event_cb(cam, person, crop)
        )
        # رفع درخواست: رویداد آتش/دود این خانه هم (در صورت وجود callback) به
        # همان الگوی face_event_signal بالا، همراه با کل دیکشنری دوربین (cam)
        # به MainWindow.on_fire_event پاس داده می‌شود.
        if fire_event_cb is not None:
            self.stream_thread.fire_event_signal.connect(
                lambda label, frame, confidence: fire_event_cb(cam, label, frame, confidence)
            )
        self.stream_thread.start()
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

    def on_connected(self):
        self.status_label.setText("متصل - پخش زنده")

    def on_error(self, msg):
        self.status_label.setText(f"خطا: {msg}")

    def on_frame_ready(self, display_frame, raw_frame):
        try:
            self.latest_raw_frame = raw_frame
            pixmap = _bgr_to_pixmap(display_frame)
            if pixmap is None:
                return
            # رفع درخواست «تصویر با تاخیر خیلی زیاد می‌آید»: SmoothTransformation
            # (درون‌یابی دوخطی با کیفیت بالا) روی هر فریمِ هر دوربینِ باز
            # قابل‌توجه کند است؛ FastTransformation (نزدیک‌ترین‌همسایه) از نظر
            # کیفیت روی یک ویدیوی زنده (نه یک عکس ثابت) عملاً غیرقابل‌تشخیص
            # است ولی چند برابر سریع‌تر است - رجوع کنید به توضیح کامل‌تر در
            # camera_stream.CameraStreamThread.__init__ (self._gui_ready).
            self.video_label.setPixmap(
                pixmap.scaled(
                    self.video_label.width(), self.video_label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
                )
            )
        finally:
            # مهم: در finally تا حتی اگر رسم به هر دلیلی استثنا بدهد، ترد
            # پخط برای همیشه منتظر «تمام‌شدنِ GUI» نماند و پخش زنده کاملاً
            # متوقف نشود.
            if self.stream_thread is not None:
                self.stream_thread.mark_display_done()

    def stop(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
        self.stream_thread = None
        self.cam = None
        self.latest_raw_frame = None
        self._set_name_text("خالی")
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

    def __init__(self, face_engine: FaceEngine, on_face_event, on_external_camera_drop=None,
                 on_region_alert=None, on_fire_event=None, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.on_face_event = on_face_event
        # رفع درخواست: تشخیص تصویری آتش/دود هر خانه (camera_stream.py:
        # fire_event_signal)، مثل on_face_event، به این callback در
        # MainWindow پاس داده می‌شود تا هم در پنل رویدادهای حریق نمایش داده
        # شود و هم در report_store ثبت شود.
        self.on_fire_event = on_fire_event
        # رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار هر خانه، این
        # callback (در MainWindow) به هر خانه‌ی تازه‌ساخته‌شده هم پاس داده
        # می‌شود تا رویداد در پنل تشخیص چهره هم ثبت شود.
        self.on_region_alert = on_region_alert
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
                )
                slot.slot_index = len(self.slots)
                slot.tripwire_changed.connect(self.tripwire_changed.emit)
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
                slot.start(cam, rtsp_url, self.face_engine, self.on_face_event, self.on_fire_event)
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
        target.start(cam, rtsp_url, self.face_engine, self.on_face_event, self.on_fire_event)
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
            slot_a.start(cam_b, url_b, self.face_engine, self.on_face_event, self.on_fire_event)
            slot_a.set_people_counting(self._people_counting_enabled)
        if cam_a is not None:
            slot_b.start(cam_a, url_a, self.face_engine, self.on_face_event, self.on_fire_event)
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
        self.setWindowTitle("CCTV Management System (CMS) & Face Recognition")
        self.setGeometry(100, 100, 1500, 780)

        self.face_engine = FaceEngine()
        self.camera_store = CameraStore()
        self.network_scan_thread = None
        self.detect_thread = None
        self._scan_ports_by_ip = {}  # ip -> [ports...] از آخرین اسکن شبکه
        self._detect_queue = []  # صف IPهایی که با انتخاب چندتایی باید پشت‌سرهم تشخیص داده شوند

        # --- پنل‌های اعلام حریق فیزیکی (fire_alarm_store.py/fire_alarm_io.py) ---
        self.fire_alarm_store = FireAlarmStore()
        self.fire_alarm_threads: dict[str, FireAlarmMonitorThread] = {}
        # بافرِ درون‌حافظه‌ی رویدادهای حریق/دود اخیر برای پنل ستون راست
        # (تشخیص تصویری + پنل فیزیکی)؛ سقف تعداد برای جلوگیری از رشد بی‌رویه.
        self._fire_events: list[dict] = []

        self.init_ui()
        self.reload_camera_list()
        self.reload_fire_alarm_list()
        self.start_all_fire_alarm_monitors()

    # ---------------------------------------------------------------- UI ---

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()

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

        # بخش Face Library
        face_group = QGroupBox("مدیریت چهره (Face Library)")
        face_layout = QVBoxLayout()
        self.face_library_btn = QPushButton("باز کردن Face Library")
        self.face_library_btn.clicked.connect(self.open_face_library)
        face_layout.addWidget(self.face_library_btn)
        face_group.setLayout(face_layout)
        left_panel.addWidget(face_group)

        # بخش گزارش‌ها: تاریخچه‌ی دائمیِ ثبت‌شده (شمارش نفرات، ورود به
        # محدوده، چهره‌ی شناخته‌شده/تعریف‌نشده) - رجوع کنید به
        # report_store.py و reports_dialog.py.
        reports_group = QGroupBox("گزارش‌ها")
        reports_layout = QVBoxLayout()
        self.reports_btn = QPushButton("📊 مشاهده و خروجی گزارش‌ها")
        self.reports_btn.clicked.connect(self.open_reports)
        reports_layout.addWidget(self.reports_btn)
        reports_group.setLayout(reports_layout)
        left_panel.addWidget(reports_group)

        # بخش پنل‌های اعلام حریق فیزیکی: افزودن/لیست/حذف پنل‌های ISAPI/CGI/
        # Modbus TCP - رجوع کنید به fire_alarm_store.py و fire_alarm_io.py.
        fire_panel_group = QGroupBox("پنل‌های اعلام حریق")
        fire_panel_layout = QVBoxLayout()

        fire_btn_row = QHBoxLayout()
        self.add_fire_panel_btn = QPushButton("+ افزودن پنل")
        self.add_fire_panel_btn.clicked.connect(lambda: self.open_add_fire_alarm_dialog())
        self.remove_fire_panel_btn = QPushButton("حذف پنل انتخاب‌شده")
        self.remove_fire_panel_btn.clicked.connect(self.remove_selected_fire_panel)
        fire_btn_row.addWidget(self.add_fire_panel_btn)
        fire_btn_row.addWidget(self.remove_fire_panel_btn)

        self.fire_panel_list = QListWidget()
        self.fire_panel_list.itemDoubleClicked.connect(self.edit_fire_panel_item)
        fire_panel_hint = QLabel("دابل‌کلیک: ویرایش پنل. 🟢 = مانیتورینگ فعال، ⚪ = متوقف")
        fire_panel_hint.setStyleSheet("color: #888; font-size: 10px;")

        fire_panel_layout.addLayout(fire_btn_row)
        fire_panel_layout.addWidget(self.fire_panel_list)
        fire_panel_layout.addWidget(fire_panel_hint)
        fire_panel_group.setLayout(fire_panel_layout)
        left_panel.addWidget(fire_panel_group)

        left_panel.addStretch()

        # ------------------------------------------------ ستون میانی: شبکه‌ی
        # نمایش هم‌زمان دوربین‌ها با تعداد خانه‌ی قابل انتخاب.
        grid_column = QVBoxLayout()
        grid_toolbar = QHBoxLayout()

        # دکمه‌ی نمایش/مخفی‌کردن پنل کناری سمت چپ (اسکن شبکه، دوربین‌ها و
        # NVRهای من، Face Library). با کلیک روی این دکمه، عرض پنل چپ صفر یا
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
        # رفع درخواست: به‌جای نمایش هم‌زمان هر سه دکمه‌ی روش رسم محدوده
        # (رسم دستی/کل کادر/تشخیص AI) در نوار اصلی، فقط یک دکمه‌ی والدِ
        # «🖊 رسم محدوده» در نوار اصلی دیده می‌شود. با زدنِ همین دکمه، یک
        # ردیفِ زیرینِ جدید (self.region_options_row) با آن سه گزینه ظاهر
        # می‌شود؛ به محض انتخاب هرکدام (یا در طول انجامش)، آن ردیف پنهان و
        # ردیفِ دیگری (self.region_actions_row) با سه دکمه‌ی «لغو / رسم
        # مجدد / تایید» جای آن را می‌گیرد - رجوع کنید به
        # _on_draw_region_master_toggled و _refresh_line_buttons.
        self.draw_region_master_btn = QPushButton("🖊 رسم محدوده")
        self.draw_region_master_btn.setCheckable(True)
        self.draw_region_master_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید تا گزینه‌های رسم محدوده (رسم دستی، کل کادر، تشخیص با AI) زیرش ظاهر شود\n"
            "۳) یکی از آن گزینه‌ها را انتخاب کنید"
        )
        self.draw_region_master_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#9b59b6; color:#fff;}"
        )
        self.draw_region_master_btn.toggled.connect(self._on_draw_region_master_toggled)
        grid_toolbar.addWidget(self.draw_region_master_btn)

        # ردیف گزینه‌های رسم محدوده (رسم دستی/کل کادر/تشخیص AI) - فقط وقتی
        # self.draw_region_master_btn تیک‌خورده باشد نمایان می‌شود (رجوع
        # کنید به _refresh_line_buttons).
        self.region_options_row = QWidget()
        region_options_layout = QHBoxLayout(self.region_options_row)
        region_options_layout.setContentsMargins(24, 0, 0, 0)
        region_options_layout.setSpacing(6)

        self.draw_line_btn = QPushButton("✏️ رسم دستی")
        self.draw_line_btn.setCheckable(True)
        self.draw_line_btn.setToolTip(
            "۱) این گزینه را بزنید\n"
            "۲) روی تصویر همان دوربین، به‌ترتیب روی نقاط زمین/اتاق کلیک کنید "
            "(برنامه نقاط را به هم وصل می‌کند)\n"
            "۳) برای بستن محدوده: روی نقطه‌ی اول (دایره‌ی بزرگ‌تر) کلیک کنید، "
            "یا دابل‌کلیک کنید (حداقل ۳ نقطه لازم است)\n"
            "کلیک راست: لغو رسمِ در حال انجام"
        )
        self.draw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#9b59b6; color:#fff;}"
        )
        self.draw_line_btn.toggled.connect(self._on_draw_line_toggled)
        region_options_layout.addWidget(self.draw_line_btn)

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
        region_options_layout.addWidget(self.auto_region_btn)

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
        region_options_layout.addWidget(self.ai_floor_btn)
        region_options_layout.addStretch()
        self.region_options_row.setVisible(False)
        # نگه‌داشتن ارجاع به تردِ در حال اجرا (اگر باشد) - هم برای جلوگیری
        # از garbage-collect شدنِ زودهنگام QThread در حال اجرا، هم برای
        # اینکه بدانیم همین الان یک تشخیص در جریان است (رجوع کنید به
        # _on_ai_floor_region_clicked).
        self._floor_detect_thread = None
        # رفع درخواست: کدام گزینه (رسم دستی/کل کادر/تشخیص AI) آخرین‌بار
        # استفاده شده - تا دکمه‌ی «🔄 رسم مجدد» بتواند همان روش را دوباره
        # از نو شروع کند بدون این‌که کاربر مجبور شود دوباره از ردیف
        # گزینه‌ها انتخاب کند (رجوع کنید به _on_restart_line_clicked).
        self._last_region_mode = None

        # ردیف دکمه‌های عملیاتِ محدوده‌ی در انتظار: لغو / رسم مجدد / تایید.
        # فقط وقتی یک محدوده‌ی در انتظار (چه تازه رسم‌شده، چه کل کادر، چه
        # AI، چه در حال ویرایش) روی تصویر باشد نمایان می‌شود.
        self.region_actions_row = QWidget()
        region_actions_layout = QHBoxLayout(self.region_actions_row)
        region_actions_layout.setContentsMargins(24, 0, 0, 0)
        region_actions_layout.setSpacing(6)

        self.redraw_line_btn = QPushButton("❌ لغو")
        self.redraw_line_btn.setEnabled(False)
        self.redraw_line_btn.setToolTip("نقاط در حال رسم/در انتظار نام‌گذاری را لغو می‌کند؛ محدوده‌های قبلاً تایید‌شده حذف نمی‌شوند.")
        self.redraw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.redraw_line_btn.clicked.connect(self._on_redraw_line_clicked)
        region_actions_layout.addWidget(self.redraw_line_btn)

        self.restart_line_btn = QPushButton("🔄 رسم مجدد")
        self.restart_line_btn.setEnabled(False)
        self.restart_line_btn.setToolTip("محدوده‌ی در انتظار فعلی را لغو و همان روش (رسم دستی/کل کادر/تشخیص AI) را دوباره از نو شروع می‌کند.")
        self.restart_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.restart_line_btn.clicked.connect(self._on_restart_line_clicked)
        region_actions_layout.addWidget(self.restart_line_btn)

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
        region_actions_layout.addWidget(self.confirm_line_btn)
        region_actions_layout.addStretch()
        self.region_actions_row.setVisible(False)

        self.manage_regions_btn = QPushButton("📋 مدیریت محدوده‌ها")
        self.manage_regions_btn.setEnabled(False)
        self.manage_regions_btn.setToolTip("مشاهده و حذف محدوده‌های هشدار تعریف‌شده برای دوربین انتخاب‌شده.")
        self.manage_regions_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#2980b9; color:#fff;}"
        )
        self.manage_regions_btn.clicked.connect(self._on_manage_regions_clicked)
        grid_toolbar.addWidget(self.manage_regions_btn)

        grid_toolbar.addStretch()

        self.camera_grid = CameraGridWidget(
            self.face_engine, self.on_face_event, on_external_camera_drop=self.on_camera_dropped_on_grid,
            on_region_alert=self.on_region_alert, on_fire_event=self.on_fire_event,
        )
        self.camera_grid.selection_changed.connect(lambda _idx: self._refresh_line_buttons())
        self.camera_grid.tripwire_changed.connect(self._refresh_line_buttons)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.camera_grid)

        grid_column.addLayout(grid_toolbar)
        grid_column.addWidget(self.region_options_row)
        grid_column.addWidget(self.region_actions_row)
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

        # رفع درخواست: پنل رویدادهای حریق/دود زنده (تشخیص تصویری + پنل
        # فیزیکی) - در همان ستون راست، زیر پنل تشخیص چهره، همراه با یک
        # کادر جست‌وجو/فیلتر ساده (substring، بدون وابستگی جدید).
        fire_events_group = QGroupBox("رویدادهای حریق/دود (زنده)")
        fire_events_layout = QVBoxLayout()
        self.fire_event_filter_input = QLineEdit()
        self.fire_event_filter_input.setPlaceholderText("جست‌وجو (دوربین، نوع رویداد، پنل)...")
        self.fire_event_filter_input.textChanged.connect(self._refresh_fire_event_list)
        self.fire_event_list = QListWidget()
        fire_events_layout.addWidget(self.fire_event_filter_input)
        fire_events_layout.addWidget(self.fire_event_list)
        fire_events_group.setLayout(fire_events_layout)
        face_panel_layout.addWidget(fire_events_group)

        face_panel_group.setLayout(face_panel_layout)

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
        self.splitter.addWidget(face_panel_group)
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
        # عرضی که پنل چپ قبل از مخفی‌شدن داشت، برای بازگرداندن آن هنگام کلیک
        # مجدد روی دکمه‌ی sidebar نگه‌داشته می‌شود.
        self._left_panel_width = left_w

        main_layout.addWidget(self.splitter)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

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

    def _selected_slot(self):
        idx = self.camera_grid.selected_index
        if idx is None or not (0 <= idx < len(self.camera_grid.slots)):
            return None
        return self.camera_grid.slots[idx]

    def _on_draw_region_master_toggled(self, checked):
        """رفع درخواست: با زدنِ دکمه‌ی اصلیِ «🖊 رسم محدوده»، ردیف گزینه‌های
        رسم (رسم دستی/کل کادر/تشخیص AI) زیرش ظاهر می‌شود. اگر هنوز
        دوربینی انتخاب نشده باشد، دکمه به‌جای باز شدن دوباره خاموش می‌شود و
        پیام راهنما نشان داده می‌شود - دقیقاً مثل رفتار قبلیِ خودِ دکمه‌ی
        رسم دستی."""
        if checked:
            slot = self._selected_slot()
            if slot is None or slot.cam is None:
                self.draw_region_master_btn.blockSignals(True)
                self.draw_region_master_btn.setChecked(False)
                self.draw_region_master_btn.blockSignals(False)
                QMessageBox.information(
                    self, "رسم محدوده هشدار",
                    "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)، سپس دوباره این دکمه را بزنید."
                )
                return
        self._refresh_line_buttons()

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
        if checked:
            self._last_region_mode = "manual"
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
        self._last_region_mode = "full"
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
        thread = FloorDetectThread(frame.copy(), parent=self)
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
        self._last_region_mode = "ai"
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

    def _on_restart_line_clicked(self):
        """رفع درخواست دکمه‌ی «🔄 رسم مجدد»: محدوده‌ی در انتظار فعلی را
        لغو می‌کند و بلافاصله همان روشی را که آخرین‌بار استفاده شده (رسم
        دستی/کل کادر/تشخیص AI) از نو شروع می‌کند - تا کاربر برای رسم
        دوباره مجبور نباشد دوباره از ردیف گزینه‌ها انتخاب کند."""
        slot = self._selected_slot()
        if slot is None or not slot.has_pending_region():
            return
        if slot.is_editing_region():
            # ویرایشِ شکل یک محدوده‌ی قبلاً تایید‌شده را نمی‌شود «رسم مجدد»
            # کرد (روش اولیه‌اش دیگر معلوم نیست) - فقط ویرایش لغو می‌شود.
            slot.cancel_region_edit()
            self.draw_line_btn.blockSignals(True)
            self.draw_line_btn.setChecked(False)
            self.draw_line_btn.blockSignals(False)
            self._refresh_line_buttons()
            return
        slot.cancel_pending_region()
        mode = self._last_region_mode
        if mode == "manual":
            self.draw_line_btn.setChecked(True)
        elif mode == "full":
            self._on_auto_region_clicked()
        elif mode == "ai":
            self._on_ai_floor_region_clicked()
        else:
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
        self.restart_line_btn.setEnabled(bool(has_pending) and not is_editing)
        self.manage_regions_btn.setEnabled(bool(has_confirmed))
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
            self.redraw_line_btn.setText("❌ لغو")
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(bool(slot.is_draw_mode()) if slot is not None else False)
        self.draw_line_btn.blockSignals(False)

        # رفع درخواست: نمایش/پنهان‌کردن ردیف‌های زیرِ دکمه‌ی اصلی. تا وقتی
        # محدوده‌ای در انتظار/در حال ویرایش نیست، با تیک‌خوردنِ دکمه‌ی
        # اصلی «🖊 رسم محدوده» ردیف گزینه‌ها (رسم دستی/کل کادر/AI) نمایان
        # می‌شود؛ به محض شروع رسم دستی یا آماده‌شدن یک محدوده‌ی در انتظار
        # (has_pending)، آن ردیف پنهان و ردیف دکمه‌های «لغو/رسم مجدد/تایید»
        # جایش را می‌گیرد. اگر دکمه‌ی اصلی خاموش باشد و محدوده‌ای هم در
        # انتظار نباشد، هر دو ردیف پنهان می‌مانند.
        in_manual_draw = bool(slot.is_draw_mode()) if slot is not None else False
        master_checked = self.draw_region_master_btn.isChecked()
        self.region_actions_row.setVisible(bool(has_pending))
        self.region_options_row.setVisible(master_checked and not has_pending and not in_manual_draw)
        self.draw_region_master_btn.blockSignals(True)
        self.draw_region_master_btn.setChecked(master_checked or has_pending or in_manual_draw)
        self.draw_region_master_btn.blockSignals(False)

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

    def open_face_library(self):
        dialog = FaceLibraryDialog(self.face_engine, self.get_active_camera_frame, self)
        dialog.exec()

    def open_reports(self):
        # camera_store پاس داده می‌شود تا دیالوگ گزارش‌ها بتواند برای دکمه‌ی
        # «پخش ویدیوی NVR»، اطلاعات اتصال NVR مربوط به هر رویداد را پیدا کند.
        dialog = ReportsDialog(report_store, self.camera_store, self)
        dialog.exec()

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

    # -------------------------------------------------------- fire/smoke --

    def reload_fire_alarm_list(self):
        """لیست پنل‌های اعلام حریق ذخیره‌شده (fire_alarm_store.py) را در
        fire_panel_list سمت چپ بازسازی می‌کند؛ 🟢/⚪ وضعیت فعلیِ ترد
        مانیتورینگ هر پنل را نشان می‌دهد."""
        self.fire_panel_list.clear()
        for panel in self.fire_alarm_store.panels:
            status = "🟢" if panel.get("id") in self.fire_alarm_threads else "⚪"
            item = QListWidgetItem(f"{status} {panel['name']} ({panel.get('protocol', '?')})")
            item.setData(Qt.ItemDataRole.UserRole, panel["id"])
            self.fire_panel_list.addItem(item)

    def open_add_fire_alarm_dialog(self, existing_panel: dict | None = None):
        dialog = AddFireAlarmDialog(self, existing_panel=existing_panel)
        if dialog.exec():
            data = dialog.get_data()
            if existing_panel:
                self.fire_alarm_store.update_panel(existing_panel["id"], **data)
                self.restart_fire_alarm_monitor(existing_panel["id"])
            else:
                panel = self.fire_alarm_store.add_panel(**data)
                self.start_fire_alarm_monitor(panel)
            self.reload_fire_alarm_list()

    def edit_fire_panel_item(self, item: QListWidgetItem):
        panel_id = item.data(Qt.ItemDataRole.UserRole)
        panel = self.fire_alarm_store.get_panel(panel_id)
        if panel:
            self.open_add_fire_alarm_dialog(existing_panel=panel)

    def remove_selected_fire_panel(self):
        item = self.fire_panel_list.currentItem()
        if not item:
            QMessageBox.information(self, "توجه", "ابتدا یک پنل از لیست انتخاب کنید.")
            return
        panel_id = item.data(Qt.ItemDataRole.UserRole)
        self.stop_fire_alarm_monitor(panel_id)
        self.fire_alarm_store.remove_panel(panel_id)
        self.reload_fire_alarm_list()

    # --------------------------------------------------- fire event panel -

    def _push_fire_event(self, text: str):
        """رویداد جدید را به بافر درون‌حافظه اضافه و پنل فیلترشده را
        بازسازی می‌کند."""
        self._fire_events.insert(0, text)
        self._fire_events = self._fire_events[:200]  # سقف بافر برای جلوگیری از رشد بی‌رویه
        self._refresh_fire_event_list()

    def _refresh_fire_event_list(self):
        query = self.fire_event_filter_input.text().strip().lower()
        self.fire_event_list.clear()
        for text in self._fire_events:
            if query and query not in text.lower():
                continue
            self.fire_event_list.addItem(text)

    # -------------------------------------------------------------- خطاها -

    def on_fire_event(self, cam: dict, label: str, frame, confidence: float):
        """تشخیص تصویری آتش/دود روی یک دوربین (camera_stream.py:
        fire_event_signal، به‌صورت cooldown-limited ارسال می‌شود). این
        اسلات روی ترد اصلی UI اجرا می‌شود (Qt سیگنال بین‌تردی را خودکار به
        صف ترد گیرنده می‌فرستد)، پس دستکاری ویجت‌ها اینجا امن است."""
        camera_name = cam.get("name", "")
        label_fa = "آتش" if label == "fire" else ("دود" if label == "smoke" else label)
        ts = time.strftime("%H:%M:%S")
        self._push_fire_event(f"[{ts}] 🔥 {camera_name} — {label_fa} ({confidence:.0%})")
        report_store.log_fire_smoke_visual(
            camera_name, label, frame, confidence,
            nvr_id=cam.get("nvr_id"), channel=cam.get("channel"),
        )

    def on_fire_alarm_panel_triggered(self, panel_id: str, zone: str):
        """با فعال شدن یک منطقه/ورودی پنل فیزیکی اعلام حریق فراخوانی
        می‌شود (FireAlarmMonitorThread.panel_triggered - فقط روی تغییر
        وضعیت، نه هر poll)."""
        panel = self.fire_alarm_store.get_panel(panel_id)
        name = panel["name"] if panel else panel_id
        ts = time.strftime("%H:%M:%S")
        self._push_fire_event(f"[{ts}] 🚨 پنل «{name}» — منطقه {zone} فعال شد")
        report_store.log_fire_alarm_panel(name, zone, state="triggered")

    def on_fire_alarm_panel_cleared(self, panel_id: str, zone: str):
        panel = self.fire_alarm_store.get_panel(panel_id)
        name = panel["name"] if panel else panel_id
        ts = time.strftime("%H:%M:%S")
        self._push_fire_event(f"[{ts}] ✅ پنل «{name}» — منطقه {zone} رفع شد")
        report_store.log_fire_alarm_panel(name, zone, state="cleared")

    def on_fire_alarm_panel_error(self, panel_id: str, message: str):
        """خطای اتصال/وابستگی (مثلاً pymodbus نصب نیست) - فقط در پنل
        رویدادها نمایش داده می‌شود، برنامه هرگز کرش نمی‌کند."""
        panel = self.fire_alarm_store.get_panel(panel_id)
        name = panel["name"] if panel else panel_id
        ts = time.strftime("%H:%M:%S")
        self._push_fire_event(f"[{ts}] ⚠️ پنل «{name}»: {message}")

    # ----------------------------------------------- fire alarm lifecycle -

    def start_fire_alarm_monitor(self, panel: dict):
        """یک FireAlarmMonitorThread جدید برای این پنل می‌سازد و
        سیگنال‌هایش را به هندلرهای بالا وصل می‌کند. اگر از قبل ترد فعالی
        برای همین پنل وجود داشته باشد، کاری نمی‌کند (idempotent)."""
        panel_id = panel["id"]
        if panel_id in self.fire_alarm_threads:
            return
        thread = FireAlarmMonitorThread(panel, parent=self)
        thread.panel_triggered.connect(
            lambda zone, pid=panel_id: self.on_fire_alarm_panel_triggered(pid, zone)
        )
        thread.panel_cleared.connect(
            lambda zone, pid=panel_id: self.on_fire_alarm_panel_cleared(pid, zone)
        )
        thread.connection_error.connect(
            lambda msg, pid=panel_id: self.on_fire_alarm_panel_error(pid, msg)
        )
        thread.start()
        self.fire_alarm_threads[panel_id] = thread

    def stop_fire_alarm_monitor(self, panel_id: str):
        """توقف تمیز ترد مانیتورینگ یک پنل: پرچم اجرای آن خاموش می‌شود و
        حداکثر ۳ ثانیه برای پایان واقعی run() صبر می‌کنیم تا Qt هنگام
        تخریب یک QThread در حال اجرا کرش نکند."""
        thread = self.fire_alarm_threads.pop(panel_id, None)
        if thread is not None:
            thread.stop()
            thread.wait(3000)

    def restart_fire_alarm_monitor(self, panel_id: str):
        self.stop_fire_alarm_monitor(panel_id)
        panel = self.fire_alarm_store.get_panel(panel_id)
        if panel:
            self.start_fire_alarm_monitor(panel)

    def start_all_fire_alarm_monitors(self):
        """با هر بار بالا آمدن برنامه، مانیتورینگ تمام پنل‌های ذخیره‌شده
        (fire_alarms.json) خودکار شروع می‌شود."""
        for panel in self.fire_alarm_store.panels:
            self.start_fire_alarm_monitor(panel)

    def stop_all_fire_alarm_monitors(self):
        for panel_id in list(self.fire_alarm_threads.keys()):
            self.stop_fire_alarm_monitor(panel_id)

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
        # رفع درخواست: تمام تردهای مانیتورینگ پنل‌های اعلام حریق فیزیکی هم
        # باید قبل از بسته شدن پنجره متوقف شوند - وگرنه دقیقاً همان کرش
        # «QThread destroyed while running» که چند خط پایین‌تر برای اسکن
        # شبکه/تشخیص نوع دستگاه توضیح داده شده، برای این تردها هم رخ می‌دهد.
        self.stop_all_fire_alarm_monitors()
        self.fire_alarm_store.clear_all_passwords()
        # جلوگیری از کرش هنگام بستن برنامه در حین اسکن شبکه/تشخیص نوع دستگاه:
        # Qt هنگام تخریب یک QThread که هنوز در حال اجراست، کرش می‌کند.
        if self.network_scan_thread is not None and self.network_scan_thread.isRunning():
            self.network_scan_thread.wait(3000)
        if self.detect_thread is not None and self.detect_thread.isRunning():
            self.detect_thread.cancel()
            self.detect_thread.wait(3000)

        # رفع درخواست: هنگام خروج از برنامه، تمام رمزهای عبوری که فقط در
        # حافظه نگه‌داشته شده بودند (هیچ‌وقت روی دیسک ذخیره نمی‌شوند - رجوع
        # کنید به camera_store.py) پاک می‌شوند؛ در اجرای بعدی دوباره پرسیده
        # خواهند شد.
        self.camera_store.clear_all_passwords()
        # رفع درخواست: کادر یوزرنیم/پسوورد بالای پنل اسکن شبکه هم هرگز روی
        # دیسک ذخیره نشده (فقط QLineEdit در حافظه بود) و هنگام خروج از
        # برنامه صراحتاً پاک می‌شود.
        self.scan_user_input.clear()
        self.scan_pass_input.clear()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

import os
import sys
import time

import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QMenu, QTreeWidget, QTreeWidgetItem, QInputDialog,
    QGridLayout, QComboBox, QScrollArea, QSizePolicy, QSplitter
)
from PyQt6.QtGui import QImage, QPixmap, QAction, QIcon, QDrag
from PyQt6.QtCore import Qt, QSize, QMimeData

from face_engine import FaceEngine
from scanner import NetworkScanThread
from camera_store import CameraStore
from camera_stream import CameraStreamThread
from add_camera_dialog import AddCameraDialog
from add_nvr_dialog import AddNVRDialog
from face_library_dialog import FaceLibraryDialog
from device_detect import DeviceDetectThread

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


class CameraSlotWidget(QWidget):
    """یک خانه (slot) در شبکه‌ی نمایش هم‌زمان دوربین‌ها. می‌تواند خالی باشد یا
    یک دوربین را پخش کند. با کلیک انتخاب (highlight) می‌شود تا فریم زنده‌اش
    برای «ثبت چهره از تصویر زنده» در دسترس باشد."""

    def __init__(self, on_clicked, on_close_requested, on_double_clicked=None,
                 on_slot_drag_swap=None, on_camera_drag_drop=None, parent=None):
        super().__init__(parent)
        self.cam = None
        self.stream_thread = None
        self.latest_raw_frame = None
        self._selected = False
        self._on_clicked = on_clicked
        self._on_close_requested = on_close_requested
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

        self.setMinimumSize(140, 110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.setSpacing(2)

        header = QHBoxLayout()
        self.name_label = QLabel("خالی")
        self.name_label.setStyleSheet("color:#dddddd; font-size:11px; font-weight:bold;")
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setStyleSheet("QPushButton{color:#ccc; background:#333; border-radius:9px;}")
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(lambda: self._on_close_requested(self))
        header.addWidget(self.name_label, 1)
        header.addWidget(self.close_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#888888; font-size:9px;")

        self.video_label = QLabel("خالی — برای افزودن دوربین،\nدر لیست سمت چپ دابل‌کلیک کنید")
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

        outer.addLayout(header)
        outer.addWidget(self.status_label)
        outer.addWidget(self.video_label, 1)
        self._apply_frame_style()

    # ---------------------------------------------------------- selection --

    def _apply_frame_style(self):
        border = "2px solid #3498db" if self._selected else "1px solid #3a3a3a"
        self.setStyleSheet(f"CameraSlotWidget {{ border: {border}; border-radius: 8px; background-color: #262626; }}")

    def set_selected(self, value: bool):
        self._selected = value
        self._apply_frame_style()

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

    def start(self, cam: dict, rtsp_url: str, face_engine: FaceEngine, face_event_cb):
        self.cam = cam
        self.name_label.setText(cam["name"])
        self.close_btn.setVisible(True)
        self.status_label.setText("در حال اتصال...")
        self.video_label.setText("در انتظار تصویر...")

        self.stream_thread = CameraStreamThread(rtsp_url, face_engine, process_every_n=_PROCESS_EVERY_N)
        self.stream_thread.frame_ready.connect(self.on_frame_ready)
        self.stream_thread.error_signal.connect(self.on_error)
        self.stream_thread.connected_signal.connect(self.on_connected)
        self.stream_thread.face_event_signal.connect(
            lambda person, crop: face_event_cb(cam["name"], person, crop)
        )
        self.stream_thread.start()

    def on_connected(self):
        self.status_label.setText("متصل - پخش زنده")

    def on_error(self, msg):
        self.status_label.setText(f"خطا: {msg}")

    def on_frame_ready(self, display_frame, raw_frame):
        self.latest_raw_frame = raw_frame
        pixmap = _bgr_to_pixmap(display_frame)
        if pixmap is None:
            return
        self.video_label.setPixmap(
            pixmap.scaled(
                self.video_label.width(), self.video_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )

    def stop(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
        self.stream_thread = None
        self.cam = None
        self.latest_raw_frame = None
        self.name_label.setText("خالی")
        self.close_btn.setVisible(False)
        self.status_label.setText("")
        self.video_label.clear()
        self.video_label.setText("خالی — برای افزودن دوربین،\nدر لیست سمت چپ دابل‌کلیک کنید")
        self.set_selected(False)


class CameraGridWidget(QWidget):
    """شبکه‌ی نمایش هم‌زمان دوربین‌ها با تعداد خانه‌ی قابل انتخاب
    (1، 4، 9، 16، 32 یا 64). با تغییر تعداد، دوربین‌های از قبل باز تا حد
    امکان در چیدمان جدید حفظ می‌شوند."""

    def __init__(self, face_engine: FaceEngine, on_face_event, on_external_camera_drop=None, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.on_face_event = on_face_event
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
                )
                slot.slot_index = len(self.slots)
                self._layout.addWidget(slot, r, c)
                self.slots.append(slot)
                self._slot_positions.append((r, c))

        for cam, rtsp_url in previous[:total]:
            if rtsp_url:
                self.assign_camera(cam, rtsp_url)

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
                slot.start(cam, rtsp_url, self.face_engine, self.on_face_event)
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
        target.start(cam, rtsp_url, self.face_engine, self.on_face_event)
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
            slot_a.start(cam_b, url_b, self.face_engine, self.on_face_event)
        if cam_a is not None:
            slot_b.start(cam_a, url_a, self.face_engine, self.on_face_event)

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

    def _on_slot_close_requested(self, slot):
        idx = self.slots.index(slot)
        slot.stop()
        if idx == self.selected_index:
            self.selected_index = None
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

        self.init_ui()
        self.reload_camera_list()

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
        scan_layout.addWidget(QLabel(
            "روی یک نتیجه دابل‌کلیک کنید، یا با تیک‌زدن چک‌باکس کنار هر دستگاه چند "
            "مورد را انتخاب و «افزودن دستگاه‌های انتخاب‌شده» را بزنید:"
        ))
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
        left_panel.addStretch()

        # ------------------------------------------------ ستون میانی: شبکه‌ی
        # نمایش هم‌زمان دوربین‌ها با تعداد خانه‌ی قابل انتخاب.
        grid_column = QVBoxLayout()
        grid_toolbar = QHBoxLayout()
        grid_toolbar_label = QLabel("تعداد نمایش هم‌زمان دوربین‌ها:")
        grid_toolbar_label.setStyleSheet("font-size: 11px;")
        self.grid_size_combo = QComboBox()
        for n in (1, 4, 9, 16, 32, 64):
            self.grid_size_combo.addItem(str(n), n)
        self.grid_size_combo.setCurrentIndex(1)  # پیش‌فرض: 4
        self.grid_size_combo.currentIndexChanged.connect(self._on_grid_size_changed)
        grid_toolbar.addWidget(grid_toolbar_label)
        grid_toolbar.addWidget(self.grid_size_combo)
        grid_toolbar.addStretch()

        self.camera_grid = CameraGridWidget(
            self.face_engine, self.on_face_event, on_external_camera_drop=self.on_camera_dropped_on_grid
        )
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

        # رفع درخواست: عرض پنل سمت راست (پنل تشخیص چهره) باید دقیقاً هم‌اندازه‌ی
        # پنل سمت چپ باشد تا فضای بیشتری به تصویر دوربین‌ها در وسط برسد. قبلاً
        # با addLayout/addWidget + ضریب کشش (stretch factor) این کار انجام
        # می‌شد، اما چون QVBoxLayout سمت چپ و QGroupBox سمت راست حداقل‌اندازه‌ی
        # (minimumSizeHint) متفاوتی داشتند، ضریب کشش یکسان همیشه به عرض واقعاً
        # یکسان منجر نمی‌شد. با QSplitter و تنظیم صریح اندازه‌ی اولیه، عرض دو
        # ستون کناری همیشه یکسان شروع می‌شود (کاربر همچنان می‌تواند با کشیدن
        # لبه‌ی splitter عرض را دستی تغییر دهد).
        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        grid_widget = QWidget()
        grid_widget.setLayout(grid_column)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(grid_widget)
        splitter.addWidget(face_panel_group)
        # رفع درخواست: عرض ستون سمت چپ (اسکن شبکه، دوربین‌ها و NVRهای من،
        # Face Library) باید نصف اندازه‌ی قبلی‌اش باشد تا فضای بیشتری به
        # تصویر دوربین‌ها در وسط برسد؛ پنل سمت راست (تشخیص چهره) بدون تغییر
        # باقی می‌ماند. قبلاً هر سه ستون با نسبت 2:5:2 (از 9 سهم) شروع می‌شدند؛
        # با نصف کردن سهم چپ (2 -> 1) و اضافه شدن همان یک سهم آزادشده به ستون
        # وسط، نسبت جدید 1:6:2 می‌شود.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 6)
        splitter.setStretchFactor(2, 2)
        total_w = max(self.width(), 1500)
        right_w = total_w * 2 // 9        # عرض قبلی/بدون‌تغییرِ پنل راست
        left_w = right_w // 2              # نصف عرض قبلی برای پنل چپ
        splitter.setSizes([left_w, total_w - left_w - right_w, right_w])

        main_layout.addWidget(splitter)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    def _on_grid_size_changed(self, _index):
        count = self.grid_size_combo.currentData()
        self.camera_grid.set_grid_size(count)

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
                cam_item = QTreeWidgetItem([cam["name"]])
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
        if entry["is_full_url"]:
            return self.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path="", full_url=entry["path_or_url"]
            )
        return self.camera_store.add_channel_camera(
            nvr, entry["channel"], default_name, path=entry["path_or_url"]
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
            delete_action = QAction("حذف NVR و همه کانال‌ها", self)
            delete_action.triggered.connect(lambda: self.delete_nvr(data["id"]))
            menu.addAction(rescan_action)
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

    def on_face_event(self, camera_name, person, crop_frame):
        """برای هر چهره‌ای که هر یک از دوربین‌ها ببیند (شناخته‌شده یا
        تعریف‌نشده) فراخوانی می‌شود و یک ردیف جدید - با تصویر برش‌خورده‌ی
        همان چهره - در بالای پنل تشخیص چهره (سمت راست تصویر دوربین‌ها) اضافه
        می‌کند."""
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

        self.face_panel_list.insertItem(0, item)
        # جلوگیری از رشد بی‌حد پنل در نشست‌های طولانی.
        while self.face_panel_list.count() > 300:
            self.face_panel_list.takeItem(self.face_panel_list.count() - 1)

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

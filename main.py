import os
import sys
import time

import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QMenu, QTreeWidget, QTreeWidgetItem, QInputDialog, QComboBox,
    QScrollArea, QFrame, QSizePolicy, QSplitter
)
from PyQt6.QtGui import QImage, QPixmap, QAction, QIcon
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal

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

# تعداد پنجره‌های نمایش هم‌زمان که کاربر می‌تواند انتخاب کند و چیدمان
# (ردیف, ستون) هرکدام - نزدیک‌ترین چیدمان تقریباً مربعی برای هر تعداد.
GRID_LAYOUTS = {
    1: (1, 1),
    4: (2, 2),
    9: (3, 3),
    16: (4, 4),
    20: (4, 5),
    36: (6, 6),
    64: (8, 8),
}
MAX_GRID_SLOTS = max(GRID_LAYOUTS)


class GridSlotWidget(QFrame):
    """یک «پنجره» تکی داخل دیوار نمایش چند-دوربینه. می‌تواند خالی باشد یا یک
    دوربین را پخش کند. با کلیک روی آن (چه خالی چه پر) به‌عنوان پنجره‌ی «فعال»
    انتخاب می‌شود: پنجره‌ی خالیِ انتخاب‌شده، مقصد بعدی دابل‌کلیک روی یک دوربین
    از لیست است؛ پنجره‌ی پرِ انتخاب‌شده، منبع فریم برای «ثبت چهره از تصویر
    زنده» در Face Library است."""

    clicked = pyqtSignal(object)

    def __init__(self, index, face_engine, parent=None):
        super().__init__(parent)
        self.index = index
        self.face_engine = face_engine
        self.cam = None
        self.stream_thread = None
        self.latest_raw_frame = None
        self._selected = False

        self.setMinimumSize(130, 100)
        self.setFrameShape(QFrame.Shape.Box)
        self._apply_border()

        layout = QVBoxLayout()
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.name_label = QLabel("")
        self.name_label.setStyleSheet("color: #cccccc; font-size: 10px;")
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setStyleSheet("font-size: 10px; padding: 0px;")
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(self.clear)
        header.addWidget(self.name_label, 1)
        header.addWidget(self.close_btn)

        self.video_label = QLabel("پنجره خالی\n(کلیک کنید، سپس روی دوربین در لیست\nدابل‌کلیک کنید)")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #1e1e1e; color: #888888; font-size: 10px; border-radius: 4px;")
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout.addLayout(header)
        layout.addWidget(self.video_label, 1)
        self.setLayout(layout)

    def _apply_border(self):
        color = "#3b82f6" if self._selected else "#333333"
        width = "2px" if self._selected else "1px"
        self.setStyleSheet(f"GridSlotWidget {{ border: {width} solid {color}; border-radius: 6px; }}")

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_border()

    def is_empty(self):
        return self.cam is None

    def mousePressEvent(self, event):
        self.clicked.emit(self)
        super().mousePressEvent(event)

    # ------------------------------------------------------------- stream ---

    def assign(self, cam: dict, rtsp_url: str, log_callback):
        if not self.is_empty():
            self.clear()
        self.cam = cam
        self.name_label.setText(cam["name"])
        self.close_btn.setVisible(True)
        self.video_label.setText(f"در حال اتصال به «{cam['name']}»...")

        self.stream_thread = CameraStreamThread(rtsp_url, self.face_engine, process_every_n=_PROCESS_EVERY_N)
        self.stream_thread.frame_ready.connect(self.on_frame_ready)
        self.stream_thread.error_signal.connect(self.on_error)
        self.stream_thread.connected_signal.connect(self.on_connected)
        self.stream_thread.known_face_signal.connect(
            lambda person, face_img: log_callback("known", self.cam["name"], person, face_img)
        )
        self.stream_thread.unknown_face_signal.connect(
            lambda face_img: log_callback("unknown", self.cam["name"], None, face_img)
        )
        self.stream_thread.start()

    def on_connected(self):
        pass  # نام دوربین در هدر ثابت است؛ نیازی به تکرار «متصل شد» روی خود ویدیو نیست.

    def on_error(self, msg):
        self.video_label.setText(f"خطا: {msg}")

    def on_frame_ready(self, display_frame, raw_frame):
        self.latest_raw_frame = raw_frame
        rgb_image = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        # نکته: .copy() الزامی است. بدون آن، QImage صرفاً به بافر numpy موقتِ rgb_image
        # اشاره می‌کند؛ به محض بازگشت از این تابع پایتون می‌تواند آن حافظه را آزاد/بازچرخانی
        # کند و رندر بعدی (scaled/QPixmap.fromImage) به حافظه‌ی نامعتبر دسترسی پیدا کند —
        # یکی دیگر از منابع کرش تصادفی برنامه هنگام پخش زنده.
        qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
        self.video_label.setPixmap(
            QPixmap.fromImage(qt_img).scaled(
                self.video_label.width(), self.video_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )

    def clear(self):
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
        self.stream_thread = None
        self.cam = None
        self.latest_raw_frame = None
        self.name_label.setText("")
        self.close_btn.setVisible(False)
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("پنجره خالی\n(کلیک کنید، سپس روی دوربین در لیست\nدابل‌کلیک کنید)")


class CameraGridWidget(QWidget):
    """دیوار نمایش چند-دوربینه با تعداد پنجره‌ی قابل‌انتخاب
    (1, 4, 9, 16, 20, 36, 64)."""

    def __init__(self, face_engine: FaceEngine, log_callback, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.log_callback = log_callback
        self.selected_slot = None
        self.visible_count = 0

        self.slots = [GridSlotWidget(i, face_engine) for i in range(MAX_GRID_SLOTS)]
        for s in self.slots:
            s.clicked.connect(self._on_slot_clicked)

        # رفع درخواست: اندازه‌ی هر پنجره (پنل) باید با درگ‌کردن مرز بین دو
        # پنجره قابل تنظیم باشد. QGridLayout قبلی سلول‌ها را همیشه با اندازه‌ی
        # مساوی و ثابت تقسیم می‌کرد و هیچ راهی برای تغییر دستی نداشت. حالا از
        # چیدمان تودرتوی QSplitter استفاده می‌شود: یک splitter عمودی بیرونی
        # (self.row_splitter) شامل یک splitter افقی برای هر ردیف
        # (self.row_splitters)، و هر splitter افقی شامل پنجره‌های همان ردیف.
        # کاربر با کشیدن مرز بین دو پنجره‌ی هم‌ردیف یا دو ردیف، اندازه‌ی
        # آن‌ها را تغییر می‌دهد.
        self.row_splitter = QSplitter(Qt.Orientation.Vertical)
        self.row_splitter.setHandleWidth(4)
        self.row_splitters = []  # splitter افقیِ هر ردیف، به ترتیب از بالا به پایین

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.row_splitter)

    def _on_slot_clicked(self, slot):
        if self.selected_slot is slot:
            return
        if self.selected_slot is not None:
            self.selected_slot.set_selected(False)
        self.selected_slot = slot
        slot.set_selected(True)

    def set_window_count(self, n):
        n = n if n in GRID_LAYOUTS else 4
        # پنجره‌هایی که دیگر در محدوده‌ی تعداد جدید نیستند، اگر در حال پخش
        # باشند متوقف می‌شوند تا هم منابع (CPU/شبکه) آزاد شود و هم ترد
        # پس‌زمینه‌شان معلق نماند.
        for s in self.slots[n:]:
            if not s.is_empty():
                s.clear()
            if s.index == getattr(self.selected_slot, "index", -1):
                self.selected_slot = None
            s.setParent(None)
            s.hide()

        # splitterهای ردیفِ چیدمان قبلی از بین می‌روند (خود ویجت‌های پنجره
        # نه؛ آن‌ها فقط setParent(None) شده و در حلقه‌ی زیر به splitterهای
        # جدید اضافه می‌شوند).
        for rs in self.row_splitters:
            rs.setParent(None)
            rs.deleteLater()
        self.row_splitters = []

        rows, cols = GRID_LAYOUTS[n]
        for r in range(rows):
            row_splitter = QSplitter(Qt.Orientation.Horizontal)
            row_splitter.setHandleWidth(4)
            for c in range(cols):
                i = r * cols + c
                if i >= n:
                    break
                slot = self.slots[i]
                slot.setParent(None)
                row_splitter.addWidget(slot)
                slot.show()
            self.row_splitter.addWidget(row_splitter)
            self.row_splitters.append(row_splitter)

        # رفع درخواست: وقتی تعداد پنجره‌های نمایش عوض می‌شود، اندازه‌ی
        # پنجره‌های جدید باید متناسب با تعداد جدید تنظیم شود - نه اینکه
        # نسبت‌های دستیِ باقی‌مانده از چیدمان قبلی (که ممکن است تعداد سطر/
        # ستون متفاوتی داشت) روی چیدمان جدید اعمال شود. به همین دلیل بعد از
        # ساخت splitterهای جدید، اندازه‌ی مساوی برای splitter عمودی بیرونی
        # (بین ردیف‌ها) و هر splitter افقی داخلی (بین پنجره‌های همان ردیف)
        # تنظیم می‌شود؛ از این‌جا به بعد کاربر می‌تواند با درگ‌کردن آن‌ها را
        # دستی تغییر دهد.
        if self.row_splitter.count():
            self.row_splitter.setSizes([1000] * self.row_splitter.count())
        for rs in self.row_splitters:
            if rs.count():
                rs.setSizes([1000] * rs.count())

        self.visible_count = n

    def assign_camera(self, cam: dict, rtsp_url: str) -> bool:
        """دوربین را در پنجره‌ی انتخاب‌شده (اگر خالی است) یا اولین پنجره‌ی
        خالی نمایش می‌دهد. اگر همان دوربین از قبل باز است، فقط آن پنجره را
        برجسته می‌کند. در صورت پر بودن همه‌ی پنجره‌ها، False برمی‌گرداند."""
        for s in self.slots[:self.visible_count]:
            if s.cam is not None and s.cam["id"] == cam["id"]:
                self._on_slot_clicked(s)
                return True

        target = None
        if self.selected_slot is not None and self.selected_slot.is_empty() \
                and self.selected_slot.index < self.visible_count:
            target = self.selected_slot
        if target is None:
            for s in self.slots[:self.visible_count]:
                if s.is_empty():
                    target = s
                    break
        if target is None:
            return False

        if self.selected_slot is not None:
            self.selected_slot.set_selected(False)
        self.selected_slot = target
        target.set_selected(True)
        target.assign(cam, rtsp_url, self.log_callback)
        return True

    def close_camera(self, cam_id):
        for s in self.slots[:self.visible_count]:
            if s.cam is not None and s.cam["id"] == cam_id:
                s.clear()

    def get_active_camera_frame(self):
        if self.selected_slot is not None and not self.selected_slot.is_empty():
            return self.selected_slot.latest_raw_frame
        return None

    def stop_all(self):
        for s in self.slots:
            if not s.is_empty():
                s.clear()


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
        outer_layout = QVBoxLayout()
        main_layout = QHBoxLayout()

        left_panel = QVBoxLayout()

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

        # رفع درخواست: قبلاً تشخیص خودکار نوع دستگاه (device_detect.py) همیشه
        # با admin/بدون-رمز تلاش می‌کرد که روی هر دستگاهی با اطلاعات ورود
        # سفارشی (رمز واقعی) شکست می‌خورد و اغلب همین باعث می‌شد NVRها اصلاً
        # درست شناسایی نشوند. حالا کاربر می‌تواند یوزرنیم/پسورد واقعی دستگاه‌ها
        # را یک‌بار اینجا وارد کند؛ همین مقدار هم برای اتصال آزمایشی تشخیص نوع
        # دستگاه و هم برای پرشدن خودکار دیالوگ افزودن دوربین/NVR استفاده می‌شود.
        scan_layout.addWidget(QLabel(
            "یوزرنیم/پسورد دستگاه‌ها (برای تشخیص نوع دستگاه و اتصال استفاده می‌شود):"
        ))
        cred_row = QHBoxLayout()
        self.scan_user_input = QLineEdit("admin")
        self.scan_user_input.setPlaceholderText("یوزرنیم")
        self.scan_pass_input = QLineEdit()
        self.scan_pass_input.setPlaceholderText("پسورد")
        self.scan_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        cred_row.addWidget(self.scan_user_input)
        cred_row.addWidget(self.scan_pass_input)
        scan_layout.addLayout(cred_row)

        self.subnet_input = QLineEdit("192.168.1")
        self.subnet_input.setPlaceholderText("پیشوند ساب‌نت (مثلاً 192.168.1)")
        self.scan_btn = QPushButton("اسکن شبکه")
        self.scan_btn.clicked.connect(self.run_network_scan)
        self.scan_result_list = QListWidget()
        # رفع درخواست: به‌جای نگه‌داشتن Ctrl/Shift برای انتخاب چند دستگاه، هر
        # ردیف نتیجه‌ی اسکن یک تیک (checkbox) دارد؛ کاربر هر دستگاهی که
        # می‌خواهد اضافه شود را تیک می‌زند.
        self.scan_result_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.scan_result_list.itemDoubleClicked.connect(self.on_scan_result_selected)
        self.add_selected_scan_btn = QPushButton("+ افزودن دستگاه‌های تیک‌خورده")
        self.add_selected_scan_btn.clicked.connect(self.on_add_selected_scan_results)
        self.detect_status_label = QLabel("")
        self.detect_status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        # نکته: دیگر از کاربر پرسیده نمی‌شود دستگاه دوربین تکی است یا NVR؛ با
        # دابل‌کلیک، نوع دستگاه به‌صورت خودکار تشخیص داده می‌شود (device_detect.py).
        scan_layout.addWidget(QLabel(
            "روی یک نتیجه دابل‌کلیک کنید (افزودن تکی)، یا چند دستگاه را تیک بزنید و "
            "«افزودن دستگاه‌های تیک‌خورده» را بزنید:"
        ))
        scan_layout.addWidget(self.scan_result_list)
        scan_check_row = QHBoxLayout()
        select_all_scan_btn = QPushButton("تیک همه")
        select_all_scan_btn.clicked.connect(lambda: self._set_all_scan_checked(True))
        select_none_scan_btn = QPushButton("لغو تیک همه")
        select_none_scan_btn.clicked.connect(lambda: self._set_all_scan_checked(False))
        scan_check_row.addWidget(select_all_scan_btn)
        scan_check_row.addWidget(select_none_scan_btn)
        scan_layout.addLayout(scan_check_row)
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
        # نکته: چون open_add_nvr_dialog اکنون آرگومان‌های اختیاری بیشتری
        # دارد، باید مثل add_camera_btn از طریق lambda وصل شود؛ در غیر این
        # صورت PyQt مقدار bool سیگنال clicked(checked) را به‌جای None به
        # اولین آرگومان پاس می‌دهد.
        self.add_nvr_btn.clicked.connect(lambda: self.open_add_nvr_dialog())
        add_btn_row.addWidget(self.add_camera_btn)
        add_btn_row.addWidget(self.add_nvr_btn)

        # دوربین‌های متصل به یک NVR به‌صورت زیرمجموعه‌ی همان NVR نمایش داده می‌شوند.
        self.camera_list = QTreeWidget()
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

        # پنل میانی: دیوار نمایش چند-دوربینه با تعداد پنجره‌ی قابل‌انتخاب
        center_panel = QVBoxLayout()
        self.alert_banner = QLabel("")
        self.alert_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.alert_banner.setFixedHeight(28)
        self.alert_banner.setStyleSheet("font-weight: bold;")

        grid_controls = QHBoxLayout()
        grid_controls.addWidget(QLabel("تعداد پنجره نمایش هم‌زمان:"))
        self.window_count_combo = QComboBox()
        for n in sorted(GRID_LAYOUTS):
            self.window_count_combo.addItem(str(n), n)
        self.window_count_combo.setCurrentIndex(sorted(GRID_LAYOUTS).index(4))
        self.window_count_combo.currentIndexChanged.connect(self._on_window_count_changed)
        grid_controls.addWidget(self.window_count_combo)
        grid_controls.addStretch()

        self.camera_grid = CameraGridWidget(self.face_engine, self.log_face_event)
        self.camera_grid.set_window_count(self.window_count_combo.currentData())
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.camera_grid)

        center_panel.addWidget(self.alert_banner)
        center_panel.addLayout(grid_controls)
        center_panel.addWidget(grid_scroll, 1)

        # پنل راست: ستون چهره‌های شناسایی‌شده (تعریف‌شده یا تعریف‌نشده)
        right_panel = QVBoxLayout()
        detected_group = QGroupBox("چهره‌های شناسایی‌شده")
        detected_layout = QVBoxLayout()
        self.detected_faces_list = QListWidget()
        self.detected_faces_list.setIconSize(QSize(64, 64))
        self.detected_faces_list.setWordWrap(True)
        detected_hint = QLabel("تصویر افرادی که در هر پنجره‌ی پخش زنده شناسایی می‌شوند (تعریف‌شده یا تعریف‌نشده) اینجا نمایش داده می‌شود.")
        detected_hint.setWordWrap(True)
        detected_hint.setStyleSheet("color: #888; font-size: 10px;")
        detected_layout.addWidget(detected_hint)
        detected_layout.addWidget(self.detected_faces_list)
        detected_group.setLayout(detected_layout)
        right_panel.addWidget(detected_group)

        main_layout.addLayout(left_panel, 2)
        main_layout.addLayout(center_panel, 5)
        main_layout.addLayout(right_panel, 2)

        # رفع درخواست: پنل جداگانه‌ی «رویدادهای شناسایی چهره» (لاگ متنی خام
        # زیر صفحه) حذف شد؛ چون هر رویداد همان لحظه هم به‌صورت یک آیتم با
        # تصویر واقعی چهره در پنل «چهره‌های شناسایی‌شده» (ستون راست) اضافه
        # می‌شود، آن لاگ متنی صرفاً همان اطلاعات را دوباره و بدون تصویر نشان
        # می‌داد. اکنون فقط همان پنل «چهره‌های شناسایی‌شده» باقی مانده است.
        outer_layout.addLayout(main_layout)

        main_widget.setLayout(outer_layout)
        self.setCentralWidget(main_widget)

        self._banner_timer = QTimer(self)
        self._banner_timer.setSingleShot(True)
        self._banner_timer.timeout.connect(lambda: self.alert_banner.setText(""))

    # -------------------------------------------------------- window grid ---

    def _on_window_count_changed(self, _index):
        n = self.window_count_combo.currentData()
        self.camera_grid.set_window_count(n)

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

    def open_add_camera_dialog(self, prefill_ip=None, detected_path=None, detected_full_url=None,
                                prefill_user=None, prefill_pass=None, prefill_port=None):
        dialog = AddCameraDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user:
            dialog.user_input.setText(prefill_user)
        if prefill_pass:
            dialog.pass_input.setText(prefill_pass)
        if prefill_port:
            # پورت RTSP واقعی‌ای که تشخیص خودکار (device_detect.py) پیدا کرده؛
            # رجوع کنید به توضیح COMMON_RTSP_PORT_FALLBACKS در آن فایل - خیلی
            # از NVR/دوربین‌ها روی پورتی غیر از پیش‌فرض 554 هستند.
            dialog.port_input.setText(str(prefill_port))
        if detected_path is not None or detected_full_url is not None:
            dialog.set_detected_stream(path=detected_path, full_url=detected_full_url)
        if dialog.exec():
            data = dialog.get_camera_data()
            self.camera_store.add_camera(
                data["name"], data["ip"], data["port"], data["user"], data["pass"], data["path"],
                full_url=data.get("full_url"),
            )
            self.reload_camera_list()

    def open_add_nvr_dialog(self, prefill_ip=None, detected_brand=None, detected_onvif_port=None,
                             prefill_user=None, prefill_pass=None, prefill_rtsp_port=None):
        dialog = AddNVRDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user:
            dialog.user_input.setText(prefill_user)
        if prefill_pass:
            dialog.pass_input.setText(prefill_pass)
        if prefill_rtsp_port:
            # پورت RTSP واقعی‌ای که تشخیص خودکار پیدا کرده؛ باید *قبل* از
            # set_detected_brand تنظیم شود چون آن متد بلافاصله اسکن کانال‌ها
            # را با مقدار فعلی این فیلد شروع می‌کند - در غیر این صورت اسکن
            # دوباره با پورت پیش‌فرض غلط (554) انجام می‌شد.
            dialog.rtsp_port_input.setText(str(prefill_rtsp_port))
        if detected_brand:
            dialog.set_detected_brand(brand=detected_brand, onvif_port=detected_onvif_port)
        if dialog.exec():
            data = dialog.get_nvr_data()
            nvr = self.camera_store.add_nvr(
                name=data["name"], ip=data["ip"], rtsp_port=data["rtsp_port"],
                onvif_port=data["onvif_port"], user=data["user"],
                pwd=data["pass"], brand=data["brand"],
            )
            for entry, default_name in dialog.get_selected_channels():
                self._add_channel_from_entry(nvr, entry, default_name)
            self.reload_camera_list()
            QMessageBox.information(
                self, "NVR اضافه شد",
                f"NVR «{nvr['name']}» با {len(dialog.get_selected_channels())} کانال اضافه شد."
            )


    def _add_channel_from_entry(self, nvr, entry, default_name):
        if entry["is_full_url"]:
            self.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path="", full_url=entry["path_or_url"]
            )
        else:
            self.camera_store.add_channel_camera(
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

        if dialog.exec():
            data = dialog.get_nvr_data()
            self.camera_store.update_nvr(nvr_id, **data)
            existing_channels = {c.get("channel") for c in self.camera_store.cameras_for_nvr(nvr_id)}
            added = 0
            for entry, default_name in dialog.get_selected_channels():
                if entry["channel"] in existing_channels:
                    continue  # این کانال قبلاً اضافه شده است
                self._add_channel_from_entry(nvr, entry, default_name)
                added += 1
            self.reload_camera_list()
            QMessageBox.information(self, "بازخوانی کامل شد", f"{added} کانال جدید اضافه شد.")

    def delete_nvr(self, nvr_id):
        confirm = QMessageBox.question(
            self, "تأیید حذف", "آیا از حذف این NVR و همه‌ی کانال‌های ثبت‌شده‌ی آن مطمئن هستید؟"
        )
        if confirm == QMessageBox.StandardButton.Yes:
            for cam in self.camera_store.cameras_for_nvr(nvr_id):
                self.camera_grid.close_camera(cam["id"])
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
            self.camera_grid.close_camera(cam_id)
            self.camera_store.remove_camera(cam_id)
            self.reload_camera_list()

    def on_camera_item_activated(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data["type"] != "camera":
            return
        cam = self.camera_store.get_camera(data["id"])
        if cam:
            self.open_live_view(cam)

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
        return self.camera_grid.get_active_camera_frame()

    # ------------------------------------------------------ face library ---

    def open_face_library(self):
        dialog = FaceLibraryDialog(self.face_engine, self.get_active_camera_frame, self)
        dialog.exec()

    @staticmethod
    def _face_image_to_icon(face_img):
        """تبدیل برش تصویر چهره (BGR ndarray) به QIcon برای نمایش در ستون
        «چهره‌های شناسایی‌شده». اگر برشی موجود نباشد (مثلاً تشخیص محل دقیق چهره
        ممکن نشده)، None برمی‌گرداند تا آیتم بدون آیکون (فقط متن) نمایش داده شود."""
        if face_img is None or face_img.size == 0:
            return None
        try:
            rgb = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qt_img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            return QIcon(QPixmap.fromImage(qt_img))
        except Exception:
            return None

    def log_face_event(self, kind, camera_name, person, face_img=None):
        timestamp = time.strftime("%H:%M:%S")
        if kind == "known":
            person_name = person.get("name", "") if person else ""
            text = f"[{timestamp}] {camera_name}: چهره شناسایی شد - {person_name}"
            self.alert_banner.setStyleSheet("font-weight: bold; color: #2ecc71;")
            face_item_text = f"{person_name}\n{camera_name} - {timestamp}"
        else:
            text = f"[{timestamp}] {camera_name}: ⚠ چهره تعریف نشده شناسایی شد!"
            self.alert_banner.setStyleSheet("font-weight: bold; color: #e74c3c;")
            face_item_text = f"⚠ چهره تعریف نشده\n{camera_name} - {timestamp}"

        self.alert_banner.setText(text)
        self._banner_timer.start(5000)

        # ستون سمت راست: تصویر خودِ شخص شناسایی‌شده (تعریف‌شده یا تعریف‌نشده).
        face_item = QListWidgetItem(face_item_text)
        icon = self._face_image_to_icon(face_img)
        if icon is not None:
            face_item.setIcon(icon)
        if kind == "unknown":
            face_item.setForeground(Qt.GlobalColor.red)
        self.detected_faces_list.insertItem(0, face_item)
        # جلوگیری از رشد بی‌حد لیست در یک نشست طولانی.
        while self.detected_faces_list.count() > 300:
            self.detected_faces_list.takeItem(self.detected_faces_list.count() - 1)

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
            # رفع درخواست: به‌جای انتخاب چندتایی با Ctrl/Shift، هر ردیف یک
            # تیک (checkbox) دارد.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.scan_result_list.addItem(item)

    def _set_all_scan_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.scan_result_list.count()):
            item = self.scan_result_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)

    def _scan_credentials(self):
        return self.scan_user_input.text().strip() or "admin", self.scan_pass_input.text()

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
        event.accept()

    # ------------------------------------------------------------ tabs ----

    def open_live_view(self, cam: dict):
        if not self._ensure_password(cam):
            return  # کاربر از وارد کردن رمز صرف‌نظر کرد

        rtsp_url = self.camera_store.build_rtsp_url(cam)
        if not self.camera_grid.assign_camera(cam, rtsp_url):
            QMessageBox.information(
                self, "پنجره‌ی خالی وجود ندارد",
                "همه‌ی پنجره‌های نمایش پر هستند. یکی از پنجره‌ها را ببندید (✕) یا "
                "روی یک پنجره‌ی خالی کلیک کنید تا آن به‌عنوان مقصد انتخاب شود، یا "
                "تعداد پنجره‌های نمایش هم‌زمان را افزایش دهید."
            )

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
        """رفع درخواست: چند نتیجه‌ی اسکن هم‌زمان (با تیک زدن) انتخاب و
        پشت‌سرهم اضافه شوند. چون هر تشخیص (و در ادامه‌ی آن، دیالوگ افزودن
        دوربین/NVR) نیاز به تعامل کاربر دارد، دستگاه‌ها یکی‌یکی (نه هم‌زمان)
        پردازش می‌شوند: بعد از بسته‌شدن دیالوگ مربوط به هر دستگاه، خودکار سراغ
        دستگاه بعدی در صف می‌رود."""
        if self.detect_thread is not None and self.detect_thread.isRunning():
            return

        ips = []
        for i in range(self.scan_result_list.count()):
            item = self.scan_result_list.item(i)
            if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
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
                self, "موردی تیک نخورده",
                "ابتدا تیک دستگاه‌(های) موردنظر را از لیست نتایج اسکن بزنید."
            )
            return

        self._detect_queue = ips[1:]
        self._start_device_detect(ips[0])

    def _start_device_detect(self, ip):
        # رفع درخواست: دیگر از کاربر «دوربین تکی یا NVR؟» پرسیده نمی‌شود؛
        # DeviceDetectThread با یک اتصال آزمایشی (ONVIF یا تست کانال‌ها)
        # خودش نوع دستگاه را تشخیص می‌دهد - رجوع کنید به device_detect.py.
        # رفع مشکل «تشخیص NVR»: قبلاً این اتصال آزمایشی همیشه با admin/بدون-رمز
        # انجام می‌شد؛ حالا از یوزرنیم/پسورد وارد شده در بالای پنل اسکن استفاده
        # می‌شود تا روی دستگاه‌هایی با اطلاعات ورود سفارشی هم درست کار کند.
        self._detect_ip = ip
        self.scan_result_list.setEnabled(False)
        self.add_selected_scan_btn.setEnabled(False)
        self.detect_status_label.setText(f"در حال تشخیص نوع دستگاه {ip}...")

        user, pwd = self._scan_credentials()
        self.detect_thread = DeviceDetectThread(
            ip=ip,
            open_ports=self._scan_ports_by_ip.get(ip, []),
            rtsp_port="554",
            user=user,
            pwd=pwd,
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
        user, pwd = self._scan_credentials()
        if kind == "nvr":
            self.open_add_nvr_dialog(
                prefill_ip=ip,
                detected_brand=payload.get("brand"),
                detected_onvif_port=payload.get("onvif_port"),
                prefill_user=user,
                prefill_pass=pwd,
                prefill_rtsp_port=payload.get("rtsp_port"),
            )
        else:
            self.open_add_camera_dialog(
                prefill_ip=ip,
                detected_path=payload.get("path"),
                detected_full_url=payload.get("full_url"),
                prefill_user=user,
                prefill_pass=pwd,
                prefill_port=payload.get("rtsp_port"),
            )
        self._advance_detect_queue()

    def _on_device_detect_failed(self, msg):
        ip = self._detect_ip
        self._reset_detect_ui()
        user, pwd = self._scan_credentials()

        # تشخیص خودکار با نام کاربری/رمز واردشده ممکن است روی دستگاه‌هایی با
        # اطلاعات ورود متفاوت شکست بخورد؛ در این حالت کاربر می‌تواند به‌صورت
        # دستی و با وارد کردن رمز درست، نوع دستگاه را انتخاب کند.
        box = QMessageBox(self)
        box.setWindowTitle("تشخیص خودکار ناموفق بود")
        box.setText(f"{msg}\n\nنوع دستگاه {ip} را به‌صورت دستی مشخص کنید:")
        camera_btn = box.addButton("دوربین تکی", QMessageBox.ButtonRole.AcceptRole)
        nvr_btn = box.addButton("NVR (چند کاناله)", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("انصراف", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if box.clickedButton() == camera_btn:
            self.open_add_camera_dialog(prefill_ip=ip, prefill_user=user, prefill_pass=pwd)
        elif box.clickedButton() == nvr_btn:
            self.open_add_nvr_dialog(prefill_ip=ip, prefill_user=user, prefill_pass=pwd)
        self._advance_detect_queue()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

# -*- coding: utf-8 -*-
"""صفحه‌ی «🗺 نقشه ساختمان» — نقشه‌ی تعاملی طبقات

امکانات:
- هر طبقه نقشه‌ی جداگانه (DXF خروجی اتوکد یا تصویر)
- رندر دقیق DXF با لایه‌ها، رنگ‌ها و واحد واقعی (میلی‌متر/متر/...)
- جای‌گذاری دوربین، NVR، رک، سوئیچ و... با درگ و زاویه‌ی دید (FOV)
- کلیک ساده روی دوربین = انتخاب و تنظیم (زاویه/پهنای دید)؛
  دابل‌کلیک روی دوربین نقشه = پخش زنده
- نمایش مسیر تردد شخص (از ردیابی اشخاص) روی نقشه با پخش متحرک
- شبکه‌ی مختصات متری + نمایش مختصات موس برای دقت جای‌گذاری
"""

import math
import os

from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QTimer, pyqtSignal, QEvent,
)
from PyQt6.QtGui import (
    QColor, QPen, QBrush, QPainter, QPainterPath, QFont,
    QCursor, QPolygonF,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QGraphicsView, QGraphicsScene, QGraphicsItemGroup,
    QGraphicsPathItem, QGraphicsSimpleTextItem, QGraphicsEllipseItem,
    QGraphicsLineItem, QSplitter, QComboBox, QLineEdit, QSpinBox, QSlider,
    QDoubleSpinBox,
    QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QFileDialog,
    QInputDialog, QGroupBox, QAbstractItemView, QGraphicsPolygonItem,
    QScrollArea, QTextEdit,
)

from building_map import (
    MapStore, DxfMapLoader, DxfError, DEVICE_KINDS, ezdxf_available,
    device_scene_xy,
)
from person_store import person_store
from plate_store import plate_store
from lane_geometry import (
    DEFAULT_ATTACH_TOLERANCE_M, build_lane_record, camera_on_lane,
    validate_lane_points,
)

# ---------------------------------------------------------------------------
# صحنه با شبکه‌ی مختصات
# ---------------------------------------------------------------------------
class MapScene(QGraphicsScene):
    """صحنه‌ی نقشه با پس‌زمینه‌ی تیره و شبکه‌ی نقطه‌ای تطبیقی.

    گام گرید از روی اندازه‌ی واقعی نقشه (به متر) انتخاب می‌شود (~۱۵ خانه
    در بزرگ‌ترین بعد، رُند به ۱/۲/۵) تا با هر مقیاسی — از سانتی‌متر تا
    کیلومتر — گرید معنادار دیده شود (2.0.23-beta).
    """

    def __init__(self, to_meter=1.0):
        super().__init__()
        self.to_meter = to_meter or 1.0
        self.setBackgroundBrush(QBrush(QColor("#0b0f14")))

    def grid_step_scene(self):
        """گام پایه‌ی گرید به واحد صحنه (تطبیقی با اندازه‌ی نقشه)."""
        tm = self.to_meter or 1.0
        try:
            w_m = max(1e-9, self.sceneRect().width() * tm)
            h_m = max(1e-9, self.sceneRect().height() * tm)
        except Exception:
            w_m = h_m = 1.0
        raw = max(w_m, h_m) / 15.0  # ~۱۵ خانه در بزرگ‌ترین بعد
        exp = math.floor(math.log10(raw)) if raw > 0 else 0
        base = 10.0 ** exp
        nice_m = base * 10
        for m in (1, 2, 5, 10):
            if base * m >= raw:
                nice_m = base * m
                break
        return max(nice_m / tm, 1e-9)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        step = self.grid_step_scene()
        if step <= 0:
            return
        # اگر خیلی زوم‌اوت است، شبکه را درشت‌تر کن
        try:
            scale = self.views()[0].transform().m11() if self.views() else 1.0
        except Exception:
            scale = 1.0
        while step * scale < 18:
            step *= 5
        painter.setPen(QPen(QColor("#1b2534"), 0))
        x = (rect.left() // step) * step
        while x <= rect.right():
            y = (rect.top() // step) * step
            while y <= rect.bottom():
                painter.drawPoint(QPointF(x, y))
                y += step
            x += step


# ---------------------------------------------------------------------------
# آیتم تجهیز روی نقشه
# ---------------------------------------------------------------------------
def _sector_path(cx, cy, radius, angle_deg, fov_deg, steps=28):
    """چندضلعی قطاع دایره؛ زاویه‌ها به سبک ریاضی (۰=شرق، خلاف عقربه‌ساعت)."""
    import math
    p = QPainterPath()
    p.moveTo(cx, cy)
    a0 = angle_deg - fov_deg / 2.0
    for i in range(steps + 1):
        a = math.radians(a0 + fov_deg * i / steps)
        p.lineTo(cx + radius * math.cos(a), cy - radius * math.sin(a))
    p.closeSubpath()
    return p


def person_color(person_id):
    """رنگ یکتا و ثابت برای هر شخص — از روی id هش می‌شود تا در اجراهای
    مختلف همان رنگ بماند. برای مسیر خط‌چین هر شخص روی نقشه."""
    import hashlib
    h = int(hashlib.md5(
        str(person_id or "?").encode("utf-8")).hexdigest()[:8], 16)
    return QColor.fromHsv(h % 360, 225, 255)


class DeviceItem(QGraphicsItemGroup):
    """نمایش یک تجهیز (دوربین/NVR/رک/...) روی نقشه."""

    def __init__(self, floor_id, device, to_meter, callbacks):
        super().__init__()
        self.floor_id = floor_id
        self.device = device
        self.to_meter = to_meter or 1.0
        self.cb = callbacks
        self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItemGroup.GraphicsItemFlag.ItemIsSelectable)
        self._press_scene = None
        self._build()
        # مختصات ذخیره‌شده به متر است؛ رندر به واحد صحنه برمی‌گردد
        # (2.0.23-beta: مستقل از واحد نقشه).
        sx, sy = device_scene_xy(device, to_meter)
        self.setPos(sx, sy)
        self.setZValue(10)

    # -- ساخت ظاهر --
    def _build(self):
        # پاک‌سازی قبلی (برای refresh)
        for ch in self.childItems():
            self.removeFromGroup(ch)
        dev = self.device
        kind = dev.get("kind", "other")
        name = dev.get("name", "")
        accent = QColor("#22d3ee")

        if kind == "camera":
            # (2.0.23-beta) نشان دوربین فقط ایموجی 🎥 است؛ دایره و کادر
            # قطاع دید به خواست کاربر حذف شد.
            glyph = QGraphicsSimpleTextItem("🎥")
            gf = QFont()
            gf.setPointSize(18)
            glyph.setFont(gf)
            glyph.setPos(-14, -19)
            glyph.setFlag(
                QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self.addToGroup(glyph)
        else:
            info = DEVICE_KINDS.get(kind, DEVICE_KINDS["other"])
            box = QGraphicsPathItem()
            pp = QPainterPath()
            pp.addRoundedRect(-16, -13, 32, 26, 6, 6)
            box.setPath(pp)
            box.setPen(QPen(accent, 2))
            box.setBrush(QBrush(QColor("#0e1620")))
            box.setFlag(
                QGraphicsPathItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self.addToGroup(box)
            glyph = QGraphicsSimpleTextItem(info["icon"])
            glyph.setPos(-10, -14)
            glyph.setFlag(
                QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self.addToGroup(glyph)

        label = QGraphicsSimpleTextItem(name)
        f = QFont()
        f.setPointSize(9)
        label.setFont(f)
        label.setBrush(QBrush(QColor("#e2e8f0")))
        label.setPos(-30, 16)
        label.setFlag(
            QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.addToGroup(label)
        self._label = label

    def refresh(self):
        """به‌روزرسانی زنده‌ی ظاهر تجهیز.

        (2.0.23-beta) دوربین فقط ایموجی است؛ چیزی برای به‌روزرسانی
        هندسی نیست و موقعیت دست نمی‌خورد.
        """
        self.update()

    def set_name(self, name):
        self._label.setText(name)

    # -- کلیک در برابر درگ --
    # کلیک ساده فقط «انتخاب» می‌کند (پنل مشخصات: زاویه/پهنای دید)؛
    # پخش زنده با «دابل‌کلیک» باز می‌شود تا هنگام تنظیم زاویه، پنجره‌ی
    # پیش‌نمایش مزاحم نشود.
    def mousePressEvent(self, event):
        self._press_scene = event.scenePos()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        cb = self.cb.get("clicked")
        if callable(cb):
            cb(self)
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        moved = True
        try:
            if self._press_scene is not None:
                moved = (event.scenePos() - self._press_scene).manhattanLength() > 4
        except Exception:
            pass
        super().mouseReleaseEvent(event)
        if moved:
            cbm = self.cb.get("moved")
            if callable(cbm):
                p = self.pos()
                cbm(self.floor_id, self.device.get("id"), p.x(), p.y())


# ---------------------------------------------------------------------------
# ویوی نقشه
# ---------------------------------------------------------------------------
class MapView(QGraphicsView):
    place_clicked = pyqtSignal(QPointF)   # کلیک در حالت جای‌گذاری
    mouse_moved = pyqtSignal(QPointF)     # مختصات موس (واحد صحنه)
    lane_click = pyqtSignal(QPointF)       # کلیک تمیز در حالت رسم/انتخاب مسیر

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.placing = False
        self.lane_drawing = False  # حالت رسم مسیر / انتخاب دوربین‌های مسیر
        self._pan = None
        self._press_pos = None

    def wheelEvent(self, event):
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        self._press_pos = event.pos()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan = event.pos()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        if self.lane_drawing and event.button() == Qt.MouseButton.LeftButton:
            # در حالت رسم مسیر: نه پن، نه درگ تجهیز؛ فقط ثبت نقطه/انتخاب
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and not self.placing:
            # اگر روی آیتم متحرک نیستیم، پن کنیم
            if self.itemAt(event.pos()) is None:
                self._pan = event.pos()
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            dx = event.pos().x() - self._pan.x()
            dy = event.pos().y() - self._pan.y()
            self._pan = event.pos()
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            # اسکرول‌بارها مخفی‌اند ولی مقدارشان جابه‌جا می‌شود
            h.setValue(h.value() - dx)
            v.setValue(v.value() - dy)
        else:
            try:
                self.mouse_moved.emit(self.mapToScene(event.pos()))
            except Exception:
                pass
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.lane_drawing and event.button() == Qt.MouseButton.LeftButton:
            self._pan = None
            try:
                self.unsetCursor()
            except Exception:
                pass
            if (self._press_pos is not None
                    and (event.pos() - self._press_pos).manhattanLength() < 5):
                try:
                    self.lane_click.emit(self.mapToScene(event.pos()))
                except Exception:
                    pass
            super().mouseReleaseEvent(event)
            try:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            except Exception:
                pass
            return
        was_pan = self._pan is not None
        self._pan = None
        self.unsetCursor()
        if self.placing and event.button() == Qt.MouseButton.LeftButton:
            try:
                self.place_clicked.emit(self.mapToScene(event.pos()))
            except Exception:
                pass
            return
        # کلیک ساده روی فضای خالی = لغو انتخاب
        if (not was_pan and event.button() == Qt.MouseButton.LeftButton
                and self._press_pos is not None
                and (event.pos() - self._press_pos).manhattanLength() < 5
                and self.itemAt(event.pos()) is None):
            try:
                sc = self.scene()
                if sc is not None:
                    sc.clearSelection()
            except Exception:
                pass
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# دیالوگ تنظیم تجهیز هنگام جای‌گذاری
# ---------------------------------------------------------------------------
class DeviceConfigDialog(QDialog):
    def __init__(self, kind, camera_store, parent=None, device=None):
        super().__init__(parent)
        self.setWindowTitle("مشخصات تجهیز")
        self.setModal(True)
        info = DEVICE_KINDS.get(kind, DEVICE_KINDS["other"])
        form = QFormLayout(self)
        form.addRow(QLabel(f"{info['icon']} {info['fa']}"))

        self.name_edit = QLineEdit((device or {}).get("name") or info["fa"])
        form.addRow("نام:", self.name_edit)

        self.link_combo = QComboBox()
        self.link_combo.addItem("— بدون اتصال —", "")
        if kind == "camera":
            for cam_id, label in self._camera_entries(camera_store):
                self.link_combo.addItem(f"🎥 {label}", cam_id)
        elif kind == "nvr":
            try:
                for nvr in camera_store.nvrs:
                    self.link_combo.addItem(
                        f"🖥 {nvr.get('name') or nvr.get('ip')}",
                        nvr.get("id"))
            except Exception:
                pass
        form.addRow("اتصال به:", self.link_combo)
        if device and device.get("ref_id"):
            idx = self.link_combo.findData(device.get("ref_id"))
            if idx >= 0:
                self.link_combo.setCurrentIndex(idx)

        self.angle_spin = QSpinBox()
        self.angle_spin.setRange(0, 359)
        self.angle_spin.setValue(int((device or {}).get("angle", 0)))
        self.angle_spin.setSuffix("°")
        form.addRow("زاویه دید (۰=شرق، ۹۰=شمال):", self.angle_spin)

        self.fov_spin = QSpinBox()
        self.fov_spin.setRange(20, 180)
        self.fov_spin.setValue(int((device or {}).get("fov", 90)))
        self.fov_spin.setSuffix("°")
        self.fov_spin.setEnabled(kind == "camera")
        form.addRow("پهنای دید (FOV):", self.fov_spin)

        # فاصله دید دوربین (متر): تا کجا را می‌گیرد؛ روی نقشه قطاع دید
        # با همین برد رسم می‌شود.
        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(2, 200)
        self.range_spin.setSingleStep(1)
        self.range_spin.setDecimals(1)
        self.range_spin.setValue(float((device or {}).get("view_distance", 8.0)))
        self.range_spin.setSuffix(" متر")
        self.range_spin.setEnabled(kind == "camera")
        form.addRow("فاصله دید (برد دوربین):", self.range_spin)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("تأیید")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    @staticmethod
    def _camera_entries(camera_store):
        out = []
        try:
            for cam in camera_store.standalone_cameras():
                out.append((cam.get("id"),
                            cam.get("name") or cam.get("ip") or "؟"))
            for nvr in camera_store.nvrs:
                nvr_name = nvr.get("name") or nvr.get("ip") or ""
                for cam in camera_store.cameras_for_nvr(nvr.get("id")):
                    label = cam.get("name") or f"کانال {cam.get('channel', '')}"
                    out.append((cam.get("id"), f"{label} ({nvr_name})"))
        except Exception:
            pass
        return out

    def values(self):
        return {
            "name": self.name_edit.text().strip(),
            "ref_id": self.link_combo.currentData() or "",
            "angle": float(self.angle_spin.value()),
            "fov": float(self.fov_spin.value()),
            "view_distance": float(self.range_spin.value()),
        }


# ---------------------------------------------------------------------------
# دیالوگ انتخاب شخص برای نمایش مسیر
# ---------------------------------------------------------------------------
class PersonPickDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتخاب شخص")
        self.setModal(True)
        self.resize(380, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("مسیر حرکت کدام شخص روی نقشه نمایش داده شود؟"))
        self.list = QListWidget()
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        try:
            for p in person_store.get_persons():
                code = p.get("code", "?")
                note = (p.get("notes") or "").strip()
                last = p.get("last_seen_j", "")
                item = QListWidgetItem(
                    f"{code}  {note + ' — ' if note else ''}{last}")
                item.setData(Qt.ItemDataRole.UserRole, p.get("id"))
                self.list.addItem(item)
        except Exception:
            pass
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self.list, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("نمایش مسیر")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


# ---------------------------------------------------------------------------
# صفحه‌ی اصلی نقشه
# ---------------------------------------------------------------------------
class BuildingMapPage(QWidget):
    """صفحه‌ی «🗺 نقشه ساختمان» داخل QStackedWidget اصلی."""

    def __init__(self, camera_store, on_camera_click=None, parent=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self.on_camera_click = on_camera_click
        self.store = MapStore()
        self.scenes = {}          # floor_id -> {"scene", "to_meter", "items", "bounds"}
        self.current_floor = None
        self._placing_kind = None
        self._path = None         # اطلاعات مسیر شخص فعال
        # --- حالت رسم مسیر پلاک‌خوان (2.0.17-beta) ---
        self._lane_mode = None    # None | "draw" | "pick"
        self._lane_points = []    # [(x, y)] به واحد صحنه
        self._lane_pick = {}      # device_id -> DeviceItem (دوربین‌های انتخاب‌شده)
        self._lane_pick_rings = {}  # device_id -> QGraphicsEllipseItem
        self._lane_selected_id = None  # مسیر انتخاب‌شده در لیست (نمایش روی نقشه)
        self._heatmap_on = False  # وضعیت نمایش هیت‌مپ تردد (2.0.18-beta)
        # --- گزارش زنده‌ی پوشش دوربین روی نقاط مسیر (2.0.21-beta) ---
        # وقتی دکمه‌ی «📡 پوشش» زده می‌شود فعال می‌شود و با هر جابه‌جایی یا
        # تغییر دوربین (موقعیت/زاویه/پهنا/برد) خودکار به‌روز می‌شود.
        self._coverage_live = None  # None | {"floor_id": ...}
        self._pending_fit = False
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._play_tick)
        # لایه‌ی زنده‌ی اشخاص: (cam_id, person_id) -> اطلاعات نشان‌ها.
        # در لحظه‌ی شناسایی شخص توسط یک دوربین، نشان سبز چشمک‌زن با کد
        # شخص روی همان دوربینِ نقشه می‌نشیند و با تمام شدن رد برداشته می‌شود.
        self._live_persons = {}
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._live_pulse_tick)
        self._live_phase = False
        self._build_ui()
        self._reload_floors()

    # ============================ رابط کاربری ============================
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # --- پنل چپ (اسکرول‌شونده: با افزودن بخش «مسیرهای پلاک‌خوان»
        # ارتفاع محتوا از قد صفحه بیشتر می‌شود؛ عرض ثابت می‌ماند) ---
        left = QWidget()
        # جهت راست‌به‌چپ: ترتیب ایموجی/متن عنوان‌ها درست می‌شود و با فونت‌های
        # پهن، بریده‌شدن عنوان‌ها از سمت چپ رخ نمی‌دهد.
        left.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(2, 2, 2, 2)

        ll.addWidget(self._title("🏢 طبقات ساختمان"))
        self.floor_list = QListWidget()
        self.floor_list.setMaximumHeight(130)
        self.floor_list.currentItemChanged.connect(self._on_floor_changed)
        ll.addWidget(self.floor_list)
        fr = QHBoxLayout()
        for text, fn in (("＋", self._add_floor), ("✏️", self._rename_floor),
                         ("🗑", self._delete_floor)):
            b = QPushButton(text)
            b.setFixedHeight(28)
            b.clicked.connect(fn)
            fr.addWidget(b)
        ll.addLayout(fr)

        ll.addWidget(self._title("🗺 نقشه‌ی طبقه"))
        mr = QHBoxLayout()
        self.dxf_btn = QPushButton("📥 DXF از اتوکد")
        self.dxf_btn.clicked.connect(self._import_dxf)
        self.img_btn = QPushButton("🖼 تصویر")
        self.img_btn.clicked.connect(self._import_image)
        mr.addWidget(self.dxf_btn)
        mr.addWidget(self.img_btn)
        ll.addLayout(mr)
        self.units_label = QLabel("واحد نقشه: —")
        self.units_label.setStyleSheet("color:#8fa3b8; font-size:11px;")
        ll.addWidget(self.units_label)

        ll.addWidget(self._title("👁 لایه‌های نقشه"))
        self.layer_list = QListWidget()
        self.layer_list.setMaximumHeight(110)
        self.layer_list.itemChanged.connect(self._on_layer_toggled)
        ll.addWidget(self.layer_list)

        ll.addWidget(self._title("➕ افزودن تجهیز"))
        pal = QVBoxLayout()
        for kind, info in DEVICE_KINDS.items():
            b = QPushButton(f"{info['icon']} {info['fa']}")
            b.setFixedHeight(30)
            b.clicked.connect(
                lambda _c=False, _k=kind: self._start_placing(_k))
            pal.addWidget(b)
        ll.addLayout(pal)
        self.place_hint = QLabel("")
        self.place_hint.setStyleSheet("color:#fbbf24; font-size:11px;")
        self.place_hint.setWordWrap(True)
        ll.addWidget(self.place_hint)

        ll.addWidget(self._title("👥 مسیر شخص"))
        pr = QHBoxLayout()
        self.path_btn = QPushButton("نمایش مسیر...")
        self.path_btn.clicked.connect(self._pick_person_path)
        self.path_close_btn = QPushButton("✖")
        self.path_close_btn.setFixedWidth(36)
        self.path_close_btn.setEnabled(False)
        self.path_close_btn.clicked.connect(self._clear_path)
        pr.addWidget(self.path_btn)
        pr.addWidget(self.path_close_btn)
        ll.addLayout(pr)

        # --- مسیرهای پلاک‌خوان (2.0.17-beta): رسم روی نقشه + اتصال دوربین ---
        ll.addWidget(self._title("🛣 مسیرهای پلاک‌خوان"))
        self.lane_draw_btn = QPushButton("✏️ رسم مسیر جدید")
        self.lane_draw_btn.setFixedHeight(30)
        self.lane_draw_btn.clicked.connect(self._start_lane_draw)
        ll.addWidget(self.lane_draw_btn)
        self.lane_list = QListWidget()
        self.lane_list.setMaximumHeight(90)
        self.lane_list.itemClicked.connect(self._on_lane_selected)
        ll.addWidget(self.lane_list)
        lanerow = QHBoxLayout()
        self.lane_del_btn = QPushButton("🗑 حذف")
        self.lane_del_btn.clicked.connect(self._delete_lane)
        self.lane_rules_btn = QPushButton("❓ قوانین")
        self.lane_rules_btn.clicked.connect(self._show_lane_rules)
        lanerow.addWidget(self.lane_del_btn)
        lanerow.addWidget(self.lane_rules_btn)
        ll.addLayout(lanerow)
        # ابزارهای مسیر (2.0.18-beta): شبیه‌سازی، پوشش، هیت‌مپ
        # عمودی (نه افقی ۳تایی): ردیف افقی با فونت‌های پهن از عرض ۲۷۲ پیکسل
        # پنل بیشتر می‌شد و عنوان‌های بخش‌ها از چپ بریده می‌شدند.
        ltoolbox = QVBoxLayout()
        ltoolbox.setSpacing(4)
        self.lane_sim_btn = QPushButton("▶️ شبیه‌سازی")
        self.lane_sim_btn.setToolTip("شبیه‌سازی حرکت خودرو روی مسیر انتخاب‌شده")
        self.lane_sim_btn.clicked.connect(self._start_lane_sim)
        self.coverage_btn = QPushButton("📡 پوشش")
        self.coverage_btn.setToolTip(
            "تحلیل زنده‌ی پوشش دوربین روی نقاط مسیر: کدام نقاط مسیر "
            "بدون دوربین‌اند و هر دوربین کدام نقاط را می‌بیند")
        self.coverage_btn.clicked.connect(self._analyze_coverage)
        self.heatmap_btn = QPushButton("🔥 هیت‌مپ")
        self.heatmap_btn.setToolTip(
            "نمایش پرترددترین مسیرها با رنگ روی نقشه")
        self.heatmap_btn.setCheckable(True)
        self.heatmap_btn.clicked.connect(self._toggle_heatmap)
        ltoolbox.addWidget(self.lane_sim_btn)
        ltoolbox.addWidget(self.coverage_btn)
        ltoolbox.addWidget(self.heatmap_btn)
        ll.addLayout(ltoolbox)

        # --- گزارش زنده‌ی پوشش دوربین روی نقاط مسیر (2.0.21-beta) ---
        # با زدن «📡 پوشش» پر می‌شود و با هر جابه‌جایی/تغییر دوربین
        # خودکار به‌روز می‌شود (در لحظه).
        ll.addWidget(self._title("📡 گزارش پوشش زنده"))
        self.coverage_report = QTextEdit()
        self.coverage_report.setReadOnly(True)
        self.coverage_report.setMaximumHeight(180)
        self.coverage_report.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft)
        self.coverage_report.setStyleSheet(
            "font-size: 11px; background: #0e1620;")
        self.coverage_report.setPlainText(
            "برای تحلیل پوشش، دکمه‌ی «📡 پوشش» را بزنید.\n"
            "بعد از آن با هر جابه‌جایی دوربین، گزارش در لحظه "
            "به‌روز می‌شود.")
        ll.addWidget(self.coverage_report)

        ll.addStretch()
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left)
        left_scroll.setFixedWidth(272)
        left_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(left_scroll)

        # --- وسط: نقشه ---
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        self.view = MapView()
        self.view.place_clicked.connect(self._on_place_clicked)
        self.view.mouse_moved.connect(self._on_mouse_moved)
        cl.addWidget(self.view, 1)
        self._center_widget = center
        # نوار شناور تأیید/لغو رسم مسیر (2.0.17-beta)
        self._lane_bar = QWidget(center)
        _bl = QHBoxLayout(self._lane_bar)
        _bl.setContentsMargins(10, 6, 10, 6)
        _bl.setSpacing(8)
        self._lane_bar_label = QLabel("")
        self._lane_bar_label.setStyleSheet("color:#fde68a; font-size:12px;")
        self._lane_bar_confirm = QPushButton("✅ تأیید مسیر")
        self._lane_bar_cancel = QPushButton("❌ لغو")
        for _b in (self._lane_bar_confirm, self._lane_bar_cancel):
            _b.setFixedHeight(30)
        self._lane_bar_confirm.clicked.connect(self._on_lane_bar_confirm)
        self._lane_bar_cancel.clicked.connect(lambda: self._cancel_lane_mode())
        _bl.addWidget(self._lane_bar_label)
        _bl.addWidget(self._lane_bar_confirm)
        _bl.addWidget(self._lane_bar_cancel)
        self._lane_bar.setStyleSheet(
            "background-color:#0e1620; border:1px solid #22d3ee; "
            "border-radius:8px;")
        self._lane_bar.hide()
        self.view.lane_click.connect(self._on_lane_click)
        self.view.installEventFilter(self)
        coord_row = QHBoxLayout()
        self.coord_label = QLabel("X: — ، Y: —")
        self.coord_label.setStyleSheet("color:#8fa3b8; font-size:11px;")
        self.zoom_label = QLabel("زوم: 100٪")
        self.zoom_label.setStyleSheet("color:#8fa3b8; font-size:11px;")
        coord_row.addWidget(self.coord_label)
        coord_row.addStretch()
        coord_row.addWidget(self.zoom_label)
        # دکمه‌های زوم
        for text, fn in (("＋", lambda: self.view.scale(1.25, 1.25)),
                         ("－", lambda: self.view.scale(0.8, 0.8)),
                         ("⤢", self._fit_current)):
            b = QPushButton(text)
            b.setFixedSize(30, 26)
            b.clicked.connect(fn)
            coord_row.addWidget(b)
        cl.addLayout(coord_row)
        root.addWidget(center, 1)

        # --- پنل راست ---
        right = QWidget()
        right.setFixedWidth(288)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(2, 2, 2, 2)

        self.prop_group = QGroupBox("⚙ مشخصات تجهیز")
        pf = QFormLayout(self.prop_group)
        self.prop_name = QLineEdit()
        self.prop_name.editingFinished.connect(self._prop_apply)
        pf.addRow("نام:", self.prop_name)
        self.prop_link = QComboBox()
        self.prop_link.currentIndexChanged.connect(self._prop_apply)
        pf.addRow("اتصال به:", self.prop_link)
        ang_row = QHBoxLayout()
        self.prop_angle = QSlider(Qt.Orientation.Horizontal)
        self.prop_angle.setRange(0, 359)
        self.prop_angle.valueChanged.connect(self._prop_angle_changed)
        self.prop_angle.sliderReleased.connect(self._prop_apply)
        self.prop_angle_num = QSpinBox()
        self.prop_angle_num.setRange(0, 359)
        self.prop_angle_num.setSuffix("°")
        self.prop_angle_num.valueChanged.connect(self._prop_angle_changed)
        ang_row.addWidget(self.prop_angle, 1)
        ang_row.addWidget(self.prop_angle_num)
        pf.addRow("زاویه:", ang_row)
        self.prop_fov = QSpinBox()
        self.prop_fov.setRange(20, 180)
        self.prop_fov.setSuffix("°")
        self.prop_fov.valueChanged.connect(self._prop_apply)
        pf.addRow("پهنای دید:", self.prop_fov)
        # فاصله دید دوربین (متر): تا کجا را می‌گیرد — قطاع دید روی نقشه
        # با همین برد رسم می‌شود.
        self.prop_range = QDoubleSpinBox()
        self.prop_range.setRange(2, 200)
        self.prop_range.setSingleStep(1)
        self.prop_range.setDecimals(1)
        self.prop_range.setSuffix(" متر")
        self.prop_range.valueChanged.connect(self._prop_range_changed)
        pf.addRow("فاصله دید:", self.prop_range)
        # رفع باگ «زاویه عوض می‌کنم ولی ذخیره نمی‌شود»: _prop_angle_changed
        # فقط حافظه/نقشه را زنده به‌روز می‌کرد و ذخیره روی دیسک فقط با
        # sliderReleased انجام می‌شد؛ یعنی تغییر زاویه با اسپین‌باکس (تایپ
        # عدد یا دکمه‌های بالا/پایین) هرگز ذخیره نمی‌شد و با عوض کردن طبقه
        # یا بستن برنامه برمی‌گشت. این تایمر تک‌شات، آخرین تغییر زاویه/فاصله
        # دید را ۷۰۰ میلی‌ثانیه بعد از توقف کاربر روی دیسک ذخیره می‌کند
        # (بدون بازنویسی JSON با هر تیک درگ). چون _prop_apply همه‌ی فیلدها
        # را یکجا می‌خواند، همین یک تایمر برای هر دو کافی است.
        self._prop_save_timer = QTimer(self)
        self._prop_save_timer.setSingleShot(True)
        self._prop_save_timer.setInterval(700)
        self._prop_save_timer.timeout.connect(self._prop_apply)
        del_btn = QPushButton("🗑 حذف تجهیز")
        del_btn.clicked.connect(self._delete_selected_device)
        pf.addRow(del_btn)
        rl.addWidget(self.prop_group)
        self.prop_group.setEnabled(False)

        self.timeline_group = QGroupBox("🧭 خط زمانی مسیر")
        tl = QVBoxLayout(self.timeline_group)
        self.timeline_list = QListWidget()
        self.timeline_list.itemClicked.connect(self._on_timeline_clicked)
        tl.addWidget(self.timeline_list, 1)
        trow = QHBoxLayout()
        self.play_btn = QPushButton("▶ پخش مسیر")
        self.play_btn.clicked.connect(self._toggle_play)
        trow.addWidget(self.play_btn)
        self.unmapped_label = QLabel("")
        self.unmapped_label.setStyleSheet("color:#8fa3b8; font-size:11px;")
        self.unmapped_label.setWordWrap(True)
        tl.addLayout(trow)
        tl.addWidget(self.unmapped_label)
        rl.addWidget(self.timeline_group, 1)
        self.timeline_group.setEnabled(False)

        rl.addStretch()
        root.addWidget(right)
        # لیست مسیرهای پلاک‌خوان (با ساخت پیش‌فرض‌ها اگر دیتابیس خالی باشد)
        self._reload_lane_list()

    @staticmethod
    def _title(text):
        l = QLabel(text)
        l.setStyleSheet("font-weight:bold; font-size:12px; padding:4px 0 2px;")
        return l

    # ============================ طبقات ============================
    def _reload_floors(self, select_id=None):
        floors = self.store.floors()
        if not floors:
            fl = self.store.add_floor("طبقه همکف")
            floors = [fl]
        self.floor_list.blockSignals(True)
        self.floor_list.clear()
        for fl in floors:
            item = QListWidgetItem(f"🏢 {fl.get('name', '')}")
            item.setData(Qt.ItemDataRole.UserRole, fl.get("id"))
            self.floor_list.addItem(item)
            if select_id and fl.get("id") == select_id:
                self.floor_list.setCurrentItem(item)
        self.floor_list.blockSignals(False)
        if self.floor_list.currentItem() is None and self.floor_list.count():
            self.floor_list.setCurrentRow(0)
        # اگر طبقه‌ی فعلی حذف شده، سوییچ کن
        cur = self._current_floor_id()
        if cur and self.store.get_floor(cur) is None:
            self._activate_floor(
                floors[0]["id"] if floors else None)

    def _current_floor_id(self):
        item = self.floor_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_floor_changed(self, cur, _prev):
        if cur:
            self._activate_floor(cur.data(Qt.ItemDataRole.UserRole))

    def _add_floor(self):
        name, ok = QInputDialog.getText(self, "طبقه جدید", "نام طبقه:")
        if ok and name.strip():
            fl = self.store.add_floor(name.strip())
            self._reload_floors(select_id=fl["id"])

    def _rename_floor(self):
        fid = self._current_floor_id()
        fl = self.store.get_floor(fid) if fid else None
        if not fl:
            return
        name, ok = QInputDialog.getText(
            self, "تغییر نام طبقه", "نام جدید:", text=fl.get("name", ""))
        if ok and name.strip():
            self.store.rename_floor(fid, name.strip())
            self._reload_floors(select_id=fid)

    def _delete_floor(self):
        fid = self._current_floor_id()
        fl = self.store.get_floor(fid) if fid else None
        if not fl:
            return
        if QMessageBox.question(
                self, "حذف طبقه",
                f"طبقه‌ی «{fl.get('name')}» و همه‌ی تجهیزاتش حذف شود؟"
                ) != QMessageBox.StandardButton.Yes:
            return
        if fid in self.scenes:
            del self.scenes[fid]
        self._clear_live_persons()
        self.store.remove_floor(fid)
        self.current_floor = None
        self._reload_floors()

    # ============================ نقشه‌ی طبقه ============================
    def _import_dxf(self):
        fid = self._current_floor_id()
        if not fid:
            return
        if not ezdxf_available():
            QMessageBox.warning(
                self, "کتابخانه لازم است",
                "برای نمایش نقشه‌های اتوکد (DXF) باید کتابخانه‌ی ezdxf نصب باشد:\n\n"
                "pip install ezdxf\n\n"
                "در ویندوز داخل همان محیطی که برنامه را اجرا می‌کنید.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "انتخاب فایل DXF (خروجی اتوکد)", "",
            "AutoCAD DXF (*.dxf)")
        if not path:
            return
        # (2.0.23-beta) مهاجرت تجهیزات قدیمی «قبل» از جایگزینی فایل:
        # مختصات قدیمی با واحد صحنه‌ی نقشه‌ی قبلی ذخیره شده‌اند؛ اگر بعد از
        # ایمپورت مهاجرت شوند، با to_meter نقشه‌ی جدید ضرب می‌شوند و جای
        # تجهیزات به‌هم می‌ریزد.
        old_tm = self.scenes.get(fid, {}).get("to_meter")
        if old_tm:
            self.store.ensure_device_meters(fid, old_tm)
        self.store.import_map_file(fid, path)
        # گارد مقیاس (2.0.23-beta): اگر ابعاد واقعی نقشه نامعقول باشد،
        # واحد واقعی از کاربر پرسیده و ذخیره می‌شود.
        self._check_dxf_scale(fid)
        if fid in self.scenes:
            del self.scenes[fid]
        self._activate_floor(fid, fit=True)
        QMessageBox.information(self, "انجام شد",
                                "نقشه‌ی DXF با دقت کامل مختصات اتوکد بارگذاری شد.")

    # گزینه‌های واحد برای override دستی هنگام ایمپورت (نام فارسی، ضریب به متر)
    MAP_UNIT_CHOICES = [
        ("میلی‌متر", 0.001),
        ("سانتی‌متر", 0.01),
        ("متر", 1.0),
        ("کیلومتر", 1000.0),
        ("اینچ", 0.0254),
        ("فوت", 0.3048),
    ]

    def _check_dxf_scale(self, fid):
        """بررسی معقول‌بودن مقیاس نقشه‌ی DXF تازه ایمپورت‌شده.

        اگر بزرگ‌ترین بعد نقشه (به متر، با واحد شناسایی‌شده‌ی فایل) کمتر
        از نیم متر یا بیشتر از ۲ کیلومتر باشد، به احتمال زیاد واحد هدر
        فایل با قصد طراح یکی نیست؛ در این صورت واحد واقعی پرسیده و به‌صورت
        دستی روی طبقه ذخیره می‌شود (map_unit_override) تا قطاع دید دوربین،
        گرید و جای تجهیزات درست کار کنند.
        """
        fl = self.store.get_floor(fid)
        if not fl:
            return
        map_path, map_kind = self.store.floor_map_abs(fl)
        if not map_path or map_kind != "dxf":
            return
        try:
            info = DxfMapLoader().load(map_path)
        except Exception:
            return
        w_m = info["bounds"].width() * info["to_meter"]
        h_m = info["bounds"].height() * info["to_meter"]
        big_m = max(w_m, h_m)
        if 0.5 <= big_m <= 2000:
            # معقول است؛ override مربوط به نقشه‌ی قبلی پاک شود
            if fl.get("map_unit_override"):
                fl.pop("map_unit_override", None)
                self.store.save()
            return
        names = [n for n, _ in self.MAP_UNIT_CHOICES]
        cur_fa = info.get("units_fa") or ""
        try:
            cur_idx = names.index(cur_fa)
        except ValueError:
            cur_idx = 2
        item, ok = QInputDialog.getItem(
            self, "واحد نقشه",
            "ابعاد نقشه با واحد شناسایی‌شده‌ی فایل "
            f"(«{cur_fa}») برابر {w_m:.2f} × {h_m:.2f} متر است که برای "
            "نقشه‌ی ساختمان نامعقول به نظر می‌رسد.\n"
            "واحد واقعی نقشه را انتخاب کنید تا دید دوربین و مقیاس درست شود:",
            names, cur_idx, False)
        if ok and item:
            for n, tm in self.MAP_UNIT_CHOICES:
                if n == item:
                    fl["map_unit_override"] = {"to_meter": tm, "units_fa": n}
                    self.store.save()
                    break
        else:
            fl.pop("map_unit_override", None)
            self.store.save()

    def _import_image(self):
        fid = self._current_floor_id()
        if not fid:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "انتخاب تصویر نقشه", "",
            "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        # (2.0.23-beta) مهاجرت تجهیزات قدیمی «قبل» از جایگزینی تصویر.
        old_tm = self.scenes.get(fid, {}).get("to_meter")
        if old_tm:
            self.store.ensure_device_meters(fid, old_tm)
        self.store.import_map_file(fid, path)
        if fid in self.scenes:
            del self.scenes[fid]
        self._activate_floor(fid, fit=True)

    def _build_scene(self, floor_id):
        """ساخت (یا بازیابی از کش) صحنه‌ی یک طبقه."""
        if floor_id in self.scenes:
            return self.scenes[floor_id]
        fl = self.store.get_floor(floor_id)
        if not fl:
            return None
        map_path, map_kind = self.store.floor_map_abs(fl)
        to_meter = 1.0
        units_fa = "—"
        scene = MapScene(to_meter)
        # رفع باگ «پنل مشخصات تا خروج/ورود مجدد کار نمی‌کند»: اتصال
        # selectionChanged باید همان لحظه‌ی ساخت صحنه انجام شود. قبلاً فقط
        # در refresh() (هنگام نمایش صفحه از هدر) وصل می‌شد؛ صحنه‌هایی که
        # بعد از آن ساخته می‌شدند (تعویض طبقه، ایمپورت نقشه، طبقه‌ی جدید)
        # این اتصال را نداشتند و کلیک روی تجهیز پنل را به‌روز نمی‌کرد تا
        # کاربر یک‌بار از صفحه بیرون برود و برگردد.
        try:
            scene.selectionChanged.connect(self._refresh_prop_panel)
        except Exception:
            pass
        layers = {}
        bounds = None

        if map_path and map_kind == "dxf":
            try:
                info = DxfMapLoader().load(map_path)
            except DxfError as ex:
                QMessageBox.warning(self, "خطا در نقشه", str(ex))
                info = None
            if info:
                to_meter = info["to_meter"]
                units_fa = info["units_fa"]
                scene.to_meter = to_meter
                for lname, linfo in info["layers"].items():
                    scene.addItem(linfo["item"])
                    layers[lname] = linfo
                for t in info["texts"]:
                    scene.addItem(t)
                bounds = info["bounds"]
        elif map_path and map_kind == "image":
            from PyQt6.QtGui import QPixmap
            from PyQt6.QtWidgets import QGraphicsPixmapItem
            pix = QPixmap(map_path)
            if not pix.isNull():
                scene.addItem(QGraphicsPixmapItem(pix))
                bounds = QRectF(pix.rect())
                units_fa = "پیکسل (تصویر)"

        if bounds is None or bounds.isNull():
            bounds = QRectF(0, 0, 1000, 700)
        scene.setSceneRect(bounds)

        # override دستی واحد نقشه (گارد مقیاس 2.0.23-beta): اگر کاربر هنگام
        # ایمپورت واحد واقعی را مشخص کرده باشد، همان اعمال می‌شود.
        try:
            _ov = fl.get("map_unit_override") or {}
            _ov_tm = float(_ov.get("to_meter") or 0)
        except (TypeError, ValueError):
            _ov_tm = 0
        if _ov_tm > 0:
            to_meter = _ov_tm
            scene.to_meter = to_meter
            units_fa = str(_ov.get("units_fa") or units_fa) + " (دستی)"

        # مهاجرت یک‌باره‌ی مختصات قدیمی (واحد صحنه) به متر
        self.store.ensure_device_meters(floor_id, to_meter)

        # تجهیزات
        items = {}
        for dev in fl.get("devices", []):
            item = DeviceItem(floor_id, dev, to_meter, {
                "moved": self._on_device_moved,
                "clicked": self._on_device_clicked,
            })
            scene.addItem(item)
            items[dev.get("id")] = item

        entry = {"scene": scene, "to_meter": to_meter, "units_fa": units_fa,
                 "items": items, "bounds": bounds, "layers": layers}
        self.scenes[floor_id] = entry
        return entry

    def _activate_floor(self, floor_id, fit=False):
        if not floor_id:
            return
        self.current_floor = floor_id
        # تحلیل پوشش زنده مال طبقه‌ی قبلی بود؛ روی طبقه‌ی جدید ریست می‌شود
        # (2.0.21-beta).
        self._coverage_live = None
        self._clear_geo_tag("coverage-geo")
        if hasattr(self, "coverage_report"):
            self.coverage_report.setPlainText(
                "برای تحلیل پوشش، دکمه‌ی «📡 پوشش» را بزنید.\n"
                "بعد از آن با هر جابه‌جایی دوربین، گزارش در لحظه "
                "به‌روز می‌شود.")
        entry = self._build_scene(floor_id)
        if not entry:
            return
        self._stop_placing()
        self.view.setScene(entry["scene"])
        self.units_label.setText(f"واحد نقشه: {entry['units_fa']}")
        # رسم دوباره‌ی نشان‌های زنده‌ی اشخاص روی این طبقه (اگر صحنه تازه
        # ساخته شده باشد، نشان‌هایی که قبلاً ثبت شده‌اند اعمال می‌شوند)
        for key in list(self._live_persons.keys()):
            self._apply_live_person(key)
        # لایه‌ها
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for lname, linfo in entry["layers"].items():
            item = QListWidgetItem(lname)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, lname)
            try:
                c = linfo["color"]
                item.setForeground(c)
            except Exception:
                pass
            self.layer_list.addItem(item)
        self.layer_list.blockSignals(False)
        if fit:
            # فیت واقعی را به بعد از جانمایی ویجت موکول می‌کنیم تا دقیق باشد
            self._pending_fit = True
            self._fit_current()
        # مسیر شخص فعال را روی صحنه‌ی جدید هم بکش
        if self._path:
            self._draw_path()
        # مسیر پلاک‌خوان انتخاب‌شده را هم روی صحنه‌ی جدید بکش
        if self._lane_selected_id:
            try:
                _lane = plate_store.get_lanes().get(self._lane_selected_id) or {}
                if (_lane.get("floor_id") or "") == floor_id:
                    self._show_lane(self._lane_selected_id)
            except Exception:
                pass

    def _fit_current(self):
        sc = self.view.scene()
        if sc is not None:
            try:
                self.view.fitInView(sc.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            except Exception:
                pass

    def _on_layer_toggled(self, item):
        lname = item.data(Qt.ItemDataRole.UserRole)
        entry = self.scenes.get(self.current_floor)
        if not entry:
            return
        linfo = entry["layers"].get(lname)
        if linfo:
            linfo["item"].setVisible(
                item.checkState() == Qt.CheckState.Checked)

    def _on_mouse_moved(self, scene_pos):
        entry = self.scenes.get(self.current_floor)
        tm = entry["to_meter"] if entry else 1.0
        self.coord_label.setText(
            f"X: {scene_pos.x() * tm:.2f} m ، Y: {-scene_pos.y() * tm:.2f} m")

    # ============================ جای‌گذاری تجهیز ============================
    def _start_placing(self, kind):
        if not self.current_floor:
            QMessageBox.information(self, "طبقه‌ای انتخاب نشده",
                                    "اول یک طبقه انتخاب کنید.")
            return
        self._placing_kind = kind
        self.view.placing = True
        self.view.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        info = DEVICE_KINDS[kind]
        self.place_hint.setText(
            f"روی نقشه کلیک کنید تا «{info['fa']}» گذاشته شود (Esc = انصراف)")

    def _stop_placing(self):
        self._placing_kind = None
        self.view.placing = False
        self.view.unsetCursor()
        self.place_hint.setText("")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and (self._placing_kind or self._lane_mode):
            self._stop_placing()
            self._cancel_lane_mode()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.view and event.type() == QEvent.Type.Resize:
            self._position_lane_bar()
        return super().eventFilter(obj, event)

    def _sync_camera_floor(self, cam_id, floor_id):
        """سینک طبقه‌ی دوربین با نقشه‌ی ساختمان (کنترل تردد طبقاتی).
        floor_id خالی یعنی پاک کردن — فقط اگر مقدار فعلی همان طبقه باشد."""
        if not cam_id or self.camera_store is None:
            return
        try:
            if floor_id:
                self.camera_store.update_camera(cam_id, floor_id=floor_id)
            else:
                cur = self.camera_store.get_camera_floor_id(cam_id)
                if cur == self.current_floor:
                    self.camera_store.update_camera(cam_id, floor_id="")
        except Exception:
            pass

    def _on_place_clicked(self, scene_pos):
        kind = self._placing_kind
        if not kind or not self.current_floor:
            return
        dlg = DeviceConfigDialog(kind, self.camera_store, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["name"]:
            vals["name"] = DEVICE_KINDS[kind]["fa"]
        # ذخیره‌سازی متری (2.0.23-beta): مختصات صحنه به متر تبدیل می‌شود
        # تا با تعویض نقشه (واحد متفاوت) جای تجهیز به‌هم نریزد.
        _entry0 = self.scenes.get(self.current_floor)
        _tm0 = (_entry0 or {}).get("to_meter") or 1.0
        dev = self.store.add_device(
            self.current_floor, kind, vals["name"],
            scene_pos.x() * _tm0, scene_pos.y() * _tm0,
            ref_id=vals["ref_id"], angle=vals["angle"], fov=vals["fov"],
            view_distance=vals["view_distance"])
        entry = self.scenes.get(self.current_floor)
        if entry and dev:
            item = DeviceItem(self.current_floor, dev, entry["to_meter"], {
                "moved": self._on_device_moved,
                "clicked": self._on_device_clicked,
            })
            entry["scene"].addItem(item)
            entry["items"][dev["id"]] = item
            item.setSelected(True)
        # سینک طبقه‌ی دوربین (کنترل تردد طبقاتی)
        if dev and dev.get("kind") == "camera" and dev.get("ref_id"):
            self._sync_camera_floor(dev["ref_id"], self.current_floor)
        # در حالت جای‌گذاری می‌مانیم تا چند تجهیز پشت سر هم بگذاریم
        self._update_zoom_label()

    def _on_device_moved(self, floor_id, dev_id, x, y):
        # x و y به واحد صحنه می‌آیند؛ ذخیره‌سازی متری است (2.0.23-beta).
        tm = (self.scenes.get(floor_id) or {}).get("to_meter") or 1.0
        self.store.update_device(floor_id, dev_id, x=float(x) * tm,
                                 y=float(y) * tm, pos_unit="m")
        # گزارش زنده‌ی پوشش: با جابه‌جایی دوربین در لحظه به‌روز می‌شود
        # (2.0.21-beta به دستور کاربر).
        self._refresh_coverage_live()

    def _on_device_clicked(self, item):
        dev = item.device
        if dev.get("kind") == "camera" and dev.get("ref_id"):
            cam = self._find_camera(dev.get("ref_id"))
            if cam and callable(self.on_camera_click):
                self.on_camera_click(cam)
                return
            elif cam is None:
                QMessageBox.information(
                    self, "دوربین یافت نشد",
                    "این دوربین در لیست دوربین‌ها نیست (شاید حذف شده). "
                    "از پنل «مشخصات تجهیز» اتصال را اصلاح کنید.")
                return
        # برای بقیه‌ی تجهیزات فقط انتخاب می‌ماند (پنل مشخصات)

    def _find_camera(self, cam_id):
        try:
            for cam in self.camera_store.standalone_cameras():
                if str(cam.get("id")) == str(cam_id):
                    return cam
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    if str(cam.get("id")) == str(cam_id):
                        return cam
        except Exception:
            pass
        return None

    # ============================ پنل مشخصات ============================
    def _refresh_prop_panel(self):
        items = self.view.scene().selectedItems() if self.view.scene() else []
        dev_item = next((i for i in items if isinstance(i, DeviceItem)), None)
        self.prop_group.setEnabled(dev_item is not None)
        if not dev_item:
            return
        dev = dev_item.device
        self.prop_name.blockSignals(True)
        self.prop_name.setText(dev.get("name", ""))
        self.prop_name.blockSignals(False)
        # کامبو اتصال
        self.prop_link.blockSignals(True)
        self.prop_link.clear()
        self.prop_link.addItem("— بدون اتصال —", "")
        kind = dev.get("kind")
        if kind == "camera":
            for cam_id, label in DeviceConfigDialog._camera_entries(self.camera_store):
                self.prop_link.addItem(f"🎥 {label}", cam_id)
        elif kind == "nvr":
            try:
                for nvr in self.camera_store.nvrs:
                    self.prop_link.addItem(
                        f"🖥 {nvr.get('name') or nvr.get('ip')}",
                        nvr.get("id"))
            except Exception:
                pass
        idx = self.prop_link.findData(dev.get("ref_id") or "")
        self.prop_link.setCurrentIndex(idx if idx >= 0 else 0)
        self.prop_link.blockSignals(False)
        # زاویه/پهنا
        for w, key in ((self.prop_angle, "angle"), (self.prop_angle_num, "angle")):
            w.blockSignals(True)
            w.setValue(int(dev.get(key, 0)))
            w.blockSignals(False)
        self.prop_fov.blockSignals(True)
        self.prop_fov.setValue(int(dev.get("fov", 90)))
        self.prop_fov.blockSignals(False)
        self.prop_range.blockSignals(True)
        self.prop_range.setValue(float(dev.get("view_distance", 8.0)))
        self.prop_range.blockSignals(False)
        self.prop_fov.setEnabled(kind == "camera")
        self.prop_angle.setEnabled(kind == "camera")
        self.prop_angle_num.setEnabled(kind == "camera")
        self.prop_range.setEnabled(kind == "camera")

    def _selected_device_item(self):
        if not self.view.scene():
            return None
        for i in self.view.scene().selectedItems():
            if isinstance(i, DeviceItem):
                return i
        return None

    def _prop_apply(self):
        item = self._selected_device_item()
        if not item:
            return
        dev = item.device
        old_ref = dev.get("ref_id") or ""
        name = self.prop_name.text().strip() or dev.get("name", "")
        ref_id = self.prop_link.currentData() or ""
        angle = float(self.prop_angle.value())
        fov = float(self.prop_fov.value())
        view_distance = float(self.prop_range.value())
        self.store.update_device(self.current_floor, dev["id"],
                                 name=name, ref_id=ref_id,
                                 angle=angle, fov=fov,
                                 view_distance=view_distance)
        dev["name"] = name
        dev["ref_id"] = ref_id
        dev["angle"] = angle
        dev["fov"] = fov
        dev["view_distance"] = view_distance
        item.set_name(name)
        item.refresh()
        # سینک طبقه‌ی دوربین (کنترل تردد طبقاتی)
        if dev.get("kind") == "camera":
            if old_ref and old_ref != ref_id:
                self._sync_camera_floor(old_ref, "")
            if ref_id:
                self._sync_camera_floor(ref_id, self.current_floor)
        # گزارش زنده‌ی پوشش: با تغییر زاویه/پهنا/برد دوربین در لحظه به‌روز
        # می‌شود (2.0.21-beta به دستور کاربر).
        self._refresh_coverage_live()

    def _prop_angle_changed(self, value):
        # سینک اسلایدر و اسپین‌باکس + به‌روزرسانی زنده‌ی قطاع دید.
        # ذخیره روی دیسک: برای اسلایدر هنگام رها کردن (sliderReleased ->
        # _prop_apply) و برای همه‌ی حالت‌ها (اسلایدر/اسپین‌باکس) ۷۰۰ms بعد
        # از آخرین تغییر، تا با هر تیک درگ فایل JSON بازنویسی نشود ولی
        # تغییر اسپین‌باکس هم گم نشود (رفع باگ «زاویه ذخیره نمی‌شود»).
        for w in (self.prop_angle, self.prop_angle_num):
            w.blockSignals(True)
            w.setValue(int(value))
            w.blockSignals(False)
        item = self._selected_device_item()
        if not item:
            return
        item.device["angle"] = float(value)
        item.refresh()
        try:
            self._prop_save_timer.start()
        except Exception:
            pass

    def _prop_range_changed(self, value):
        # تغییر «فاصله دید»: قطاع دید زنده روی نقشه بزرگ/کوچک می‌شود و
        # ذخیره روی دیسک با همان تایمر مشترک ۷۰۰ms بعد انجام می‌شود.
        item = self._selected_device_item()
        if not item:
            return
        item.device["view_distance"] = float(value)
        item.refresh()
        try:
            self._prop_save_timer.start()
        except Exception:
            pass

    def _delete_selected_device(self):
        item = self._selected_device_item()
        if not item:
            return
        if QMessageBox.question(
                self, "حذف تجهیز",
                f"«{item.device.get('name')}» از روی نقشه حذف شود؟"
                ) != QMessageBox.StandardButton.Yes:
            return
        self.store.remove_device(self.current_floor, item.device["id"])
        # اگر تجهیز دوربین لینک‌شده بود، سینک طبقه را پاک کن
        _dev = item.device
        if _dev.get("kind") == "camera" and _dev.get("ref_id"):
            self._sync_camera_floor(_dev["ref_id"], "")
        entry = self.scenes.get(self.current_floor)
        if entry:
            entry["scene"].removeItem(item)
            entry["items"].pop(item.device["id"], None)
        self._refresh_prop_panel()

    def _update_zoom_label(self):
        try:
            z = self.view.transform().m11() * 100
            self.zoom_label.setText(f"زوم: {z:.0f}٪")
        except Exception:
            pass

    # ============================ مسیر شخص ============================
    def _pick_person_path(self):
        dlg = PersonPickDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        pid = dlg.selected_id()
        if pid:
            self.show_person_path(pid)

    def _stops_for_person(self, person_id):
        """ساخت لیست توقف‌های یک شخص: هر «حضور» + نگاشت به دوربینِ روی نقشه.

        خروجی: [{sighting, cam, placements:[(floor, dev), ...]}, ...] به ترتیب
        زمان. «حضور»های باز (در لحظه) هم لحاظ می‌شوند تا مسیر، زنده ادامه
        پیدا کند.
        """
        sightings = person_store.get_path(person_id)
        cam_by_name = {}
        try:
            for cam in self.camera_store.standalone_cameras():
                cam_by_name[cam.get("name")] = cam
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    cam_by_name[cam.get("name")] = cam
        except Exception:
            pass
        stops = []
        for s in sightings:
            cam = cam_by_name.get(s.get("camera_name"))
            placements = self.store.devices_by_camera(cam.get("id")) if cam else []
            stops.append({"sighting": s, "cam": cam, "placements": placements})
        return stops

    def _map_devices_ready(self):
        """آیا نقشه و دوربین برای نمایش مسیر آماده‌اند؟
        خروجی: (has_map, has_cam) — نقشه یعنی حداقل یک طبقه فایل DXF/تصویر
        داشته باشد، دوربین یعنی حداقل یک تجهیز camera روی نقشه گذاشته شده
        باشد."""
        has_map = False
        has_cam = False
        try:
            for fl in self.store.floors():
                try:
                    mp, kind = self.store.floor_map_abs(fl)
                except Exception:
                    mp, kind = None, None
                if mp and kind in ("dxf", "image"):
                    has_map = True
                for dev in fl.get("devices", []) or []:
                    if dev.get("kind") == "camera":
                        has_cam = True
        except Exception:
            pass
        return has_map, has_cam

    def _check_map_ready(self):
        """گیت نمایش مسیر: فقط وقتی نقشه و دوربین‌ها اضافه شده باشند مسیر
        رسم می‌شود؛ در غیر این صورت راهنمایی فارسی نمایش داده می‌شود."""
        has_map, has_cam = self._map_devices_ready()
        if has_map and has_cam:
            return True
        missing = []
        if not has_map:
            missing.append("نقشه‌ی طبقه (فایل DXF اتوکد یا تصویر)")
        if not has_cam:
            missing.append("دوربین روی نقشه (از جعبه‌ابزار، 🎥 دوربین)")
        QMessageBox.information(
            self, "نقشه آماده نیست",
            "برای نمایش مسیر حرکت روی نقشه، اول این‌ها را در همین صفحه‌ی "
            "«نقشه ساختمان» اضافه کنید:\n• " + "\n• ".join(missing))
        return False

    def show_person_path(self, person_id):
        """نمایش مسیر تردد یک شخص روی نقشه‌ها (قابل فراخوانی از بیرون)."""
        if not self._check_map_ready():
            return
        persons = {p.get("id"): p for p in person_store.get_persons()}
        person = persons.get(person_id)
        if not person:
            QMessageBox.information(self, "شخص یافت نشد",
                                    "این شخص در دیتابیس ردیابی نیست.")
            return
        sightings = person_store.get_path(person_id)
        if not sightings:
            QMessageBox.information(self, "مسیری نیست",
                                    "برای این شخص هنوز ترددی ثبت نشده است.")
            return
        self._path = {"persons": [self._person_path_entry(person)]}
        self.timeline_group.setEnabled(True)
        self.path_close_btn.setEnabled(True)
        self._draw_path()
        self._fill_timeline()
        # رفتن به طبقه‌ی اولین توقفِ دارای نقشه
        for st in self._path["persons"][0]["stops"]:
            if st["placements"]:
                fid = st["placements"][0][0].get("id")
                self._select_floor(fid)
                break

    def show_all_person_paths(self):
        """نمایش مسیر تردد «همه‌ی» اشخاص روی نقشه — هر شخص با خط‌چینِ رنگ
        مخصوص خودش (از دکمه‌ی «🗺 نمایش روی نقشه» وقتی «همه‌ی اشخاص»
        انتخاب شده باشد)."""
        if not self._check_map_ready():
            return
        entries = []
        for person in person_store.get_persons():
            if not person_store.get_path(person.get("id")):
                continue
            entries.append(self._person_path_entry(person))
        if not entries:
            QMessageBox.information(self, "مسیری نیست",
                                    "هنوز برای هیچ شخصی ترددی ثبت نشده است.")
            return
        self._path = {"persons": entries}
        self.timeline_group.setEnabled(True)
        self.path_close_btn.setEnabled(True)
        self._draw_path()
        self._fill_timeline()
        for entry in entries:
            for st in entry["stops"]:
                if st["placements"]:
                    self._select_floor(st["placements"][0][0].get("id"))
                    return

    def _person_path_entry(self, person):
        """یک ورودی مسیر برای _path: شخص + توقف‌ها + رنگ مخصوصش."""
        return {"person": person,
                "stops": self._stops_for_person(person.get("id")),
                "code": person.get("code", ""),
                "color": person_color(person.get("id"))}

    def _select_floor(self, floor_id):
        for i in range(self.floor_list.count()):
            item = self.floor_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == floor_id:
                self.floor_list.setCurrentItem(item)
                return

    def _draw_path(self):
        if not self._path:
            return
        # پاک‌سازی مسیر قبلی از همه‌ی صحنه‌ها
        for entry in self.scenes.values():
            for it in list(entry["scene"].items()):
                try:
                    if it.data(0) == "person-path":
                        entry["scene"].removeItem(it)
                except Exception:
                    pass
        self._stop_play()
        # هر شخص با خط‌چینِ رنگ مخصوص خودش رسم می‌شود
        for entry_p in self._path.get("persons", []):
            color = entry_p.get("color") or QColor("#f472b6")
            dark = QColor.fromHsv(color.hue(), 160, 70)
            stops = entry_p["stops"]
            # شماره‌گذاری به ترتیب زمان
            per_floor_points = {}  # floor_id -> [(x, y, seq, sighting)]
            seq = 0
            for st in stops:
                seq += 1
                s = st["sighting"]
                for fl, dev in st["placements"]:
                    fid = fl.get("id")
                    # مهاجرت مختصات قدیمی به متر، بعد تبدیل به واحد صحنه
                    # (2.0.23-beta: x/y ذخیره‌شده متری است)
                    _e0 = self._build_scene(fid)
                    _tm0 = (_e0 or {}).get("to_meter") or 1.0
                    _xs, _ys = device_scene_xy(dev, _tm0)
                    per_floor_points.setdefault(fid, []).append(
                        (_xs, _ys, seq, s, dev))
            # رسم روی هر طبقه
            for fid, pts in per_floor_points.items():
                entry = self._build_scene(fid)
                if not entry:
                    continue
                sc = entry["scene"]
                # خطوط اتصال متوالیِ همان طبقه — خط‌چین به رنگ شخص
                for a, b in zip(pts, pts[1:]):
                    line = QGraphicsLineItem(a[0], a[1], b[0], b[1])
                    pen = QPen(color, 0)
                    pen.setCosmetic(True)
                    pen.setStyle(Qt.PenStyle.DashLine)
                    pen.setDashOffset(0)
                    line.setPen(pen)
                    line.setZValue(20)
                    line.setData(0, "person-path")
                    sc.addItem(line)
                # نشان‌های شماره‌دار
                for (x, y, n, s, _dev) in pts:
                    badge = QGraphicsEllipseItem(-13, -13, 26, 26)
                    badge.setPos(x, y)
                    badge.setPen(QPen(color, 2))
                    badge.setBrush(QBrush(dark))
                    badge.setFlag(
                        QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
                    badge.setZValue(21)
                    badge.setData(0, "person-path")
                    sc.addItem(badge)
                    num = QGraphicsSimpleTextItem(str(n))
                    num.setFont(QFont("", 10, QFont.Weight.Bold))
                    num.setBrush(QBrush(QColor("white")))
                    num.setPos(x - 6, y - 10)
                    num.setFlag(
                        QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
                    num.setZValue(22)
                    num.setData(0, "person-path")
                    sc.addItem(num)
                    tlabel = QGraphicsSimpleTextItem(
                        f"{s.get('enter_time', '')}")
                    tlabel.setFont(QFont("", 8))
                    tlabel.setBrush(QBrush(color))
                    tlabel.setPos(x - 26, y - 34)
                    tlabel.setFlag(
                        QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
                    tlabel.setZValue(22)
                    tlabel.setData(0, "person-path")
                    sc.addItem(tlabel)
        # اگر صحنه‌ای تازه ساخته شد، نشان‌های زنده را هم روی آن بنشان
        for key in list(self._live_persons.keys()):
            self._apply_live_person(key)

    def _fill_timeline(self):
        self.timeline_list.clear()
        unmapped = 0
        total = 0
        persons = self._path.get("persons", []) if self._path else []
        multi = len(persons) > 1
        for pidx, entry_p in enumerate(persons):
            color = entry_p.get("color") or QColor("#f472b6")
            code = entry_p.get("code", "")
            if multi:
                # سرفصل رنگی هر شخص = راهنمای رنگ‌ها
                head = QListWidgetItem(f"⬤ {code} — مسیر")
                head.setForeground(QBrush(color))
                f = head.font()
                f.setBold(True)
                head.setFont(f)
                head.setData(Qt.ItemDataRole.UserRole, None)
                self.timeline_list.addItem(head)
            for i, st in enumerate(entry_p["stops"], 1):
                s = st["sighting"]
                cam_name = s.get("camera_name") or "؟"
                when = f"{s.get('enter_j', '')} {s.get('enter_time', '')}"
                if not st["placements"]:
                    unmapped += 1
                total += 1
                item = QListWidgetItem(f"{i}. 🎥 {cam_name}\n    🕐 {when}")
                item.setData(Qt.ItemDataRole.UserRole, (pidx, i - 1))
                self.timeline_list.addItem(item)
        names = "، ".join(e.get("code", "") for e in persons)
        self.unmapped_label.setText(
            f"{total} توقف در مسیر «{names}»"
            + (f" — {unmapped} توقف خارج از نقشه است (دوربین روی نقشه گذاشته نشده)"
               if unmapped else ""))

    def _on_timeline_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        pidx, idx = data
        try:
            st = self._path["persons"][pidx]["stops"][idx]
        except (IndexError, KeyError, TypeError):
            return
        if not st["placements"]:
            QMessageBox.information(
                self, "خارج از نقشه",
                "دوربین این توقف روی هیچ نقشه‌ای گذاشته نشده است.")
            return
        fl, dev = st["placements"][0]
        self._select_floor(fl.get("id"))
        entry = self.scenes.get(fl.get("id"))
        if entry:
            _cx, _cy = device_scene_xy(dev, entry.get("to_meter"))
            self.view.centerOn(_cx, _cy)

    def _clear_path(self):
        self._path = None
        self._stop_play()
        for entry in self.scenes.values():
            for it in list(entry["scene"].items()):
                try:
                    if it.data(0) == "person-path":
                        entry["scene"].removeItem(it)
                except Exception:
                    pass
        self.timeline_list.clear()
        self.unmapped_label.setText("")
        self.timeline_group.setEnabled(False)
        self.path_close_btn.setEnabled(False)

    # ============================ اشخاص زنده روی نقشه ============================
    def set_live_person(self, cam_id, person_id, code, camera_name, present,
                        kind="live"):
        """نمایش/حذف زنده‌ی موقعیت فعلی یک شخص روی نقشه.

        در همان لحظه‌ای که دوربینی یک شخص را شناسایی می‌کند (present=True)،
        یک نشان چشمک‌زن با کد شخص روی همان دوربینِ نقشه می‌نشیند؛ با
        تمام شدن رد (present=False) نشان برداشته می‌شود. حتماً از ترد اصلی
        صدا زده شود.

        kind: ‏"live" نشان سبز تأییدشده؛ "candidate" نشان زرد «در حال
        شناسایی» که با اولین نشانه‌های رد (پیش از تأیید نهایی) نمایش داده
        می‌شود تا ردیابی حس «در لحظه» داشته باشد.
        """
        key = (str(cam_id), str(person_id))
        self._remove_live_person(key)
        if not present:
            return
        try:
            placements = self.store.devices_by_camera(cam_id)
        except Exception:
            placements = []
        if not placements:
            return
        self._live_persons[key] = {
            # dev به‌صورت رفرنس نگه داشته می‌شود تا مهاجرت متریِ بعدی
            # (ensure_device_meters) روی همین آبجکت اعمال شود.
            "placements": [(fl.get("id"), dev) for fl, dev in placements],
            "items": [], "rings": [],
            "code": code, "camera_name": camera_name,
            "kind": kind if kind == "candidate" else "live",
        }
        self._apply_live_person(key)

    def _apply_live_person(self, key):
        """رسم نشان‌های یک شخص زنده روی صحنه‌های ساخته‌شده (بدون ساخت صحنه‌ی
        جدید تا ترد اصلی هنگام تشخیص، درگیر پارس DXF نشود)."""
        info = self._live_persons.get(key)
        if not info:
            return
        for sc, it in info["items"]:
            try:
                sc.removeItem(it)
            except Exception:
                pass
        info["items"] = []
        info["rings"] = []
        code = info["code"]
        cand = info.get("kind") == "candidate"
        main_c = "#eab308" if cand else "#22c55e"   # زرد برای کاندیدا، سبز برای تأییدشده
        fill_c = QColor(234, 179, 8, 40) if cand else QColor(34, 197, 94, 40)
        text_c = "#fef9c3" if cand else "#bbf7d0"
        emoji = "🟡" if cand else "🟢"
        for fid, dev in info["placements"]:
            entry = self.scenes.get(fid)
            if not entry:
                continue
            tm = entry.get("to_meter") or 1.0
            self.store.ensure_device_meters(fid, tm)
            x, y = device_scene_xy(dev, tm)
            sc = entry["scene"]
            ring = QGraphicsEllipseItem(-20, -20, 40, 40)
            ring.setPos(x, y)
            ring.setPen(QPen(QColor(main_c), 3))
            ring.setBrush(QBrush(fill_c))
            ring.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            ring.setZValue(25)
            ring.setData(0, "person-live")
            sc.addItem(ring)
            dot = QGraphicsEllipseItem(-8, -8, 16, 16)
            dot.setPos(x, y)
            dot.setPen(QPen(QColor("#ffffff"), 2))
            dot.setBrush(QBrush(QColor(main_c)))
            dot.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            dot.setZValue(26)
            dot.setData(0, "person-live")
            sc.addItem(dot)
            lab = QGraphicsSimpleTextItem(f"{emoji} {code}")
            f = QFont()
            f.setPointSize(10)
            f.setBold(True)
            lab.setFont(f)
            lab.setBrush(QBrush(QColor(text_c)))
            lab.setPos(x + 24, y - 16)
            lab.setFlag(
                QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            lab.setZValue(27)
            lab.setData(0, "person-live")
            sc.addItem(lab)
            info["items"].extend([(sc, ring), (sc, dot), (sc, lab)])
            info["rings"].append(ring)
        if info["items"] and not self._live_timer.isActive():
            self._live_phase = True
            self._live_timer.start(650)

    def _remove_live_person(self, key):
        info = self._live_persons.pop(key, None)
        if not info:
            return
        for sc, it in info["items"]:
            try:
                sc.removeItem(it)
            except Exception:
                pass
        if not self._live_persons:
            try:
                self._live_timer.stop()
            except Exception:
                pass

    def _clear_live_persons(self):
        for key in list(self._live_persons.keys()):
            self._remove_live_person(key)

    def _live_pulse_tick(self):
        """چشمک‌زدن نشان‌های زنده (حس «در لحظه»)."""
        self._live_phase = not self._live_phase
        op = 1.0 if self._live_phase else 0.35
        for info in self._live_persons.values():
            for r in info["rings"]:
                try:
                    r.setOpacity(op)
                except Exception:
                    pass

    def append_live_stop(self, person_id):
        """افزودن زنده‌ی «حضور» تازه‌ثبت‌شده به مسیر نمایشی.

        اگر مسیر همین شخص همین حالا روی نقشه نمایش داده می‌شود، توقف جدید
        (که start_sighting همان لحظه در دیتابیس ثبت کرده) به انتهای مسیر
        اضافه و نقشه/خط زمانی بی‌درنگ به‌روز می‌شوند.
        """
        if not self._path:
            return
        persons = self._path.get("persons", [])
        hit = None
        for entry_p in persons:
            if str((entry_p.get("person") or {}).get("id")) == str(person_id):
                hit = entry_p
                break
        if not hit:
            return
        try:
            hit["stops"] = self._stops_for_person(person_id)
            self._draw_path()
            self._fill_timeline()
        except Exception:
            pass

    # -- پخش متحرک مسیر --
    def _toggle_play(self):
        if self._play_timer.isActive():
            self._stop_play()
            self.play_btn.setText("▶ پخش مسیر")
            return
        if not self._path:
            return
        # ساخت سگمنت‌های همان‌طبقه به ترتیب — در حالت چندنفره، مسیر
        # نفر اول پخش می‌شود.
        persons = self._path.get("persons", [])
        stops = persons[0]["stops"] if persons else []
        seq_pts = []
        n = 0
        for st in stops:
            n += 1
            for fl, dev in st["placements"]:
                _fid = fl.get("id")
                _e = self._build_scene(_fid)
                _tm = (_e or {}).get("to_meter") or 1.0
                _xs, _ys = device_scene_xy(dev, _tm)
                seq_pts.append((_fid, _xs, _ys))
        segs = []
        for a, b in zip(seq_pts, seq_pts[1:]):
            if a[0] == b[0]:
                segs.append((a[0], (a[1], a[2]), (b[1], b[2])))
        if not segs:
            QMessageBox.information(self, "مسیر قابل پخش نیست",
                                    "توقف‌های روی نقشه در یک طبقه‌ی مشترک نیستند.")
            return
        dot = QGraphicsEllipseItem(-8, -8, 16, 16)
        dot.setBrush(QBrush(persons[0].get("color") or QColor("#f472b6")))
        dot.setPen(QPen(QColor("white"), 2))
        dot.setFlag(
            QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
        dot.setZValue(30)
        dot.setData(0, "person-path")
        self._play = {"segs": segs, "i": 0, "t": 0.0, "dot": dot}
        sc = self.scenes[segs[0][0]]["scene"]
        sc.addItem(dot)
        self._select_floor(segs[0][0])
        self._play_timer.start(40)
        self.play_btn.setText("⏸ توقف")

    def _play_tick(self):
        pl = getattr(self, "_play", None)
        if not pl:
            self._stop_play()
            return
        pl["t"] += 0.035
        if pl["t"] >= 1.0:
            pl["i"] += 1
            pl["t"] = 0.0
            if pl["i"] >= len(pl["segs"]):
                self._stop_play()
                self.play_btn.setText("▶ پخش مسیر")
                return
            fid = pl["segs"][pl["i"]][0]
            self._select_floor(fid)
            sc = self.scenes[fid]["scene"]
            if pl["dot"].scene() is not sc:
                if pl["dot"].scene():
                    pl["dot"].scene().removeItem(pl["dot"])
                sc.addItem(pl["dot"])
        fid, p1, p2 = pl["segs"][pl["i"]]
        t = pl["t"]
        x = p1[0] + (p2[0] - p1[0]) * t
        y = p1[1] + (p2[1] - p1[1]) * t
        pl["dot"].setPos(x, y)
        self.view.centerOn(x, y)

    def _stop_play(self):
        try:
            self._play_timer.stop()
        except Exception:
            pass
        pl = getattr(self, "_play", None)
        if pl and pl.get("dot") and pl["dot"].scene():
            try:
                pl["dot"].scene().removeItem(pl["dot"])
            except Exception:
                pass
        self._play = None
        try:
            self.play_btn.setText("▶ پخش مسیر")
        except Exception:
            pass

    # ============================ مسیرهای پلاک‌خوان روی نقشه (2.0.17-beta) ==
    LANE_RULES_TEXT = (
        "قوانین رسم و اتصال مسیر:\n"
        "۱) حداقل ۲ نقطه روی نقشه کلیک کنید؛ جهت رسم (نقطه‌ی اول ← آخر) = جهت «رفت» مسیر.\n"
        "۲) فقط دوربین‌هایی که حداکثر ۵ متر از خط مسیر فاصله دارند قابل اتصال‌اند.\n"
        "۳) دوربین باید نقش پلاکی (ورود/خروج) داشته باشد.\n"
        "۴) هر دوربین فقط عضو یک مسیر است؛ اتصال به مسیر جدید، اتصال قبلی را قطع می‌کند.\n"
        "۵) ترتیب دوربین‌ها از ابتدای مسیر محاسبه و برای تشخیص «حرکت معکوس» استفاده می‌شود.\n"
        "\nقوانین موتور تردد:\n"
        "الف) خروج بدون ورود ثبت‌شده ← تخلف\n"
        "ب) ورود مجدد بدون خروج قبلی ← تخلف (بدون اغماض)\n"
        "ج) تردد خلاف جهت مجاز مسیر ← تخلف\n"
        "د) حرکت معکوس در مسیر (برعکس ترتیب دوربین‌ها) ← تخلف"
    )

    def _show_lane_rules(self):
        QMessageBox.information(self, "قوانین مسیر", self.LANE_RULES_TEXT)

    # -- شروع/لغو حالت رسم --
    def _start_lane_draw(self):
        if not self.current_floor:
            QMessageBox.information(self, "طبقه‌ای انتخاب نشده",
                                    "اول یک طبقه انتخاب کنید.")
            return
        if self.current_floor not in self.scenes:
            return
        self._stop_placing()
        self._cancel_lane_mode(silent=True)
        self._lane_mode = "draw"
        self._lane_points = []
        self.view.lane_drawing = True
        self.view.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self._update_lane_bar()

    def _cancel_lane_mode(self, silent=False):
        if not self._lane_mode and not self.view.lane_drawing:
            return
        self._lane_mode = None
        self._lane_points = []
        self.view.lane_drawing = False
        self.view.unsetCursor()
        for entry in self.scenes.values():
            for it in list(entry["scene"].items()):
                try:
                    if it.data(0) in ("lane-draw", "lane-pick"):
                        entry["scene"].removeItem(it)
                except Exception:
                    pass
        self._lane_pick = {}
        self._lane_pick_rings = {}
        try:
            self._lane_bar.hide()
        except Exception:
            pass

    # -- نوار شناور تأیید/لغو --
    def _position_lane_bar(self):
        try:
            if not self._lane_bar.isVisible():
                return
            self._lane_bar.adjustSize()
            bw, bh = self._lane_bar.width(), self._lane_bar.height()
            x = self.view.x() + max(0, (self.view.width() - bw) // 2)
            y = self.view.y() + max(0, self.view.height() - bh - 14)
            self._lane_bar.move(x, y)
            self._lane_bar.raise_()
        except Exception:
            pass

    def _update_lane_bar(self):
        if self._lane_mode == "draw":
            n = len(self._lane_points)
            self._lane_bar_label.setText(
                f"🖊 نقطه‌ی {n + 1} — روی نقشه کلیک کنید (حداقل ۲ نقطه)")
            self._lane_bar_confirm.setText("✅ تأیید مسیر")
            self._lane_bar_confirm.setEnabled(n >= 2)
        elif self._lane_mode == "pick":
            n = len(self._lane_pick)
            self._lane_bar_label.setText(
                f"🎥 {n} دوربین انتخاب شد — روی دوربین‌های روی خط مسیر کلیک کنید")
            self._lane_bar_confirm.setText("💾 ذخیره مسیر")
            self._lane_bar_confirm.setEnabled(n >= 1)
        else:
            return
        self._lane_bar.show()
        self._position_lane_bar()

    def _on_lane_bar_confirm(self):
        if self._lane_mode == "draw":
            self._confirm_lane_points()
        elif self._lane_mode == "pick":
            self._save_lane()

    # -- کلیک‌های رسم/انتخاب --
    def _on_lane_click(self, scene_pos):
        if self._lane_mode == "draw":
            if (self.current_floor or "") not in self.scenes:
                return
            self._lane_points.append((scene_pos.x(), scene_pos.y()))
            self._redraw_lane_preview()
            self._update_lane_bar()
        elif self._lane_mode == "pick":
            self._toggle_pick_camera(scene_pos)

    def _redraw_lane_preview(self):
        entry = self.scenes.get(self.current_floor)
        if not entry:
            return
        sc = entry["scene"]
        for it in list(sc.items()):
            try:
                if it.data(0) == "lane-draw":
                    sc.removeItem(it)
            except Exception:
                pass
        pts = self._lane_points
        if len(pts) >= 2:
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            for x, y in pts[1:]:
                path.lineTo(x, y)
            line = QGraphicsPathItem(path)
            pen = QPen(QColor("#fbbf24"), 0)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            line.setPen(pen)
            line.setZValue(23)
            line.setData(0, "lane-draw")
            sc.addItem(line)
        for i, (x, y) in enumerate(pts, 1):
            badge = QGraphicsEllipseItem(-13, -13, 26, 26)
            badge.setPos(x, y)
            badge.setPen(QPen(QColor("#fbbf24"), 2))
            badge.setBrush(QBrush(QColor("#451a03")))
            badge.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            badge.setZValue(24)
            badge.setData(0, "lane-draw")
            sc.addItem(badge)
            num = QGraphicsSimpleTextItem(str(i))
            f = QFont()
            f.setBold(True)
            f.setPointSize(10)
            num.setFont(f)
            num.setBrush(QBrush(QColor("#fde68a")))
            num.setPos(x - 5, y - 10)
            num.setFlag(
                QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            num.setZValue(25)
            num.setData(0, "lane-draw")
            sc.addItem(num)

    def _confirm_lane_points(self):
        ok, msg = validate_lane_points(self._lane_points)
        if not ok:
            QMessageBox.information(self, "مسیر ناقص", msg)
            return
        self._lane_mode = "pick"
        self._update_lane_bar()

    # -- انتخاب دوربین‌های روی مسیر --
    def _device_at(self, scene_pos):
        entry = self.scenes.get(self.current_floor)
        if not entry:
            return None
        try:
            scale = self.view.transform().m11()
        except Exception:
            scale = 1.0
        tol = 30.0 / max(scale, 0.05)
        best, best_d = None, None
        for item in entry["items"].values():
            try:
                if not isinstance(item, DeviceItem):
                    continue
                if item.device.get("kind") != "camera":
                    continue
                p = item.pos()
                d = math.hypot(p.x() - scene_pos.x(), p.y() - scene_pos.y())
                if d <= tol and (best_d is None or d < best_d):
                    best, best_d = item, d
            except Exception:
                continue
        return best

    def _toggle_pick_camera(self, scene_pos):
        item = self._device_at(scene_pos)
        if item is None:
            return
        dev = item.device
        dev_id = dev.get("id")
        ref_id = dev.get("ref_id") or ""
        name = dev.get("name") or "دوربین"
        if not ref_id:
            QMessageBox.information(
                self, "دوربین متصل نیست",
                f"«{name}» به هیچ دوربینی وصل نیست؛ اول از پنل «مشخصات تجهیز» اتصال را بزنید.")
            return
        cam = self._find_camera(ref_id)
        if cam is None:
            QMessageBox.information(
                self, "دوربین یافت نشد",
                "این دوربین در لیست دوربین‌ها نیست (شاید حذف شده).")
            return
        role = (cam.get("plate_role") or "").strip()
        if role not in ("entry", "exit"):
            QMessageBox.information(
                self, "نقش پلاکی ندارد",
                f"دوربین «{cam.get('name') or name}» نقش ورود/خروج ندارد.\n"
                "اول در کتابخانه‌ی پلاک ← تب «مسیرها و قوانین»، نقش آن را «ورود» یا «خروج» بگذارید.")
            return
        entry = self.scenes.get(self.current_floor)
        tm = entry["to_meter"] if entry else 1.0
        p = item.pos()
        ok, dist_m, _s = camera_on_lane(
            p.x(), p.y(), self._lane_points, tm, DEFAULT_ATTACH_TOLERANCE_M)
        if not ok:
            QMessageBox.information(
                self, "دوربین روی مسیر نیست",
                f"فاصله‌ی «{cam.get('name') or name}» از خط مسیر "
                f"{dist_m:.1f} متر است (حد مجاز {DEFAULT_ATTACH_TOLERANCE_M:.0f} متر).")
            return
        if dev_id in self._lane_pick:
            ring = self._lane_pick_rings.pop(dev_id, None)
            if ring is not None:
                try:
                    entry["scene"].removeItem(ring)
                except Exception:
                    pass
            self._lane_pick.pop(dev_id, None)
        else:
            ring = QGraphicsEllipseItem(-26, -26, 52, 52)
            ring.setPos(p.x(), p.y())
            ring.setPen(QPen(QColor("#22c55e"), 3))
            ring.setBrush(QBrush(QColor(34, 197, 94, 30)))
            ring.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            ring.setZValue(24)
            ring.setData(0, "lane-pick")
            entry["scene"].addItem(ring)
            self._lane_pick[dev_id] = item
            self._lane_pick_rings[dev_id] = ring
        self._update_lane_bar()

    # -- ذخیره‌ی مسیر --
    def _save_lane(self):
        if not self._lane_pick:
            QMessageBox.information(self, "دوربینی انتخاب نشده",
                                    "حداقل یک دوربین روی مسیر انتخاب کنید.")
            return
        ok, msg = validate_lane_points(self._lane_points)
        if not ok:
            QMessageBox.information(self, "مسیر ناقص", msg)
            return
        lanes = plate_store.get_lanes()
        k = len(lanes) + 1
        while f"lane{k}" in lanes:
            k += 1
        dlg = QDialog(self)
        dlg.setWindowTitle("ذخیره‌ی مسیر")
        form = QFormLayout(dlg)
        name_edit = QLineEdit(f"مسیر {k}")
        form.addRow("نام مسیر:", name_edit)
        dir_combo = QComboBox()
        dir_combo.addItem("فقط رفت (جهت رسم شما)", "going")
        dir_combo.addItem("فقط برگشت", "return")
        form.addRow("جهت مجاز:", dir_combo)
        form.addRow(QLabel("راهنما: جهت رسم شما (نقطه‌ی ۱ ← آخر) = جهت «رفت» است."))
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("ذخیره")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip() or f"مسیر {k}"
        allowed = dir_combo.currentData() or "going"
        entry = self.scenes.get(self.current_floor)
        tm = entry["to_meter"] if entry else 1.0
        cams_xy = []
        for _dev_id, item in self._lane_pick.items():
            p = item.pos()
            cams_xy.append({"camera_id": item.device.get("ref_id"),
                            "x": p.x(), "y": p.y()})
        lid = f"lane{k}"
        rec = build_lane_record(name, allowed, self.current_floor,
                                self._lane_points, tm, cams_xy)
        lanes[lid] = rec
        plate_store.set_lanes(lanes)
        # اتصال دوربین‌ها به این مسیر (قانون: هر دوربین فقط یک مسیر)
        for c in cams_xy:
            self._detach_camera_from_lanes(c["camera_id"], except_lid=lid)
            try:
                self.camera_store.update_camera(c["camera_id"], lane_id=lid)
            except Exception:
                pass
        self._cancel_lane_mode()
        self._reload_lane_list(select_id=lid)
        self._show_lane(lid)
        QMessageBox.information(
            self, "ذخیره شد",
            f"مسیر «{name}» با {len(cams_xy)} دوربین ذخیره و به پلاک‌خوان اضافه شد.")

    def _detach_camera_from_lanes(self, camera_id, except_lid=""):
        try:
            lanes = plate_store.get_lanes()
            changed = False
            for lid, lane in lanes.items():
                if lid == except_lid:
                    continue
                cams = (lane or {}).get("cameras") or []
                new_cams = [c for c in cams
                            if str(c.get("camera_id")) != str(camera_id)]
                if len(new_cams) != len(cams):
                    lane["cameras"] = new_cams
                    changed = True
            if changed:
                plate_store.set_lanes(lanes)
        except Exception:
            pass

    # -- لیست مسیرها / نمایش / حذف --
    def _reload_lane_list(self, select_id=None):
        try:
            lanes = plate_store.get_lanes()
        except Exception:
            lanes = {}
        self.lane_list.blockSignals(True)
        self.lane_list.clear()
        for lid, lane in lanes.items():
            lane = lane or {}
            allowed = (lane.get("allowed") or "").strip()
            dir_txt = {"going": "فقط رفت", "return": "فقط برگشت"}.get(allowed, "؟")
            geo = " 🗺" if (lane.get("points") and len(lane.get("points")) >= 2) else ""
            ncam = len(lane.get("cameras") or [])
            item = QListWidgetItem(
                f"{lane.get('name') or lid} — {dir_txt}{geo} ({ncam} دوربین)")
            item.setData(Qt.ItemDataRole.UserRole, lid)
            self.lane_list.addItem(item)
            if select_id and lid == select_id:
                self.lane_list.setCurrentItem(item)
        self.lane_list.blockSignals(False)

    def _on_lane_selected(self, item):
        lid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if lid:
            self._show_lane(lid)

    def _clear_lane_geo(self):
        for entry in self.scenes.values():
            for it in list(entry["scene"].items()):
                try:
                    if it.data(0) == "lane-geo":
                        entry["scene"].removeItem(it)
                except Exception:
                    pass

    def _show_lane(self, lane_id):
        """نمایش هندسه‌ی یک مسیر روی نقشه (خط + جهت + ترتیب دوربین‌ها)."""
        self._lane_selected_id = lane_id
        self._clear_lane_geo()
        try:
            lane = plate_store.get_lanes().get(lane_id) or {}
        except Exception:
            return
        pts = lane.get("points") or []
        fid = (lane.get("floor_id") or "").strip()
        if len(pts) < 2 or not fid:
            return
        if fid != self.current_floor:
            self._select_floor(fid)
            return  # _activate_floor دوباره _show_lane را صدا می‌زند
        entry = self._build_scene(fid)
        if not entry:
            return
        sc = entry["scene"]
        cur_tm = entry.get("to_meter") or 1.0
        # نقاط مسیر با واحد صحنه‌ی زمان رسم ذخیره شده‌اند؛ اگر نقشه عوض شده
        # (واحد متفاوت)، به قاب صحنه‌ی فعلی تبدیل می‌شوند (2.0.23-beta).
        lane_tm = lane.get("to_meter", cur_tm) or cur_tm
        _lk = lane_tm / cur_tm if cur_tm else 1.0
        pts = [[float(x) * _lk, float(y) * _lk] for x, y in pts]
        # خط مسیر
        path = QPainterPath()
        path.moveTo(pts[0][0], pts[0][1])
        for x, y in pts[1:]:
            path.lineTo(x, y)
        line = QGraphicsPathItem(path)
        pen = QPen(QColor("#38bdf8"), 0)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        line.setPen(pen)
        line.setZValue(23)
        line.setData(0, "lane-geo")
        sc.addItem(line)
        # پیکان جهت در انتهای مسیر
        try:
            (ax, ay), (bx, by) = pts[-2], pts[-1]
            ang = math.degrees(math.atan2(-(by - ay), bx - ax))
            tri = QPolygonF([QPointF(18, 0), QPointF(-8, 11), QPointF(-8, -11)])
            arrow = QGraphicsPolygonItem(tri)
            arrow.setPos(bx, by)
            arrow.setRotation(-ang)
            arrow.setPen(QPen(QColor("#38bdf8"), 2))
            arrow.setBrush(QBrush(QColor("#38bdf8")))
            arrow.setFlag(
                QGraphicsPolygonItem.GraphicsItemFlag.ItemIgnoresTransformations)
            arrow.setZValue(24)
            arrow.setData(0, "lane-geo")
            sc.addItem(arrow)
        except Exception:
            pass
        # نشان ترتیب دوربین‌ها (موقعیت زنده‌ی روی نقشه)
        for c in (lane.get("cameras") or []):
            cam_id = c.get("camera_id")
            order = c.get("order", 0)
            x = y = None
            try:
                for _fl, dev in self.store.devices_by_camera(cam_id):
                    if _fl.get("id") == fid:
                        x, y = device_scene_xy(dev, cur_tm)
                        break
            except Exception:
                pass
            if x is None:
                continue
            badge = QGraphicsEllipseItem(-14, -14, 28, 28)
            badge.setPos(x, y - 34)
            badge.setPen(QPen(QColor("#38bdf8"), 2))
            badge.setBrush(QBrush(QColor("#0c4a6e")))
            badge.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            badge.setZValue(24)
            badge.setData(0, "lane-geo")
            sc.addItem(badge)
            num = QGraphicsSimpleTextItem(str(order + 1))
            f = QFont()
            f.setBold(True)
            f.setPointSize(10)
            num.setFont(f)
            num.setBrush(QBrush(QColor("#e0f2fe")))
            num.setPos(x - 5, y - 44)
            num.setFlag(
                QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            num.setZValue(25)
            num.setData(0, "lane-geo")
            sc.addItem(num)

    def _delete_lane(self):
        item = self.lane_list.currentItem()
        if item is None:
            QMessageBox.information(self, "حذف مسیر",
                                    "اول یک مسیر را از لیست انتخاب کنید.")
            return
        lid = item.data(Qt.ItemDataRole.UserRole)
        try:
            lanes = plate_store.get_lanes()
            lane = lanes.get(lid) or {}
            name = lane.get("name") or lid
        except Exception:
            return
        if QMessageBox.question(
                self, "حذف مسیر",
                f"مسیر «{name}» حذف شود؟\nدوربین‌هایش بدون مسیر می‌شوند."
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            lanes = plate_store.get_lanes()
            lanes.pop(lid, None)
            plate_store.set_lanes(lanes)
            # جدا کردن دوربین‌ها از این مسیر
            for cam in list(self.camera_store.standalone_cameras()):
                if cam.get("lane_id") == lid:
                    self.camera_store.update_camera(cam.get("id"), lane_id="")
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    if cam.get("lane_id") == lid:
                        self.camera_store.update_camera(cam.get("id"), lane_id="")
        except Exception:
            pass
        if self._lane_selected_id == lid:
            self._lane_selected_id = None
        self._clear_lane_geo()
        self._reload_lane_list()

    # ================= شبیه‌سازی / پوشش / هیت‌مپ (2.0.18-beta) ==============

    def _lane_camera_positions(self, lane, fid):
        """موقعیت صحنه‌ی دوربین‌های یک مسیر: ({id: (x,y)}, {id: name})."""
        entry = self._build_scene(fid)
        tm = (entry or {}).get("to_meter") or 1.0
        xy, names = {}, {}
        for c in (lane.get("cameras") or []):
            cid = str(c.get("camera_id"))
            try:
                for _fl, dev in self.store.devices_by_camera(cid):
                    if _fl.get("id") == fid:
                        xy[cid] = device_scene_xy(dev, tm)
                        names[cid] = dev.get("name") or cid
                        break
            except Exception:
                continue
        return xy, names

    def _start_lane_sim(self):
        """شبیه‌سازی حرکت خودرو روی مسیر انتخاب‌شده."""
        item = self.lane_list.currentItem()
        if item is None:
            QMessageBox.information(self, "شبیه‌سازی",
                                    "اول یک مسیر را از لیست انتخاب کنید.")
            return
        lid = item.data(Qt.ItemDataRole.UserRole)
        try:
            lane = plate_store.get_lanes().get(lid) or {}
        except Exception:
            lane = {}
        pts = lane.get("points") or []
        if len(pts) < 2:
            QMessageBox.information(self, "شبیه‌سازی",
                                    "این مسیر نقطه‌ی کافی برای شبیه‌سازی ندارد.")
            return
        fid = (lane.get("floor_id") or "").strip()
        # (2.0.23-beta) نقاط مسیر با واحد صحنه‌ی زمان رسم ذخیره شده‌اند؛
        # اگر نقشه بعداً با مقیاس متفاوت جایگزین شده باشد، به مقیاس جاری
        # تبدیل می‌شوند تا با موقعیت دوربین‌ها هم‌خوان باشند.
        entry = self._build_scene(fid) if fid else None
        cur_tm = (entry or {}).get("to_meter") or 1.0
        lane_tm = float(lane.get("to_meter") or 1.0)
        if lane_tm != cur_tm:
            ratio = lane_tm / cur_tm
            lane = dict(lane,
                        points=[[float(x) * ratio, float(y) * ratio]
                                for x, y in pts],
                        to_meter=cur_tm)
            pts = lane["points"]
        xy, names = self._lane_camera_positions(lane, fid)
        try:
            from lane_simulator import LaneSimDialog
        except Exception as e:
            QMessageBox.warning(self, "خطا",
                                f"باز کردن شبیه‌ساز ناموفق بود:\n{e}")
            return
        dlg = LaneSimDialog(lane, xy, names, self)
        dlg.exec()

    def _clear_geo_tag(self, tag):
        for entry in self.scenes.values():
            for it in list(entry["scene"].items()):
                try:
                    if it.data(0) == tag:
                        entry["scene"].removeItem(it)
                except Exception:
                    pass

    def _analyze_coverage(self):
        """تحلیل زنده‌ی پوشش دوربین روی نقاط مسیرهای پلاک‌خوان (2.0.21-beta).

        برای هر مسیرِ این طبقه، هر ۲ متر یک نقطه نمونه‌برداری می‌شود؛ نقطه‌ای
        که در قطاع دید هیچ دوربینی نباشد «بدون پوشش» است. نتیجه در پنل
        «گزارش پوشش زنده» نوشته می‌شود و از این به بعد با هر جابه‌جایی یا
        تغییر دوربین (موقعیت/زاویه/پهنا/برد) خودکار و در لحظه به‌روز می‌شود.
        """
        fid = self.current_floor
        if not fid:
            QMessageBox.information(self, "تحلیل پوشش",
                                    "اول یک طبقه را انتخاب کنید.")
            return
        lanes = self._floor_lanes(fid)
        if not lanes:
            QMessageBox.information(
                self, "تحلیل پوشش",
                "برای این طبقه مسیری رسم نشده است.\n"
                "اول با «✏️ رسم مسیر جدید» یک مسیر پلاک‌خوان رسم کنید.")
            return
        self._coverage_live = {"floor_id": fid}
        self._refresh_coverage_live()

    def _floor_lanes(self, fid):
        """مسیرهای رسم‌شده‌ی یک طبقه (حداقل ۲ نقطه)."""
        try:
            all_lanes = plate_store.get_lanes() or {}
        except Exception:
            return []
        out = []
        for lid, lane in all_lanes.items():
            if not isinstance(lane, dict):
                continue
            if (lane.get("floor_id") or "") != fid:
                continue
            if len(lane.get("points") or []) < 2:
                continue
            out.append(dict(lane, _id=lid))
        return out

    def _refresh_coverage_live(self):
        """به‌روزرسانی در لحظه‌ی گزارش پوشش.

        بعد از هر جابه‌جایی دوربین (_on_device_moved) و هر تغییر مشخصات
        دوربین (_prop_apply) صدا زده می‌شود؛ اگر تحلیل پوشش فعال نباشد یا
        طبقه عوض شده باشد کاری نمی‌کند.
        """
        live = getattr(self, "_coverage_live", None)
        if not live or live.get("floor_id") != self.current_floor:
            return
        fid = self.current_floor
        try:
            from lane_coverage import (DEFAULT_SAMPLE_STEP_M,
                                       analyze_lane_coverage)
        except Exception:
            return
        lanes = self._floor_lanes(fid)
        if not lanes:
            self._coverage_live = None
            self._clear_geo_tag("coverage-geo")
            if hasattr(self, "coverage_report"):
                self.coverage_report.setPlainText(
                    "مسیری برای این طبقه باقی نمانده؛ تحلیل پوشش متوقف شد.")
            return
        fl = self.store.get_floor(fid) or {}
        try:
            to_meter = (self.scenes.get(fid) or {}).get("to_meter") or 1.0
        except Exception:
            to_meter = 1.0
        # همه‌چیز به قاب صحنه‌ی فعلی: نقاط مسیر با to_meter زمان رسم ذخیره
        # شده‌اند و مختصات دوربین‌ها متری است (2.0.23-beta).
        cur_tm = to_meter
        lanes_conv = []
        for lane in lanes:
            ltm = lane.get("to_meter", cur_tm) or cur_tm
            k = ltm / cur_tm if cur_tm else 1.0
            pts = lane.get("points") or []
            lanes_conv.append(dict(
                lane,
                points=[[float(x) * k, float(y) * k] for x, y in pts],
                to_meter=cur_tm))
        cameras = []
        for d in (fl.get("devices") or []):
            if d.get("kind") != "camera":
                continue
            cx, cy = device_scene_xy(d, cur_tm)
            cameras.append({"name": d.get("name") or d.get("id") or "؟",
                            "id": d.get("id"),
                            "x": cx, "y": cy,
                            "angle": d.get("angle", 0.0),
                            "fov": d.get("fov", 90.0),
                            "view_distance": d.get("view_distance", 8.0)})
        result = analyze_lane_coverage(lanes_conv, cameras, to_meter=cur_tm,
                                       step_m=DEFAULT_SAMPLE_STEP_M)
        self._draw_lane_coverage(result)
        self._render_coverage_report(result, DEFAULT_SAMPLE_STEP_M)

    def _draw_lane_coverage(self, result):
        """نقاط نمونه‌برداری‌شده روی نقشه: سبز = دارای پوشش، قرمز = بدون پوشش."""
        self._clear_geo_tag("coverage-geo")
        entry = self._build_scene(self.current_floor)
        if not entry:
            return
        sc = entry["scene"]
        for lane in result.get("lanes", []):
            for p in lane.get("points", []):
                x, y = p["x"], p["y"]
                if p.get("covered_by"):
                    dot = QGraphicsEllipseItem(-5, -5, 10, 10)
                    dot.setPos(x, y)
                    dot.setPen(QPen(QColor("#16a34a"), 2))
                    dot.setBrush(QBrush(QColor("#16a34a")))
                else:
                    dot = QGraphicsEllipseItem(-12, -12, 24, 24)
                    dot.setPos(x, y)
                    dot.setPen(QPen(QColor("#ef4444"), 3))
                    dot.setBrush(QBrush(QColor(239, 68, 68, 90)))
                dot.setFlag(
                    QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
                dot.setZValue(26)
                dot.setData(0, "coverage-geo")
                sc.addItem(dot)

    def _render_coverage_report(self, result, step_m):
        """نوشتن گزارش پوشش در پنل «گزارش پوشش زنده».

        دو بخش: ۱) کدام نقاط هر مسیر بدون پوشش‌اند ۲) هر دوربین کدام
        نقاط کدام مسیر را پوشش می‌دهد.
        """
        lines = [f"📡 پوشش مسیرها (زنده — هر {step_m:g} متر یک نقطه)",
                 ""]
        for lane in result.get("lanes", []):
            pts = lane.get("points", [])
            unc = lane.get("uncovered", [])
            lines.append(
                f"🛣 «{lane['name']}»: {len(pts)} نقطه | "
                f"✅ {len(pts) - len(unc)} با پوشش | "
                f"⛔ {len(unc)} بدون پوشش")
            if unc:
                spots = "، ".join(f"{p['s_m']:g}م" for p in unc[:12])
                if len(unc) > 12:
                    spots += f" …و {len(unc) - 12} نقطه‌ی دیگر"
                lines.append(
                    f"   نقاط بدون پوشش (متراژ از ابتدای مسیر): {spots}")
            lines.append("")
        lines.append("🎥 هر دوربین کدام نقاط را می‌بیند:")
        cams = result.get("cameras", [])
        if not cams:
            lines.append("   دوربینی در این طبقه ثبت نشده است.")
        for cam in cams:
            cov = cam.get("covered", [])
            if not cov:
                lines.append(f"   • {cam['name']}: —")
                continue
            # گروه‌بندی بر اساس مسیر + فشرده‌سازی متراژهای پشت‌سرهم
            by_lane = {}
            for c in cov:
                by_lane.setdefault(c["lane"], []).append(c["s_m"])
            parts = []
            for lname, sms in by_lane.items():
                sms = sorted(sms)
                parts.append(f"«{lname}» ← {self._compact_ranges(sms)}")
            lines.append(f"   • {cam['name']}: " + "؛ ".join(parts))
        self.coverage_report.setPlainText("\n".join(lines).strip())

    @staticmethod
    def _compact_ranges(values):
        """فشرده‌سازی لیست مرتب متراژها: [0,2,4,10] -> «0–4، 10» (متر)."""
        vals = sorted(values)
        if not vals:
            return "—"
        ranges = []
        start = prev = vals[0]
        for v in vals[1:]:
            if abs(v - prev - 2.0) < 0.05:  # گام نمونه‌برداری
                prev = v
                continue
            ranges.append((start, prev))
            start = prev = v
        ranges.append((start, prev))
        out = []
        for a, b in ranges:
            out.append(f"{a:g}–{b:g}" if abs(b - a) > 1e-9 else f"{a:g}")
        return "، ".join(out) + "م"

    def _toggle_heatmap(self):
        """نمایش/پنهان‌سازی هیت‌مپ تردد روی نقشه."""
        self._heatmap_on = self.heatmap_btn.isChecked()
        self._clear_geo_tag("heatmap-geo")
        if not self._heatmap_on:
            return
        try:
            counts = plate_store.lane_traffic_counts()
        except Exception:
            counts = {}
        if not counts:
            QMessageBox.information(
                self, "🔥 هیت‌مپ تردد",
                "هنوز عبوری در دیتابیس ثبت نشده است؛ هیت‌مپ خالی است.")
            self.heatmap_btn.setChecked(False)
            self._heatmap_on = False
            return
        try:
            lanes = plate_store.get_lanes() or {}
        except Exception:
            lanes = {}
        vmax = max(counts.values()) if counts else 1
        shown = 0
        for lid, n in counts.items():
            lane = lanes.get(lid) or {}
            pts = lane.get("points") or []
            fid = (lane.get("floor_id") or "").strip()
            if len(pts) < 2 or not fid or fid != self.current_floor:
                continue
            entry = self._build_scene(fid)
            if not entry:
                continue
            sc = entry["scene"]
            t = n / vmax if vmax else 0
            color = self._heat_color(t)
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            for x, y in pts[1:]:
                path.lineTo(x, y)
            line = QGraphicsPathItem(path)
            pen = QPen(QColor(*color), 0)
            pen.setCosmetic(True)
            pen.setWidth(max(3, int(3 + 7 * t)))
            line.setPen(pen)
            line.setZValue(22)
            line.setData(0, "heatmap-geo")
            sc.addItem(line)
            shown += 1
        if shown == 0:
            QMessageBox.information(
                self, "🔥 هیت‌مپ تردد",
                "هیچ مسیرِ دارای ترددی روی این طبقه نیست.")

    @staticmethod
    def _heat_color(t):
        """رنگ هیت‌مپ: سبز -> زرد -> قرمز بر اساس t در [۰،۱]."""
        t = max(0.0, min(1.0, t))
        if t < 0.5:
            k = t / 0.5
            return (int(34 + (250 - 34) * k), int(197 + (204 - 197) * k), 94)
        k = (t - 0.5) / 0.5
        return (250, int(204 - (204 - 68) * k), int(94 - (94 - 68) * k))

    # ============================ تازه‌سازی ============================
    def refresh(self):
        """هنگام نمایش صفحه از هدر صدا زده می‌شود."""
        if getattr(self, "_pending_fit", False):
            self._pending_fit = False
            self._fit_current()
        # اتصال سیگنال انتخاب صحنه (فقط یک‌بار برای هر صحنه)
        for entry in self.scenes.values():
            try:
                entry["scene"].selectionChanged.disconnect(
                    self._refresh_prop_panel)
            except Exception:
                pass
            try:
                entry["scene"].selectionChanged.connect(
                    self._refresh_prop_panel)
            except Exception:
                pass
        self._refresh_prop_panel()
        self._update_zoom_label()

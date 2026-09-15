# -*- coding: utf-8 -*-
"""صفحه‌ی «🗺 نقشه ساختمان» — نقشه‌ی تعاملی طبقات

امکانات:
- هر طبقه نقشه‌ی جداگانه (DXF خروجی اتوکد یا تصویر)
- رندر دقیق DXF با لایه‌ها، رنگ‌ها و واحد واقعی (میلی‌متر/متر/...)
- جای‌گذاری دوربین، NVR، رک، سوئیچ و... با درگ و زاویه‌ی دید (FOV)
- کلیک روی دوربین نقشه = پخش زنده
- نمایش مسیر تردد شخص (از ردیابی اشخاص) روی نقشه با پخش متحرک
- شبکه‌ی مختصات متری + نمایش مختصات موس برای دقت جای‌گذاری
"""

import os

from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QPen, QBrush, QPainter, QPainterPath, QFont,
    QCursor,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QGraphicsView, QGraphicsScene, QGraphicsItemGroup,
    QGraphicsPathItem, QGraphicsSimpleTextItem, QGraphicsEllipseItem,
    QGraphicsLineItem, QSplitter, QComboBox, QLineEdit, QSpinBox, QSlider,
    QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QFileDialog,
    QInputDialog, QGroupBox, QAbstractItemView,
)

from building_map import (
    MapStore, DxfMapLoader, DxfError, DEVICE_KINDS, ezdxf_available,
)
from person_store import person_store

# ---------------------------------------------------------------------------
# صحنه با شبکه‌ی مختصات
# ---------------------------------------------------------------------------
class MapScene(QGraphicsScene):
    """صحنه‌ی نقشه با پس‌زمینه‌ی تیره و شبکه‌ی نقطه‌ای هر ۱ متر."""

    def __init__(self, to_meter=1.0):
        super().__init__()
        self.to_meter = to_meter or 1.0
        self.setBackgroundBrush(QBrush(QColor("#0b0f14")))

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        step = 1.0 / self.to_meter  # یک متر به واحد صحنه
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
        self._sector = None
        self._tick = None
        self._build()
        self.setPos(device.get("x", 0), device.get("y", 0))
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
            angle = float(dev.get("angle", 0))
            fov = float(dev.get("fov", 90))
            rng = 8.0 / self.to_meter  # برد نمایشی ۸ متر
            sector = QGraphicsPathItem(_sector_path(0, 0, rng, angle, fov))
            sector.setPen(QPen(QColor(34, 211, 238, 110), 0))
            sector.setBrush(QBrush(QColor(34, 211, 238, 38)))
            self.addToGroup(sector)
            self._sector = sector
            # بدنه‌ی دوربین (اندازه ثابت روی صفحه)
            body = QGraphicsEllipseItem(-11, -11, 22, 22)
            body.setPen(QPen(accent, 2))
            body.setBrush(QBrush(QColor("#0e1620")))
            body.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self.addToGroup(body)
            # جهت لنز
            import math
            a = math.radians(angle)
            dx, dy = 11 * math.cos(a), -11 * math.sin(a)
            tick = QGraphicsLineItem(0, 0, dx * 1.5, dy * 1.5)
            tick.setPen(QPen(QColor("#f472b6"), 3))
            tick.setFlag(
                QGraphicsLineItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self.addToGroup(tick)
            self._tick = tick
            glyph = QGraphicsSimpleTextItem("🎥")
            glyph.setPos(-9, -13)
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
        """به‌روزرسانی زنده‌ی ظاهر تجهیز، بدون بازسازی گروه.

        رفع باگ «پرش دوربین هنگام تغییر زاویه»: نسخه‌ی قبلی کل گروه را با
        removeFromGroup/addToGroup از نو می‌ساخت؛ چون گروه از قبل در موقعیت
        P روی صحنه بود، Qt موقعیت صحنه‌ای فرزندهای تازه (۰٬۰) را حفظ می‌کرد
        و همه‌ی گرافیک دوربین به گوشه‌ی نقشه (مبدأ صحنه) می‌پرید، ضمن این‌که
        فرزندهای قبلی به‌صورت «روح» در صحنه می‌ماندند. حالا فقط مسیر قطاع
        دید و خط جهت لنز درجا به‌روز می‌شوند؛ موقعیت دست نمی‌خورد.
        """
        dev = self.device
        if dev.get("kind") == "camera":
            angle = float(dev.get("angle", 0))
            fov = float(dev.get("fov", 90))
            rng = 8.0 / (self.to_meter or 1.0)
            if self._sector is not None:
                self._sector.setPath(_sector_path(0, 0, rng, angle, fov))
            if self._tick is not None:
                import math
                a = math.radians(angle)
                dx, dy = 11 * math.cos(a), -11 * math.sin(a)
                self._tick.setLine(0, 0, dx * 1.5, dy * 1.5)
        self.update()

    def set_name(self, name):
        self._label.setText(name)

    # -- کلیک در برابر درگ --
    def mousePressEvent(self, event):
        self._press_scene = event.scenePos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        moved = True
        try:
            if self._press_scene is not None:
                moved = (event.scenePos() - self._press_scene).manhattanLength() > 4
        except Exception:
            pass
        super().mouseReleaseEvent(event)
        if not moved:
            cb = self.cb.get("clicked")
            if callable(cb):
                cb(self)
        else:
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

        # --- پنل چپ ---
        left = QWidget()
        left.setFixedWidth(264)
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
        ll.addStretch()
        root.addWidget(left)

        # --- وسط: نقشه ---
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        self.view = MapView()
        self.view.place_clicked.connect(self._on_place_clicked)
        self.view.mouse_moved.connect(self._on_mouse_moved)
        cl.addWidget(self.view, 1)
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
        self.store.import_map_file(fid, path)
        if fid in self.scenes:
            del self.scenes[fid]
        self._activate_floor(fid, fit=True)
        QMessageBox.information(self, "انجام شد",
                                "نقشه‌ی DXF با دقت کامل مختصات اتوکد بارگذاری شد.")

    def _import_image(self):
        fid = self._current_floor_id()
        if not fid:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "انتخاب تصویر نقشه", "",
            "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
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
        if event.key() == Qt.Key.Key_Escape and self._placing_kind:
            self._stop_placing()
            event.accept()
            return
        super().keyPressEvent(event)

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
        dev = self.store.add_device(
            self.current_floor, kind, vals["name"],
            scene_pos.x(), scene_pos.y(),
            ref_id=vals["ref_id"], angle=vals["angle"], fov=vals["fov"])
        entry = self.scenes.get(self.current_floor)
        if entry and dev:
            item = DeviceItem(self.current_floor, dev, entry["to_meter"], {
                "moved": self._on_device_moved,
                "clicked": self._on_device_clicked,
            })
            entry["scene"].addItem(item)
            entry["items"][dev["id"]] = item
            item.setSelected(True)
        # در حالت جای‌گذاری می‌مانیم تا چند تجهیز پشت سر هم بگذاریم
        self._update_zoom_label()

    def _on_device_moved(self, floor_id, dev_id, x, y):
        self.store.update_device(floor_id, dev_id, x=float(x), y=float(y))

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
        self.prop_fov.setEnabled(kind == "camera")
        self.prop_angle.setEnabled(kind == "camera")
        self.prop_angle_num.setEnabled(kind == "camera")

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
        name = self.prop_name.text().strip() or dev.get("name", "")
        ref_id = self.prop_link.currentData() or ""
        angle = float(self.prop_angle.value())
        fov = float(self.prop_fov.value())
        self.store.update_device(self.current_floor, dev["id"],
                                 name=name, ref_id=ref_id,
                                 angle=angle, fov=fov)
        dev["name"] = name
        dev["ref_id"] = ref_id
        dev["angle"] = angle
        dev["fov"] = fov
        item.set_name(name)
        item.refresh()

    def _prop_angle_changed(self, value):
        # سینک اسلایدر و اسپین‌باکس + به‌روزرسانی زنده‌ی قطاع دید.
        # ذخیره روی دیسک فقط هنگام رها کردن اسلایدر انجام می‌شود
        # (sliderReleased -> _prop_apply) تا با هر تیک درگ، فایل JSON
        # بازنویسی نشود.
        for w in (self.prop_angle, self.prop_angle_num):
            w.blockSignals(True)
            w.setValue(int(value))
            w.blockSignals(False)
        item = self._selected_device_item()
        if not item:
            return
        item.device["angle"] = float(value)
        item.refresh()

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

    def show_person_path(self, person_id):
        """نمایش مسیر تردد یک شخص روی نقشه‌ها (قابل فراخوانی از بیرون)."""
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
        stops = self._stops_for_person(person_id)

        self._path = {"person": person, "stops": stops,
                      "code": person.get("code", "")}
        self.timeline_group.setEnabled(True)
        self.path_close_btn.setEnabled(True)
        self._draw_path()
        self._fill_timeline()
        # رفتن به طبقه‌ی اولین توقفِ دارای نقشه
        for st in stops:
            if st["placements"]:
                fid = st["placements"][0][0].get("id")
                self._select_floor(fid)
                break

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
        stops = self._path["stops"]
        # شماره‌گذاری به ترتیب زمان
        per_floor_points = {}  # floor_id -> [(x, y, seq, sighting)]
        seq = 0
        for st in stops:
            seq += 1
            s = st["sighting"]
            for fl, dev in st["placements"]:
                fid = fl.get("id")
                per_floor_points.setdefault(fid, []).append(
                    (dev.get("x", 0), dev.get("y", 0), seq, s, dev))
        # رسم روی هر طبقه
        for fid, pts in per_floor_points.items():
            entry = self._build_scene(fid)
            if not entry:
                continue
            sc = entry["scene"]
            # خطوط اتصال متوالیِ همان طبقه
            for a, b in zip(pts, pts[1:]):
                line = QGraphicsLineItem(a[0], a[1], b[0], b[1])
                pen = QPen(QColor("#f472b6"), 0)
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
                badge.setPen(QPen(QColor("#f472b6"), 2))
                badge.setBrush(QBrush(QColor("#1a0f1e")))
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
                tlabel.setBrush(QBrush(QColor("#fbcfe8")))
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
        for i, st in enumerate(self._path["stops"], 1):
            s = st["sighting"]
            cam_name = s.get("camera_name") or "؟"
            when = f"{s.get('enter_j', '')} {s.get('enter_time', '')}"
            floors = ", ".join(
                fl.get("name", "") for fl, _d in st["placements"]) or "—"
            if not st["placements"]:
                unmapped += 1
            item = QListWidgetItem(f"{i}. 🎥 {cam_name}\n    🕐 {when}")
            item.setData(Qt.ItemDataRole.UserRole, i - 1)
            self.timeline_list.addItem(item)
        self.unmapped_label.setText(
            f"{len(self._path['stops'])} توقف در مسیر «{self._path['code']}»"
            + (f" — {unmapped} توقف خارج از نقشه است (دوربین روی نقشه گذاشته نشده)"
               if unmapped else ""))

    def _on_timeline_clicked(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        st = self._path["stops"][idx]
        if not st["placements"]:
            QMessageBox.information(
                self, "خارج از نقشه",
                "دوربین این توقف روی هیچ نقشه‌ای گذاشته نشده است.")
            return
        fl, dev = st["placements"][0]
        self._select_floor(fl.get("id"))
        entry = self.scenes.get(fl.get("id"))
        if entry:
            self.view.centerOn(dev.get("x", 0), dev.get("y", 0))

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
    def set_live_person(self, cam_id, person_id, code, camera_name, present):
        """نمایش/حذف زنده‌ی موقعیت فعلی یک شخص روی نقشه.

        در همان لحظه‌ای که دوربینی یک شخص را شناسایی می‌کند (present=True)،
        یک نشان سبز چشمک‌زن با کد شخص روی همان دوربینِ نقشه می‌نشیند؛ با
        تمام شدن رد (present=False) نشان برداشته می‌شود. حتماً از ترد اصلی
        صدا زده شود.
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
            "placements": [(fl.get("id"), dev.get("x", 0), dev.get("y", 0))
                           for fl, dev in placements],
            "items": [], "rings": [],
            "code": code, "camera_name": camera_name,
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
        for fid, x, y in info["placements"]:
            entry = self.scenes.get(fid)
            if not entry:
                continue
            sc = entry["scene"]
            ring = QGraphicsEllipseItem(-20, -20, 40, 40)
            ring.setPos(x, y)
            ring.setPen(QPen(QColor("#22c55e"), 3))
            ring.setBrush(QBrush(QColor(34, 197, 94, 40)))
            ring.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            ring.setZValue(25)
            ring.setData(0, "person-live")
            sc.addItem(ring)
            dot = QGraphicsEllipseItem(-8, -8, 16, 16)
            dot.setPos(x, y)
            dot.setPen(QPen(QColor("#ffffff"), 2))
            dot.setBrush(QBrush(QColor("#22c55e")))
            dot.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            dot.setZValue(26)
            dot.setData(0, "person-live")
            sc.addItem(dot)
            lab = QGraphicsSimpleTextItem(f"🟢 {code}")
            f = QFont()
            f.setPointSize(10)
            f.setBold(True)
            lab.setFont(f)
            lab.setBrush(QBrush(QColor("#bbf7d0")))
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
        person = self._path.get("person") or {}
        if str(person.get("id")) != str(person_id):
            return
        try:
            self._path["stops"] = self._stops_for_person(person_id)
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
        # ساخت سگمنت‌های همان‌طبقه به ترتیب
        seq_pts = []
        n = 0
        for st in self._path["stops"]:
            n += 1
            for fl, dev in st["placements"]:
                seq_pts.append((fl.get("id"), dev.get("x", 0), dev.get("y", 0)))
        segs = []
        for a, b in zip(seq_pts, seq_pts[1:]):
            if a[0] == b[0]:
                segs.append((a[0], (a[1], a[2]), (b[1], b[2])))
        if not segs:
            QMessageBox.information(self, "مسیر قابل پخش نیست",
                                    "توقف‌های روی نقشه در یک طبقه‌ی مشترک نیستند.")
            return
        dot = QGraphicsEllipseItem(-8, -8, 16, 16)
        dot.setBrush(QBrush(QColor("#f472b6")))
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

# -*- coding: utf-8 -*-
"""lane_simulator.py — شبیه‌ساز حرکت خودرو روی مسیر پلاک‌خوان (2.0.18-beta).

یک نقطه‌ی متحرک روی چندخطی مسیر رسم‌شده حرکت می‌کند؛ ترتیب دوربین‌ها و
جهت مجاز مسیر را نشان می‌دهد تا کاربر قبل از اتصال واقعی، رسم مسیر را
تست و راستی‌آزمایی کند. منطق هندسی خالص در lane_geometry است تا بدون Qt
هم قابل تست باشد.
"""

import math

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen
from PyQt6.QtWidgets import (QDialog, QGraphicsEllipseItem,
                             QGraphicsPathItem, QGraphicsPolygonItem,
                             QGraphicsScene, QGraphicsSimpleTextItem,
                             QGraphicsView, QHBoxLayout, QLabel, QPushButton,
                             QSlider, QTextEdit, QVBoxLayout)

from lane_geometry import (camera_s_at_order, point_at_distance,
                           polyline_length)

_DOT_R = 9


class LaneSimDialog(QDialog):
    """شبیه‌سازی حرکت خودرو روی یک مسیر.

    lane: رکورد مسیر (name/allowed/points/to_meter/cameras با order).
    cameras_xy: {camera_id: (x, y)} موقعیت صحنه‌ی دوربین‌ها روی نقشه.
    cam_names: {camera_id: name} اختیاری.
    """

    def __init__(self, lane, cameras_xy, cam_names=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"🚗 شبیه‌سازی حرکت — {lane.get('name', '')}")
        self.resize(720, 560)
        self._lane = lane or {}
        pts = [(float(x), float(y)) for x, y in (lane.get("points") or [])]
        self._pts = pts
        self._to_meter = float(lane.get("to_meter") or 1.0)
        allowed = (lane.get("allowed") or "going").strip()
        self._forward = allowed != "return"  # رفت: اول->آخر
        self._total = polyline_length(pts)
        self._total_m = self._total * self._to_meter

        # دوربین‌های مسیر با موقعیت طولی روی مسیر
        self._cams = []
        for c in (lane.get("cameras") or []):
            cid = str(c.get("camera_id"))
            xy = (cameras_xy or {}).get(cid)
            if not xy:
                continue
            s = camera_s_at_order(pts, xy[0], xy[1])
            name = (cam_names or {}).get(cid, cid)
            self._cams.append({"id": cid, "order": int(c.get("order", 0)),
                              "name": name, "x": xy[0], "y": xy[1],
                              "s": s if s is not None else 0.0,
                              "passed": False, "badge": None})
        self._cams.sort(key=lambda c: c["order"])

        self._dist = 0.0 if self._forward else self._total
        self._speed_m_s = 5.0
        self._playing = False

        self._build_ui()
        self._draw_map()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._update_dot()

    # ------------------------------- UI ---------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        self.view = QGraphicsView()
        self.view.setRenderHint(self.view.renderHints())
        self.scene = QGraphicsScene(self)
        self.view.setScene(self.scene)
        root.addWidget(self.view, 1)

        info = QHBoxLayout()
        self.pos_label = QLabel("موقعیت: ۰٫۰ متر")
        self.pos_label.setStyleSheet("color:#8fa3b8; font-size:12px;")
        info.addWidget(self.pos_label)
        info.addStretch()
        self.dir_label = QLabel("جهت: رفت ➡" if self._forward else "جهت: برگشت ⬅")
        self.dir_label.setStyleSheet("color:#fbbf24; font-size:12px;")
        info.addWidget(self.dir_label)
        root.addLayout(info)

        ctl = QHBoxLayout()
        self.play_btn = QPushButton("▶️ شروع")
        self.play_btn.clicked.connect(self._toggle_play)
        ctl.addWidget(self.play_btn)
        step_btn = QPushButton("⏭ گام")
        step_btn.setToolTip("حرکت به اندازه‌ی ۱ متر")
        step_btn.clicked.connect(lambda: self._advance(1.0 * self._to_meter))
        ctl.addWidget(step_btn)
        reset_btn = QPushButton("↺ از اول")
        reset_btn.clicked.connect(self._reset)
        ctl.addWidget(reset_btn)
        ctl.addWidget(QLabel("سرعت:"))
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(1, 20)
        self.speed.setValue(5)
        self.speed.valueChanged.connect(
            lambda v: self.speed_label.setText(f"{v} م/ث"))
        ctl.addWidget(self.speed, 1)
        self.speed_label = QLabel("5 م/ث")
        ctl.addWidget(self.speed_label)
        root.addLayout(ctl)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        root.addWidget(self.log)

        brow = QHBoxLayout()
        brow.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        brow.addWidget(close_btn)
        root.addLayout(brow)

    # ------------------------------ رسم ----------------------------------
    def _draw_map(self):
        sc = self.scene
        sc.clear()
        if len(self._pts) < 2:
            return
        path = QPainterPath()
        path.moveTo(self._pts[0][0], self._pts[0][1])
        for x, y in self._pts[1:]:
            path.lineTo(x, y)
        line = QGraphicsPathItem(path)
        pen = QPen(QColor("#38bdf8"), 0)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        line.setPen(pen)
        line.setZValue(1)
        sc.addItem(line)

        for cam in self._cams:
            badge = QGraphicsEllipseItem(-13, -13, 26, 26)
            badge.setPos(cam["x"], cam["y"] - 30)
            badge.setPen(QPen(QColor("#38bdf8"), 2))
            badge.setBrush(QBrush(QColor("#0c4a6e")))
            badge.setFlag(
                QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            badge.setZValue(2)
            sc.addItem(badge)
            cam["badge"] = badge
            num = QGraphicsSimpleTextItem(str(cam["order"] + 1))
            f = QFont()
            f.setBold(True)
            f.setPointSize(10)
            num.setFont(f)
            num.setBrush(QBrush(QColor("#e0f2fe")))
            num.setPos(cam["x"] - 5, cam["y"] - 40)
            num.setFlag(
                QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            num.setZValue(3)
            sc.addItem(num)

        self._dot = QGraphicsEllipseItem(-_DOT_R, -_DOT_R,
                                         _DOT_R * 2, _DOT_R * 2)
        self._dot.setPen(QPen(QColor("#fbbf24"), 2))
        self._dot.setBrush(QBrush(QColor("#f59e0b")))
        self._dot.setFlag(
            QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._dot.setZValue(5)
        sc.addItem(self._dot)
        sc.setSceneRect(sc.itemsBoundingRect().adjusted(-60, -60, 60, 60))
        self.view.fitInView(sc.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------ حرکت ---------------------------------
    def _toggle_play(self):
        self._playing = not self._playing
        if self._playing:
            if self._finished():
                self._reset(silent=True)
            self._timer.start()
            self.play_btn.setText("⏸ توقف")
        else:
            self._timer.stop()
            self.play_btn.setText("▶️ ادامه")

    def _reset(self, silent=False):
        self._timer.stop()
        self._playing = False
        self.play_btn.setText("▶️ شروع")
        self._dist = 0.0 if self._forward else self._total
        for cam in self._cams:
            cam["passed"] = False
            if cam["badge"] is not None:
                cam["badge"].setBrush(QBrush(QColor("#0c4a6e")))
        if not silent:
            self.log.append("↺ شبیه‌سازی از اول شروع شد.")
        self._update_dot()

    def _finished(self):
        return (self._dist >= self._total) if self._forward \
            else (self._dist <= 0.0)

    def _tick(self):
        step = float(self.speed.value()) * self._to_meter * 0.05
        self._advance(step if self._forward else -step)

    def _advance(self, delta_scene):
        self._dist = max(0.0, min(self._total, self._dist + delta_scene))
        # عبور از دوربین‌ها
        for cam in self._cams:
            if cam["passed"]:
                continue
            s = cam["s"]
            hit = (self._dist >= s) if self._forward else (self._dist <= s)
            if hit:
                cam["passed"] = True
                if cam["badge"] is not None:
                    cam["badge"].setBrush(QBrush(QColor("#16a34a")))
                arrow = "➡" if self._forward else "⬅"
                self.log.append(
                    f"{arrow} عبور از دوربین «{cam['name']}» "
                    f"(ترتیب {cam['order'] + 1} از {len(self._cams)}) — "
                    f"{cam['s'] * self._to_meter:.1f} متر از ابتدا")
        self._update_dot()
        if self._finished() and self._playing:
            self._timer.stop()
            self._playing = False
            self.play_btn.setText("▶️ شروع")
            self.log.append("✅ پایان شبیه‌سازی — ترتیب دوربین‌ها درست بود.")

    def _update_dot(self):
        if len(self._pts) < 2:
            return
        x, y = point_at_distance(self._pts, self._dist)
        self._dot.setPos(x, y)
        self.pos_label.setText(
            f"موقعیت: {self._dist * self._to_meter:.1f} متر "
            f"از {self._total_m:.1f} متر")

    def closeEvent(self, event):
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

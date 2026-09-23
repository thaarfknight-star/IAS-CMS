# -*- coding: utf-8 -*-
"""plate_stats_dialog.py — داشبورد آماری تردد پلاک‌ها (2.0.18-beta).

نمودارهای میله‌ای ساعتی/روزانه‌ی عبورها (ورود/خروج) به تفکیک مسیر، با رسم
دستی QPainter — بدون وابستگی جدید (matplotlib لازم نیست) تا روی ویندوز
بدون نصب اضافه کار کند. خروجی CSV هم دارد.
"""

import csv

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QDialog, QFileDialog,
                             QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QVBoxLayout, QWidget)


class BarChart(QWidget):
    """نمودار میله‌ای ساده: data = لیست (label, value)."""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self._title = title
        self._data = []
        self.setMinimumHeight(190)

    def set_data(self, data):
        self._data = list(data or [])
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#0b1220"))
        p.setPen(QColor("#e2e8f0"))
        f = QFont()
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(8, 18, self._title)
        if not self._data:
            p.setPen(QColor("#64748b"))
            p.drawText(8, 44, "داده‌ای در این بازه نیست.")
            p.end()
            return
        vals = [v for _l, v in self._data]
        vmax = max(vals) if vals else 1
        if vmax <= 0:
            vmax = 1
        n = len(self._data)
        top, bottom = 30, h - 34
        plot_h = max(10, bottom - top)
        slot = w / max(n, 1)
        bw = max(2, min(slot * 0.62, 46))
        p.setFont(QFont())
        for i, (label, v) in enumerate(self._data):
            x = slot * i + (slot - bw) / 2
            bh = plot_h * (v / vmax)
            y = bottom - bh
            p.fillRect(int(x), int(y), int(bw), int(bh), QColor("#0ea5e9"))
            p.setPen(QColor("#e2e8f0"))
            p.drawText(int(x), int(y) - 14, int(bw), 14,
                      Qt.AlignmentFlag.AlignHCenter, str(v))
            p.setPen(QColor("#94a3b8"))
            p.drawText(int(slot * i), bottom + 4, int(slot), 16,
                      Qt.AlignmentFlag.AlignHCenter, str(label))
        p.end()


class PlateStatsDialog(QDialog):
    """داشبورد آماری تردد: فیلتر تاریخ/مسیر/نوع عبور + نمودار + CSV."""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("📊 آمار تردد پلاک‌ها")
        self.resize(860, 620)
        self._rows = []
        self._build_ui()
        self._reload_lanes()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("از تاریخ:"))
        self.from_date = QDateEdit(calendarPopup=True)
        self.from_date.setDate(QDate.currentDate().addDays(-7))
        self.from_date.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.from_date)
        frow.addWidget(QLabel("تا تاریخ:"))
        self.to_date = QDateEdit(calendarPopup=True)
        self.to_date.setDate(QDate.currentDate())
        self.to_date.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.to_date)
        frow.addWidget(QLabel("مسیر:"))
        self.lane_combo = QComboBox()
        frow.addWidget(self.lane_combo)
        frow.addWidget(QLabel("نوع:"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("همه", "")
        self.type_combo.addItem("⬅ ورود", "entry")
        self.type_combo.addItem("➡ خروج", "exit")
        frow.addWidget(self.type_combo)
        go = QPushButton("🔍 اعمال")
        go.clicked.connect(self.refresh)
        frow.addWidget(go)
        frow.addStretch()
        root.addLayout(frow)

        self.hourly = BarChart("🕐 توزیع ساعتی عبورها (مجموع بازه)")
        root.addWidget(self.hourly, 1)
        self.daily = BarChart("📅 عبور روزانه")
        root.addWidget(self.daily, 1)

        brow = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setStyleSheet("color:#8fa3b8; font-size:12px;")
        brow.addWidget(self.summary)
        brow.addStretch()
        csv_btn = QPushButton("📤 خروجی CSV")
        csv_btn.clicked.connect(self._export_csv)
        brow.addWidget(csv_btn)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        brow.addWidget(close_btn)
        root.addLayout(brow)

    def _reload_lanes(self):
        self.lane_combo.clear()
        self.lane_combo.addItem("همه‌ی مسیرها", "")
        try:
            lanes = self.store.get_lanes() or {}
        except Exception:
            lanes = {}
        for lid, lane in lanes.items():
            name = (lane or {}).get("name") or lid
            self.lane_combo.addItem(name, lid)

    def refresh(self):
        df = self.from_date.date().toString("yyyy-MM-dd")
        dt = self.to_date.date().toString("yyyy-MM-dd")
        lid = self.lane_combo.currentData() or ""
        ctype = self.type_combo.currentData() or ""
        try:
            rows = self.store.crossing_stats(df, dt, lid, ctype)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"خواندن آمار ناموفق بود:\n{e}")
            return
        self._rows = rows
        # ساعتی: جمع ۲۴ ساعت روی کل بازه
        hours = {f"{h:02d}": 0 for h in range(24)}
        # روزانه: جمع هر روز
        days = {}
        total = 0
        for date_g, hour, c in rows:
            hours[hour] = hours.get(hour, 0) + c
            days[date_g] = days.get(date_g, 0) + c
            total += c
        self.hourly.set_data([(h, hours[h]) for h in sorted(hours)])
        self.daily.set_data([(d[5:], days[d]) for d in sorted(days)])
        peak_h = max(hours, key=lambda k: hours[k]) if total else "—"
        peak_d = max(days, key=lambda k: days[k]) if total else "—"
        self.summary.setText(
            f"مجموع عبورها: {total} — شلوغ‌ترین ساعت: {peak_h} — "
            f"شلوغ‌ترین روز: {peak_d}")

    def _export_csv(self):
        if not self._rows:
            QMessageBox.information(self, "خروجی CSV", "داده‌ای برای خروجی نیست.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "خروجی CSV آمار تردد", "plate_stats.csv",
            "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["date", "hour", "count"])
                for date_g, hour, c in self._rows:
                    w.writerow([date_g, hour, c])
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره ناموفق بود:\n{e}")
            return
        QMessageBox.information(self, "خروجی CSV",
                                f"✅ {len(self._rows)} ردیف ذخیره شد.")

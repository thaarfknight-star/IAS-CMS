# -*- coding: utf-8 -*-
"""دیالوگ «گزارش‌ها»: جست‌وجو در تاریخچه‌ی ذخیره‌شده‌ی report_store.py
(شمارش نفرات، ورود به محدوده، چهره‌ی شناخته‌شده/تعریف‌نشده) بر اساس بازه‌ی
تاریخ، دوربین و نوع رویداد، به‌همراه خروجی CSV/Excel."""

from datetime import datetime

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDateEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox,
)

from report_store import EVENT_TYPE_LABELS_FA


class ReportsDialog(QDialog):
    def __init__(self, report_store, parent=None):
        super().__init__(parent)
        self.report_store = report_store
        self.setWindowTitle("گزارش‌ها")
        self.resize(1000, 600)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        layout = QVBoxLayout(self)

        # --------------------------------------------------------- فیلترها -
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("از تاریخ:"))
        self.from_date = QDateEdit(calendarPopup=True)
        self.from_date.setDate(QDate.currentDate().addDays(-7))
        filter_row.addWidget(self.from_date)

        filter_row.addWidget(QLabel("تا تاریخ:"))
        self.to_date = QDateEdit(calendarPopup=True)
        self.to_date.setDate(QDate.currentDate())
        filter_row.addWidget(self.to_date)

        filter_row.addWidget(QLabel("نوع رویداد:"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("همه", None)
        for key, label in EVENT_TYPE_LABELS_FA.items():
            self.type_combo.addItem(label, key)
        filter_row.addWidget(self.type_combo)

        filter_row.addWidget(QLabel("دوربین:"))
        self.camera_combo = QComboBox()
        self.camera_combo.addItem("همه", None)
        for cam_name in self.report_store.distinct_cameras():
            self.camera_combo.addItem(cam_name, cam_name)
        filter_row.addWidget(self.camera_combo)

        self.search_btn = QPushButton("🔍 جست‌وجو")
        self.search_btn.clicked.connect(self.run_search)
        filter_row.addWidget(self.search_btn)

        filter_row.addStretch()

        self.export_btn = QPushButton("📤 خروجی Excel (CSV)")
        self.export_btn.clicked.connect(self.export_csv)
        filter_row.addWidget(self.export_btn)

        layout.addLayout(filter_row)

        # ----------------------------------------------------------- جدول -
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "تاریخ و ساعت", "نوع رویداد", "دوربین", "نام فرد", "تلفن",
            "شماره کارمندی", "محدوده", "تعداد نفرات", "تصویر",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self.run_search()

    # ------------------------------------------------------------- کمکی -

    def _current_filters(self):
        start = self.from_date.date().toString("yyyy-MM-dd") + " 00:00:00"
        end = self.to_date.date().toString("yyyy-MM-dd") + " 23:59:59"
        event_type = self.type_combo.currentData()
        camera_name = self.camera_combo.currentData()
        return start, end, event_type, camera_name

    # ------------------------------------------------------------- عملیات -

    def run_search(self):
        start, end, event_type, camera_name = self._current_filters()
        rows = self.report_store.query(start=start, end=end, event_type=event_type,
                                        camera_name=camera_name, limit=5000)
        self.table.setRowCount(0)
        for row_data in rows:
            (ts, ev_type, camera, person_name, phone, employee_id,
             region_number, region_name, person_count, image_path) = row_data

            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(ts or ""))
            self.table.setItem(r, 1, QTableWidgetItem(EVENT_TYPE_LABELS_FA.get(ev_type, ev_type)))
            self.table.setItem(r, 2, QTableWidgetItem(camera or ""))
            self.table.setItem(r, 3, QTableWidgetItem(person_name or ""))
            self.table.setItem(r, 4, QTableWidgetItem(phone or ""))
            self.table.setItem(r, 5, QTableWidgetItem(employee_id or ""))
            region_label = ""
            if region_number:
                region_label = f"شماره {region_number}" + (f" / {region_name}" if region_name else "")
            self.table.setItem(r, 6, QTableWidgetItem(region_label))
            self.table.setItem(r, 7, QTableWidgetItem("" if person_count is None else str(person_count)))

            img_item = QTableWidgetItem("")
            if image_path:
                pixmap = QPixmap(image_path)
                if not pixmap.isNull():
                    img_item.setIcon(QIcon(pixmap.scaledToHeight(48, Qt.TransformationMode.SmoothTransformation)))
                img_item.setToolTip(image_path)
            self.table.setItem(r, 8, img_item)

        self.summary_label.setText(f"{self.table.rowCount()} ردیف یافت شد.")

    def export_csv(self):
        default_name = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "ذخیره‌ی خروجی گزارش", default_name, "CSV (*.csv)")
        if not path:
            return
        start, end, event_type, camera_name = self._current_filters()
        try:
            self.report_store.export_csv(path, start=start, end=end, event_type=event_type,
                                          camera_name=camera_name)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"خروجی گرفتن ناموفق بود:\n{e}")
            return
        QMessageBox.information(self, "انجام شد", f"فایل خروجی ذخیره شد:\n{path}")

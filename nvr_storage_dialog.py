# -*- coding: utf-8 -*-
"""دیالوگ «هارد و ضبط‌های NVR»: تب اول وضعیت هارددیسک‌ها را نشان می‌دهد، تب
دوم برای یک کانال و بازه‌ی تاریخ مشخص، فهرست بازه‌های زمانی‌ای که واقعاً
روی هارد NVR ضبط شده‌اند را برمی‌گرداند (رجوع کنید به nvr_storage_api.py).

هر دو پرس‌وجو روی یک QThread جدا اجرا می‌شوند (نه ترد UI) چون درخواست‌های
HTTP به NVR - به‌خصوص گفت‌وگوی چندمرحله‌ای جست‌وجوی Dahua - می‌تواند چند
ثانیه طول بکشد و در غیر این صورت کل برنامه در همان مدت فریز می‌شد."""

from datetime import datetime

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDateEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QTabWidget, QWidget,
)

import nvr_storage_api


class _StorageWorker(QThread):
    done = pyqtSignal(object)  # list یا None

    def __init__(self, nvr, parent=None):
        super().__init__(parent)
        self.nvr = nvr

    def run(self):
        onvif_port = self.nvr.get("onvif_port")
        ports = [int(onvif_port)] if onvif_port else None
        result = nvr_storage_api.get_storage_status(
            self.nvr["ip"], self.nvr.get("user", ""), self.nvr.get("pass", ""), ports=ports,
        )
        self.done.emit(result)


class _SearchWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, nvr, channel, start_dt, end_dt, parent=None):
        super().__init__(parent)
        self.nvr = nvr
        self.channel = channel
        self.start_dt = start_dt
        self.end_dt = end_dt

    def run(self):
        onvif_port = self.nvr.get("onvif_port")
        ports = [int(onvif_port)] if onvif_port else None
        result = nvr_storage_api.search_recordings(
            self.nvr["ip"], self.nvr.get("user", ""), self.nvr.get("pass", ""),
            self.channel, self.start_dt, self.end_dt, ports=ports,
        )
        self.done.emit(result)


class NVRStorageDialog(QDialog):
    def __init__(self, nvr: dict, channels: list, parent=None):
        """``channels``: لیستی از تاپل‌های (شماره‌ی کانال، نام) همین NVR
        (از ``camera_store.cameras_for_nvr``) برای پرکردن کمبوی کانال."""
        super().__init__(parent)
        self.nvr = nvr
        self.setWindowTitle(f"هارد و ضبط‌ها - {nvr.get('name', '')}")
        self.resize(760, 520)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._storage_worker = None
        self._search_worker = None

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ------------------------------------------------------------ هارد -
        storage_tab = QWidget()
        storage_layout = QVBoxLayout(storage_tab)
        storage_btn_row = QHBoxLayout()
        self.storage_refresh_btn = QPushButton("🔄 بررسی وضعیت هارد")
        self.storage_refresh_btn.clicked.connect(self.refresh_storage)
        storage_btn_row.addWidget(self.storage_refresh_btn)
        storage_btn_row.addStretch()
        storage_layout.addLayout(storage_btn_row)

        self.storage_table = QTableWidget(0, 5)
        self.storage_table.setHorizontalHeaderLabels(
            ["شناسه", "وضعیت", "نوع", "ظرفیت (GB)", "فضای آزاد (GB)"]
        )
        self.storage_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.storage_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        storage_layout.addWidget(self.storage_table)
        self.storage_status_label = QLabel("برای مشاهده‌ی وضعیت هارد، دکمه‌ی بالا را بزنید.")
        storage_layout.addWidget(self.storage_status_label)
        tabs.addTab(storage_tab, "وضعیت هارد")

        # ------------------------------------------------------ جست‌وجوی ضبط -
        search_tab = QWidget()
        search_layout = QVBoxLayout(search_tab)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("کانال:"))
        self.channel_combo = QComboBox()
        for ch_num, ch_name in channels:
            label = f"{ch_num} - {ch_name}" if ch_name else str(ch_num)
            self.channel_combo.addItem(label, ch_num)
        filter_row.addWidget(self.channel_combo)

        filter_row.addWidget(QLabel("از تاریخ:"))
        self.from_date = QDateEdit(calendarPopup=True)
        self.from_date.setDate(QDate.currentDate())
        filter_row.addWidget(self.from_date)

        filter_row.addWidget(QLabel("تا تاریخ:"))
        self.to_date = QDateEdit(calendarPopup=True)
        self.to_date.setDate(QDate.currentDate())
        filter_row.addWidget(self.to_date)

        self.search_btn = QPushButton("🔍 جست‌وجوی ضبط‌ها")
        self.search_btn.clicked.connect(self.run_search)
        filter_row.addWidget(self.search_btn)
        filter_row.addStretch()
        search_layout.addLayout(filter_row)

        self.search_table = QTableWidget(0, 3)
        self.search_table.setHorizontalHeaderLabels(["شروع", "پایان", "نوع ضبط"])
        self.search_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.search_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        search_layout.addWidget(self.search_table)
        self.search_status_label = QLabel(
            "یک کانال و بازه‌ی تاریخ انتخاب کنید و «جست‌وجوی ضبط‌ها» را بزنید."
        )
        search_layout.addWidget(self.search_status_label)
        tabs.addTab(search_tab, "جست‌وجوی ضبط‌ها")

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        if not channels:
            self.channel_combo.setEnabled(False)
            self.search_btn.setEnabled(False)
            self.search_status_label.setText(
                "هیچ کانالی برای این NVR ثبت نشده - ابتدا کانال‌ها را از «افزودن NVR»/«بازخوانی کانال‌ها» اضافه کنید."
            )

    # ------------------------------------------------------------- هارد -

    def refresh_storage(self):
        self.storage_refresh_btn.setEnabled(False)
        self.storage_status_label.setText("در حال بررسی...")
        self.storage_table.setRowCount(0)
        self._storage_worker = _StorageWorker(self.nvr, self)
        self._storage_worker.done.connect(self._on_storage_done)
        self._storage_worker.start()

    def _on_storage_done(self, result):
        self.storage_refresh_btn.setEnabled(True)
        if result is None:
            self.storage_status_label.setText(
                "اطلاعات هارد در دسترس نبود (پورت وب دستگاه بسته است، رمز اشتباه "
                "است، یا این برند از این API پشتیبانی نمی‌کند)."
            )
            return
        if not result:
            self.storage_status_label.setText("دستگاه پاسخ داد ولی هیچ هارد/فضای ذخیره‌ای گزارش نشد.")
            return
        for drive in result:
            r = self.storage_table.rowCount()
            self.storage_table.insertRow(r)
            self.storage_table.setItem(r, 0, QTableWidgetItem(str(drive.get("id", ""))))
            self.storage_table.setItem(r, 1, QTableWidgetItem(str(drive.get("status", ""))))
            self.storage_table.setItem(r, 2, QTableWidgetItem(str(drive.get("property", ""))))
            cap, free = drive.get("capacity_gb"), drive.get("free_gb")
            self.storage_table.setItem(r, 3, QTableWidgetItem("" if cap is None else f"{cap:g}"))
            self.storage_table.setItem(r, 4, QTableWidgetItem("" if free is None else f"{free:g}"))
        self.storage_status_label.setText(f"{len(result)} هارد/فضای ذخیره یافت شد.")

    # ------------------------------------------------------------- جست‌وجو -

    def run_search(self):
        channel = self.channel_combo.currentData()
        if channel is None:
            return
        start_dt = datetime.combine(self.from_date.date().toPyDate(), datetime.min.time())
        end_dt = datetime.combine(self.to_date.date().toPyDate(), datetime.max.time().replace(microsecond=0))
        if end_dt < start_dt:
            QMessageBox.warning(self, "بازه‌ی نامعتبر", "تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.")
            return

        self.search_btn.setEnabled(False)
        self.search_status_label.setText("در حال جست‌وجو روی NVR...")
        self.search_table.setRowCount(0)
        self._search_worker = _SearchWorker(self.nvr, channel, start_dt, end_dt, self)
        self._search_worker.done.connect(self._on_search_done)
        self._search_worker.start()

    def _on_search_done(self, result):
        self.search_btn.setEnabled(True)
        if result is None:
            self.search_status_label.setText(
                "جست‌وجو ممکن نشد (پورت وب دستگاه بسته است، رمز اشتباه است، یا این "
                "برند از این API پشتیبانی نمی‌کند)."
            )
            return
        if not result:
            self.search_status_label.setText("در این بازه‌ی زمانی برای این کانال هیچ ضبطی روی هارد پیدا نشد.")
            return
        type_fa = {"video": "ویدیو", "dav": "ویدیو (dav)", "jpg": "تصویر"}
        for item in result:
            r = self.search_table.rowCount()
            self.search_table.insertRow(r)
            self.search_table.setItem(r, 0, QTableWidgetItem(item.get("start", "")))
            self.search_table.setItem(r, 1, QTableWidgetItem(item.get("end", "")))
            self.search_table.setItem(
                r, 2, QTableWidgetItem(type_fa.get(item.get("type", ""), item.get("type", "")))
            )
        self.search_status_label.setText(f"{len(result)} بازه‌ی ضبط‌شده یافت شد.")

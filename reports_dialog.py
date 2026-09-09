# -*- coding: utf-8 -*-
"""دیالوگ «گزارش‌ها»: جست‌وجو در تاریخچه‌ی ذخیره‌شده‌ی report_store.py
(شمارش نفرات، ورود به محدوده، چهره‌ی شناخته‌شده/تعریف‌نشده) بر اساس بازه‌ی
تاریخ، دوربین و نوع رویداد، به‌همراه خروجی CSV/Excel و دکمه‌ی «پخش ویدیوی
NVR» برای دیدن ویدیوی واقعیِ همان لحظه از روی خودِ NVR (رجوع کنید به
nvr_playback_dialog.py)."""

from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDateEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox,
)

from report_store import EVENT_TYPE_LABELS_FA
from nvr_playback_dialog import NVRPlaybackDialog

# چند ثانیه قبل/بعد از لحظه‌ی ثبت‌شده‌ی هر رویداد که برای پخش بازبینی از NVR
# درخواست می‌شود - چون ساعت رویداد دقیقاً لحظه‌ی تشخیص در برنامه است، نه
# شروع/پایان یک بازه؛ کمی حاشیه لازم است تا لحظه‌ی واقعی وسط ویدیوی پخش‌شده
# بیفتد (نه دقیقاً روی مرز اول/آخر آن).
PLAYBACK_MARGIN_BEFORE = timedelta(seconds=10)
PLAYBACK_MARGIN_AFTER = timedelta(seconds=30)


class ReportsDialog(QDialog):
    def __init__(self, report_store, camera_store=None, parent=None):
        super().__init__(parent)
        self.report_store = report_store
        self.camera_store = camera_store
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
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "تاریخ و ساعت", "نوع رویداد", "دوربین", "نام فرد", "تلفن",
            "شماره کارمندی", "محدوده", "تعداد نفرات", "تصویر", "ویدیوی NVR",
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
             region_number, region_name, person_count, image_path,
             nvr_id, channel) = row_data

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

            # رفع درخواست «دکمه‌ی پخش ویدیو ظاهر نمی‌شود»: قبلاً این دکمه فقط
            # وقتی ساخته می‌شد که خودِ ردیف گزارش از قبل nvr_id/channel
            # ذخیره‌شده داشت؛ برای ردیف‌های قدیمی‌تر (قبل از این ویژگی) یا
            # هر رویدادی که این دو مقدار به هر دلیلی هنگام ثبت خالی مانده
            # بود، اصلاً دکمه‌ای نشان داده نمی‌شد. حالا تا وقتی camera_store
            # در دسترس است، دکمه همیشه ساخته می‌شود؛ خودِ تلاش برای پخش
            # (_open_nvr_playback) در لحظه‌ی کلیک هم از nvr_id/channel ذخیره‌
            # شده در ردیف استفاده می‌کند و هم - اگر آن دو خالی بودند - با
            # نام دوربین در لیست فعلی دوربین‌ها/NVRها می‌گردد
            # (camera_store.find_nvr_channel_by_camera_name)؛ فقط اگر واقعاً
            # هیچ NVR ای برای این دوربین پیدا نشود، با کلیک روی دکمه پیام
            # روشن نمایش داده می‌شود (نه اینکه از همان اول دکمه پنهان بماند).
            if self.camera_store is not None:
                play_btn = QPushButton("▶ پخش از NVR")
                play_btn.clicked.connect(
                    lambda _checked=False, _ts=ts, _nvr_id=nvr_id, _ch=channel, _cam=camera:
                    self._open_nvr_playback(_ts, _nvr_id, _ch, _cam)
                )
                self.table.setCellWidget(r, 9, play_btn)
            else:
                self.table.setItem(r, 9, QTableWidgetItem(""))

        self.summary_label.setText(f"{self.table.rowCount()} ردیف یافت شد.")

    def _open_nvr_playback(self, ts, nvr_id, channel, camera_name):
        # اگر این ردیف گزارش از قبل nvr_id/channel نداشت (رویداد قدیمی یا
        # ثبت‌شده قبل از این ویژگی)، با نام دوربین در لیست فعلی دوربین‌ها/
        # NVRها می‌گردیم - رجوع کنید به توضیح بالای متد run_search.
        if not nvr_id or not channel:
            fallback_nvr_id, fallback_channel = self.camera_store.find_nvr_channel_by_camera_name(camera_name)
            nvr_id = nvr_id or fallback_nvr_id
            channel = channel or fallback_channel

        if not nvr_id or not channel:
            QMessageBox.warning(
                self, "خطا",
                "برای این رویداد اطلاعات NVR/کانال ثبت نشده و دوربینی با همین نام هم "
                "زیرمجموعه‌ی هیچ NVR ای در لیست «دوربین‌ها و NVRهای من» پیدا نشد؛ پخش "
                "بازبینی از روی NVR برای این ردیف ممکن نیست (احتمالاً این دوربین مستقل "
                "است یا نام آن تغییر کرده/حذف شده)."
            )
            return

        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            QMessageBox.warning(
                self, "خطا",
                "این NVR دیگر در لیست «دوربین‌ها و NVRهای من» وجود ندارد (احتمالاً حذف شده)."
            )
            return
        try:
            event_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            QMessageBox.warning(self, "خطا", "ساعت این رویداد نامعتبر است.")
            return
        start_dt = event_dt - PLAYBACK_MARGIN_BEFORE
        end_dt = event_dt + PLAYBACK_MARGIN_AFTER
        dialog = NVRPlaybackDialog(nvr, channel, start_dt, end_dt, camera_label=camera_name, parent=self)
        dialog.exec()

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

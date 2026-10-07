"""پنجره‌ی گالری تصاویر رویدادها (1.0.0).

نمایش تصاویر ثبت‌شده‌ی رویدادها (چهره، پلاک، ...) با قابلیت:
- فیلتر بر اساس نوع رویداد، تاریخ، دوربین
- مرتب‌سازی بر اساس تاریخ/ساعت
- نمایش جزئیات هر تصویر (دوربین، زمان، نوع رویداد، ...)
- ذخیره‌ی تصویر با کیفیت بالا
"""

import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QSplitter, QTextEdit, QFileDialog,
    QMessageBox, QDateEdit, QGroupBox
)
from PyQt6.QtCore import Qt, QDate, QSize
from PyQt6.QtGui import QPixmap, QIcon


# نگاشت نوع رویداد به نام فارسی
EVENT_TYPE_FA = {
    "face_known": "چهره‌ی شناخته‌شده",
    "face_unknown": "چهره‌ی ناشناس",
    "plate": "پلاک",
    "person": "شخص",
    "region_alert": "هشدار محدوده",
    "fire": "حریق",
    "smoke": "دود",
}


def _fa_event_type(et):
    return EVENT_TYPE_FA.get(et, et or "نامشخص")


class GalleryDialog(QDialog):
    """پنجره‌ی گالری تصاویر رویدادها."""

    def __init__(self, report_store, parent=None):
        super().__init__(parent)
        self.report_store = report_store
        self._events = []  # لیست رویدادهای بارگذاری‌شده
        self._current_event = None

        self.setWindowTitle("🖼 گالری تصاویر رویدادها")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(1000, 700)

        self._build_ui()
        self._load_events()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        # --- نوار فیلتر ---
        filter_box = QGroupBox("فیلتر")
        flay = QHBoxLayout(filter_box)

        flay.addWidget(QLabel("نوع رویداد:"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("همه", "")
        for et, fa in EVENT_TYPE_FA.items():
            self.type_combo.addItem(fa, et)
        self.type_combo.currentIndexChanged.connect(self._apply_filter)
        flay.addWidget(self.type_combo)

        flay.addWidget(QLabel("از تاریخ:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_from.dateChanged.connect(self._apply_filter)
        flay.addWidget(self.date_from)

        flay.addWidget(QLabel("تا تاریخ:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.dateChanged.connect(self._apply_filter)
        flay.addWidget(self.date_to)

        flay.addWidget(QLabel("دوربین:"))
        self.cam_combo = QComboBox()
        self.cam_combo.addItem("همه", "")
        self.cam_combo.currentIndexChanged.connect(self._apply_filter)
        flay.addWidget(self.cam_combo)

        flay.addStretch()
        lay.addWidget(filter_box)

        # --- اسپلیتر: لیست تصاویر | پیش‌نمایش + جزئیات ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # لیست تصاویر (سمت راست در RTL)
        self.img_list = QListWidget()
        self.img_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.img_list.setIconSize(QSize(120, 90))
        self.img_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.img_list.setSpacing(8)
        self.img_list.itemClicked.connect(self._on_item_selected)
        self.img_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        splitter.addWidget(self.img_list)

        # پنل پیش‌نمایش و جزئیات
        right = QVBoxLayout()
        right_widget = QLabel()  # placeholder for layout
        right_widget.setLayout(right)

        self.preview_label = QLabel("تصویری انتخاب نشده است")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(300, 250)
        self.preview_label.setStyleSheet(
            "border: 1px solid #555; background: #1a1a1a;")
        right.addWidget(self.preview_label)

        right.addWidget(QLabel("<b>جزئیات:</b>"))
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(150)
        right.addWidget(self.detail_text)

        btn_lay = QHBoxLayout()
        self.save_btn = QPushButton("💾 ذخیره با کیفیت بالا")
        self.save_btn.clicked.connect(self._save_current)
        self.save_btn.setEnabled(False)
        btn_lay.addWidget(self.save_btn)
        btn_lay.addStretch()
        right.addLayout(btn_lay)

        splitter.addWidget(right_widget)
        splitter.setSizes([650, 350])
        lay.addWidget(splitter, 1)

        # دکمه‌ی بستن
        close_lay = QHBoxLayout()
        close_lay.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        close_lay.addWidget(close_btn)
        lay.addLayout(close_lay)

    def _load_events(self):
        """بارگذاری رویدادهای دارای تصویر از دیتابیس."""
        try:
            # همه‌ی رویدادها را می‌گیریم و آن‌هایی که تصویر دارند نگه می‌داریم
            events = self.report_store.query(limit=5000)
            self._events = [e for e in events if e.get("image_path")]
            # دوربین‌ها را برای فیلتر جمع می‌کنیم
            cams = sorted(set(e.get("camera_name") or "" for e in self._events
                              if e.get("camera_name")))
            self.cam_combo.clear()
            self.cam_combo.addItem("همه", "")
            for c in cams:
                self.cam_combo.addItem(c, c)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"بارگذاری رویدادها ناموفق بود:\n{e}")
            self._events = []
        self._apply_filter()

    def _apply_filter(self):
        """اعمال فیلترها و نمایش تصاویر."""
        et_filter = self.type_combo.currentData()
        cam_filter = self.cam_combo.currentData()
        d_from = self.date_from.date().toString("yyyy-MM-dd")
        d_to = self.date_to.date().toString("yyyy-MM-dd")

        self.img_list.clear()
        count = 0
        for ev in self._events:
            # فیلتر نوع رویداد
            if et_filter and ev.get("event_type") != et_filter:
                continue
            # فیلتر دوربین
            if cam_filter and ev.get("camera_name") != cam_filter:
                continue
            # فیلتر تاریخ (ts به صورت رشته‌ی ISO است)
            ts = str(ev.get("ts") or "")
            ev_date = ts[:10]
            if ev_date < d_from or ev_date > d_to:
                continue
            # تصویر وجود دارد؟
            img_path = ev.get("image_path") or ""
            if not os.path.isfile(img_path):
                continue

            item = QListWidgetItem()
            # آیکون تصویر
            pix = QPixmap(img_path)
            if not pix.isNull():
                item.setIcon(QIcon(pix.scaled(
                    120, 90, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            # متن: نوع + ساعت
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M:%S")
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                time_str, date_str = "", ts[:10]
            item.setText(f"{_fa_event_type(ev.get('event_type'))}\n{date_str} {time_str}")
            item.setData(Qt.ItemDataRole.UserRole, ev)
            self.img_list.addItem(item)
            count += 1

        self.setWindowTitle(f"🖼 گالری تصاویر رویدادها ({count} تصویر)")

    def _on_item_selected(self, item):
        """نمایش پیش‌نمایش و جزئیات."""
        ev = item.data(Qt.ItemDataRole.UserRole)
        if not ev:
            return
        self._current_event = ev
        img_path = ev.get("image_path") or ""

        # پیش‌نمایش
        pix = QPixmap(img_path)
        if not pix.isNull():
            self.preview_label.setPixmap(pix.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview_label.setText("تصویر قابل نمایش نیست")

        # جزئیات
        lines = []
        lines.append(f"<b>نوع رویداد:</b> {_fa_event_type(ev.get('event_type'))}")
        if ev.get("camera_name"):
            lines.append(f"<b>دوربین:</b> {ev.get('camera_name')}")
        if ev.get("ts"):
            lines.append(f"<b>زمان:</b> {ev.get('ts')}")
        if ev.get("person_name"):
            lines.append(f"<b>شخص:</b> {ev.get('person_name')}")
        if ev.get("region_name"):
            lines.append(f"<b>محدوده:</b> {ev.get('region_name')}")
        if ev.get("detail"):
            lines.append(f"<b>توضیح:</b> {ev.get('detail')}")
        lines.append(f"<b>مسیر فایل:</b> {img_path}")
        self.detail_text.setHtml("<br>".join(lines))
        self.save_btn.setEnabled(True)

    def _on_item_double_clicked(self, item):
        """دبل‌کلیک: باز کردن تصویر در نمایشگر پیش‌فرض."""
        ev = item.data(Qt.ItemDataRole.UserRole)
        if not ev:
            return
        img_path = ev.get("image_path") or ""
        if os.path.isfile(img_path):
            try:
                from PyQt6.QtCore import QUrl
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(img_path))
            except Exception as e:
                QMessageBox.warning(self, "خطا", f"باز کردن تصویر ناموفق بود:\n{e}")

    def _save_current(self):
        """ذخیره‌ی تصویر جاری با کیفیت بالا."""
        if not self._current_event:
            return
        src = self._current_event.get("image_path") or ""
        if not os.path.isfile(src):
            QMessageBox.warning(self, "خطا", "فایل تصویر پیدا نشد.")
            return
        # نام پیشنهادی: نوع_رویداد_تاریخ_ساعت
        ev = self._current_event
        ts = str(ev.get("ts") or "").replace(":", "-").replace(" ", "_")[:19]
        et = ev.get("event_type") or "event"
        _, ext = os.path.splitext(src)
        suggested = f"{et}_{ts}{ext or '.jpg'}"
        dst, _ = QFileDialog.getSaveFileName(
            self, "ذخیره‌ی تصویر", suggested,
            "تصاویر (*.jpg *.jpeg *.png *.bmp)")
        if not dst:
            return
        try:
            import shutil
            shutil.copy2(src, dst)
            QMessageBox.information(self, "موفق", f"تصویر ذخیره شد:\n{dst}")
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره ناموفق بود:\n{e}")

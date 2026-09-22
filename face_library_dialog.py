import cv2
import numpy as np

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QImageReader
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QMessageBox, QDialogButtonBox,
    QHeaderView, QLabel, QFileDialog, QComboBox
)

from image_viewer_dialog import ImageViewerDialog  # 👁 دیدن تصویر (دابل‌کلیک روی عکس)


def _work_group_combo(work_groups=(), current=""):
    """کامبوباکس قابل‌ویرایش «گروه کاری»: گروه‌های موجود پیشنهاد می‌شوند و
    کاربر می‌تواند گروه جدید هم تایپ کند."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setMinimumWidth(160)
    seen = []
    for g in work_groups:
        g = (g or "").strip()
        if g and g not in seen:
            seen.append(g)
    combo.addItems(seen)
    if current:
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(current)
    elif seen:
        combo.setCurrentIndex(-1)
        combo.setEditText("")
    return combo


def _imread_unicode(path):
    """مثل cv2.imread عمل می‌کند اما از مسیرهای فارسی/یونیکد هم پشتیبانی
    می‌کند (cv2.imread در ویندوز مسیرهای غیر-ASCII را درست نمی‌خواند)."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


class AddFaceFromImageDialog(QDialog):
    """افزودن چهره از یک فایل تصویر (به‌جای تصویر زنده‌ی دوربین): کاربر یک
    تصویر از سیستم خودش انتخاب می‌کند، پیش‌نمایش آن را می‌بیند و مشخصات فرد
    را وارد می‌کند."""

    def __init__(self, parent=None, work_groups=()):
        super().__init__(parent)
        self.setWindowTitle("افزودن چهره از تصویر")
        self.setMinimumWidth(360)
        self.selected_frame = None  # numpy BGR frame برای face_engine
        self.selected_path = None

        self.preview_label = QLabel("تصویری انتخاب نشده")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedSize(240, 240)
        self.preview_label.setStyleSheet(
            "background-color: #1e1e1e; color: #aaaaaa; border-radius: 8px;"
        )

        self.choose_btn = QPushButton("انتخاب تصویر از سیستم...")
        self.choose_btn.clicked.connect(self.choose_image)

        self.name_input = QLineEdit()
        self.phone_input = QLineEdit()
        self.employee_id_input = QLineEdit()
        self.work_group_input = _work_group_combo(work_groups)
        self.note_input = QTextEdit()
        self.note_input.setFixedHeight(60)

        form = QFormLayout()
        form.addRow("نام و نام‌خانوادگی:", self.name_input)
        form.addRow("شماره تلفن:", self.phone_input)
        form.addRow("شماره کارمندی:", self.employee_id_input)
        form.addRow("گروه کاری:", self.work_group_input)
        form.addRow("توضیحات:", self.note_input)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.handle_accept)
        self.buttons.rejected.connect(self.reject)

        preview_row = QHBoxLayout()
        preview_row.addStretch()
        preview_col = QVBoxLayout()
        preview_col.addWidget(self.preview_label)
        preview_col.addWidget(self.choose_btn)
        preview_row.addLayout(preview_col)
        preview_row.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(preview_row)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "انتخاب تصویر چهره", "",
            "تصاویر (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return

        frame = _imread_unicode(path)
        if frame is None:
            QMessageBox.warning(self, "خطا", "این فایل به‌عنوان تصویر قابل خواندن نیست.")
            return

        self.selected_frame = frame
        self.selected_path = path

        # پیش‌نمایش تصویر برای خود کاربر (از طریق QPixmap که مسیرهای یونیکد را
        # هم به‌درستی می‌خواند).
        pix = QPixmap(path)
        if not pix.isNull():
            self.preview_label.setPixmap(
                pix.scaled(
                    self.preview_label.width(), self.preview_label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
        self.status_label.setText("")

    def handle_accept(self):
        if self.selected_frame is None:
            QMessageBox.warning(self, "خطا", "لطفاً ابتدا یک تصویر انتخاب کنید.")
            return
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "خطا", "لطفاً نام فرد را وارد کنید.")
            return
        self.accept()

    def get_data(self):
        return {
            "name": self.name_input.text().strip(),
            "phone": self.phone_input.text().strip(),
            "employee_id": self.employee_id_input.text().strip(),
            "work_group": self.work_group_input.currentText().strip(),
            "note": self.note_input.toPlainText().strip(),
            "frame": self.selected_frame,
        }


class PersonFormDialog(QDialog):
    """فرم وارد کردن مشخصات فرد: نام، شماره تلفن، شماره کارمندی، گروه کاری، توضیحات."""

    def __init__(self, parent=None, existing=None, work_groups=()):
        super().__init__(parent)
        self.setWindowTitle("مشخصات فرد")
        self.setMinimumWidth(320)

        self.name_input = QLineEdit()
        self.phone_input = QLineEdit()
        self.employee_id_input = QLineEdit()
        self.work_group_input = _work_group_combo(
            work_groups, (existing or {}).get("work_group", ""))
        self.note_input = QTextEdit()
        self.note_input.setFixedHeight(60)

        if existing:
            self.name_input.setText(existing.get("name", ""))
            self.phone_input.setText(existing.get("phone", ""))
            self.employee_id_input.setText(existing.get("employee_id", ""))
            self.note_input.setPlainText(existing.get("note", ""))

        form = QFormLayout()
        form.addRow("نام و نام‌خانوادگی:", self.name_input)
        form.addRow("شماره تلفن:", self.phone_input)
        form.addRow("شماره کارمندی:", self.employee_id_input)
        form.addRow("گروه کاری:", self.work_group_input)
        form.addRow("توضیحات:", self.note_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.handle_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def handle_accept(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "خطا", "لطفاً نام فرد را وارد کنید.")
            return
        self.accept()

    def get_data(self):
        return {
            "name": self.name_input.text().strip(),
            "phone": self.phone_input.text().strip(),
            "employee_id": self.employee_id_input.text().strip(),
            "work_group": self.work_group_input.currentText().strip(),
            "note": self.note_input.toPlainText().strip(),
        }


class FaceLibraryPage(QWidget):
    """مدیریت Face Library: افزودن از تصویر زنده، ویرایش، حذف - به‌عنوان یک
    صفحه‌ی جداگانه داخل QStackedWidget پنجره‌ی اصلی (قابل دسترسی از هدر
    بالای برنامه)، نه یک دیالوگ مستقل."""

    COLUMNS = ["عکس", "نام", "گروه کاری", "شماره تلفن", "شماره کارمندی", "توضیحات"]

    def __init__(self, face_engine, get_current_frame_callback, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.get_current_frame_callback = get_current_frame_callback

        title = QLabel("👤 Face Library - مدیریت چهره‌ها")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")

        # فیلتر دسته‌بندی بر اساس گروه کاری
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("🏷 فیلتر گروه کاری:"))
        self.group_filter = QComboBox()
        self.group_filter.setMinimumWidth(180)
        self.group_filter.currentIndexChanged.connect(self.refresh_table)
        filter_row.addWidget(self.group_filter)
        filter_row.addStretch()

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # دابل‌کلیک روی ستون عکس → باز شدن «👁 دیدن تصویر» (تصویر اصلی، نه بندانگشتی)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        add_btn = QPushButton("افزودن چهره از تصویر زنده دوربین فعال")
        add_btn.clicked.connect(self.add_from_live)
        add_image_btn = QPushButton("افزودن چهره از تصویر...")
        add_image_btn.clicked.connect(self.add_from_image)
        edit_btn = QPushButton("ویرایش مشخصات")
        edit_btn.clicked.connect(self.edit_selected)
        delete_btn = QPushButton("حذف")
        delete_btn.clicked.connect(self.delete_selected)

        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(add_image_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch()

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addLayout(filter_row)
        layout.addWidget(self.table, 1)
        layout.addLayout(btn_row)
        self.setLayout(layout)

        self.refresh_table()

    def _reload_group_filter(self):
        cur = self.group_filter.currentData()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem("همه‌ی گروه‌ها", None)
        self.group_filter.addItem("— بدون گروه —", "")
        for g in self.face_engine.list_work_groups():
            self.group_filter.addItem(g, g)
        if cur is not None:
            idx = self.group_filter.findData(cur)
            if idx >= 0:
                self.group_filter.setCurrentIndex(idx)
        self.group_filter.blockSignals(False)

    def refresh(self):
        """هر بار که صفحه از هدر باز می‌شود صدا زده می‌شود تا جدول تازه باشد."""
        self.refresh_table()

    def refresh_table(self):
        self._reload_group_filter()
        wanted = self.group_filter.currentData()
        people = self.face_engine.list_people()
        self.table.setRowCount(0)
        for person in people:
            pg = (person.get("work_group") or "").strip()
            if wanted is None:
                pass
            elif wanted == "":
                if pg:
                    continue
            elif pg != wanted:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)

            photo_label = QLabel()
            # بارگذاری مستقیم در اندازه‌ی هدف (48×48) با QImageReader برای
            # کاهش مصرف RAM؛ تصویر کامل decode نمی‌شود.
            reader = QImageReader(person.get("photo", ""))
            reader.setAutoTransform(True)
            reader.setScaledSize(QSize(48, 48))
            pix = QPixmap.fromImageReader(reader)
            if not pix.isNull():
                photo_label.setPixmap(pix)
            # مسیر تصویر اصلی را در ویجت نگه می‌داریم تا دابل‌کلیک آن را باز کند.
            photo_label.setProperty("photo_path", person.get("photo", "") or "")
            self.table.setCellWidget(row, 0, photo_label)

            self.table.setItem(row, 1, QTableWidgetItem(person.get("name", "")))
            self.table.setItem(row, 2, QTableWidgetItem(pg or "—"))
            self.table.setItem(row, 3, QTableWidgetItem(person.get("phone", "")))
            self.table.setItem(row, 4, QTableWidgetItem(person.get("employee_id", "")))
            self.table.setItem(row, 5, QTableWidgetItem(person.get("note", "")))
            # شناسه داخلی را در آیتم مخفی نگه می‌داریم تا هنگام ویرایش/حذف قابل بازیابی باشد.
            self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, person.get("id"))

    def _on_cell_double_clicked(self, row, column):
        """دابل‌کلیک روی ستون عکس → باز شدن دیالوگ «👁 دیدن تصویر».

        فقط ستون صفر (عکس) واکنش نشان می‌دهد؛ ستون‌های دیگر هیچ کاری
        نمی‌کنند تا رفتار ویرایش/انتخاب موجود تغییر نکند."""
        if column != 0:
            return
        widget = self.table.cellWidget(row, 0)
        path = widget.property("photo_path") if widget is not None else None
        if not path:
            item = self.table.item(row, 1)
            person_id = item.data(Qt.ItemDataRole.UserRole) if item else None
            if person_id is not None:
                person = self.face_engine.get_person(person_id) or {}
                path = person.get("photo", "")
        if path:
            ImageViewerDialog(path, parent=self).exec()
        # مسیر خالی/ناموجود: خود دیالوگ پیام مناسب نشان می‌دهد؛ هیچ‌وقت کرش نمی‌دهد.

    def _selected_person_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def add_from_live(self):
        frame = self.get_current_frame_callback()
        if frame is None:
            QMessageBox.warning(self, "خطا", "ابتدا یک دوربین را متصل و انتخاب کنید تا از تصویر زنده آن چهره ثبت شود.")
            return

        form = PersonFormDialog(self, work_groups=self.face_engine.list_work_groups())
        if form.exec() == QDialog.DialogCode.Accepted:
            data = form.get_data()
            person = self.face_engine.register_face(
                name=data["name"],
                image_frame=frame,
                phone=data["phone"],
                employee_id=data["employee_id"],
                note=data["note"],
                work_group=data["work_group"],
            )
            if person:
                QMessageBox.information(self, "موفقیت", f"چهره «{data['name']}» با موفقیت در Face Library ثبت شد.")
                self.refresh_table()
            else:
                QMessageBox.warning(self, "خطا", "چهره‌ای در تصویر تشخیص داده نشد. لطفاً نزدیک‌تر و روبه‌روی دوربین قرار بگیرید.")

    def add_from_image(self):
        dialog = AddFaceFromImageDialog(self, work_groups=self.face_engine.list_work_groups())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            person = self.face_engine.register_face(
                name=data["name"],
                image_frame=data["frame"],
                phone=data["phone"],
                employee_id=data["employee_id"],
                note=data["note"],
                work_group=data["work_group"],
            )
            if person:
                QMessageBox.information(self, "موفقیت", f"چهره «{data['name']}» با موفقیت در Face Library ثبت شد.")
                self.refresh_table()
            else:
                QMessageBox.warning(
                    self, "خطا",
                    "چهره‌ای در تصویر انتخاب‌شده تشخیص داده نشد. لطفاً تصویری واضح و روبه‌رو انتخاب کنید."
                )

    def edit_selected(self):
        person_id = self._selected_person_id()
        if not person_id:
            QMessageBox.warning(self, "خطا", "لطفاً یک فرد را از لیست انتخاب کنید.")
            return
        existing = self.face_engine.get_person(person_id)
        form = PersonFormDialog(
            self, existing=existing,
            work_groups=self.face_engine.list_work_groups())
        if form.exec() == QDialog.DialogCode.Accepted:
            self.face_engine.update_person(person_id, **form.get_data())
            self.refresh_table()

    def delete_selected(self):
        person_id = self._selected_person_id()
        if not person_id:
            QMessageBox.warning(self, "خطا", "لطفاً یک فرد را از لیست انتخاب کنید.")
            return
        confirm = QMessageBox.question(
            self, "تأیید حذف", "آیا از حذف این فرد از Face Library مطمئن هستید؟"
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.face_engine.delete_person(person_id)
            self.refresh_table()


# نام قدیمی برای سازگاری با کدی که هنوز دیالوگ را ایمپورت می‌کند.
FaceLibraryDialog = FaceLibraryPage

import cv2
import numpy as np

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QMessageBox, QDialogButtonBox,
    QHeaderView, QLabel, QFileDialog
)


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

    def __init__(self, parent=None):
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
        self.note_input = QTextEdit()
        self.note_input.setFixedHeight(60)

        form = QFormLayout()
        form.addRow("نام و نام‌خانوادگی:", self.name_input)
        form.addRow("شماره تلفن:", self.phone_input)
        form.addRow("شماره کارمندی:", self.employee_id_input)
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
            "note": self.note_input.toPlainText().strip(),
            "frame": self.selected_frame,
        }


class PersonFormDialog(QDialog):
    """فرم وارد کردن مشخصات فرد: نام، شماره تلفن، شماره کارمندی، توضیحات."""

    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("مشخصات فرد")
        self.setMinimumWidth(320)

        self.name_input = QLineEdit()
        self.phone_input = QLineEdit()
        self.employee_id_input = QLineEdit()
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
            "note": self.note_input.toPlainText().strip(),
        }


class FaceLibraryDialog(QDialog):
    """مدیریت Face Library: افزودن از تصویر زنده، ویرایش، حذف."""

    COLUMNS = ["عکس", "نام", "شماره تلفن", "شماره کارمندی", "توضیحات"]

    def __init__(self, face_engine, get_current_frame_callback, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.get_current_frame_callback = get_current_frame_callback
        self.setWindowTitle("Face Library - مدیریت چهره‌ها")
        self.resize(700, 420)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        add_btn = QPushButton("افزودن چهره از تصویر زنده دوربین فعال")
        add_btn.clicked.connect(self.add_from_live)
        add_image_btn = QPushButton("افزودن چهره از تصویر...")
        add_image_btn.clicked.connect(self.add_from_image)
        edit_btn = QPushButton("ویرایش مشخصات")
        edit_btn.clicked.connect(self.edit_selected)
        delete_btn = QPushButton("حذف")
        delete_btn.clicked.connect(self.delete_selected)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(add_image_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout()
        layout.addWidget(self.table)
        layout.addLayout(btn_row)
        self.setLayout(layout)

        self.refresh_table()

    def refresh_table(self):
        people = self.face_engine.list_people()
        self.table.setRowCount(0)
        for person in people:
            row = self.table.rowCount()
            self.table.insertRow(row)

            photo_label = QLabel()
            pix = QPixmap(person.get("photo", ""))
            if not pix.isNull():
                photo_label.setPixmap(pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio))
            self.table.setCellWidget(row, 0, photo_label)

            self.table.setItem(row, 1, QTableWidgetItem(person.get("name", "")))
            self.table.setItem(row, 2, QTableWidgetItem(person.get("phone", "")))
            self.table.setItem(row, 3, QTableWidgetItem(person.get("employee_id", "")))
            self.table.setItem(row, 4, QTableWidgetItem(person.get("note", "")))
            # شناسه داخلی را در آیتم مخفی نگه می‌داریم تا هنگام ویرایش/حذف قابل بازیابی باشد.
            self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, person.get("id"))

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

        form = PersonFormDialog(self)
        if form.exec() == QDialog.DialogCode.Accepted:
            data = form.get_data()
            person = self.face_engine.register_face(
                name=data["name"],
                image_frame=frame,
                phone=data["phone"],
                employee_id=data["employee_id"],
                note=data["note"],
            )
            if person:
                QMessageBox.information(self, "موفقیت", f"چهره «{data['name']}» با موفقیت در Face Library ثبت شد.")
                self.refresh_table()
            else:
                QMessageBox.warning(self, "خطا", "چهره‌ای در تصویر تشخیص داده نشد. لطفاً نزدیک‌تر و روبه‌روی دوربین قرار بگیرید.")

    def add_from_image(self):
        dialog = AddFaceFromImageDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            person = self.face_engine.register_face(
                name=data["name"],
                image_frame=data["frame"],
                phone=data["phone"],
                employee_id=data["employee_id"],
                note=data["note"],
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
        form = PersonFormDialog(self, existing=existing)
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

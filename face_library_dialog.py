from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QMessageBox, QDialogButtonBox,
    QHeaderView, QLabel
)


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
        edit_btn = QPushButton("ویرایش مشخصات")
        edit_btn.clicked.connect(self.edit_selected)
        delete_btn = QPushButton("حذف")
        delete_btn.clicked.connect(self.delete_selected)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
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

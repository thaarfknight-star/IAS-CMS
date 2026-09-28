# -*- coding: utf-8 -*-
"""user_admin_dialog.py — «👥 مدیریت کاربران» مخصوص ادمین (2.0.72-beta).

ادمین می‌تواند کاربر بسازد/ویرایش/حذف کند و برای هر کاربر تعیین کند به
کدام صفحه‌ها دسترسی دارد. لایسنس و مدیریت کاربران همیشه مخصوص ادمین‌اند
و در این صفحه سطح‌بندی نمی‌شوند.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QMessageBox, QFormLayout, QLineEdit, QCheckBox, QGroupBox,
    QScrollArea, QWidget,
)

from user_manager import UserManager, ACCESS_PAGES, PAGE_KEYS, DEFAULT_ADMIN_USER


class UserEditDialog(QDialog):
    """ساخت/ویرایش یک کاربر."""

    def __init__(self, manager: UserManager, username=None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.editing = username
        self.setWindowTitle("ویرایش کاربر" if username else "کاربر جدید")
        self.setModal(True)
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.user_edit = QLineEdit()
        if username:
            self.user_edit.setText(username)
            self.user_edit.setReadOnly(True)
            self.user_edit.setStyleSheet("color: #888;")
        else:
            self.user_edit.setPlaceholderText("مثلاً operator1")
        form.addRow("نام‌کاربری:", self.user_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setPlaceholderText(
            "بدون تغییر" if username else "حداقل ۶ کاراکتر")
        form.addRow("رمز عبور:", self.pass_edit)
        lay.addLayout(form)

        self.admin_check = QCheckBox("⭐ دسترسی ادمین (همه‌ی صفحه‌ها + لایسنس + مدیریت کاربران)")
        lay.addWidget(self.admin_check)

        perm_group = QGroupBox("دسترسی به صفحه‌ها (برای کاربر عادی)")
        play = QVBoxLayout()
        self.perm_checks = {}
        for key, label in ACCESS_PAGES:
            cb = QCheckBox(label)
            play.addWidget(cb)
            self.perm_checks[key] = cb
        perm_group.setLayout(play)
        lay.addWidget(perm_group)

        if username:
            u = manager.users.get(username, {})
            self.admin_check.setChecked(bool(u.get("is_admin")))
            perms = u.get("permissions", {})
            for key, cb in self.perm_checks.items():
                cb.setChecked(bool(perms.get(key, False)))
            if username == DEFAULT_ADMIN_USER:
                self.admin_check.setChecked(True)
                self.admin_check.setEnabled(False)
        else:
            # پیش‌فرض کاربر جدید: فقط صفحه‌ی اصلی
            self.perm_checks["home"].setChecked(True)

        self.admin_check.toggled.connect(self._on_admin_toggled)
        self._on_admin_toggled(self.admin_check.isChecked())

        row = QHBoxLayout()
        row.addStretch()
        ok_btn = QPushButton("✅ ثبت")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self._on_ok)
        row.addWidget(ok_btn)
        cancel_btn = QPushButton("انصراف")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)
        lay.addLayout(row)

    def _on_admin_toggled(self, on):
        # ادمین به همه‌چیز دسترسی دارد؛ چک‌باکس‌های صفحه بی‌معنی می‌شوند.
        for cb in self.perm_checks.values():
            cb.setEnabled(not on)

    def _on_ok(self):
        username = self.user_edit.text().strip()
        password = self.pass_edit.text()
        if not username:
            QMessageBox.warning(self, "خطا", "نام‌کاربری خالی است.")
            return
        if not self.editing and len(password) < 6:
            QMessageBox.warning(self, "رمز ضعیف",
                                "رمز عبور باید حداقل ۶ کاراکتر باشد.")
            return
        if self.editing and password and len(password) < 6:
            QMessageBox.warning(self, "رمز ضعیف",
                                "رمز عبور باید حداقل ۶ کاراکتر باشد.")
            return
        is_admin = self.admin_check.isChecked()
        perms = {k: cb.isChecked() for k, cb in self.perm_checks.items()}
        if not is_admin and not any(perms.values()):
            QMessageBox.warning(self, "خطا",
                                "کاربر عادی باید حداقل به یک صفحه دسترسی داشته باشد.")
            return
        if self.editing:
            ok = self.manager.update_user(
                self.editing,
                password=password or None,
                is_admin=is_admin,
                permissions=perms)
        else:
            ok = self.manager.create_user(username, password,
                                          is_admin=is_admin,
                                          permissions=perms)
        if not ok:
            QMessageBox.warning(
                self, "خطا",
                "عملیات ممکن نشد (نام تکراری؟ آخرین ادمین را نمی‌شود خلع/حذف کرد).")
            return
        self.accept()


class UserAdminDialog(QDialog):
    def __init__(self, manager: UserManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("👥 مدیریت کاربران")
        self.setModal(True)
        self.resize(520, 480)

        lay = QVBoxLayout(self)
        hint = QLabel(
            "ادمین می‌تواند کاربر تعریف کند و سطح دسترسی هر کاربر را مشخص کند.\n"
            "«لایسنس» و «مدیریت کاربران» همیشه فقط برای ادمین است.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        lay.addWidget(hint)

        self.user_list = QListWidget()
        lay.addWidget(self.user_list, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("➕ کاربر جدید")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._on_add)
        edit_btn = QPushButton("✏️ ویرایش")
        edit_btn.clicked.connect(self._on_edit)
        del_btn = QPushButton("🗑 حذف")
        del_btn.clicked.connect(self._on_delete)
        pwd_btn = QPushButton("🔑 تعویض رمز خودم")
        pwd_btn.clicked.connect(self._on_change_own)
        for b in (add_btn, edit_btn, del_btn, pwd_btn):
            row.addWidget(b)
        lay.addLayout(row)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        lay.addLayout(close_row)

        self._refresh()

    def _refresh(self):
        self.user_list.clear()
        for name in sorted(self.manager.users.keys()):
            u = self.manager.users[name]
            if u.get("is_admin"):
                desc = "⭐ ادمین (دسترسی کامل)"
            else:
                pages = [lbl for k, lbl in ACCESS_PAGES
                         if u.get("permissions", {}).get(k)]
                desc = "دسترسی: " + ("، ".join(pages) if pages else "هیچ")
            item = QListWidgetItem(f"{name} — {desc}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.user_list.addItem(item)

    def _selected(self):
        item = self.user_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_add(self):
        dlg = UserEditDialog(self.manager, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _on_edit(self):
        name = self._selected()
        if not name:
            return
        dlg = UserEditDialog(self.manager, username=name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _on_delete(self):
        name = self._selected()
        if not name:
            return
        if name == DEFAULT_ADMIN_USER:
            QMessageBox.warning(self, "خطا",
                                "کاربر ادمین پیش‌فرض حذف نمی‌شود.")
            return
        if QMessageBox.question(
                self, "حذف کاربر",
                f"کاربر «{name}» حذف شود؟") != QMessageBox.StandardButton.Yes:
            return
        if not self.manager.delete_user(name):
            QMessageBox.warning(self, "خطا",
                                "حذف ممکن نشد (آخرین ادمین حذف نمی‌شود).")
            return
        self._refresh()

    def _on_change_own(self):
        from login_dialog import ChangePasswordDialog
        # «خودم» = اولین ادمینِ لیست (این دیالوگ فقط برای ادمین باز می‌شود)
        admins = self.manager.admins()
        if not admins:
            return
        me = admins[0]["username"]
        dlg = ChangePasswordDialog(me, forced=False, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.new_password:
            self.manager.set_password(me, dlg.new_password)
            QMessageBox.information(self, "انجام شد", "رمز شما تعویض شد.")

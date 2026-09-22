# -*- coding: utf-8 -*-
"""دیالوگ انتخاب کانال‌های NVR برای اتصال (2.0.24-beta).

بعد از «دریافت لیست کانال‌های این NVR» از پنل وب، کاربر با این پنجره
می‌تواند تیک بزند کدام کانال‌ها واقعاً به لیست دوربین‌ها اضافه و متصل
شوند (به‌جای افزودن همه با یک Yes/No).
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QDialogButtonBox,
)
from PyQt6.QtCore import Qt


class NVRChannelSelectDialog(QDialog):
    """entries: لیستی از (chn, name, cam_ip)."""

    def __init__(self, nvr_label, entries, parent=None):
        super().__init__(parent)
        self._entries = list(entries or [])
        self.setWindowTitle("انتخاب کانال‌ها برای اتصال")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(520, 460)

        layout = QVBoxLayout(self)
        info = QLabel(
            f"{len(self._entries)} کانال جدید از NVR «{nvr_label}» پیدا شد.\n"
            "تیک بزنید کدام‌ها اضافه و متصل شوند:\n"
            "• کانال‌هایی که IP دوربینشان مشخص است، مستقیماً به همان IP وصل می‌شوند "
            "(با یوزرنیم/رمز همین NVR).\n"
            "• بقیه از طریق خودِ NVR اضافه می‌شوند."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.list = QListWidget(self)
        for chn, name, cam_ip in self._entries:
            if cam_ip:
                text = f"کانال {chn} ({name}) — IP دوربین: {cam_ip} (اتصال مستقیم)"
            else:
                text = f"کانال {chn} ({name}) — بدون IP دوربین (اتصال از طریق NVR)"
            it = QListWidgetItem(text)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            it.setData(Qt.ItemDataRole.UserRole, (chn, name, cam_ip))
            self.list.addItem(it)
        layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        all_btn = QPushButton("انتخاب همه")
        none_btn = QPushButton("حذف انتخاب")
        all_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        none_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        row.addStretch()
        layout.addLayout(row)

        buttons = QDialogButtonBox(self)
        ok_btn = buttons.addButton("✅ افزودن انتخاب‌شده‌ها",
                                   QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("❌ انصراف", QDialogButtonBox.ButtonRole.RejectRole)
        ok_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, state):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def selected(self):
        """کانال‌های تیک‌خورده: لیستی از (chn, name, cam_ip)."""
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(tuple(it.data(Qt.ItemDataRole.UserRole)))
        return out

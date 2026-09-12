# -*- coding: utf-8 -*-
"""دیالوگ افزودن/ویرایش یک پنل فیزیکی اعلام حریق (ISAPI/CGI/Modbus TCP)،
با دکمه‌ی «تست اتصال» که یک تلاش poll انجام می‌دهد بدون شروع مانیتورینگ
دائمی. مشابه ساختار add_camera_dialog.py."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QLabel, QDialogButtonBox, QMessageBox,
)

from fire_alarm_io import FireAlarmMonitorThread

PROTOCOL_CHOICES = [
    ("Hikvision ISAPI", "isapi"),
    ("Dahua CGI", "cgi"),
    ("Modbus TCP", "modbus"),
]

DEFAULT_PORTS = {"isapi": 80, "cgi": 80, "modbus": 502}


class AddFireAlarmDialog(QDialog):
    def __init__(self, parent=None, existing_panel: dict | None = None):
        super().__init__(parent)
        self.existing_panel = existing_panel
        self.setWindowTitle("ویرایش پنل اعلام حریق" if existing_panel else "افزودن پنل اعلام حریق")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit(existing_panel.get("name", "") if existing_panel else "")
        self.name_input.setPlaceholderText("مثلاً پنل طبقه همکف")
        form.addRow("نام:", self.name_input)

        self.protocol_combo = QComboBox()
        for label, value in PROTOCOL_CHOICES:
            self.protocol_combo.addItem(label, value)
        if existing_panel:
            idx = self.protocol_combo.findData(existing_panel.get("protocol"))
            if idx >= 0:
                self.protocol_combo.setCurrentIndex(idx)
        self.protocol_combo.currentIndexChanged.connect(self._on_protocol_changed)
        form.addRow("پروتکل:", self.protocol_combo)

        self.ip_input = QLineEdit(existing_panel.get("ip", "") if existing_panel else "")
        self.ip_input.setPlaceholderText("192.168.1.50")
        form.addRow("IP:", self.ip_input)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(
            existing_panel.get("port", DEFAULT_PORTS["isapi"]) if existing_panel else DEFAULT_PORTS["isapi"]
        )
        form.addRow("پورت:", self.port_input)

        self.user_input = QLineEdit(existing_panel.get("user", "") if existing_panel else "admin")
        form.addRow("نام کاربری:", self.user_input)

        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("رمز عبور:", self.pass_input)

        self.input_count_spin = QSpinBox()
        self.input_count_spin.setRange(1, 64)
        self.input_count_spin.setValue(existing_panel.get("input_count", 8) if existing_panel else 8)
        form.addRow("تعداد ورودی دیجیتال (فقط Modbus):", self.input_count_spin)

        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        layout.addWidget(self.status_label)

        test_btn = QDialogButtonBox()
        self.test_btn = test_btn.addButton("تست اتصال", QDialogButtonBox.ButtonRole.ActionRole)
        self.test_btn.clicked.connect(self.test_connection)
        layout.addWidget(test_btn)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._on_protocol_changed()

    def _on_protocol_changed(self):
        protocol = self.protocol_combo.currentData()
        if not (self.existing_panel and self.existing_panel.get("port")):
            self.port_input.setValue(DEFAULT_PORTS.get(protocol, 80))
        self.input_count_spin.setEnabled(protocol == "modbus")

    def _current_panel_dict(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "protocol": self.protocol_combo.currentData(),
            "ip": self.ip_input.text().strip(),
            "port": self.port_input.value(),
            "user": self.user_input.text().strip(),
            "pass": self.pass_input.text(),
            "input_count": self.input_count_spin.value(),
        }

    def test_connection(self):
        """یک تلاش poll مستقیم (بدون QThread) انجام می‌دهد تا کاربر سریع
        بفهمد اتصال/اطلاعات ورود درست است، بدون شروع مانیتورینگ دائمی."""
        panel = self._current_panel_dict()
        if not panel["ip"]:
            self.status_label.setText("⚠️ آدرس IP را وارد کنید.")
            return

        prober = FireAlarmMonitorThread(panel)
        poller = {
            "isapi": prober._poll_isapi,
            "cgi": prober._poll_cgi,
            "modbus": prober._poll_modbus,
        }.get(panel["protocol"])
        try:
            state = poller()
            self.status_label.setText(f"✅ اتصال موفق - {len(state)} منطقه/ورودی خوانده شد.")
        except Exception as e:
            self.status_label.setText(f"❌ اتصال ناموفق: {e}")

    def get_data(self) -> dict:
        return self._current_panel_dict()

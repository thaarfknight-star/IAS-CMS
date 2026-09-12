from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QPushButton, QLabel, QDialogButtonBox, QMessageBox
)

from fire_alarm_io import (
    PANEL_TYPE_LABELS_FA, DEFAULT_PORTS,
    check_hikvision_alarm_input, check_dahua_alarm_input, check_modbus_discrete_input,
)


class _TestConnectionThread(QThread):
    """تست اتصال در ترد جدا اجرا می‌شود تا کلیک روی «تست اتصال» - که ممکن
    است چند ثانیه طول بکشد (تایم‌اوت شبکه) - دیالوگ را قفل نکند."""

    result_signal = pyqtSignal(object, str)  # (True/False/None, error_msg)

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel

    def run(self):
        p = self.panel
        try:
            if p["type"] == "hikvision_isapi":
                state = check_hikvision_alarm_input(
                    p["ip"], p["port"], p["user"], p["pass"], input_id=p["input_id"])
            elif p["type"] == "dahua_cgi":
                state = check_dahua_alarm_input(
                    p["ip"], p["port"], p["user"], p["pass"], input_id=p["input_id"])
            else:
                state = check_modbus_discrete_input(p["ip"], p["port"], p["input_id"])
            self.result_signal.emit(state, "")
        except Exception as e:
            self.result_signal.emit(None, str(e))


class AddFireAlarmDialog(QDialog):
    """افزودن یک پنل/سنسور *فیزیکی* اعلام حریق (نه تشخیص تصویری - برای آن
    رجوع کنید به fire_smoke_detector.py). سه نوع اتصال پشتیبانی می‌شود؛
    رجوع کنید به بالای fire_alarm_io.py برای توضیح کامل هرکدام."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("افزودن پنل/سنسور اعلام حریق")
        self.setMinimumWidth(430)
        self._test_thread = None

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("مثلاً: پنل اعلام حریق - انبار")

        self.type_combo = QComboBox()
        for key, label in PANEL_TYPE_LABELS_FA.items():
            self.type_combo.addItem(label, key)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP دستگاه (NVR/دوربین یا ماژول رله)")

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)

        self.user_input = QLineEdit("admin")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.input_id_input = QSpinBox()
        self.input_id_input.setRange(0, 128)
        self.input_id_input.setValue(1)

        self.poll_input = QSpinBox()
        self.poll_input.setRange(1, 300)
        self.poll_input.setValue(3)
        self.poll_input.setSuffix(" ثانیه")

        form = QFormLayout()
        form.addRow("نام:", self.name_input)
        form.addRow("نوع اتصال:", self.type_combo)
        form.addRow("آدرس IP:", self.ip_input)
        form.addRow("پورت:", self.port_input)
        form.addRow("نام کاربری:", self.user_input)
        form.addRow("رمز عبور:", self.pass_input)
        self.input_id_label = QLabel()
        form.addRow(self.input_id_label, self.input_id_input)
        form.addRow("فاصله‌ی بررسی:", self.poll_input)

        self.test_btn = QPushButton("تست اتصال")
        self.test_btn.clicked.connect(self._on_test_clicked)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#aaaaaa; font-size:11px;")
        self.status_label.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("افزودن پنل")
        self.buttons.accepted.connect(self.handle_accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.test_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self._on_type_changed()

    def _on_type_changed(self):
        ptype = self.type_combo.currentData()
        self.port_input.setValue(DEFAULT_PORTS.get(ptype, 80))
        is_modbus = (ptype == "modbus_tcp")
        self.user_input.setEnabled(not is_modbus)
        self.pass_input.setEnabled(not is_modbus)
        self.input_id_label.setText(
            "آدرس رجیستر (Discrete Input):" if is_modbus else "شماره‌ی ورودی آلارم:"
        )

    def _on_test_clicked(self):
        panel = self.get_panel_data()
        if not panel["ip"]:
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP را وارد کنید.")
            return
        self.test_btn.setEnabled(False)
        self.status_label.setText("در حال تست اتصال...")
        self._test_thread = _TestConnectionThread(panel, self)
        self._test_thread.result_signal.connect(self._on_test_result)
        self._test_thread.start()

    def _on_test_result(self, state, error):
        self.test_btn.setEnabled(True)
        if error:
            self.status_label.setText(f"❌ خطا: {error}")
        elif state is None:
            self.status_label.setText(
                "⚠ پاسخ معتبری دریافت نشد - IP/پورت/نام‌کاربری/رمز یا شماره‌ی "
                "ورودی را بررسی کنید."
            )
        elif state:
            self.status_label.setText(
                "✅ اتصال برقرار شد - وضعیت فعلی: آلارم فعال است (احتمالاً یک "
                "تست/آلارم واقعی در جریان است)."
            )
        else:
            self.status_label.setText("✅ اتصال برقرار شد - وضعیت فعلی: عادی (بدون آلارم).")

    def closeEvent(self, event):
        if self._test_thread is not None and self._test_thread.isRunning():
            self._test_thread.wait(500)
        event.accept()

    def reject(self):
        if self._test_thread is not None and self._test_thread.isRunning():
            self._test_thread.wait(500)
        super().reject()

    def handle_accept(self):
        if not self.ip_input.text().strip():
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP را وارد کنید.")
            return
        self.accept()

    def get_panel_data(self):
        return {
            "name": self.name_input.text().strip() or self.ip_input.text().strip(),
            "type": self.type_combo.currentData(),
            "ip": self.ip_input.text().strip(),
            "port": self.port_input.value(),
            "user": self.user_input.text().strip(),
            "pass": self.pass_input.text().strip(),
            "input_id": self.input_id_input.value(),
            "poll_interval": self.poll_input.value(),
        }

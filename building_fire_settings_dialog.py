# -*- coding: utf-8 -*-
"""
building_fire_settings_dialog.py — دیالوگ تنظیمات اتصال به سیستم حریق ساختمان + صدای هشدار.
"""

import os

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QCheckBox, QComboBox, QLineEdit, QSpinBox,
                             QDoubleSpinBox, QPushButton, QLabel, QFileDialog,
                             QGroupBox, QMessageBox, QSlider)
from PyQt6.QtCore import Qt

from building_fire_output import load_config, save_config, BuildingFireOutput
from alarm_sound import load_config as load_sound_config, save_config as save_sound_config


class BuildingFireSettingsDialog(QDialog):
    def __init__(self, parent=None, log_callback=None):
        super().__init__(parent)
        self.setWindowTitle("اتصال به سیستم حریق ساختمان + صدای هشدار")
        self.setMinimumWidth(480)
        self._log = log_callback or (lambda m: None)

        self.cfg = load_config()
        self.sound_cfg = load_sound_config()
        self._tester = BuildingFireOutput(config=dict(self.cfg), logger=self._log)

        layout = QVBoxLayout(self)

        # ---- فعال‌سازی کلی ----
        self.chk_enabled = QCheckBox("اتصال به سیستم حریق ساختمان فعال باشد")
        self.chk_enabled.setChecked(bool(self.cfg.get("enabled", False)))
        layout.addWidget(self.chk_enabled)

        # ---- روش اتصال ----
        conn_group = QGroupBox("روش اتصال به پنل ساختمان")
        form = QFormLayout(conn_group)

        self.cmb_backend = QComboBox()
        self.cmb_backend.addItem("وب‌هوک HTTP (پنل تحت شبکه / رله هوشمند)", "webhook")
        self.cmb_backend.addItem("Modbus TCP (پنل‌های صنعتی)", "modbus_tcp")
        self.cmb_backend.addItem("رله‌ی USB سریال (کنتاکت خشک به زون پنل)", "serial_relay")
        _be = self.cfg.get("backend", "webhook")
        if _be == "simulator":  # شبیه‌ساز حذف شده؛ به وب‌هوک مهاجرت می‌کند
            _be = "webhook"
        idx = self.cmb_backend.findData(_be)
        self.cmb_backend.setCurrentIndex(max(0, idx))
        self.cmb_backend.currentIndexChanged.connect(self._refresh_fields)
        form.addRow("روش:", self.cmb_backend)

        self.webhook_url = QLineEdit(self.cfg.get("webhook_url", ""))
        self.webhook_url.setPlaceholderText("http://192.168.1.50:8080/api/alarm")
        form.addRow("آدرس Webhook:", self.webhook_url)

        self.modbus_host = QLineEdit(self.cfg.get("modbus_host", "192.168.1.100"))
        form.addRow("هاست Modbus:", self.modbus_host)
        self.modbus_port = QSpinBox()
        self.modbus_port.setRange(1, 65535)
        self.modbus_port.setValue(int(self.cfg.get("modbus_port", 502)))
        form.addRow("پورت Modbus:", self.modbus_port)
        self.modbus_coil = QSpinBox()
        self.modbus_coil.setRange(0, 65535)
        self.modbus_coil.setValue(int(self.cfg.get("modbus_coil", 0)))
        form.addRow("شماره کویل:", self.modbus_coil)

        self.serial_port = QLineEdit(self.cfg.get("serial_port", "COM3"))
        self.serial_port.setPlaceholderText("COM3 (ویندوز) یا /dev/ttyUSB0 (لینوکس)")
        form.addRow("پورت سریال:", self.serial_port)

        self.chk_smoke = QCheckBox("دود هم آلارم ساختمان را فعال کند")
        self.chk_smoke.setChecked(bool(self.cfg.get("trigger_on_smoke", True)))
        form.addRow(self.chk_smoke)
        self.chk_fire = QCheckBox("آتش آلارم ساختمان را فعال کند")
        self.chk_fire.setChecked(bool(self.cfg.get("trigger_on_fire", True)))
        form.addRow(self.chk_fire)

        self.spin_autoclear = QSpinBox()
        self.spin_autoclear.setRange(0, 3600)
        self.spin_autoclear.setSuffix(" ثانیه (۰ = دستی)")
        self.spin_autoclear.setValue(int(self.cfg.get("auto_clear_seconds", 0)))
        form.addRow("ریست خودکار:", self.spin_autoclear)

        layout.addWidget(conn_group)

        # ---- صدای هشدار ----
        snd_group = QGroupBox("صدای هشدار حریق")
        snd_form = QFormLayout(snd_group)
        self.chk_sound = QCheckBox("پخش صدای هشدار هنگام تشخیص حریق")
        self.chk_sound.setChecked(bool(self.sound_cfg.get("fire_enabled", False)))
        snd_form.addRow(self.chk_sound)

        snd_row = QHBoxLayout()
        self.sound_path = QLineEdit(self.sound_cfg.get("sound_file", ""))
        self.sound_path.setPlaceholderText("خالی = آژیر پیش‌فرض")
        self.sound_path.setReadOnly(True)
        btn_browse = QPushButton("انتخاب فایل…")
        btn_browse.clicked.connect(self._browse_sound)
        snd_row.addWidget(self.sound_path, 1)
        snd_row.addWidget(btn_browse)
        snd_form.addRow("فایل صوتی:", snd_row)

        self.sld_volume = QSlider(Qt.Orientation.Horizontal)
        self.sld_volume.setRange(0, 100)
        self.sld_volume.setValue(int(float(self.sound_cfg.get("volume", 0.9)) * 100))
        snd_form.addRow("بلندی صدا:", self.sld_volume)

        self.chk_loop = QCheckBox("تکرار آژیر تا قطع شدن")
        self.chk_loop.setChecked(bool(self.sound_cfg.get("loop", True)))
        snd_form.addRow(self.chk_loop)
        layout.addWidget(snd_group)

        # ---- دکمه‌ها ----
        btn_row = QHBoxLayout()
        self.btn_test_sound = QPushButton("🔊 تست صدا")
        self.btn_test_sound.clicked.connect(self._test_sound)
        self.btn_test_conn = QPushButton("📡 تست اتصال به پنل")
        self.btn_test_conn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.btn_test_sound)
        btn_row.addWidget(self.btn_test_conn)
        layout.addLayout(btn_row)

        hint = QLabel(
            "💡 راهنمای تست اتصال:\n"
            "روش اتصال را انتخاب کنید، آدرس/مشخصات را وارد کنید و «تست اتصال» را بزنید؛\n"
            "نتیجه‌ی تست همین‌جا نمایش داده می‌شود."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7d8f99; font-size: 12px;")
        layout.addWidget(hint)

        ok_row = QHBoxLayout()
        ok_row.addStretch(1)
        btn_save = QPushButton("ذخیره")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("انصراف")
        btn_cancel.clicked.connect(self.reject)
        ok_row.addWidget(btn_save)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

        self._refresh_fields()

    def _refresh_fields(self):
        b = self.cmb_backend.currentData()
        is_webhook = (b == "webhook")
        is_modbus = (b == "modbus_tcp")
        is_serial = (b == "serial_relay")
        self.webhook_url.setEnabled(is_webhook)
        self.modbus_host.setEnabled(is_modbus)
        self.modbus_port.setEnabled(is_modbus)
        self.modbus_coil.setEnabled(is_modbus)
        self.serial_port.setEnabled(is_serial)

    def _browse_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "انتخاب فایل صدای هشدار", "",
            "فایل صوتی (*.wav *.mp3 *.ogg)")
        if path:
            self.sound_path.setText(path)

    def _collect(self):
        self.cfg["enabled"] = self.chk_enabled.isChecked()
        self.cfg["backend"] = self.cmb_backend.currentData()
        self.cfg["webhook_url"] = self.webhook_url.text().strip()
        self.cfg["modbus_host"] = self.modbus_host.text().strip()
        self.cfg["modbus_port"] = self.modbus_port.value()
        self.cfg["modbus_coil"] = self.modbus_coil.value()
        self.cfg["serial_port"] = self.serial_port.text().strip()
        self.cfg["trigger_on_smoke"] = self.chk_smoke.isChecked()
        self.cfg["trigger_on_fire"] = self.chk_fire.isChecked()
        self.cfg["auto_clear_seconds"] = self.spin_autoclear.value()

        self.sound_cfg["fire_enabled"] = self.chk_sound.isChecked()
        self.sound_cfg["sound_file"] = self.sound_path.text().strip()
        self.sound_cfg["volume"] = self.sld_volume.value() / 100.0
        self.sound_cfg["loop"] = self.chk_loop.isChecked()

    def _test_sound(self):
        from alarm_sound import AlarmSoundPlayer
        self._collect()
        p = AlarmSoundPlayer(config=dict(self.sound_cfg, fire_enabled=True))
        p.play_once()
        QMessageBox.information(self, "تست صدا", "صدای هشدار یک‌بار پخش شد 🔊")

    def _test_connection(self):
        self._collect()
        # ابتدا ذخیره‌ی موقت برای تست با همین مقادیر فرم
        tester = BuildingFireOutput(config=dict(self.cfg),
                                    logger=lambda m: QMessageBox.information(
                                        self, "نتیجه‌ی تست اتصال", m))
        # برای تست، enabled را نادیده می‌گیریم (داخل test_connection لحاظ شده)
        tester.test_connection()

    def accept(self):
        self._collect()
        save_config(self.cfg)
        save_sound_config(self.sound_cfg)
        super().accept()

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QLabel, QMenu, QGroupBox, QComboBox, QCheckBox,
)

from fire_alarm_io import PANEL_TYPE_LABELS_FA
from add_fire_alarm_dialog import AddFireAlarmDialog
from fire_config import (
    SENSITIVITY_LEVELS, get_sensitivity, set_sensitivity, level_label,
)


class FireAlarmPage(QWidget):
    """مدیریت پنل‌ها/سنسورهای فیزیکی اعلام حریق - به‌عنوان یک صفحه‌ی جداگانه
    داخل QStackedWidget پنجره‌ی اصلی (قابل دسترسی از هدر بالای برنامه)،
    نه یک دیالوگ مستقل. افزودن/حذف پنل همچنان از طریق fire_alarm_store
    انجام می‌شود؛ شروع/توقف ترد مانیتور پس‌زمینه‌ی هر پنل با دو callback به
    MainWindow سپرده می‌شود چون آن تردها در سطح MainWindow نگهداری
    می‌شوند."""

    def __init__(self, fire_alarm_store, start_monitor_callback, stop_monitor_callback, parent=None,
                 camera_store=None, on_fire_toggle=None):
        super().__init__(parent)
        self.fire_alarm_store = fire_alarm_store
        self.start_monitor_callback = start_monitor_callback
        self.stop_monitor_callback = stop_monitor_callback
        self.camera_store = camera_store
        self.on_fire_toggle = on_fire_toggle  # (cam_id, enabled) -> None

        title = QLabel("🔥 پنل‌های اعلام حریق")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")

        self.add_fire_alarm_btn = QPushButton("+ افزودن پنل/سنسور اعلام حریق")
        self.add_fire_alarm_btn.clicked.connect(self.open_add_fire_alarm_dialog)

        self.fire_alarm_list = QListWidget()
        self.fire_alarm_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fire_alarm_list.customContextMenuRequested.connect(self.show_fire_alarm_context_menu)

        fire_alarm_hint = QLabel("کلیک راست روی هر پنل: حذف")
        fire_alarm_hint.setStyleSheet("color: #888; font-size: 10px;")

        # --- تنظیمات تشخیص تصویری آتش/دود (آشکارساز شعله‌ی کوچک + تأیید چندفریمی) ---
        vision_group = QGroupBox("🎥 تشخیص تصویری آتش/دود")
        vision_layout = QVBoxLayout()
        sens_row = QHBoxLayout()
        sens_row.addWidget(QLabel("حساسیت تشخیص:"))
        self.sensitivity_combo = QComboBox()
        for key in ("low", "medium", "high"):
            self.sensitivity_combo.addItem(f"حساسیت {level_label(key)}", key)
        self.sensitivity_combo.setCurrentIndex(
            self.sensitivity_combo.findData(get_sensitivity())
        )
        self.sensitivity_combo.currentIndexChanged.connect(self._on_sensitivity_changed)
        sens_row.addWidget(self.sensitivity_combo, 1)
        vision_layout.addLayout(sens_row)
        vision_hint = QLabel(
            "«زیاد»: حتی شعله‌ی فندک نزدیک دوربین را می‌گیرد (احتمال هشدار اشتباه بیشتر).\n"
            "تغییر بلافاصله و بدون ری‌استارت روی همه‌ی دوربین‌ها اعمال می‌شود."
        )
        vision_hint.setStyleSheet("color: #888; font-size: 10px;")
        vision_hint.setWordWrap(True)
        vision_layout.addWidget(vision_hint)
        cam_hint = QLabel(
            "دوربین‌هایی که تشخیص تصویری آتش/دود روی آن‌ها فعال باشد "
            "(مثل پلاک‌خوان، برای هر دوربین جداگانه):")
        cam_hint.setStyleSheet("color: #888; font-size: 10px;")
        cam_hint.setWordWrap(True)
        vision_layout.addWidget(cam_hint)
        self.camera_checklist = QListWidget()
        self.camera_checklist.setMaximumHeight(130)
        self.camera_checklist.itemChanged.connect(self._on_camera_check_changed)
        vision_layout.addWidget(self.camera_checklist)
        # صدای آلارم: اختیاری، پیش‌فرض خاموش
        sound_row = QHBoxLayout()
        from alarm_sound import load_config, save_config
        self._alarm_cfg = load_config()
        self.sound_enabled_chk = QCheckBox("🔊 پخش صدای آلارم (آژیر/بیپ)")
        self.sound_enabled_chk.setChecked(bool(self._alarm_cfg.get("enabled", False)))
        self.sound_enabled_chk.toggled.connect(self._on_sound_enabled_toggled)
        sound_row.addWidget(self.sound_enabled_chk)
        sound_test_btn = QPushButton("تست صدا")
        sound_test_btn.clicked.connect(self._on_sound_test)
        sound_row.addWidget(sound_test_btn)
        sound_row.addStretch()
        vision_layout.addLayout(sound_row)
        vision_group.setLayout(vision_layout)

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(vision_group)
        layout.addWidget(self.add_fire_alarm_btn)
        layout.addWidget(self.fire_alarm_list, 1)
        layout.addWidget(fire_alarm_hint)
        self.setLayout(layout)

        self.reload_fire_alarm_list()

    def _on_sensitivity_changed(self, index):
        key = self.sensitivity_combo.itemData(index)
        if key:
            set_sensitivity(key)

    def refresh(self):
        """هر بار که صفحه از هدر باز می‌شود صدا زده می‌شود تا لیست تازه باشد."""
        self.sensitivity_combo.setCurrentIndex(
            self.sensitivity_combo.findData(get_sensitivity())
        )
        self.reload_fire_alarm_list()
        self._reload_camera_checklist()

    def _all_cameras(self):
        out = []
        try:
            for cam in self.camera_store.standalone_cameras():
                out.append((cam.get("id"),
                            cam.get("name") or cam.get("ip") or "دوربین"))
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    out.append((cam.get("id"),
                                cam.get("name") or cam.get("ip") or "دوربین"))
        except Exception:
            pass
        return out

    def _reload_camera_checklist(self):
        if self.camera_store is None:
            return
        cam_by_id = {}
        try:
            for cam in self.camera_store.standalone_cameras():
                cam_by_id[cam.get("id")] = cam
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    cam_by_id[cam.get("id")] = cam
        except Exception:
            pass
        self.camera_checklist.blockSignals(True)
        self.camera_checklist.clear()
        for cam_id, label in self._all_cameras():
            cam = cam_by_id.get(cam_id, {})
            item = QListWidgetItem(f"🎥 {label}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if cam.get("fire_detection") else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, cam_id)
            self.camera_checklist.addItem(item)
        self.camera_checklist.blockSignals(False)

    def _on_sound_enabled_toggled(self, checked):
        try:
            from alarm_sound import save_config
            self._alarm_cfg["enabled"] = bool(checked)
            save_config(self._alarm_cfg)
        except Exception:
            pass

    def _on_sound_test(self):
        try:
            from alarm_sound import AlarmSoundPlayer
            AlarmSoundPlayer(dict(self._alarm_cfg, enabled=True)).play_once()
        except Exception:
            pass

    def _on_camera_check_changed(self, item):
        cam_id = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            self.camera_store.update_camera(cam_id, fire_detection=enabled)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "خطا", f"ذخیره‌ی تنظیم دوربین ناموفق بود:\n{e}")
            return
        if callable(self.on_fire_toggle):
            try:
                self.on_fire_toggle(cam_id, enabled)
            except Exception:
                pass

    def open_add_fire_alarm_dialog(self):
        dialog = AddFireAlarmDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        panel = self.fire_alarm_store.add_panel(dialog.get_panel_data())
        self.reload_fire_alarm_list()
        self.start_monitor_callback(panel)

    def reload_fire_alarm_list(self):
        self.fire_alarm_list.clear()
        for panel in self.fire_alarm_store.panels:
            type_label = PANEL_TYPE_LABELS_FA.get(panel.get("type"), panel.get("type"))
            item = QListWidgetItem(f"🔥 {panel['name']}  ({panel['ip']}) — {type_label}")
            item.setData(Qt.ItemDataRole.UserRole, panel["id"])
            self.fire_alarm_list.addItem(item)

    def show_fire_alarm_context_menu(self, pos):
        item = self.fire_alarm_list.itemAt(pos)
        if item is None:
            return
        panel_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        remove_action = menu.addAction("🗑 حذف این پنل")
        action = menu.exec(self.fire_alarm_list.viewport().mapToGlobal(pos))
        if action == remove_action:
            self.stop_monitor_callback(panel_id)
            self.fire_alarm_store.remove_panel(panel_id)
            self.reload_fire_alarm_list()


# نام قدیمی برای سازگاری با کدی که هنوز دیالوگ را ایمپورت می‌کند.
FireAlarmDialog = FireAlarmPage

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QLabel, QMenu, QGroupBox, QComboBox,
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

    def __init__(self, fire_alarm_store, start_monitor_callback, stop_monitor_callback, parent=None):
        super().__init__(parent)
        self.fire_alarm_store = fire_alarm_store
        self.start_monitor_callback = start_monitor_callback
        self.stop_monitor_callback = stop_monitor_callback

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

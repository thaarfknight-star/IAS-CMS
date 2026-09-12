from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QLabel, QMenu
)

from fire_alarm_io import PANEL_TYPE_LABELS_FA
from add_fire_alarm_dialog import AddFireAlarmDialog


class FireAlarmDialog(QDialog):
    """مدیریت پنل‌ها/سنسورهای فیزیکی اعلام حریق - به‌عنوان یک صفحه‌ی جداگانه
    (دقیقاً هم‌الگو با FaceLibraryDialog و ReportsDialog) به‌جای تبی که قبلاً
    داخل QTabWidget مشترک با «چهره» و «گزارش‌ها» بود. افزودن/حذف پنل همچنان
    از طریق fire_alarm_store انجام می‌شود؛ شروع/توقف ترد مانیتور پس‌زمینه‌ی
    هر پنل با دو callback به MainWindow سپرده می‌شود چون آن تردها در سطح
    MainWindow نگهداری می‌شوند."""

    def __init__(self, fire_alarm_store, start_monitor_callback, stop_monitor_callback, parent=None):
        super().__init__(parent)
        self.fire_alarm_store = fire_alarm_store
        self.start_monitor_callback = start_monitor_callback
        self.stop_monitor_callback = stop_monitor_callback
        self.setWindowTitle("پنل‌های اعلام حریق")
        self.resize(480, 420)

        self.add_fire_alarm_btn = QPushButton("+ افزودن پنل/سنسور اعلام حریق")
        self.add_fire_alarm_btn.clicked.connect(self.open_add_fire_alarm_dialog)

        self.fire_alarm_list = QListWidget()
        self.fire_alarm_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fire_alarm_list.customContextMenuRequested.connect(self.show_fire_alarm_context_menu)

        fire_alarm_hint = QLabel("کلیک راست روی هر پنل: حذف")
        fire_alarm_hint.setStyleSheet("color: #888; font-size: 10px;")

        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout()
        layout.addWidget(self.add_fire_alarm_btn)
        layout.addWidget(self.fire_alarm_list)
        layout.addWidget(fire_alarm_hint)
        layout.addLayout(btn_row)
        self.setLayout(layout)

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

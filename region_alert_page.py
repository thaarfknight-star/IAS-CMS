# -*- coding: utf-8 -*-
"""صفحه‌ی «⚠ محدوده هشدار» (1.0.0).

دو تب:
- 📊 ثبت گزارش: گزارش ورود افراد به محدوده‌های هشدار
- 📷 انتخاب دوربین: کدام دوربین‌ها هشدار محدوده داشته باشند
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QListWidget, QListWidgetItem, QMessageBox
)
from PyQt6.QtCore import Qt


class RegionAlertPage(QWidget):
    def __init__(self, camera_store=None, report_store=None, parent=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self.report_store = report_store
        self._build_ui()
        self._load_cameras()
        self._refresh_report()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        title = QLabel("⚠ محدوده هشدار")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        lay.addWidget(title)

        tab_widget = QTabWidget()

        # تب ۱: ثبت گزارش
        report_tab = QWidget()
        report_lay = QVBoxLayout(report_tab)
        report_lay.addWidget(QLabel("گزارش ورود به محدوده‌های هشدار:"))
        self.report_list = QListWidget()
        report_lay.addWidget(self.report_list)
        report_btn_lay = QHBoxLayout()
        report_btn_lay.addStretch()
        refresh_btn = QPushButton("🔄 به‌روزرسانی")
        refresh_btn.clicked.connect(self._refresh_report)
        report_btn_lay.addWidget(refresh_btn)
        report_lay.addLayout(report_btn_lay)
        tab_widget.addTab(report_tab, "📊 ثبت گزارش")

        # تب ۲: انتخاب دوربین
        cam_tab = QWidget()
        cam_lay = QVBoxLayout(cam_tab)
        cam_lay.addWidget(QLabel(
            "مشخص کنید کدام دوربین‌ها هشدار محدوده داشته باشند:"))
        self.cam_list = QListWidget()
        cam_lay.addWidget(self.cam_list)
        cam_btn_lay = QHBoxLayout()
        cam_btn_lay.addStretch()
        select_all_btn = QPushButton("✅ انتخاب همه (تا سقف لایسنس)")
        select_all_btn.clicked.connect(self._select_all_cameras)
        cam_btn_lay.addWidget(select_all_btn)
        save_btn = QPushButton("💾 ذخیره")
        save_btn.clicked.connect(self._save_cameras)
        cam_btn_lay.addWidget(save_btn)
        cam_lay.addLayout(cam_btn_lay)
        tab_widget.addTab(cam_tab, "📷 انتخاب دوربین")

        lay.addWidget(tab_widget)

    def refresh(self):
        """تازه‌سازی لیست دوربین‌ها و گزارش هنگام نمایش صفحه."""
        self._load_cameras()
        self._refresh_report()

    def _select_all_cameras(self):
        """انتخاب همه‌ی دوربین‌ها تا سقف سهمیه‌ی لایسنس."""
        try:
            from license import effective_quotas
            quota = int(effective_quotas().get("region_alert", 0))
        except Exception:
            quota = 0
        # اگر سهمیه نامحدود/نامشخص است، همه را انتخاب کن
        max_select = quota if quota > 0 else self.cam_list.count()
        for i in range(self.cam_list.count()):
            item = self.cam_list.item(i)
            item.setCheckState(
                Qt.CheckState.Checked if i < max_select
                else Qt.CheckState.Unchecked)

    def _load_cameras(self):
        """بارگذاری دوربین‌ها."""
        try:
            self.cam_list.clear()
            if not self.camera_store:
                return
            try:
                cameras = self.camera_store.get_cameras()
            except Exception:
                cameras = []
            for cam in cameras:
                if not isinstance(cam, dict):
                    continue
                cam_id = cam.get("id", "")
                cam_name = cam.get("name", cam_id)
                is_enabled = bool(cam.get("region_alert_enabled", False))
                item = QListWidgetItem(cam_name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if is_enabled
                    else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, cam_id)
                self.cam_list.addItem(item)
        except Exception:
            pass

    def _save_cameras(self):
        """ذخیره‌ی انتخاب دوربین‌ها."""
        try:
            try:
                from license import effective_quotas
                quota = int(effective_quotas().get("region_alert", 0))
            except Exception:
                quota = 0
            selected = []
            for i in range(self.cam_list.count()):
                item = self.cam_list.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    selected.append(item.data(Qt.ItemDataRole.UserRole))
            if quota > 0 and len(selected) > quota:
                QMessageBox.warning(
                    self, "خطا",
                    f"حداکثر {quota} دوربین برای هشدار محدوده مجاز است.")
                return
            if self.camera_store:
                for i in range(self.cam_list.count()):
                    item = self.cam_list.item(i)
                    cam_id = item.data(Qt.ItemDataRole.UserRole)
                    enabled = (item.checkState() == Qt.CheckState.Checked)
                    try:
                        self.camera_store.update_camera(
                            cam_id, region_alert_enabled=enabled)
                    except Exception:
                        pass
            QMessageBox.information(self, "موفق", "تنظیمات ذخیره شد.")
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره ناموفق:\n{e}")

    def _refresh_report(self):
        """به‌روزرسانی گزارش."""
        try:
            self.report_list.clear()
            if not self.report_store:
                return
            try:
                events = self.report_store.query(
                    event_type="zone_entry", limit=100)
            except Exception:
                events = []
            try:
                events.sort(key=lambda e: str(e.get("ts", "")), reverse=True)
            except Exception:
                pass
            for ev in events[:100]:
                cam = ev.get("camera_name", "")
                region = ev.get("region_name") or f"محدوده {ev.get('region_number', '')}"
                ts = str(ev.get("ts", ""))[:19]
                item = QListWidgetItem(f"[{ts}] {cam} — ورود به {region}")
                # ذخیره‌ی داده برای نمایش جزئیات
                item.setData(Qt.ItemDataRole.UserRole, ev)
                self.report_list.addItem(item)
        except Exception:
            pass

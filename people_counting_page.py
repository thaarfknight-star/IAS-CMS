# -*- coding: utf-8 -*-
"""صفحه‌ی «🔢 شمارش افراد» (1.0.0).

صفحه‌ی جدا برای شمارش افراد با انتخاب دوربین‌ها.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QGroupBox
)
from PyQt6.QtCore import Qt


class PeopleCountingPage(QWidget):
    def __init__(self, camera_store=None, parent=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self._build_ui()
        self._load_cameras()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        title = QLabel("🔢 شمارش افراد")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        lay.addWidget(title)

        # بخش انتخاب دوربین
        cam_group = QGroupBox("📷 انتخاب دوربین‌ها")
        cam_lay = QVBoxLayout()
        cam_lay.addWidget(QLabel(
            "مشخص کنید کدام دوربین‌ها شمارش افراد داشته باشند:"))
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
        cam_group.setLayout(cam_lay)
        lay.addWidget(cam_group)

        # بخش آمار
        stats_group = QGroupBox("📊 آمار شمارش")
        stats_lay = QVBoxLayout()
        self.stats_label = QLabel("در حال بارگذاری...")
        self.stats_label.setWordWrap(True)
        stats_lay.addWidget(self.stats_label)
        stats_btn_lay = QHBoxLayout()
        stats_btn_lay.addStretch()
        refresh_btn = QPushButton("🔄 به‌روزرسانی")
        refresh_btn.clicked.connect(self._refresh_stats)
        stats_btn_lay.addWidget(refresh_btn)
        stats_lay.addLayout(stats_btn_lay)
        stats_group.setLayout(stats_lay)
        lay.addWidget(stats_group)

        lay.addStretch()

    def refresh(self):
        """تازه‌سازی لیست دوربین‌ها و آمار هنگام نمایش صفحه."""
        self._load_cameras()
        self._refresh_stats()

    def _select_all_cameras(self):
        """انتخاب همه‌ی دوربین‌ها تا سقف سهمیه‌ی لایسنس."""
        try:
            from license import effective_quotas
            quota = int(effective_quotas().get("people_counting", 0))
        except Exception:
            quota = 0
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
                is_enabled = bool(cam.get("people_counting_enabled", False))
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
                quota = int(effective_quotas().get("people_counting", 0))
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
                    f"حداکثر {quota} دوربین برای شمارش افراد مجاز است.")
                return
            if self.camera_store:
                for i in range(self.cam_list.count()):
                    item = self.cam_list.item(i)
                    cam_id = item.data(Qt.ItemDataRole.UserRole)
                    enabled = (item.checkState() == Qt.CheckState.Checked)
                    try:
                        self.camera_store.update_camera(
                            cam_id, people_counting_enabled=enabled)
                    except Exception:
                        pass
            QMessageBox.information(self, "موفق", "تنظیمات ذخیره شد.")
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره ناموفق:\n{e}")

    def _refresh_stats(self):
        """به‌روزرسانی آمار."""
        try:
            # شمارش دوربین‌های فعال
            enabled_count = 0
            total_count = 0
            if self.camera_store:
                try:
                    cameras = self.camera_store.get_cameras()
                    total_count = len(cameras)
                    for cam in cameras:
                        if isinstance(cam, dict) and cam.get(
                                "people_counting_enabled", False):
                            enabled_count += 1
                except Exception:
                    pass
            try:
                from license import effective_quotas
                quota = int(effective_quotas().get("people_counting", 0))
            except Exception:
                quota = 0
            self.stats_label.setText(
                f"دوربین‌های فعال برای شمارش: {enabled_count}\n"
                f"سهمیه‌ی مجاز: {quota}\n"
                f"کل دوربین‌ها: {total_count}")
        except Exception:
            pass

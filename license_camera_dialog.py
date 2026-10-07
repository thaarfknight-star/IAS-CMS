# -*- coding: utf-8 -*-
"""دیالوگ «انتخاب دوربین برای قابلیت‌ها» (1.0.0).

ادمین مشخص می‌کند کدام دوربین‌ها قابلیت‌های زیر را داشته باشند:
- چهره‌خوان (face_recognition)
- هشدار محدوده (region_alert)
- شمارش افراد (people_counting)

تعداد مجاز هر قابلیت از لایسنس خوانده می‌شود.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QWidget, QListWidget, QListWidgetItem, QMessageBox
)
from PyQt6.QtCore import Qt


FEATURES = [
    ("face_recognition", "👤 چهره‌خوان"),
    ("region_alert", "⚠ هشدار محدوده"),
    ("people_counting", "🔢 شمارش افراد"),
]


class LicenseCameraDialog(QDialog):
    def __init__(self, camera_store, parent=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self.setWindowTitle("🎫 انتخاب دوربین برای قابلیت‌ها")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(500, 600)
        self._tabs = {}
        self._build_ui()
        self._load()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "مشخص کنید کدام دوربین‌ها هر قابلیت را داشته باشند.\n"
            "تعداد مجاز از لایسنس خوانده می‌شود."))

        self.tab_widget = QTabWidget()
        for feat_key, feat_name in FEATURES:
            tab = QWidget()
            tab_lay = QVBoxLayout(tab)
            # برچسب سهمیه
            quota_label = QLabel()
            quota_label.setObjectName(f"quota_{feat_key}")
            tab_lay.addWidget(quota_label)
            # لیست دوربین‌ها (چک‌باکس)
            lst = QListWidget()
            tab_lay.addWidget(lst)
            self.tab_widget.addTab(tab, feat_name)
            self._tabs[feat_key] = (lst, quota_label)
        lay.addWidget(self.tab_widget)

        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        ok_btn = QPushButton("💾 ذخیره")
        ok_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("انصراف")
        cancel_btn.clicked.connect(self.reject)
        btn_lay.addWidget(ok_btn)
        btn_lay.addWidget(cancel_btn)
        lay.addLayout(btn_lay)

    def _get_quota(self, feat_key):
        """سهمیه‌ی مجاز از لایسنس."""
        try:
            from license import effective_quotas
            quotas = effective_quotas()
            return int(quotas.get(feat_key, 0))
        except Exception:
            return 0

    def _load(self):
        """بارگذاری دوربین‌ها و وضعیت فعلی."""
        try:
            cameras = self.camera_store.get_cameras()
        except Exception:
            cameras = []
        for feat_key, feat_name in FEATURES:
            lst, quota_label = self._tabs[feat_key]
            quota = self._get_quota(feat_key)
            # شمارش فعال‌ها
            enabled_count = 0
            lst.clear()
            for cam in cameras:
                if not isinstance(cam, dict):
                    continue
                cam_id = cam.get("id", "")
                cam_name = cam.get("name", cam_id)
                # فلگ قابلیت
                field = f"{feat_key}_enabled"
                is_enabled = bool(cam.get(field, False))
                if is_enabled:
                    enabled_count += 1
                item = QListWidgetItem(cam_name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if is_enabled
                    else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, cam_id)
                lst.addItem(item)
            quota_label.setText(
                f"مجاز: {quota} دوربین | انتخاب‌شده: {enabled_count}")
            # ذخیره برای بررسی در _save
            lst.setProperty("quota", quota)

    def _save(self):
        """ذخیره‌ی انتخاب‌ها با رعایت سهمیه."""
        try:
            for feat_key, feat_name in FEATURES:
                lst, _ = self._tabs[feat_key]
                quota = int(lst.property("quota") or 0)
                # جمع‌آوری انتخاب‌شده‌ها
                selected = []
                for i in range(lst.count()):
                    item = lst.item(i)
                    if item.checkState() == Qt.CheckState.Checked:
                        selected.append(item.data(Qt.ItemDataRole.UserRole))
                if len(selected) > quota and quota > 0:
                    QMessageBox.warning(
                        self, "خطا",
                        f"برای «{feat_name}» حداکثر {quota} دوربین مجاز است "
                        f"(شما {len(selected)} انتخاب کردید).")
                    return
                # ذخیره در camera_store
                field = f"{feat_key}_enabled"
                for i in range(lst.count()):
                    item = lst.item(i)
                    cam_id = item.data(Qt.ItemDataRole.UserRole)
                    enabled = (item.checkState() == Qt.CheckState.Checked)
                    try:
                        self.camera_store.update_camera(cam_id, **{field: enabled})
                    except Exception:
                        pass
            QMessageBox.information(self, "موفق", "تنظیمات ذخیره شد.")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره ناموفق بود:\n{e}")

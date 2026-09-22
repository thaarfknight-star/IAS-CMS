# -*- coding: utf-8 -*-
"""دیالوگ سبک «👁 دیدن تصویر» — نمایش تصویر ثبت‌شده با دابل‌کلیک.

کاربر در «پنل تشخیص چهره» (کتابخانه‌ی چهره) یا «گزارش تردد» (ردیابی
اشخاص) روی عکس ثبت‌شده دابل‌کلیک می‌کند و این دیالوگ تصویر اصلی را
با حفظ نسبت ابعاد نشان می‌دهد.

سبک و کم‌فشار بودن عمدی است:
  - تصویر فقط هنگام باز شدن دیالوگ از دیسک خوانده می‌شود (نه قبلش)؛
  - با بسته شدن دیالوگ، پیکس‌مپ آزاد می‌شود؛
  - مسیر ناموجود یا فایل خراب هرگز کرش نمی‌دهد، فقط پیام نشان می‌دهد.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)


class ImageViewerDialog(QDialog):
    """نمایش یک تصویر از روی مسیر فایل."""

    def __init__(self, image_path, title="👁 دیدن تصویر", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(640, 480)

        self._path = image_path or ""
        self._zoom = 1.0  # ضریب بزرگ‌نمایی نسبت به اندازه‌ی جاافتاده در پنجره
        self._base_pixmap = QPixmap()

        self.image_label = QLabel("در حال بارگذاری...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background:#111; border-radius:6px;")

        zoom_row = QHBoxLayout()
        zoom_row.addStretch()
        self.zoom_in_btn = QPushButton("🔍+ بزرگ‌تر")
        self.zoom_in_btn.clicked.connect(self._on_zoom_in)
        zoom_row.addWidget(self.zoom_in_btn)
        self.zoom_out_btn = QPushButton("🔍− کوچک‌تر")
        self.zoom_out_btn.clicked.connect(self._on_zoom_out)
        zoom_row.addWidget(self.zoom_out_btn)
        fit_btn = QPushButton("⤢ اندازه‌ی پنجره")
        fit_btn.clicked.connect(self._on_zoom_reset)
        zoom_row.addWidget(fit_btn)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        zoom_row.addWidget(close_btn)
        zoom_row.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self.image_label, 1)
        layout.addLayout(zoom_row)

        self._load()

    # ------------------------------------------------------------------ بارگذاری
    def _load(self):
        """خواندن تصویر فقط در لحظه‌ی باز شدن (on-demand)."""
        if not self._path or not os.path.exists(self._path):
            self.image_label.setText("⚠ تصویر یافت نشد")
            self._set_zoom_enabled(False)
            return
        pix = QPixmap(self._path)
        if pix.isNull():
            self.image_label.setText("⚠ فایل تصویر خراب است")
            self._set_zoom_enabled(False)
            return
        self._base_pixmap = pix
        self._set_zoom_enabled(True)
        self._render()

    def _set_zoom_enabled(self, enabled):
        self.zoom_in_btn.setEnabled(enabled)
        self.zoom_out_btn.setEnabled(enabled)

    # ------------------------------------------------------------------ نمایش
    def _render(self):
        if self._base_pixmap.isNull():
            return
        target_w = max(1, int(self.image_label.width() * self._zoom))
        target_h = max(1, int(self.image_label.height() * self._zoom))
        scaled = self._base_pixmap.scaled(
            target_w, target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    # ------------------------------------------------------------------ زوم
    def _on_zoom_in(self):
        self._zoom = min(4.0, self._zoom * 1.25)
        self._render()

    def _on_zoom_out(self):
        self._zoom = max(0.2, self._zoom / 1.25)
        self._render()

    def _on_zoom_reset(self):
        self._zoom = 1.0
        self._render()

    def closeEvent(self, event):
        """بسته شدن دیالوگ → آزادسازی صریح پیکس‌مپ برای برگرداندن حافظه."""
        self.image_label.setPixmap(QPixmap())
        self._base_pixmap = QPixmap()
        super().closeEvent(event)


def show_image(image_path, title="👁 دیدن تصویر", parent=None):
    """میان‌بر: باز کردن دیالوگ «دیدن تصویر» برای یک مسیر فایل."""
    dlg = ImageViewerDialog(image_path, title=title, parent=parent)
    dlg.exec()
    return dlg

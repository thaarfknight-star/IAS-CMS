# -*- coding: utf-8 -*-
"""دیالوگ زنده‌ی «📊 مدیریت پهنای باند» (2.0.52-beta).

نمایش لحظه‌ای بیت‌ریت هر دوربین فعال، مجموع مصرف، سقف تعیین‌شده و سهم برابر
هر دوربین + دکمه‌ی «اعمال سهم برابر» که بیت‌ریت انکدر دوربین‌های مستقیم را
از طریق ONVIF روی سهم برابر تنظیم می‌کند.
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QMessageBox, QAbstractItemView,
)

from bandwidth import BandwidthMonitor, apply_fair_share_blocking


class _FairShareThread(QThread):
    progress = pyqtSignal(str, bool, str)  # label, ok, message
    finished_all = pyqtSignal()

    def __init__(self, cameras, share_kbps, parent=None):
        super().__init__(parent)
        self._cameras = cameras
        self._share = share_kbps

    def run(self):
        for cam in self._cameras:
            label = cam.get("name") or cam.get("ip") or "؟"
            try:
                ok, msg = apply_fair_share_blocking(cam, self._share)
            except Exception as e:
                ok, msg = False, str(e)[:100]
            self.progress.emit(label, ok, msg)
        self.finished_all.emit()


class BandwidthDialog(QDialog):
    COLUMNS = ["دوربین", "بیت‌ریت زنده", "نسبت به سهم برابر", "وضعیت"]

    def __init__(self, monitor: BandwidthMonitor, camera_store,
                 total_mbps=0, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.camera_store = camera_store
        self.total_mbps = total_mbps
        self.setWindowTitle("📊 مدیریت پهنای باند")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(620, 420)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-size: 13px; font-weight: bold; padding: 6px;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.table, 1)

        hint = QLabel(
            "💡 سهم برابر = سقف کلی ÷ تعداد دوربین‌های فعال. «اعمال سهم برابر» بیت‌ریت "
            "انکدر دوربین‌های مستقیم را از طریق ONVIF استاندارد تنظیم می‌کند؛ "
            "کانال‌های NVR و دوربین‌های بدون ONVIF فقط نمایشی‌اند و باید دستی از "
            "پنل خود دستگاه کم شوند.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self.apply_btn = QPushButton("⚖ اعمال سهم برابر روی دوربین‌ها (ONVIF)")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self.apply_btn)
        btn_row.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._apply_thread = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(2000)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        snap = self.monitor.snapshot(self.total_mbps)
        total = snap["total_kbps"]
        share = snap["share_kbps"]
        n = snap["n_active"]
        if share:
            cap_txt = f"{self.total_mbps:g} مگابیت/ثانیه"
            self.summary_label.setText(
                f"مصرف کل: {total:,.0f} کیلوبیت/ثانیه   |   سقف: {cap_txt}   |   "
                f"سهم برابر هر دوربین ({n} فعال): {share:,.0f} کیلوبیت/ثانیه")
        else:
            self.summary_label.setText(
                f"مصرف کل: {total:,.0f} کیلوبیت/ثانیه   |   سقف: نامحدود (فقط نمایش)   |   "
                f"دوربین‌های فعال: {n}")

        items = snap["items"]
        self.table.setRowCount(len(items))
        for row, it in enumerate(items):
            self.table.setItem(row, 0, QTableWidgetItem(it["label"]))
            self.table.setItem(row, 1, QTableWidgetItem(f"{it['kbps']:,.0f} kbps"))

            bar = QProgressBar()
            bar.setTextVisible(True)
            if share and share > 0:
                pct = min(100, int(it["kbps"] / share * 100))
                bar.setValue(pct)
                bar.setFormat(f"%p% از سهم ({it['kbps']:,.0f} از {share:,.0f})")
            else:
                bar.setRange(0, 0)  # نامشخص
                bar.setFormat(f"{it['kbps']:,.0f} kbps")
            if it["status"] == "over":
                bar.setStyleSheet("QProgressBar::chunk { background-color: #e74c3c; }")
            elif it["status"] == "stale":
                bar.setStyleSheet("QProgressBar::chunk { background-color: #7f8c8d; }")
            else:
                bar.setStyleSheet("QProgressBar::chunk { background-color: #2ecc71; }")
            self.table.setCellWidget(row, 2, bar)

            status_txt = {"ok": "✅ عادی", "over": "🔴 پرمصرف",
                          "stale": "⚪ بدون داده"}[it["status"]]
            self.table.setItem(row, 3, QTableWidgetItem(status_txt))

        self.apply_btn.setEnabled(bool(share))

    # ------------------------------------------------------------------
    def _direct_cameras(self):
        cams = []
        for cam in self.camera_store.get_cameras():
            if cam.get("nvr_id"):
                continue  # کانال NVR: از طریق ONVIF خود NVR قابل تنظیم نیست
            cams.append(cam)
        return cams

    def _on_apply(self):
        snap = self.monitor.snapshot(self.total_mbps)
        share = snap["share_kbps"]
        if not share:
            QMessageBox.information(self, "سهم برابر",
                                    "اول در تنظیمات یک سقف کلی (مگابیت/ثانیه) تعیین کنید.")
            return
        cams = self._direct_cameras()
        if not cams:
            QMessageBox.information(self, "سهم برابر",
                                    "دوربین مستقیمی برای اعمال وجود ندارد (کانال‌های NVR "
                                    "از این طریق قابل تنظیم نیستند).")
            return
        confirm = QMessageBox.question(
            self, "اعمال سهم برابر",
            f"بیت‌ریت انکدر {len(cams)} دوربین مستقیم روی {share:,.0f} کیلوبیت/ثانیه "
            f"تنظیم شود؟\n(از طریق ONVIF؛ ممکن است چند ثانیه طول بکشد)")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("⏳ در حال اعمال...")
        self._results = []
        self._apply_thread = _FairShareThread(cams, share, self)
        self._apply_thread.progress.connect(self._on_one_result)
        self._apply_thread.finished_all.connect(self._on_apply_done)
        self._apply_thread.start()

    def _on_one_result(self, label, ok, msg):
        self._results.append((label, ok, msg))

    def _on_apply_done(self):
        self.apply_btn.setEnabled(True)
        self.apply_btn.setText("⚖ اعمال سهم برابر روی دوربین‌ها (ONVIF)")
        ok_n = sum(1 for _, ok, _ in self._results if ok)
        lines = [f"{'✅' if ok else '❌'} {label}: {msg}"
                 for label, ok, msg in self._results]
        QMessageBox.information(
            self, "نتیجه‌ی اعمال سهم برابر",
            f"{ok_n} از {len(self._results)} دوربین موفق.\n\n" + "\n".join(lines))

    def closeEvent(self, event):
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

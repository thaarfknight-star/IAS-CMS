# -*- coding: utf-8 -*-
"""دیالوگ زنده‌ی «📊 مدیریت پهنای باند» (2.0.52-beta؛ بازنویسی 2.0.64-beta).

نمایش لحظه‌ای بیت‌ریت هر دوربین + «بیت‌ریت درخواستی» قابل‌ویرایش هر دوربین
(کیلوبیت/ثانیه — واحد kbps کنار هر کادر نوشته شده) + سهم نهایی محاسبه‌شده
+ ذخیره و «اعمال روی دوربین‌ها» که بیت‌ریت انکدر دوربین‌های مستقیم را از
طریق ONVIF دقیقاً روی سهم نهایی همان دوربین تنظیم می‌کند.

قانون تخصیص (bandwidth.allocate_bandwidth):
  دوربین دارای بیت‌ریت درخواستی → دقیقاً همان مقدار؛
  بقیه → باقی‌مانده‌ی سقف به‌تساوی بینشان؛
  اگر سقف نامحدود بود → بقیه «خودکار» می‌مانند.
کانال‌های NVR از طریق ONVIF قابل تنظیم نیستند: فقط نمایشی‌اند.
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QMessageBox,
    QAbstractItemView, QSpinBox,
)

from bandwidth import (
    BandwidthMonitor, allocate_bandwidth, apply_target_bitrate_blocking,
)

# ستون‌ها: 0=دوربین، 1=بیت‌ریت زنده، 2=بیت‌ریت درخواستی (اسپین kbps)،
#          3=سهم نهایی، 4=نسبت به هدف (نوار)، 5=وضعیت
(COL_CAM, COL_LIVE, COL_WANT, COL_TARGET, COL_BAR, COL_STATUS) = range(6)


class _ApplyThread(QThread):
    progress = pyqtSignal(str, bool, str)  # label, ok, message
    finished_all = pyqtSignal()

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self._jobs = jobs  # [(cam_dict, target_kbps)]

    def run(self):
        for cam, target in self._jobs:
            label = cam.get("name") or cam.get("ip") or "؟"
            try:
                ok, msg = apply_target_bitrate_blocking(cam, target)
            except Exception as e:
                ok, msg = False, str(e)[:100]
            self.progress.emit(label, ok, msg)
        self.finished_all.emit()


class BandwidthDialog(QDialog):
    COLUMNS = ["دوربین", "بیت‌ریت زنده", "بیت‌ریت درخواستی (kbps)",
               "سهم نهایی", "نسبت به هدف", "وضعیت"]

    def __init__(self, monitor: BandwidthMonitor, camera_store,
                 total_mbps=0, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.camera_store = camera_store
        self.total_mbps = total_mbps
        self.setWindowTitle("📊 مدیریت پهنای باند")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(820, 470)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-size: 13px; font-weight: bold; padding: 6px;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.warn_label = QLabel("")
        self.warn_label.setStyleSheet("color: #e74c3c; font-size: 12px; font-weight: bold;")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        layout.addWidget(self.warn_label)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(COL_CAM, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_BAR, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.table, 1)

        hint = QLabel(
            "💡 «بیت‌ریت درخواستی» هر دوربین به کیلوبیت/ثانیه (kbps) است؛ "
            "«خودکار» یعنی سهم برابر از باقی‌مانده‌ی سقف. «ذخیره» مقادیر را در "
            "تنظیمات دوربین ثبت می‌کند و «اعمال روی دوربین‌ها» بیت‌ریت انکدر "
            "دوربین‌های مستقیم را از طریق ONVIF دقیقاً روی سهم نهایی همان "
            "دوربین تنظیم می‌کند. کانال‌های NVR فقط نمایشی‌اند.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("💾 ذخیره‌ی بیت‌ریت‌ها")
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.save_btn)
        self.apply_btn = QPushButton("⚖ اعمال روی دوربین‌ها (ONVIF)")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self.apply_btn)
        btn_row.addStretch()
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._apply_thread = None
        self._results = []
        self._row_cams = []   # [cam_dict] هم‌ترتیب با ردیف‌های جدول
        self._spins = {}      # cam_id -> QSpinBox
        self._rebuild_rows()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(2000)
        self.refresh()

    # ------------------------------------------------------------ ساختار جدول
    def _managed_cameras(self):
        try:
            return self.camera_store.get_cameras() or []
        except Exception:
            return []

    @staticmethod
    def _wanted_kbps(cam):
        try:
            return max(0, int(float(cam.get("bitrate_kbps") or 0)))
        except Exception:
            return 0

    def _rebuild_rows(self):
        cams = self._managed_cameras()
        self._row_cams = list(cams)
        self._spins = {}
        self.table.setRowCount(len(cams))
        for row, cam in enumerate(cams):
            cid = cam.get("id")
            is_nvr = bool(cam.get("nvr_id"))
            label = cam.get("name") or cam.get("ip") or "؟"
            if is_nvr:
                label = f"{label} (کانال NVR)"
            self.table.setItem(row, COL_CAM, QTableWidgetItem(label))
            self.table.setItem(row, COL_LIVE, QTableWidgetItem("—"))

            spin = QSpinBox()
            spin.setRange(0, 100000)
            spin.setSingleStep(256)
            spin.setSuffix(" kbps")          # واحد دقیقاً کنار کادر
            spin.setSpecialValueText("خودکار")
            spin.setValue(self._wanted_kbps(cam))
            spin.valueChanged.connect(self.refresh)
            if is_nvr:
                spin.setEnabled(False)
                spin.setToolTip("کانال NVR از طریق ONVIF قابل تنظیم نیست؛ فقط نمایشی است.")
            else:
                spin.setToolTip("بیت‌ریت درخواستی این دوربین (kbps)؛ «خودکار» یعنی سهم برابر.")
            self.table.setCellWidget(row, COL_WANT, spin)
            self._spins[cid] = spin

            self.table.setItem(row, COL_TARGET, QTableWidgetItem("—"))
            bar = QProgressBar()
            bar.setTextVisible(True)
            self.table.setCellWidget(row, COL_BAR, bar)
            self.table.setItem(row, COL_STATUS, QTableWidgetItem("—"))

    # ------------------------------------------------------------ تخصیص لحظه‌ای
    def _current_allocation(self):
        """تخصیص بر اساس مقادیر فعلی اسپین‌ها (پیش‌نمایش زنده، حتی قبل از ذخیره)."""
        managed = []
        for cam in self._row_cams:
            if cam.get("nvr_id"):
                continue
            cid = cam.get("id")
            spin = self._spins.get(cid)
            want = spin.value() if spin is not None else self._wanted_kbps(cam)
            managed.append({"id": cid,
                            "label": cam.get("name") or cam.get("ip") or "؟",
                            "bitrate_kbps": want})
        return allocate_bandwidth(managed, self.total_mbps)

    # ------------------------------------------------------------------ رفرش
    def refresh(self):
        # اگر لیست دوربین‌ها عوض شده، ردیف‌ها را بازسازی کن
        try:
            cur_ids = [c.get("id") for c in self._managed_cameras()]
        except Exception:
            cur_ids = []
        if cur_ids != [c.get("id") for c in self._row_cams]:
            self._rebuild_rows()

        alloc = self._current_allocation()
        allocs = alloc["allocations"]
        targets = {cid: a["target_kbps"] for cid, a in allocs.items()
                   if a["target_kbps"]}
        snap = self.monitor.snapshot(self.total_mbps, targets=targets)
        live = {it["cam_id"]: it for it in snap["items"]}

        # خلاصه
        cap_txt = f"{self.total_mbps:g} مگابیت/ثانیه" if self.total_mbps > 0 else "نامحدود"
        if alloc["equal_share_kbps"]:
            share_txt = f"{alloc['equal_share_kbps']:,.0f} kbps"
        elif self.total_mbps > 0:
            share_txt = "۰ (سقف پر شده)"
        else:
            share_txt = "خودکار"
        self.summary_label.setText(
            f"سقف کلی: {cap_txt}   |   دوربین دارای بیت‌ریت ثابت: {alloc['n_fixed']} "
            f"(مجموع {alloc['total_kbps']:,.0f} kbps)   |   سهم برابر بقیه "
            f"({alloc['n_auto']} دوربین): {share_txt}")
        if alloc["warnings"]:
            self.warn_label.setText("⚠ " + " ".join(alloc["warnings"]))
            self.warn_label.setVisible(True)
        else:
            self.warn_label.setVisible(False)

        for row, cam in enumerate(self._row_cams):
            cid = cam.get("id")
            is_nvr = bool(cam.get("nvr_id"))
            it = live.get(cid)
            has_live = bool(it and it["status"] != "stale")

            # ستون 1: بیت‌ریت زنده
            if it and it["status"] != "stale":
                self.table.item(row, COL_LIVE).setText(f"{it['kbps']:,.0f} kbps")
            elif it:
                self.table.item(row, COL_LIVE).setText("بدون داده")
            else:
                self.table.item(row, COL_LIVE).setText("—")

            bar = self.table.cellWidget(row, COL_BAR)

            if is_nvr:
                self.table.item(row, COL_TARGET).setText("—")
                self.table.item(row, COL_STATUS).setText("⛔ فقط نمایشی (NVR)")
                if isinstance(bar, QProgressBar):
                    bar.setRange(0, 0)
                    bar.setFormat("—")
                    bar.setStyleSheet("QProgressBar::chunk { background-color: #7f8c8d; }")
                continue

            a = allocs.get(cid, {})
            target = a.get("target_kbps")
            mode = a.get("mode", "auto")
            if target:
                mode_txt = {"fixed": "ثابت", "equal": "سهم برابر"}.get(mode, "")
                self.table.item(row, COL_TARGET).setText(f"{target:,.0f} kbps ({mode_txt})")
            else:
                self.table.item(row, COL_TARGET).setText("خودکار")

            if not it or it["status"] == "stale":
                status_txt, chunk = "⚪ بدون داده", "#7f8c8d"
            elif it["status"] == "over":
                status_txt, chunk = "🔴 پرمصرف", "#e74c3c"
            else:
                status_txt, chunk = "✅ عادی", "#2ecc71"
            self.table.item(row, COL_STATUS).setText(status_txt)

            if isinstance(bar, QProgressBar):
                if target and has_live and target > 0:
                    pct = min(100, int(it["kbps"] / target * 100))
                    bar.setRange(0, 100)
                    bar.setValue(pct)
                    bar.setFormat(f"%p% از هدف ({it['kbps']:,.0f} از {target:,.0f})")
                else:
                    bar.setRange(0, 0)
                    bar.setFormat("—")
                bar.setStyleSheet(
                    f"QProgressBar::chunk {{ background-color: {chunk}; }}")

        self.apply_btn.setEnabled(bool(allocs))

    # ------------------------------------------------------------------ ذخیره
    def _on_save(self):
        n = 0
        for cam in self._row_cams:
            if cam.get("nvr_id"):
                continue
            cid = cam.get("id")
            spin = self._spins.get(cid)
            if spin is None:
                continue
            try:
                self.camera_store.update_camera(cid, bitrate_kbps=int(spin.value()))
                n += 1
            except Exception:
                pass
        QMessageBox.information(self, "ذخیره", f"بیت‌ریت درخواستی {n} دوربین ذخیره شد.")
        self.refresh()

    # ------------------------------------------------------------------ اعمال
    def _on_apply(self):
        alloc = self._current_allocation()
        allocs = alloc["allocations"]
        jobs = []
        for cam in self._row_cams:
            if cam.get("nvr_id"):
                continue
            a = allocs.get(cam.get("id"), {})
            target = a.get("target_kbps")
            if target:
                jobs.append((cam, target))
        if not jobs:
            QMessageBox.information(
                self, "اعمال",
                "سهم نهایی هیچ دوربینی مشخص نیست؛ اول سقف کلی یا بیت‌ریت درخواستی تعیین کنید.")
            return
        detail = "\n".join(
            f"• {(c.get('name') or c.get('ip'))}: {t:,.0f} kbps" for c, t in jobs)
        confirm = QMessageBox.question(
            self, "اعمال روی دوربین‌ها",
            f"بیت‌ریت انکدر {len(jobs)} دوربین مستقیم دقیقاً روی این مقادیر تنظیم شود؟\n"
            f"(از طریق ONVIF؛ ممکن است چند ثانیه طول بکشد)\n\n{detail}")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.apply_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.apply_btn.setText("⏳ در حال اعمال...")
        self._results = []
        self._apply_thread = _ApplyThread(jobs, self)
        self._apply_thread.progress.connect(self._on_one_result)
        self._apply_thread.finished_all.connect(self._on_apply_done)
        self._apply_thread.start()

    def _on_one_result(self, label, ok, msg):
        self._results.append((label, ok, msg))

    def _on_apply_done(self):
        self.apply_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.apply_btn.setText("⚖ اعمال روی دوربین‌ها (ONVIF)")
        ok_n = sum(1 for _, ok, _ in self._results if ok)
        lines = [f"{'✅' if ok else '❌'} {label}: {msg}"
                 for label, ok, msg in self._results]
        QMessageBox.information(
            self, "نتیجه‌ی اعمال",
            f"{ok_n} از {len(self._results)} دوربین موفق.\n\n" + "\n".join(lines))
        self.refresh()

    def closeEvent(self, event):
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

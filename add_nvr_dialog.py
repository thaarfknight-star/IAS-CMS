from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QLabel,
    QComboBox, QSpinBox, QPushButton, QListWidget, QListWidgetItem,
    QDialogButtonBox, QMessageBox
)

from nvr_scanner import NVRScanThread, BRAND_LABELS


class AddNVRDialog(QDialog):
    """افزودن یک NVR: اطلاعات اتصال گرفته می‌شود، سپس کانال‌های (دوربین‌های)
    متصل به آن اسکن و لیست می‌شوند تا کاربر انتخاب کند کدام‌ها اضافه شوند."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("افزودن NVR")
        self.setMinimumWidth(420)
        self.scan_thread = None
        self.found_channels = []  # [{"channel": int, "name": str, "path_or_url": str, "is_full_url": bool}]

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("مثلاً: اتاق سرور، NVR ساختمان اصلی")
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP دستگاه NVR")
        self.rtsp_port_input = QLineEdit("554")
        self.onvif_port_input = QLineEdit("80")
        self.onvif_port_input.setPlaceholderText("اختیاری - برای کشف دقیق‌تر کانال‌ها")
        self.user_input = QLineEdit("admin")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.brand_combo = QComboBox()
        for key, label in BRAND_LABELS.items():
            self.brand_combo.addItem(label, key)

        self.max_channels_input = QSpinBox()
        self.max_channels_input.setRange(1, 128)
        self.max_channels_input.setValue(16)

        form = QFormLayout()
        form.addRow("نام NVR:", self.name_input)
        form.addRow("آدرس IP:", self.ip_input)
        form.addRow("پورت RTSP:", self.rtsp_port_input)
        form.addRow("پورت ONVIF (اختیاری):", self.onvif_port_input)
        form.addRow("نام کاربری:", self.user_input)
        form.addRow("رمز عبور:", self.pass_input)
        form.addRow("برند NVR:", self.brand_combo)
        form.addRow("حداکثر تعداد کانال برای بررسی:", self.max_channels_input)

        self.scan_btn = QPushButton("جستجوی کانال‌های متصل")
        self.scan_btn.clicked.connect(self.start_scan)
        # نکته (رفع باگ قفل‌شدن دیالوگ حین اسکن): NVRScanThread از قبل متد
        # cancel() را داشت اما در نسخه‌ی قبلی هیچ دکمه‌ای آن را صدا نمی‌زد و کل
        # دکمه‌ها (از جمله Cancel) حین اسکن غیرفعال می‌شدند؛ اگر اسکن طول
        # می‌کشید یا دستگاه پاسخ نمی‌داد، کاربر هیچ راهی برای لغو/بستن دیالوگ
        # از طریق دکمه‌ها نداشت (فقط بستن با ضربدر که باعث کرش هنگام تخریب
        # ترد در حال اجرا می‌شد). اکنون خود دکمه‌ی جستجو حین اسکن به «لغو
        # جستجو» تبدیل و همچنان فعال می‌ماند.

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")

        self.channels_list = QListWidget()
        self.channels_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)

        select_row = QHBoxLayout()
        select_all_btn = QPushButton("انتخاب همه")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        select_none_btn = QPushButton("لغو انتخاب همه")
        select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all_btn)
        select_row.addWidget(select_none_btn)
        select_row.addStretch()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("افزودن NVR و کانال‌های انتخاب‌شده")
        self.buttons.accepted.connect(self.handle_accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.scan_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("کانال‌های شناسایی‌شده (تیک بزنید تا اضافه شوند):"))
        layout.addWidget(self.channels_list)
        layout.addLayout(select_row)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

    # ------------------------------------------------------------- scan ---

    def start_scan(self):
        if self.scan_thread is not None and self.scan_thread.isRunning():
            # دکمه در حالت اسکن به «لغو جستجو» تبدیل شده؛ کلیک دوباره یعنی لغو.
            self.status_label.setText("در حال لغو جستجو...")
            self.scan_thread.cancel()
            self.scan_btn.setEnabled(False)
            return

        ip = self.ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP دستگاه NVR را وارد کنید.")
            return

        self.channels_list.clear()
        self.found_channels = []
        self.scan_btn.setText("لغو جستجو")
        # فقط دکمه‌ی OK غیرفعال می‌شود (چون نتایج هنوز کامل نیست)؛ خود
        # QDialogButtonBox دیگر به‌صورت کامل غیرفعال نمی‌شود تا کاربر همیشه
        # بتواند دیالوگ را از طریق دکمه‌ی Cancel ببندد.
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(False)
        self.status_label.setText("در حال جستجوی کانال‌های متصل به NVR...")

        onvif_port = self.onvif_port_input.text().strip()
        self.scan_thread = NVRScanThread(
            ip=ip,
            rtsp_port=self.rtsp_port_input.text().strip() or "554",
            onvif_port=onvif_port,
            user=self.user_input.text().strip(),
            pwd=self.pass_input.text().strip(),
            brand=self.brand_combo.currentData(),
            max_channels=self.max_channels_input.value(),
        )
        self.scan_thread.progress_signal.connect(self.status_label.setText)
        self.scan_thread.channel_found_signal.connect(self._on_channel_found)
        self.scan_thread.finished_signal.connect(self._on_scan_finished)
        self.scan_thread.failed_signal.connect(self._on_scan_failed)
        self.scan_thread.start()

    def _on_channel_found(self, channel, name, path_or_url):
        is_full_url = path_or_url.startswith("rtsp://")
        entry = {
            "channel": channel,
            "name": name,
            "path_or_url": path_or_url,
            "is_full_url": is_full_url,
        }
        self.found_channels.append(entry)

        default_name = f"{self.name_input.text().strip() or 'NVR'} - کانال {channel}"
        item = QListWidgetItem(f"{default_name}   ({'ONVIF' if is_full_url else path_or_url})")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setData(Qt.ItemDataRole.UserRole + 1, default_name)
        self.channels_list.addItem(item)

    def _on_scan_finished(self, count):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("جستجوی کانال‌های متصل")
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(True)
        if count:
            self.status_label.setText(f"{count} کانال یافت شد. کانال‌های موردنظر برای افزودن را تیک بزنید.")

    def _on_scan_failed(self, msg):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("جستجوی کانال‌های متصل")
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(True)
        self.status_label.setText(msg)
        QMessageBox.warning(self, "نتیجه جستجو", msg)

    def _stop_scan_thread(self):
        """در صورت فعال بودن اسکن، آن را لغو و منتظر پایان امن ترد می‌ماند.
        بدون این کار، اگر کاربر دیالوگ را حین اسکن ببندد، Qt هنگام تخریب یک
        QThread هنوز در حال اجرا کرش می‌کند - یکی از منابع بسته شدن ناگهانی
        برنامه هنگام کار با NVR."""
        if self.scan_thread is not None and self.scan_thread.isRunning():
            self.scan_thread.cancel()
            self.scan_thread.wait(3000)

    def closeEvent(self, event):
        self._stop_scan_thread()
        event.accept()

    def reject(self):
        self._stop_scan_thread()
        super().reject()

    def _set_all_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.channels_list.count()):
            self.channels_list.item(i).setCheckState(state)

    # ------------------------------------------------------------ accept ---

    def handle_accept(self):
        ip = self.ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP دستگاه NVR را وارد کنید.")
            return

        selected = []
        for i in range(self.channels_list.count()):
            item = self.channels_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                entry = item.data(Qt.ItemDataRole.UserRole)
                default_name = item.data(Qt.ItemDataRole.UserRole + 1)
                selected.append((entry, default_name))

        if not selected and self.found_channels:
            QMessageBox.warning(self, "خطا", "حداقل یک کانال را برای افزودن انتخاب کنید.")
            return

        if not self.found_channels:
            confirm = QMessageBox.question(
                self, "بدون کانال",
                "هیچ کانالی جستجو/انتخاب نشده است. آیا فقط خود NVR (بدون کانال) اضافه شود؟"
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self._selected_channels = selected
        self.accept()

    def get_nvr_data(self):
        # نکته: کلید "pass" (نه "pwd") استفاده شده چون دقیقاً همان نامی است که در
        # camera_store برای فیلد ذخیره‌شده‌ی NVR/دوربین به‌کار می‌رود (update_nvr /
        # cam["pass"])؛ "pass" کلمه‌ی رزرو شده‌ی پایتون است پس نمی‌تواند نام آرگومان
        # تابع باشد، به همین دلیل در main.py هنگام فراخوانی add_nvr به‌صورت دستی به
        # آرگومان pwd نگاشت می‌شود.
        return {
            "name": self.name_input.text().strip() or self.ip_input.text().strip(),
            "ip": self.ip_input.text().strip(),
            "rtsp_port": self.rtsp_port_input.text().strip() or "554",
            "onvif_port": self.onvif_port_input.text().strip(),
            "user": self.user_input.text().strip(),
            "pass": self.pass_input.text().strip(),
            "brand": self.brand_combo.currentData(),
        }

    def get_selected_channels(self):
        """[(entry_dict, default_name), ...] entry_dict has channel/path_or_url/is_full_url."""
        return getattr(self, "_selected_channels", [])

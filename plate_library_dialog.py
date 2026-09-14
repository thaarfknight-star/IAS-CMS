# -*- coding: utf-8 -*-
"""صفحه‌ی «پلاک‌خوان» - مثل Face Library یک صفحه‌ی جداگانه داخل QStackedWidget
پنجره‌ی اصلی (قابل دسترسی از هدر بالای برنامه) با دو تب داخلی:

  تب ۱ «تعریف پلاک‌ها»:   تعریف حرفه‌ای پلاک (ورودی بخش‌بندی‌شده‌ی پلاک ایرانی
                          با اعتبارسنجی، مشخصات مالک/خودرو، تصویر نمونه،
                          تشخیص تکراری) + انتخاب دوربین‌های فعال پلاک‌خوان
  تب ۲ «گزارش عبور»:      گزارش عبور پلاک‌های تعریف‌شده و تعریف‌نشده با فیلتر
                          تاریخ/دوربین/وضعیت، تصویر هر عبور، تعریف سریع پلاک
                          ناشناس از روی همان ردیف، و خروجی CSV

نکته: خوانش OCR ممکن است خطا داشته باشد؛ برای همین تطبیق با پلاک‌های تعریف‌شده
هم دقیق و هم فازی (تحمل خطای OCR) انجام می‌شود و هر عبورِ کم‌اطمینان با تصویر
برش‌خورده ذخیره می‌شود تا کاربر با یک کلیک آن را تعریف کند.
"""

import os
import re
import threading
import uuid

import cv2

from PyQt6.QtCore import Qt, QDate, QTimer
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (
    QBoxLayout,
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLineEdit, QTextEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QDialogButtonBox, QHeaderView, QLabel,
    QFileDialog, QDateEdit, QGroupBox, QListWidget, QListWidgetItem,
    QCheckBox, QDoubleSpinBox, QSpinBox, QSplitter,
)

from plate_store import (
    plate_store, normalize_plate_text, prettify_plate,
    validate_iranian_plate, validate_phone,
    IRANIAN_PLATE_LETTERS, VEHICLE_TYPES, VEHICLE_COLORS,
)


def _bgr_to_pixmap(frame, max_w=320):
    """تبدیل فریم BGR به QPixmap برای پیش‌نمایش."""
    if frame is None:
        return None
    try:
        h, w = frame.shape[:2]
        scale = min(1.0, max_w / max(1, w))
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        hh, ww = rgb.shape[:2]
        from PyQt6.QtGui import QImage
        qimg = QImage(rgb.data, ww, hh, ww * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())
    except Exception:
        return None


def _save_sample_image(frame):
    """ذخیره‌ی تصویر نمونه‌ی پلاک؛ خروجی مسیر فایل یا رشته‌ی خالی."""
    if frame is None:
        return ""
    try:
        d = os.path.join(plate_store.base_dir, "samples")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{uuid.uuid4().hex}.jpg")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok:
            with open(path, "wb") as f:
                f.write(buf.tobytes())
            return path
    except Exception:
        pass
    return ""


def _detect_plate_in_frame(frame):
    """اجرای تشخیص+OCR روی یک فریم (برای «افزودن از دوربین»).
    خروجی: (crop_bgr یا None, متن خوانده‌شده یا "", پیام خطا یا "")."""
    try:
        from plate_detector import get_shared_plate_detector, get_shared_plate_ocr
    except Exception as e:
        return None, "", f"خطا در بارگذاری ماژول پلاک‌خوان: {e}"
    det = get_shared_plate_detector()
    if det is None or not det.available:
        return None, "", getattr(det, "load_error", "مدل پلاک‌خوان در دسترس نیست.") \
            or "مدل پلاک‌خوان در دسترس نیست."
    boxes = det.detect(frame)
    if not boxes:
        return None, "", "پلاکی در تصویر فعلی دوربین تشخیص داده نشد؛ خودرو را نزدیک‌تر بیاورید."
    # بزرگ‌ترین باکس = نزدیک‌ترین/واضح‌ترین پلاک
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    x1, y1, x2, y2, _c = boxes[0]
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    crop = frame[y1:y2, x1:x2].copy()
    text = ""
    try:
        ocr = get_shared_plate_ocr()
        reads = ocr.read(crop)
        if reads:
            text = reads[0][0]
    except Exception:
        pass
    return crop, text, ""


class PlateFormDialog(QDialog):
    """فرم حرفه‌ای تعریف/ویرایش پلاک: ورودی بخش‌بندی‌شده‌ی پلاک ایرانی با
    اعتبارسنجی زنده، مشخصات کامل مالک و خودرو، تصویر نمونه از دوربین، و
    کنترل تکراری بودن."""

    def __init__(self, parent=None, existing=None, prefill_text="",
                 prefill_snapshot=None, get_frame_callback=None):
        super().__init__(parent)
        self.setWindowTitle("تعریف پلاک جدید" if existing is None else "ویرایش پلاک")
        self.setMinimumWidth(460)
        self.existing = existing
        self.get_frame_callback = get_frame_callback
        self.sample_frame = prefill_snapshot  # numpy BGR یا None
        self._capture_timer = None

        # ------------------------------------------------- تب‌های نوع پلاک -
        self.kind_tabs = QTabWidget()
        # --- پلاک ایرانی (بخش‌بندی‌شده)
        ir_widget = QWidget()
        ir_form = QFormLayout(ir_widget)
        seg_row = QHBoxLayout()
        seg_row.setDirection(QBoxLayout.Direction.RightToLeft)
        self.d1_input = QLineEdit()
        self.d1_input.setMaxLength(2)
        self.d1_input.setFixedWidth(60)
        self.d1_input.setPlaceholderText("۱۲")
        self.letter_combo = QComboBox()
        self.letter_combo.addItems(IRANIAN_PLATE_LETTERS)
        self.letter_combo.setFixedWidth(70)
        self.d2_input = QLineEdit()
        self.d2_input.setMaxLength(3)
        self.d2_input.setFixedWidth(70)
        self.d2_input.setPlaceholderText("۳۴۵")
        self.code_input = QLineEdit()
        self.code_input.setMaxLength(2)
        self.code_input.setFixedWidth(60)
        self.code_input.setPlaceholderText("۶۷")
        # ترتیب راست‌به‌چپ: کد ایران | ۳ رقم | حرف | ۲ رقم
        seg_row.addWidget(QLabel("ایران:"))
        seg_row.addWidget(self.code_input)
        seg_row.addWidget(self.d2_input)
        seg_row.addWidget(self.letter_combo)
        seg_row.addWidget(self.d1_input)
        seg_row.addStretch()
        ir_form.addRow("شماره پلاک:", seg_row)
        self.kind_tabs.addTab(ir_widget, "پلاک ایرانی")
        # --- سایر پلاک‌ها
        other_widget = QWidget()
        other_form = QFormLayout(other_widget)
        self.other_input = QLineEdit()
        self.other_input.setPlaceholderText("مثلاً: 12ABC345 یا پلاک تشریفاتی")
        other_form.addRow("متن پلاک:", self.other_input)
        self.kind_tabs.addTab(other_widget, "سایر پلاک‌ها")

        self.preview_label = QLabel("")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #4fc3f7; "
            "background: #1e1e1e; border-radius: 8px; padding: 8px;")
        self.preview_label.setMinimumHeight(52)

        for w in (self.d1_input, self.d2_input, self.code_input):
            w.textChanged.connect(self._update_preview)
        self.letter_combo.currentIndexChanged.connect(self._update_preview)
        self.other_input.textChanged.connect(self._update_preview)
        self.kind_tabs.currentChanged.connect(self._update_preview)

        # ---------------------------------------------------- تصویر نمونه -
        sample_row = QHBoxLayout()
        self.sample_label = QLabel("تصویر نمونه ثبت نشده")
        self.sample_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sample_label.setFixedSize(240, 120)
        self.sample_label.setStyleSheet(
            "background-color: #1e1e1e; color: #aaaaaa; border-radius: 8px;")
        sample_col = QVBoxLayout()
        sample_col.addWidget(self.sample_label)
        self.capture_btn = QPushButton("📷 گرفتن تصویر نمونه از دوربین فعال")
        self.capture_btn.clicked.connect(self.capture_from_camera)
        sample_col.addWidget(self.capture_btn)
        sample_row.addStretch()
        sample_row.addLayout(sample_col)
        sample_row.addStretch()

        # ---------------------------------------------------- مشخصات مالک -
        self.owner_input = QLineEdit()
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("09xxxxxxxxx")
        self.phone_input.setMaxLength(11)
        self.vehicle_type_combo = QComboBox()
        self.vehicle_type_combo.addItems(VEHICLE_TYPES)
        self.vehicle_model_input = QLineEdit()
        self.vehicle_model_input.setPlaceholderText("مثلاً: پژو ۲۰۶")
        self.vehicle_color_combo = QComboBox()
        self.vehicle_color_combo.addItems(VEHICLE_COLORS)
        self.desc_input = QTextEdit()
        self.desc_input.setFixedHeight(56)
        self.active_check = QCheckBox("پلاک فعال باشد (در تطبیق شرکت کند)")
        self.active_check.setChecked(True)

        form = QFormLayout()
        form.addRow("نام مالک: *", self.owner_input)
        form.addRow("شماره تلفن:", self.phone_input)
        form.addRow("نوع خودرو:", self.vehicle_type_combo)
        form.addRow("مدل خودرو:", self.vehicle_model_input)
        form.addRow("رنگ خودرو:", self.vehicle_color_combo)
        form.addRow("توضیحات:", self.desc_input)
        form.addRow("", self.active_check)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #ff9e80; font-size: 11px;")
        self.status_label.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("ثبت پلاک")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        buttons.accepted.connect(self.handle_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("مشخصات پلاک:"))
        layout.addWidget(self.kind_tabs)
        layout.addWidget(self.preview_label)
        layout.addLayout(sample_row)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)
        self.setLayout(layout)

        # مقداردهی اولیه (ویرایش یا پیش‌فرض از رویداد)
        if existing:
            self._fill_from_plate(existing)
        elif prefill_text:
            self._prefill_text(prefill_text)
        if prefill_snapshot is not None:
            self._set_sample_frame(prefill_snapshot)
        self._update_preview()

    # ------------------------------------------------------------- کمکی -

    def _fill_from_plate(self, p):
        self.owner_input.setText(p.get("owner_name", ""))
        self.phone_input.setText(p.get("phone", ""))
        idx = self.vehicle_type_combo.findText(p.get("vehicle_type", ""))
        if idx >= 0:
            self.vehicle_type_combo.setCurrentIndex(idx)
        self.vehicle_model_input.setText(p.get("vehicle_model", ""))
        idx = self.vehicle_color_combo.findText(p.get("vehicle_color", ""))
        if idx >= 0:
            self.vehicle_color_combo.setCurrentIndex(idx)
        self.desc_input.setPlainText(p.get("description", ""))
        self.active_check.setChecked(bool(p.get("active", True)))
        canon = p.get("plate_text", "")
        m = re.match(r"^([0-9]{2})([^0-9]{1,2})([0-9]{3})([0-9]{2})$", canon)
        if m:
            d1, letter, d2, code = m.groups()
            self.d1_input.setText(d1)
            li = self.letter_combo.findText(letter)
            if li >= 0:
                self.letter_combo.setCurrentIndex(li)
            self.d2_input.setText(d2)
            self.code_input.setText(code)
            self.kind_tabs.setCurrentIndex(0)
        else:
            self.other_input.setText(canon)
            self.kind_tabs.setCurrentIndex(1)
        sp = p.get("sample_image", "")
        if sp and os.path.isfile(sp):
            pix = QPixmap(sp)
            if not pix.isNull():
                self.sample_label.setPixmap(pix.scaled(
                    240, 120, Qt.AspectRatioMode.KeepAspectRatio))

    def _prefill_text(self, text):
        canon = normalize_plate_text(text)
        m = re.match(r"^([0-9]{2})([^0-9]{1,2})([0-9]{3})([0-9]{2})$", canon)
        if m:
            d1, letter, d2, code = m.groups()
            self.d1_input.setText(d1)
            li = self.letter_combo.findText(letter)
            if li >= 0:
                self.letter_combo.setCurrentIndex(li)
            else:
                # حرف ناشناخته: تب «سایر»
                self.other_input.setText(canon)
                self.kind_tabs.setCurrentIndex(1)
                return
            self.d2_input.setText(d2)
            self.code_input.setText(code)
            self.kind_tabs.setCurrentIndex(0)
        else:
            self.other_input.setText(canon)
            self.kind_tabs.setCurrentIndex(1)

    def _set_sample_frame(self, frame):
        self.sample_frame = frame
        pix = _bgr_to_pixmap(frame, max_w=240)
        if pix is not None:
            self.sample_label.setPixmap(pix.scaled(
                240, 120, Qt.AspectRatioMode.KeepAspectRatio))

    def _update_preview(self):
        canon, _err = self._current_canonical()
        if canon:
            self.preview_label.setText(f"🚗 {prettify_plate(canon)}")
        else:
            self.preview_label.setText("—")

    def _current_canonical(self):
        """خوانش فعلی فرم -> (کانونیکال, پیام خطا)."""
        if self.kind_tabs.currentIndex() == 0:
            ok, err, canon = validate_iranian_plate(
                self.d1_input.text().strip(),
                self.letter_combo.currentText(),
                self.d2_input.text().strip(),
                self.code_input.text().strip())
            return (canon, err) if ok else ("", err)
        canon = normalize_plate_text(self.other_input.text())
        if len(canon) < 3:
            return "", "متن پلاک باید حداقل ۳ نویسه باشد."
        return canon, ""

    # ---------------------------------------------------- گرفتن از دوربین -

    def capture_from_camera(self):
        if self.get_frame_callback is None:
            QMessageBox.warning(self, "خطا", "دسترسی به تصویر دوربین در دسترس نیست.")
            return
        frame = self.get_frame_callback()
        if frame is None:
            QMessageBox.warning(
                self, "خطا",
                "ابتدا یک دوربین را متصل و انتخاب کنید تا تصویر نمونه از آن گرفته شود.")
            return
        self.capture_btn.setEnabled(False)
        self.capture_btn.setText("در حال تشخیص پلاک...")
        self._capture_out = {}
        threading.Thread(target=self._capture_worker,
                         args=(frame.copy(),), daemon=True).start()
        self._capture_timer = QTimer(self)
        self._capture_timer.timeout.connect(self._check_capture)
        self._capture_timer.start(300)

    def _capture_worker(self, frame):
        try:
            crop, text, err = _detect_plate_in_frame(frame)
            self._capture_out = {"crop": crop, "text": text, "err": err}
        except Exception as e:
            self._capture_out = {"crop": None, "text": "", "err": str(e)}
        self._capture_out["done"] = True

    def _check_capture(self):
        if not self._capture_out.get("done"):
            return
        self._capture_timer.stop()
        self.capture_btn.setEnabled(True)
        self.capture_btn.setText("📷 گرفتن تصویر نمونه از دوربین فعال")
        err = self._capture_out.get("err", "")
        if err:
            QMessageBox.warning(self, "تشخیص پلاک", err)
            return
        crop = self._capture_out.get("crop")
        text = self._capture_out.get("text", "")
        if crop is not None:
            self._set_sample_frame(crop)
        if text:
            self._prefill_text(text)
            self._update_preview()
            QMessageBox.information(
                self, "تشخیص پلاک",
                f"پلاک «{prettify_plate(normalize_plate_text(text))}» تشخیص داده شد "
                "و در فرم قرار گرفت؛ لطفاً صحت آن را بررسی و سپس ثبت کنید.")
        else:
            QMessageBox.information(
                self, "تشخیص پلاک",
                "ناحیه‌ی پلاک پیدا شد ولی متنی خوانده نشد؛ تصویر نمونه ثبت شد و "
                "می‌توانید شماره را دستی وارد کنید.")

    # ------------------------------------------------------------- ثبت -

    def handle_accept(self):
        canon, err = self._current_canonical()
        if not canon:
            self.status_label.setText(err)
            return
        if not self.owner_input.text().strip():
            self.status_label.setText("نام مالک الزامی است.")
            return
        ok_phone, phone_norm = validate_phone(self.phone_input.text())
        if not ok_phone:
            self.status_label.setText("شماره تلفن باید به شکل 09xxxxxxxxx باشد (یا خالی بماند).")
            return
        # کنترل تکراری بودن (به‌جز وقتی همین رکورد در حال ویرایش است)
        existing_id = (self.existing or {}).get("id")
        match, _s, _k = plate_store.find_match(canon)
        if match is not None and match["id"] != existing_id:
            self.status_label.setText(
                f"این پلاک قبلاً برای «{match.get('owner_name', '')}» ثبت شده است.")
            return
        self._result_canonical = canon
        self._result_phone = phone_norm
        self.accept()

    def get_data(self):
        sample_path = ""
        if self.sample_frame is not None:
            # اگر در حالت ویرایش تصویر قبلی بود و کاربر عکسی تازه نگرفت، همان بماند
            existing_sample = (self.existing or {}).get("sample_image", "")
            if existing_sample and self.sample_frame is None:
                sample_path = existing_sample
            else:
                sample_path = _save_sample_image(self.sample_frame)
                if not sample_path and existing_sample:
                    sample_path = existing_sample
        elif self.existing:
            sample_path = self.existing.get("sample_image", "")
        return {
            "plate_text": getattr(self, "_result_canonical", ""),
            "plate_display": prettify_plate(getattr(self, "_result_canonical", "")),
            "owner_name": self.owner_input.text().strip(),
            "phone": getattr(self, "_result_phone", ""),
            "vehicle_type": self.vehicle_type_combo.currentText(),
            "vehicle_model": self.vehicle_model_input.text().strip(),
            "vehicle_color": self.vehicle_color_combo.currentText(),
            "description": self.desc_input.toPlainText().strip(),
            "active": self.active_check.isChecked(),
            "sample_image": sample_path,
        }


# --------------------------------------------------------------------------
# دیالوگ جزئیات یک عبور
# --------------------------------------------------------------------------

class PlateEventDetailDialog(QDialog):
    """نمایش بزرگ تصویر عبور + همه‌ی مشخصات + «تعریف این پلاک» برای ناشناس‌ها."""

    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.event = event
        self.setWindowTitle("جزئیات عبور پلاک")
        self.setMinimumWidth(420)
        self.defined_plate_id = None

        layout = QVBoxLayout()

        # تصویر بزرگ
        img_label = QLabel()
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setMinimumSize(380, 190)
        img_label.setStyleSheet("background: #1e1e1e; border-radius: 8px;")
        snap = event.get("snapshot_path", "")
        pix = QPixmap(snap) if snap and os.path.isfile(snap) else QPixmap()
        if pix.isNull():
            img_label.setText("تصویری ثبت نشده")
            img_label.setStyleSheet(
                "background: #1e1e1e; color: #888; border-radius: 8px;")
        else:
            img_label.setPixmap(pix.scaled(
                380, 190, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(img_label)

        info = QFormLayout()
        status = "✅ تعریف‌شده" if event.get("is_defined") else "⚠️ تعریف‌نشده"
        info.addRow("وضعیت:", QLabel(status))
        info.addRow("پلاک خوانده‌شده:", QLabel(event.get("plate_display", "") or "—"))
        info.addRow("مالک:", QLabel(event.get("owner_name", "") or "—"))
        info.addRow("دوربین:", QLabel(event.get("camera_name", "") or "—"))
        info.addRow("تاریخ (شمسی):", QLabel(event.get("date_j", "") or "—"))
        info.addRow("ساعت:", QLabel(event.get("time_g", "") or "—"))
        conf = event.get("confidence") or 0
        info.addRow("اطمینان خوانش:", QLabel(f"{conf:.0%}"))
        layout.addLayout(info)

        btn_row = QHBoxLayout()
        if not event.get("is_defined"):
            self.define_btn = QPushButton("➕ تعریف این پلاک")
            self.define_btn.clicked.connect(self.define_this_plate)
            btn_row.addWidget(self.define_btn)
        self.delete_btn = QPushButton("🗑 حذف این رویداد")
        self.delete_btn.clicked.connect(self.delete_this_event)
        btn_row.addWidget(self.delete_btn)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self.setLayout(layout)

    def define_this_plate(self):
        page = self.parent()
        get_frame = getattr(page, "get_frame_callback", None)
        dlg = PlateFormDialog(
            self, prefill_text=self.event.get("plate_text", ""),
            prefill_snapshot=cv2.imread(self.event.get("snapshot_path", ""))
            if self.event.get("snapshot_path") and os.path.isfile(
                self.event.get("snapshot_path", "")) else None,
            get_frame_callback=get_frame)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, "خطا", result)
                return
            plate = plate_store.get_plate(result)
            plate_store.attach_event_to_plate(self.event["id"], plate)
            self.defined_plate_id = result
            QMessageBox.information(
                self, "انجام شد",
                f"پلاک «{data['plate_display']}» تعریف شد و این عبور به آن متصل شد.")
            self.accept()

    def delete_this_event(self):
        confirm = QMessageBox.question(
            self, "تأیید حذف", "این رویداد عبور حذف شود؟")
        if confirm == QMessageBox.StandardButton.Yes:
            plate_store.delete_event(self.event["id"])
            self.accept()


# --------------------------------------------------------------------------
# صفحه‌ی اصلی پلاک‌خوان (دو تب)
# --------------------------------------------------------------------------

class PlateLibraryPage(QWidget):
    """صفحه‌ی «پلاک‌خوان» داخل QStackedWidget پنجره‌ی اصلی."""

    PLATE_COLUMNS = ["پلاک", "مالک", "تلفن", "نوع خودرو", "مدل", "رنگ", "وضعیت", "تاریخ ثبت"]
    EVENT_COLUMNS = ["تصویر", "تاریخ", "ساعت", "دوربین", "پلاک", "مالک", "وضعیت", "اطمینان"]

    def __init__(self, get_frame_callback, camera_store, on_plate_toggle=None,
                 parent=None):
        super().__init__(parent)
        self.get_frame_callback = get_frame_callback
        self.camera_store = camera_store
        self.on_plate_toggle = on_plate_toggle  # (cam_id, enabled) -> None
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        layout = QVBoxLayout(self)
        title = QLabel("🚗 پلاک‌خوان - تشخیص و گزارش عبور پلاک‌ها")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_define_tab(), "📝 تعریف پلاک‌ها")
        self.tabs.addTab(self._build_report_tab(), "📋 گزارش عبور")
        layout.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.refresh()

    def refresh(self):
        """هر بار که صفحه از هدر باز می‌شود صدا زده می‌شود."""
        self._reload_camera_checklist()
        self.refresh_plates_table()
        self._reload_report_camera_combo()
        self.run_report_search()
        self._update_stats()

    # ============================================================ تب تعریف -

    def _build_define_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # --- دوربین‌های فعال پلاک‌خوان
        cam_group = QGroupBox("🎥 پلاک‌خوان برای کدام دوربین‌ها فعال باشد؟")
        cam_layout = QVBoxLayout()
        self.camera_checklist = QListWidget()
        self.camera_checklist.setMaximumHeight(110)
        self.camera_checklist.itemChanged.connect(self._on_camera_check_changed)
        cam_layout.addWidget(self.camera_checklist)
        cam_hint = QLabel(
            "فقط دوربین‌های تیک‌خورده پلاک را تشخیص می‌دهند (تشخیص در پس‌زمینه و "
            "بدون کند کردن پخش زنده انجام می‌شود).")
        cam_hint.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        cam_hint.setWordWrap(True)
        cam_layout.addWidget(cam_hint)
        cam_group.setLayout(cam_layout)
        layout.addWidget(cam_group)

        # --- جدول پلاک‌ها
        self.plates_table = QTableWidget(0, len(self.PLATE_COLUMNS))
        self.plates_table.setHorizontalHeaderLabels(self.PLATE_COLUMNS)
        self.plates_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.plates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.plates_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.plates_table.doubleClicked.connect(self.edit_plate)
        layout.addWidget(self.plates_table, 1)

        # --- دکمه‌ها
        btn_row = QHBoxLayout()
        add_btn = QPushButton("➕ افزودن پلاک")
        add_btn.clicked.connect(self.add_plate)
        btn_row.addWidget(add_btn)
        add_cam_btn = QPushButton("📷 افزودن از تصویر دوربین")
        add_cam_btn.setToolTip(
            "از تصویر زنده‌ی دوربینِ انتخاب‌شده پلاک را تشخیص می‌دهد و فرم را پر می‌کند")
        add_cam_btn.clicked.connect(self.add_plate_from_camera)
        btn_row.addWidget(add_cam_btn)
        edit_btn = QPushButton("✏️ ویرایش")
        edit_btn.clicked.connect(self.edit_plate)
        btn_row.addWidget(edit_btn)
        del_btn = QPushButton("🗑 حذف")
        del_btn.clicked.connect(self.delete_plate)
        btn_row.addWidget(del_btn)
        toggle_btn = QPushButton("⏸ فعال/غیرفعال")
        toggle_btn.clicked.connect(self.toggle_plate_active)
        btn_row.addWidget(toggle_btn)
        btn_row.addStretch()
        # تنظیمات تطبیق
        btn_row.addWidget(QLabel("آستانه‌ی تطبیق:"))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.50, 1.00)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setValue(plate_store.match_threshold)
        self.threshold_spin.setToolTip(
            "اگر خوانش OCR کمی با پلاک تعریف‌شده فرق داشت (مثلاً یک رقم اشتباه)، "
            "تا چه حد شباهت قابل قبول است. کمتر = بخشنده‌تر، بیشتر = سخت‌گیرانه‌تر.")
        self.threshold_spin.valueChanged.connect(
            lambda v: setattr(plate_store, "match_threshold", float(v)))
        btn_row.addWidget(self.threshold_spin)
        btn_row.addWidget(QLabel("کول‌داون (ثانیه):"))
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(5, 600)
        self.cooldown_spin.setValue(plate_store.cooldown_seconds)
        self.cooldown_spin.setToolTip(
            "حداقل فاصله‌ی بین دو ثبت عبور برای یک پلاک در یک دوربین (جلوگیری از "
            "ثبت تکراری وقتی خودرو جلوی دوربین توقف کرده).")
        self.cooldown_spin.valueChanged.connect(
            lambda v: plate_store.set_setting("cooldown_seconds", str(int(v))))
        btn_row.addWidget(self.cooldown_spin)
        layout.addLayout(btn_row)
        return tab

    def _all_cameras(self):
        """لیست همه‌ی دوربین‌ها: [(cam_id, label)] شامل مستقل و کانال‌های NVR."""
        cams = []
        try:
            for cam in self.camera_store.standalone_cameras():
                cams.append((cam.get("id"), cam.get("name") or cam.get("ip") or "؟"))
            for nvr in self.camera_store.nvrs:
                nvr_name = nvr.get("name") or nvr.get("ip") or ""
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    label = (cam.get("name") or f"کانال {cam.get('channel', '')}")
                    cams.append((cam.get("id"), f"{label} ({nvr_name})"))
        except Exception:
            pass
        return cams

    def _reload_camera_checklist(self):
        self.camera_checklist.blockSignals(True)
        self.camera_checklist.clear()
        cam_by_id = {}
        try:
            for cam in self.camera_store.standalone_cameras():
                cam_by_id[cam.get("id")] = cam
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    cam_by_id[cam.get("id")] = cam
        except Exception:
            pass
        for cam_id, label in self._all_cameras():
            cam = cam_by_id.get(cam_id, {})
            item = QListWidgetItem(f"🎥 {label}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if cam.get("plate_detection") else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, cam_id)
            self.camera_checklist.addItem(item)
        self.camera_checklist.blockSignals(False)

    def _on_camera_check_changed(self, item):
        cam_id = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            self.camera_store.update_camera(cam_id, plate_detection=enabled)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"ذخیره‌ی تنظیم دوربین ناموفق بود:\n{e}")
            return
        # اعمال زنده روی دوربینی که همین حالا باز است
        if callable(self.on_plate_toggle):
            try:
                self.on_plate_toggle(cam_id, enabled)
            except Exception:
                pass

    # ------------------------------------------------------- عملیات پلاک -

    def _selected_plate_id(self):
        row = self.plates_table.currentRow()
        if row < 0:
            return None
        item = self.plates_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_plates_table(self):
        plates = plate_store.list_plates()
        self.plates_table.setRowCount(0)
        for p in plates:
            r = self.plates_table.rowCount()
            self.plates_table.insertRow(r)
            item0 = QTableWidgetItem(p["plate_display"])
            item0.setData(Qt.ItemDataRole.UserRole, p["id"])
            font = item0.font()
            font.setBold(True)
            item0.setFont(font)
            self.plates_table.setItem(r, 0, item0)
            self.plates_table.setItem(r, 1, QTableWidgetItem(p["owner_name"]))
            self.plates_table.setItem(r, 2, QTableWidgetItem(p["phone"]))
            self.plates_table.setItem(r, 3, QTableWidgetItem(p["vehicle_type"]))
            self.plates_table.setItem(r, 4, QTableWidgetItem(p["vehicle_model"]))
            self.plates_table.setItem(r, 5, QTableWidgetItem(p["vehicle_color"]))
            status_item = QTableWidgetItem("✅ فعال" if p["active"] else "⏸ غیرفعال")
            if not p["active"]:
                status_item.setForeground(Qt.GlobalColor.gray)
            self.plates_table.setItem(r, 6, status_item)
            self.plates_table.setItem(r, 7, QTableWidgetItem(p["created_jalali"]))
        self._update_stats()

    def add_plate(self):
        dlg = PlateFormDialog(self, get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, "خطا", result)
                return
            QMessageBox.information(
                self, "انجام شد",
                f"پلاک «{data['plate_display']}» با موفقیت تعریف شد.")
            self.refresh_plates_table()

    def add_plate_from_camera(self):
        """فرم تعریف با تصویر نمونه و متنِ ازپیش‌تشخیص‌شده از دوربین فعال."""
        if self.get_frame_callback is None:
            QMessageBox.warning(self, "خطا", "دسترسی به تصویر دوربین در دسترس نیست.")
            return
        frame = self.get_frame_callback()
        if frame is None:
            QMessageBox.warning(
                self, "خطا",
                "ابتدا یک دوربین را متصل و انتخاب کنید تا پلاک از تصویر زنده‌ی آن خوانده شود.")
            return
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            crop, text, err = _detect_plate_in_frame(frame.copy())
        finally:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if err:
            QMessageBox.warning(self, "تشخیص پلاک", err)
            return
        dlg = PlateFormDialog(
            self, prefill_text=text, prefill_snapshot=crop,
            get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, "خطا", result)
                return
            QMessageBox.information(
                self, "انجام شد",
                f"پلاک «{data['plate_display']}» با موفقیت تعریف شد.")
            self.refresh_plates_table()

    def edit_plate(self):
        pid = self._selected_plate_id()
        if not pid:
            QMessageBox.warning(self, "خطا", "لطفاً یک پلاک را از لیست انتخاب کنید.")
            return
        existing = plate_store.get_plate(pid)
        dlg = PlateFormDialog(self, existing=existing,
                              get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            data.pop("plate_text", None)
            data.pop("plate_display", None)
            ok, err = plate_store.update_plate(pid, **data)
            if not ok:
                QMessageBox.warning(self, "خطا", err or "به‌روزرسانی ناموفق بود.")
                return
            self.refresh_plates_table()

    def delete_plate(self):
        pid = self._selected_plate_id()
        if not pid:
            QMessageBox.warning(self, "خطا", "لطفاً یک پلاک را از لیست انتخاب کنید.")
            return
        p = plate_store.get_plate(pid)
        confirm = QMessageBox.question(
            self, "تأیید حذف",
            f"پلاک «{p['plate_display']}» ({p['owner_name']}) حذف شود؟\n"
            "رویدادهای عبورِ قبلاً ثبت‌شده باقی می‌مانند.")
        if confirm == QMessageBox.StandardButton.Yes:
            plate_store.delete_plate(pid)
            self.refresh_plates_table()

    def toggle_plate_active(self):
        pid = self._selected_plate_id()
        if not pid:
            QMessageBox.warning(self, "خطا", "لطفاً یک پلاک را از لیست انتخاب کنید.")
            return
        p = plate_store.get_plate(pid)
        plate_store.set_plate_active(pid, not p["active"])
        self.refresh_plates_table()

    def _update_stats(self):
        s = plate_store.stats()
        self.status_label.setText(
            f"🚗 {s['plates']} پلاک تعریف‌شده ({s['plates_active']} فعال) | "
            f"📋 {s['total']} عبور ثبت‌شده ({s['defined']} تعریف‌شده / "
            f"{s['undefined']} تعریف‌نشده) | امروز: {s['today']} عبور")

    # ============================================================ تب گزارش -

    def _build_report_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # --- فیلترها
        frow = QHBoxLayout()
        frow.addWidget(QLabel("از تاریخ:"))
        self.from_date = QDateEdit(calendarPopup=True)
        self.from_date.setDate(QDate.currentDate().addDays(-7))
        self.from_date.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.from_date)
        frow.addWidget(QLabel("تا تاریخ:"))
        self.to_date = QDateEdit(calendarPopup=True)
        self.to_date.setDate(QDate.currentDate())
        self.to_date.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.to_date)
        frow.addWidget(QLabel("دوربین:"))
        self.rep_camera_combo = QComboBox()
        frow.addWidget(self.rep_camera_combo)
        frow.addWidget(QLabel("وضعیت:"))
        self.rep_status_combo = QComboBox()
        self.rep_status_combo.addItem("همه", None)
        self.rep_status_combo.addItem("✅ تعریف‌شده", True)
        self.rep_status_combo.addItem("⚠️ تعریف‌نشده", False)
        frow.addWidget(self.rep_status_combo)
        frow.addWidget(QLabel("جست‌وجو:"))
        self.rep_search = QLineEdit()
        self.rep_search.setPlaceholderText("پلاک یا نام مالک...")
        self.rep_search.returnPressed.connect(self.run_report_search)
        frow.addWidget(self.rep_search)
        search_btn = QPushButton("🔍 اعمال")
        search_btn.clicked.connect(self.run_report_search)
        frow.addWidget(search_btn)
        layout.addLayout(frow)

        # --- جدول
        self.events_table = QTableWidget(0, len(self.EVENT_COLUMNS))
        self.events_table.setHorizontalHeaderLabels(self.EVENT_COLUMNS)
        self.events_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.events_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.events_table.verticalHeader().setDefaultSectionSize(56)
        self.events_table.doubleClicked.connect(self.open_event_detail)
        layout.addWidget(self.events_table, 1)

        # --- دکمه‌ها
        brow = QHBoxLayout()
        detail_btn = QPushButton("🔍 جزئیات")
        detail_btn.clicked.connect(self.open_event_detail)
        brow.addWidget(detail_btn)
        define_btn = QPushButton("➕ تعریف این پلاک")
        define_btn.setToolTip("پلاک تعریف‌نشده‌ی انتخاب‌شده را با همین تصویر تعریف می‌کند")
        define_btn.clicked.connect(self.define_selected_event_plate)
        brow.addWidget(define_btn)
        del_btn = QPushButton("🗑 حذف رویداد")
        del_btn.clicked.connect(self.delete_selected_event)
        brow.addWidget(del_btn)
        brow.addStretch()
        refresh_btn = QPushButton("🔄 به‌روزرسانی")
        refresh_btn.clicked.connect(self.run_report_search)
        brow.addWidget(refresh_btn)
        export_btn = QPushButton("📤 خروجی CSV")
        export_btn.clicked.connect(self.export_report_csv)
        brow.addWidget(export_btn)
        layout.addLayout(brow)

        self.rep_summary = QLabel("")
        layout.addWidget(self.rep_summary)
        return tab

    def _reload_report_camera_combo(self):
        current = self.rep_camera_combo.currentData()
        self.rep_camera_combo.blockSignals(True)
        self.rep_camera_combo.clear()
        self.rep_camera_combo.addItem("همه", None)
        names = set(plate_store.distinct_event_cameras())
        for _cid, label in self._all_cameras():
            names.add(label.split(" (")[0])
        for n in sorted(names):
            self.rep_camera_combo.addItem(n, n)
        idx = self.rep_camera_combo.findData(current)
        self.rep_camera_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.rep_camera_combo.blockSignals(False)

    def _current_report_filters(self):
        return {
            "date_from": self.from_date.date().toString("yyyy-MM-dd"),
            "date_to": self.to_date.date().toString("yyyy-MM-dd"),
            "camera_name": self.rep_camera_combo.currentData(),
            "defined": self.rep_status_combo.currentData(),
            "search": normalize_plate_text(self.rep_search.text().strip()),
        }

    def run_report_search(self):
        f = self._current_report_filters()
        rows = plate_store.query_events(**f)
        self.events_table.setRowCount(0)
        for ev in rows:
            r = self.events_table.rowCount()
            self.events_table.insertRow(r)
            # تصویر
            img_item = QTableWidgetItem("")
            snap = ev.get("snapshot_path", "")
            if snap and os.path.isfile(snap):
                pix = QPixmap(snap)
                if not pix.isNull():
                    img_item.setIcon(QIcon(pix.scaledToHeight(
                        48, Qt.TransformationMode.SmoothTransformation)))
            img_item.setData(Qt.ItemDataRole.UserRole, ev["id"])
            self.events_table.setItem(r, 0, img_item)
            self.events_table.setItem(r, 1, QTableWidgetItem(ev.get("date_j", "")))
            self.events_table.setItem(r, 2, QTableWidgetItem(ev.get("time_g", "")))
            self.events_table.setItem(r, 3, QTableWidgetItem(ev.get("camera_name", "")))
            plate_item = QTableWidgetItem(ev.get("plate_display", "") or "—")
            font = plate_item.font()
            font.setBold(True)
            plate_item.setFont(font)
            self.events_table.setItem(r, 4, plate_item)
            self.events_table.setItem(r, 5, QTableWidgetItem(ev.get("owner_name", "") or "—"))
            st_item = QTableWidgetItem(
                "✅ تعریف‌شده" if ev.get("is_defined") else "⚠️ تعریف‌نشده")
            st_item.setForeground(Qt.GlobalColor.darkGreen if ev.get("is_defined")
                                  else Qt.GlobalColor.darkRed)
            self.events_table.setItem(r, 6, st_item)
            conf = ev.get("confidence") or 0
            self.events_table.setItem(r, 7, QTableWidgetItem(f"{conf:.0%}"))
        n_def = sum(1 for e in rows if e.get("is_defined"))
        self.rep_summary.setText(
            f"{len(rows)} عبور یافت شد ({n_def} تعریف‌شده / {len(rows) - n_def} تعریف‌نشده)")

    def _selected_event_id(self):
        row = self.events_table.currentRow()
        if row < 0:
            return None
        item = self.events_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _get_event_by_id(self, eid):
        for ev in plate_store.query_events(limit=100000):
            if ev["id"] == eid:
                return ev
        return None

    def open_event_detail(self):
        eid = self._selected_event_id()
        if not eid:
            QMessageBox.warning(self, "خطا", "لطفاً یک ردیف را انتخاب کنید.")
            return
        ev = self._get_event_by_id(eid)
        if not ev:
            return
        dlg = PlateEventDetailDialog(ev, parent=self)
        dlg.exec()
        self.run_report_search()
        self._update_stats()

    def define_selected_event_plate(self):
        eid = self._selected_event_id()
        if not eid:
            QMessageBox.warning(self, "خطا", "لطفاً یک ردیف را انتخاب کنید.")
            return
        ev = self._get_event_by_id(eid)
        if not ev:
            return
        if ev.get("is_defined"):
            QMessageBox.information(self, "اطلاع", "این پلاک قبلاً تعریف شده است.")
            return
        snap = None
        sp = ev.get("snapshot_path", "")
        if sp and os.path.isfile(sp):
            snap = cv2.imread(sp)
        dlg = PlateFormDialog(
            self, prefill_text=ev.get("plate_text", ""),
            prefill_snapshot=snap, get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, "خطا", result)
                return
            plate = plate_store.get_plate(result)
            plate_store.attach_event_to_plate(eid, plate)
            QMessageBox.information(
                self, "انجام شد",
                f"پلاک «{data['plate_display']}» تعریف شد و این عبور به آن متصل شد.")
            self.refresh_plates_table()
            self.run_report_search()

    def delete_selected_event(self):
        eid = self._selected_event_id()
        if not eid:
            QMessageBox.warning(self, "خطا", "لطفاً یک ردیف را انتخاب کنید.")
            return
        confirm = QMessageBox.question(self, "تأیید حذف", "این رویداد عبور حذف شود؟")
        if confirm == QMessageBox.StandardButton.Yes:
            plate_store.delete_event(eid)
            self.run_report_search()
            self._update_stats()

    def export_report_csv(self):
        from datetime import datetime as _dt
        default = f"plate_report_{_dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "ذخیره‌ی خروجی گزارش عبور", default, "CSV (*.csv)")
        if not path:
            return
        f = self._current_report_filters()
        try:
            n = plate_store.export_events_csv(path, **f)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"خروجی گرفتن ناموفق بود:\n{e}")
            return
        QMessageBox.information(self, "انجام شد", f"{n} ردیف در فایل ذخیره شد:\n{path}")


# نام قدیمی برای سازگاری
PlateLibraryDialog = PlateLibraryPage

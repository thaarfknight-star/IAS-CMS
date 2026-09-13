"""دیالوگ «تنظیمات تصویر» هر دوربین (روشنایی، کنتراست، WDR، ضد مه و...).

برای دوربینِ خانه‌ی انتخاب‌شده باز می‌شود؛ هر تغییری بلافاصله (زنده) روی
تصویر همان دوربین اعمال می‌شود تا کاربر نتیجه را ببیند. با «ذخیره»، پروفایل
در cameras.json (فیلد image_profile همان دوربین) ماندگار می‌شود و با باز شدن
بعدی همان دوربین دوباره اعمال می‌گردد. با «انصراف»، تصویر به وضعیت قبل از
باز شدن دیالوگ برمی‌گردد.

نکته: این تنظیمات فقط روی «تصویر نمایشی» اثر می‌گذارد؛ ورودی موتورهای
تشخیص (چهره/شخص/حریق) دست‌نخورده می‌ماند - رجوع کنید به image_profile.py.

بخش «WDR سخت‌افزاری» جدا از این است: پارامتر واقعی خود دوربین را از طریق
ONVIF Imaging می‌خواند/می‌نویسد (رجوع کنید به onvif_imaging.py) و روی همه‌ی
بیننده‌ها اثر می‌گذارد.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QComboBox,
    QCheckBox, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

import image_profile as ip
import onvif_imaging as oi


class _OnvifWorker(QThread):
    """کارگر ترد برای خواندن/نوشتن WDR سخت‌افزاری؛ فراخوانی شبکه‌ای ONVIF
    هرگز نباید در ترد GUI انجام شود."""
    done = pyqtSignal(dict)

    def __init__(self, op, cam, store, mode=None, level=None, parent=None):
        super().__init__(parent)
        self._op = op            # "get" یا "set"
        self._cam = dict(cam or {})
        self._store = store
        self._mode = mode
        self._level = level

    def run(self):
        try:
            if self._op == "get":
                res = oi.get_wdr(self._cam, self._store)
            else:
                res = oi.set_wdr(self._cam, self._mode, self._level, self._store)
        except Exception as e:  # آخرین تور امنیت
            res = {"ok": False, "error": "خطای غیرمنتظره: %s" % e}
        self.done.emit(res)


# (کلید پروفایل، برچسب فارسی، کمینه‌ی اسلایدر، بیشینه، مقیاس، قالب نمایش مقدار)
_SLIDER_SPECS = [
    ("brightness", "روشنایی", -100, 100, 1, "{:+d}"),
    ("contrast", "کنتراست", 50, 200, 100, "{:.2f}×"),
    ("saturation", "اشباع رنگ", 0, 200, 100, "{:.2f}×"),
    ("gamma", "گاما (روشنایی سایه‌ها)", 40, 250, 100, "{:.2f}"),
    ("wdr", "WDR (جبران نور شدید)", 0, 100, 100, "{:.0f}٪"),
    ("dehaze", "ضد مه", 0, 100, 100, "{:.0f}٪"),
]


class ImageSettingsDialog(QDialog):
    def __init__(self, slot, camera_store, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.camera_store = camera_store
        cam = slot.cam or {}
        self._cam_id = cam.get("id")
        cam_name = cam.get("name") or cam.get("ip", "")
        self.setWindowTitle(f"تنظیمات تصویر — {cam_name}")
        self.resize(420, 640)

        # پروفایل فعلی دوربین؛ نسخه‌ی اصلی برای بازگردانی در صورت انصراف.
        self._original = ip.get_camera_profile(cam)
        self._profile = dict(self._original)

        layout = QVBoxLayout(self)

        hint = QLabel(
            "هر دوربین تنظیمات مستقل خودش را دارد. تغییرات بلافاصله روی تصویر "
            "همین دوربین دیده می‌شود و فقط روی «نمایش» اثر می‌گذارد (تشخیص "
            "چهره/شخص/حریق تغییری نمی‌کند)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888888; font-size:10px;")
        layout.addWidget(hint)

        # ---- انتخاب پیش‌فرض ----
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("پیش‌فرض آماده:"))
        self.preset_combo = QComboBox()
        self._preset_keys = list(ip.PRESETS.keys()) + [ip.CUSTOM_PRESET_KEY]
        for key in self._preset_keys:
            label = ip.PRESETS[key]["label"] if key in ip.PRESETS else ip.CUSTOM_PRESET_LABEL
            self.preset_combo.addItem(label, key)
        self.preset_combo.setCurrentIndex(
            self._preset_keys.index(ip.detect_preset(self._profile)))
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # ---- اسلایدرها ----
        self._sliders = {}   # key -> (QSlider, QLabel مقدار)
        for key, label, lo, hi, scale, fmt in _SLIDER_SPECS:
            row = QHBoxLayout()
            name_label = QLabel(label)
            name_label.setMinimumWidth(150)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(self._to_slider(key, lo, hi, scale))
            value_label = QLabel()
            value_label.setMinimumWidth(52)
            value_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
            row.addWidget(name_label)
            row.addWidget(slider, 1)
            row.addWidget(value_label)
            layout.addLayout(row)
            self._sliders[key] = (slider, value_label, scale, fmt)
            slider.valueChanged.connect(
                lambda _v, _k=key: self._on_slider_changed(_k))
            self._refresh_value_label(key)

        # ---- کاهش نویز ----
        self.denoise_check = QCheckBox("کاهش نویز (مناسب حالت شب - کمی پردازش بیشتر)")
        self.denoise_check.setChecked(bool(self._profile.get("denoise")))
        self.denoise_check.toggled.connect(self._on_denoise_toggled)
        layout.addWidget(self.denoise_check)

        layout.addStretch(1)

        # ---- WDR سخت‌افزاری دوربین (ONVIF) ----
        # برخلاف اسلایدرهای بالا که فقط «نمایش» را تغییر می‌دهند، این بخش
        # پارامتر واقعی خود دوربین را ست می‌کند و روی همه‌ی بیننده‌ها اثر
        # می‌گذارد. ارتباط شبکه‌ای در ترد جدا انجام می‌شود تا رابط کاربری
        # قفل نشود.
        hw_sep = QFrame()
        hw_sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(hw_sep)

        hw_title = QLabel("🔧 WDR سخت‌افزاری دوربین (از طریق ONVIF)")
        hw_title.setStyleSheet("font-weight:bold;")
        layout.addWidget(hw_title)

        self.hw_status = QLabel("…")
        self.hw_status.setWordWrap(True)
        self.hw_status.setStyleSheet("color:#888888; font-size:10px;")
        layout.addWidget(self.hw_status)

        hw_row = QHBoxLayout()
        hw_row.addWidget(QLabel("حالت WDR:"))
        self.hw_mode_combo = QComboBox()
        self.hw_mode_combo.addItem("خاموش", oi.WDR_MODE_OFF)
        self.hw_mode_combo.addItem("روشن", oi.WDR_MODE_ON)
        self.hw_mode_combo.setEnabled(False)
        hw_row.addWidget(self.hw_mode_combo)
        hw_row.addWidget(QLabel("شدت:"))
        self.hw_level_slider = QSlider(Qt.Orientation.Horizontal)
        self.hw_level_slider.setRange(0, 100)
        self.hw_level_slider.setValue(50)
        self.hw_level_slider.setEnabled(False)
        self.hw_level_value = QLabel("۵۰٪")
        self.hw_level_value.setMinimumWidth(44)
        self.hw_level_slider.valueChanged.connect(
            lambda v: self.hw_level_value.setText(f"{v}٪"))
        hw_row.addWidget(self.hw_level_slider, 1)
        hw_row.addWidget(self.hw_level_value)
        layout.addLayout(hw_row)

        hw_btn_row = QHBoxLayout()
        self.hw_read_btn = QPushButton("🔄 خواندن از دوربین")
        self.hw_read_btn.clicked.connect(lambda: self._start_hw_worker("get"))
        self.hw_apply_btn = QPushButton("📡 اعمال روی دوربین")
        self.hw_apply_btn.setToolTip(
            "تنظیم WDR واقعی خود دوربین (ماندگار روی دستگاه)")
        self.hw_apply_btn.setEnabled(False)
        self.hw_apply_btn.clicked.connect(self._on_hw_apply)
        hw_btn_row.addWidget(self.hw_read_btn)
        hw_btn_row.addWidget(self.hw_apply_btn)
        hw_btn_row.addStretch(1)
        layout.addLayout(hw_btn_row)
        self._hw_worker = None
        self._hw_supported = False
        self._start_hw_worker("get")  # خواندن خودکار هنگام باز شدن

        # ---- دکمه‌ها ----
        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 ذخیره")
        save_btn.setStyleSheet("QPushButton{background:#27ae60; color:#fff; border-radius:4px; padding:6px 16px;}")
        save_btn.clicked.connect(self.accept)
        reset_btn = QPushButton("↩ بازنشانی به پیش‌فرض")
        reset_btn.setToolTip("برگرداندن همه‌ی تنظیمات به حالت پیش‌فرض (بدون تغییر)")
        reset_btn.clicked.connect(self._on_reset_defaults)
        cancel_btn = QPushButton("انصراف")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------ کمکی‌ها --
    def _to_slider(self, key, lo, hi, scale):
        raw = float(self._profile.get(key, 0))
        return min(hi, max(lo, int(round(raw * scale))))

    def _from_slider(self, key, slider_value, scale):
        if key == "brightness":
            return int(slider_value)
        return slider_value / scale

    def _refresh_value_label(self, key):
        slider, value_label, scale, fmt = self._sliders[key]
        val = self._from_slider(key, slider.value(), scale)
        if key in ("wdr", "dehaze"):
            value_label.setText(fmt.format(val * 100))
        elif key == "brightness":
            value_label.setText(fmt.format(int(val)))
        else:
            value_label.setText(fmt.format(val))

    def _sync_sliders_from_profile(self):
        for key, (slider, _vl, scale, _fmt) in self._sliders.items():
            lo, hi = slider.minimum(), slider.maximum()
            slider.blockSignals(True)
            slider.setValue(self._to_slider(key, lo, hi, scale))
            slider.blockSignals(False)
            self._refresh_value_label(key)
        self.denoise_check.blockSignals(True)
        self.denoise_check.setChecked(bool(self._profile.get("denoise")))
        self.denoise_check.blockSignals(False)

    def _mark_custom_preset(self):
        idx = self._preset_keys.index(ip.CUSTOM_PRESET_KEY)
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(idx)
        self.preset_combo.blockSignals(False)

    def _apply_live(self):
        self.slot.apply_image_profile(dict(self._profile))

    # ------------------------------------------------------------- رویدادها --
    def _on_preset_changed(self, index):
        key = self._preset_keys[index]
        if key == ip.CUSTOM_PRESET_KEY:
            return
        self._profile = ip.preset_profile(key)
        self._sync_sliders_from_profile()
        self._apply_live()

    def _on_slider_changed(self, key):
        slider, _vl, scale, _fmt = self._sliders[key]
        self._profile[key] = self._from_slider(key, slider.value(), scale)
        self._refresh_value_label(key)
        self._mark_custom_preset()
        self._apply_live()

    def _on_denoise_toggled(self, checked):
        self._profile["denoise"] = bool(checked)
        self._mark_custom_preset()
        self._apply_live()

    def _on_reset_defaults(self):
        self._profile = ip.default_profile()
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(self._preset_keys.index("default"))
        self.preset_combo.blockSignals(False)
        self._sync_sliders_from_profile()
        self._apply_live()

    # ------------------------------------------- WDR سخت‌افزاری (ONVIF) --
    def _hw_set_busy(self, busy, msg=""):
        self.hw_read_btn.setEnabled(not busy)
        self.hw_apply_btn.setEnabled(not busy and self._hw_supported)
        self.hw_mode_combo.setEnabled(not busy and self._hw_supported)
        self.hw_level_slider.setEnabled(
            not busy and self._hw_supported
            and self.hw_mode_combo.currentData() == oi.WDR_MODE_ON)
        if msg:
            self.hw_status.setText(msg)

    def _start_hw_worker(self, op, mode=None, level=None):
        if self._hw_worker is not None and self._hw_worker.isRunning():
            return
        ok, _ = oi.is_available()
        if not ok:
            self.hw_status.setText(oi.availability_message())
            self._hw_set_busy(False)
            return
        cam = self.slot.cam or {}
        if not (cam.get("ip") or "").strip():
            self.hw_status.setText("IP دوربین مشخص نیست؛ WDR سخت‌افزاری ممکن نیست.")
            return
        self._hw_worker = _OnvifWorker(op, cam, self.camera_store,
                                       mode=mode, level=level, parent=self)
        self._hw_worker.done.connect(
            self._on_hw_read_done if op == "get" else self._on_hw_set_done)
        self._hw_set_busy(True, "در حال ارتباط با دوربین…")
        self._hw_worker.start()

    def _on_hw_read_done(self, res):
        self._hw_worker = None
        if not res.get("ok"):
            self._hw_supported = False
            self._hw_set_busy(False, "❌ " + str(res.get("error", "خطا")))
            return
        if not res.get("supported"):
            self._hw_supported = False
            self._hw_set_busy(False,
                              "این دستگاه WDR سخت‌افزاری (ONVIF Imaging) را "
                              "پشتیبانی نمی‌کند. از WDR نرم‌افزاری بالا استفاده کنید.")
            return
        self._hw_supported = True
        mode = (res.get("mode") or oi.WDR_MODE_OFF).upper()
        idx = 1 if mode == oi.WDR_MODE_ON else 0
        self.hw_mode_combo.blockSignals(True)
        self.hw_mode_combo.setCurrentIndex(idx)
        self.hw_mode_combo.blockSignals(False)
        if res.get("level") is not None:
            self.hw_level_slider.blockSignals(True)
            self.hw_level_slider.setValue(int(res["level"]))
            self.hw_level_slider.blockSignals(False)
            self.hw_level_value.setText(f"{int(res['level'])}٪")
        via = res.get("label") or ""
        self._hw_set_busy(False,
                          f"✅ خوانده شد ({via}) — حالت فعلی: "
                          f"{'روشن' if mode == oi.WDR_MODE_ON else 'خاموش'}")
        # با تغییر حالت، اسلایدر شدت فعال/غیرفعال شود
        try:
            self.hw_mode_combo.currentIndexChanged.disconnect()
        except Exception:
            pass
        self.hw_mode_combo.currentIndexChanged.connect(
            lambda _i: self.hw_level_slider.setEnabled(
                self._hw_supported
                and self.hw_mode_combo.currentData() == oi.WDR_MODE_ON))

    def _on_hw_apply(self):
        mode = self.hw_mode_combo.currentData() or oi.WDR_MODE_OFF
        level = (self.hw_level_slider.value()
                 if mode == oi.WDR_MODE_ON else None)
        self._start_hw_worker("set", mode=mode, level=level)

    def _on_hw_set_done(self, res):
        self._hw_worker = None
        if not res.get("ok"):
            self._hw_set_busy(False, "❌ " + str(res.get("error", "خطا")))
            return
        mode = res.get("mode")
        level = res.get("level")
        # ثبت آخرین وضعیت سخت‌افزاری در رکورد دوربین (فقط برای نمایش/ارجاع)
        if self._cam_id is not None and self.camera_store is not None:
            try:
                self.camera_store.update_camera(
                    self._cam_id,
                    onvif_imaging={"wdr_mode": mode, "wdr_level": level})
                if self.slot.cam is not None:
                    self.slot.cam["onvif_imaging"] = {
                        "wdr_mode": mode, "wdr_level": level}
            except Exception:
                pass
        self._hw_set_busy(False,
                          f"✅ روی دوربین اعمال شد ({res.get('label') or ''}) — "
                          f"WDR {'روشن' if mode == oi.WDR_MODE_ON else 'خاموش'}"
                          + (f" با شدت {level}٪" if level is not None else ""))

    # ----------------------------------------------------- ذخیره / انصراف --
    def accept(self):
        profile = dict(self._profile)
        profile["preset"] = ip.detect_preset(profile)
        if self._cam_id is not None:
            self.camera_store.update_camera(self._cam_id, image_profile=profile)
        if self.slot.cam is not None:
            # هم‌گام نگه داشتن کپیِ داخل حافظه‌ی همان خانه (اگر شیء جدا باشد)
            self.slot.cam["image_profile"] = dict(profile)
        self.slot.apply_image_profile(profile)
        super().accept()

    def reject(self):
        # برگرداندن تصویر به وضعیت قبل از باز شدن دیالوگ
        self.slot.apply_image_profile(dict(self._original))
        super().reject()

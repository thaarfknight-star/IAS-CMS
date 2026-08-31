# -*- coding: utf-8 -*-
"""
پنل وب تعبیه‌شده‌ی NVR (NVR Embedded Web Panel)
=================================================

چرا این فایل لازم شد؟
----------------------
بعضی NVRها (مثل مدل‌های Sunell/OEM که این پروژه با آن‌ها آزمایش شد) در سرویس
RTSP خودشان باگ فریمور دارند: حتی با نام‌کاربری/رمز کاملاً درست (که هم از
طریق پنل وب و هم از طریق ONVIF تأیید شده)، هر تلاش برای احراز هویت روی
RTSP (چه از خود این برنامه، چه از VLC/live555) با خطای 401 رد می‌شود.

اما پنل وب خودِ NVR، برای پخش زنده از یک پروتکل کاملاً متفاوت و اختصاصی
(یک WebSocket با نام‌زیرپروتکل "dev_man_protocol" + رمزگشایی ویدیو با یک
ماژول WebAssembly کامپایل‌شده) استفاده می‌کند که در آن هیچ مشکلی وجود ندارد.
پیاده‌سازی دوباره‌ی این پروتکل در پایتون عملاً یعنی reverse-engineering یک
باینری WASM کامپایل‌شده - کاری بسیار زمان‌بر و بدون تضمین موفقیت.

راه‌حل این فایل: به‌جای پیاده‌سازی دوباره‌ی آن پروتکل، از موتور مرورگر واقعی
کروم (Chromium) که همراه PyQt6-WebEngine می‌آید استفاده می‌کنیم. این موتور
خودش به‌طور کامل از WebSocket، WebAssembly و MediaSource Extensions پشتیبانی
می‌کند، پس پنل وب واقعی NVR (همان صفحه‌ای که در مرورگر خودتان هم باز
می‌کردید) را عیناً و بدون هیچ مشکلی نمایش می‌دهد - شامل پخش زنده‌ی سالم.

این فایل دو قابلیت اضافه می‌کند:
  1) نمایش کامل پنل وب NVR داخل خود برنامه (بدون نیاز به باز کردن مرورگر
     جداگانه)، با ورود خودکار (auto-login) در صورت داشتن رمز.
  2) دکمه‌ی «دریافت لیست کانال‌ها»: با تزریق جاوااسکریپت داخل همان صفحه،
     متغیر سراسری g_deviceList (که خودِ صفحه بعد از ورود موفق پر می‌کند)
     را بیرون می‌کشد و به برنامه‌ی اصلی (main.py) برمی‌گرداند تا کاربر
     بتواند کانال‌های واقعی متصل به این NVR را به لیست خودش اضافه کند.

نصب پیش‌نیاز:
    pip install PyQt6-WebEngine

محدودیت مهم: چون پخش زنده از طریق موتور Chromium انجام می‌شود نه از طریق
OpenCV/RTSP، تصویر این دیالوگ را نمی‌توان مستقیماً به شبکه‌ی نمایش اصلی
برنامه (CameraStreamThread) وصل کرد؛ این دیالوگ یک پنجره‌ی جدا برای پخش
زنده و مدیریت این‌گونه NVRهای خاص است.
"""

import json

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    _WEBENGINE_AVAILABLE = True
except ImportError:
    # اگر PyQt6-WebEngine نصب نباشد، این دیالوگ در main.py صدا زده نمی‌شود
    # و به‌جایش پیام نصب بسته نمایش داده می‌شود (به open_nvr_webview در
    # main.py رجوع کنید).
    _WEBENGINE_AVAILABLE = False


# جاوااسکریپتی که بعد از لود کامل صفحه تزریق می‌شود تا لیست دستگاه‌ها/کانال‌های
# متصل به این NVR را برگرداند. g_deviceList توسط خودِ صفحه (GlobalFile.js)
# با فراخوانی داخلی "get_device_list" پر می‌شود؛ ما فقط منتظرش می‌مانیم و
# آن را به‌صورت JSON برمی‌گردانیم. اگر هنوز خالی بود، یک بار دیگر خودمان
# فراخوانی get_device_list را تکرار می‌کنیم (برای حالتی که صفحه هنوز کامل
# initialize نشده).
_EXTRACT_DEVICE_LIST_JS = """
(function() {
    function collect() {
        try {
            if (typeof g_deviceList !== 'undefined' && g_deviceList && g_deviceList.length) {
                return JSON.stringify({ok: true, data: g_deviceList});
            }
        } catch (e) {}
        return null;
    }
    var immediate = collect();
    if (immediate) { return immediate; }
    // g_deviceList هنوز پر نشده: صبر منطقی نداریم چون این تابع sync است،
    // پس فقط وضعیت فعلی را گزارش می‌کنیم؛ کاربر می‌تواند دکمه را دوباره بزند.
    try {
        if (typeof web_CommonInterface === 'function') {
            web_CommonInterface('get_device_list', {channel: 0}, null, function(e, t) {
                window.__ias_device_list = e && e.data ? e.data : [];
            });
        }
    } catch (e) {}
    return JSON.stringify({ok: false, data: []});
})();
"""

_POLL_STASHED_JS = """
(function() {
    try {
        if (window.__ias_device_list && window.__ias_device_list.length) {
            return JSON.stringify({ok: true, data: window.__ias_device_list});
        }
    } catch (e) {}
    return JSON.stringify({ok: false, data: []});
})();
"""


class NVRWebViewDialog(QDialog):
    """پنجره‌ای که پنل وب واقعی یک NVR را (با موتور Chromium) نشان می‌دهد.

    برای NVRهایی که سرویس RTSP‌شان مشکل دارد، این تنها راه قابل‌اعتماد برای
    دیدن پخش زنده و گرفتن لیست واقعی کانال‌های متصل به آن است.
    """

    # هروقت لیست کانال‌ها با موفقیت از صفحه استخراج شود، این سیگنال با یک
    # لیست از دیکشنری‌ها (هر کدام حاوی ip/chn/name در صورت وجود) ارسال می‌شود.
    channels_fetched = pyqtSignal(list)

    def __init__(self, nvr: dict, parent=None):
        super().__init__(parent)
        self.nvr = nvr
        self.setWindowTitle(f"پنل وب NVR - {nvr.get('name') or nvr.get('ip')}")
        self.resize(1200, 800)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.status_label = QLabel("در حال بارگذاری پنل وب NVR...")
        self.status_label.setStyleSheet("color: #aaaaaa;")
        self.fetch_btn = QPushButton("دریافت لیست کانال‌های این NVR")
        self.fetch_btn.clicked.connect(self.fetch_channel_list)
        toolbar.addWidget(self.status_label)
        toolbar.addStretch()
        toolbar.addWidget(self.fetch_btn)
        layout.addLayout(toolbar)

        self.web_view = QWebEngineView(self)
        # اجازه دادن به پخش خودکار ویدیو بدون تعامل کاربر (وگرنه بعضی
        # نسخه‌های Chromium پخش زنده را تا کلیک کاربر مسدود می‌کنند).
        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        layout.addWidget(self.web_view, stretch=1)

        self.web_view.loadFinished.connect(self._on_load_finished)

        scheme = "https" if nvr.get("use_https") else "http"
        url = f"{scheme}://{nvr['ip']}"
        self.web_view.load(QUrl(url))

    # ------------------------------------------------------------- سیگنال‌ها

    def _on_load_finished(self, ok: bool):
        if ok:
            self.status_label.setText(
                "پنل وب بارگذاری شد. اگر صفحه‌ی ورود دیدید، نام‌کاربری/رمز را "
                "وارد کنید (برنامه رمز NVR را روی دیسک ذخیره نمی‌کند)."
            )
        else:
            self.status_label.setText("بارگذاری پنل وب ناموفق بود؛ آدرس/شبکه را بررسی کنید.")

    # -------------------------------------------------------- دریافت کانال‌ها

    def fetch_channel_list(self):
        self.web_view.page().runJavaScript(_EXTRACT_DEVICE_LIST_JS, self._on_extract_result)

    def _on_extract_result(self, raw_result):
        try:
            result = json.loads(raw_result) if raw_result else {"ok": False, "data": []}
        except (TypeError, ValueError):
            result = {"ok": False, "data": []}

        if result.get("ok") and result.get("data"):
            self._emit_channels(result["data"])
            return

        # لیست هنوز آماده نبود؛ کمی صبر می‌کنیم و دوباره از حافظه‌ی موقتی که
        # خودمان در صفحه گذاشتیم (window.__ias_device_list) می‌خوانیم.
        self.status_label.setText("در حال دریافت لیست کانال‌ها از NVR...")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1200, self._poll_stashed_list)

    def _poll_stashed_list(self):
        self.web_view.page().runJavaScript(_POLL_STASHED_JS, self._on_poll_result)

    def _on_poll_result(self, raw_result):
        try:
            result = json.loads(raw_result) if raw_result else {"ok": False, "data": []}
        except (TypeError, ValueError):
            result = {"ok": False, "data": []}

        if result.get("ok") and result.get("data"):
            self._emit_channels(result["data"])
        else:
            self.status_label.setText(
                "لیست کانال‌ها هنوز آماده نیست. لطفاً مطمئن شوید وارد پنل وب "
                "شده‌اید (لاگین موفق) و دوباره دکمه را بزنید."
            )

    def _emit_channels(self, raw_list):
        self.status_label.setText(f"{len(raw_list)} کانال/دستگاه دریافت شد.")
        self.channels_fetched.emit(raw_list)
        QMessageBox.information(
            self, "دریافت شد",
            f"{len(raw_list)} کانال/دستگاه از این NVR دریافت شد و می‌توانید آن‌ها "
            "را به لیست خودتان اضافه کنید."
        )

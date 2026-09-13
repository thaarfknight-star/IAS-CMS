# -*- coding: utf-8 -*-
"""
fire_panel_simulator.py — شبیه‌ساز پنل اعلام حریق ساختمان (برای تست بدون سخت‌افزار).

چرا این فایل؟
    شما الان پنل اعلام حریق واقعی در ساختمان ندارید، پس نمی‌توانید اتصال
    IAS-CMS به پنل را تست کنید. این شبیه‌ساز نقش همان پنل را بازی می‌کند:
    یک سرور HTTP محلی روی پورت 8910 که آلارم‌های IAS-CMS را می‌گیرد و با
    چراغ قرمز چشمک‌زن + لیست رویدادها نشان می‌دهد.

روش استفاده:
    1. python fire_panel_simulator.py را اجرا کنید (پنجره‌ی شبیه‌ساز باز می‌شود).
    2. در IAS-CMS وارد تنظیمات «اتصال به سیستم حریق ساختمان» شوید:
       - فعال‌سازی: روشن
       - روش اتصال: شبیه‌ساز (simulator)
       - آدرس شبیه‌ساز: http://127.0.0.1:8910/api/fire-alarm (پیش‌فرض)
    3. دکمه‌ی «تست اتصال» را بزنید → باید در شبیه‌ساز «آلارم آزمایشی» ببینید.
    4. برای تست واقعی: یک فندک جلوی دوربین بگیرید → آلارم واقعی ثبت می‌شود.

نکته: این شبیه‌ساز فقط برای تست است؛ در ساختمان واقعی، به‌جای آن آدرس
پنل/رله‌ی واقعی را در تنظیمات وارد می‌کنید.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8910

events = []          # لیست رویدادهای دریافتی (dict)
on_alarm = None      # کال‌بک UI: وقتی آلارم جدید می‌رسد صدا زده می‌شود


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # لاگ HTTP را ساکت نگه دار

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/fire-alarm":
            return self._json({"ok": False, "error": "unknown endpoint"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {"raw": raw.decode("utf-8", "ignore")}
        payload["_received_at"] = time.strftime("%H:%M:%S")
        events.append(payload)
        if on_alarm is not None:
            try:
                on_alarm(payload)
            except Exception:
                pass
        self._json({"ok": True, "message": "alarm received by simulator"})

    def do_GET(self):
        if self.path == "/api/fire-alarm":
            return self._json({"ok": True, "events": events[-50:]})
        return self._json({"ok": True, "service": "fire-panel-simulator"})


def run_server():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()


def main():
    threading.Thread(target=run_server, daemon=True).start()

    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                 QLabel, QListWidget, QListWidgetItem, QPushButton,
                                 QHBoxLayout)
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
    from PyQt6.QtGui import QColor
    import sys

    class Bridge(QObject):
        new_event = pyqtSignal(dict)

    app = QApplication(sys.argv)
    bridge = Bridge()

    win = QMainWindow()
    win.setWindowTitle("شبیه‌ساز پنل اعلام حریق ساختمان (تست)")
    win.resize(520, 560)

    central = QWidget()
    layout = QVBoxLayout(central)

    status = QLabel("⏳ در انتظار آلارم…")
    status.setAlignment(Qt.AlignmentFlag.AlignCenter)
    status.setStyleSheet("font-size: 22px; font-weight: bold; padding: 18px; "
                         "background: #1c2b33; color: #9fb3bd; border-radius: 10px;")
    layout.addWidget(status)

    info = QLabel(
        "این پنجره نقش پنل اعلام حریق ساختمان را بازی می‌کند.\n"
        "در IAS-CMS روش اتصال را روی «شبیه‌ساز» بگذارید و «تست اتصال» را بزنید."
    )
    info.setWordWrap(True)
    info.setStyleSheet("color: #7d8f99; font-size: 12px;")
    layout.addWidget(info)

    lst = QListWidget()
    lst.setWordWrap(True)
    layout.addWidget(lst, 1)

    btn_row = QHBoxLayout()
    btn_reset = QPushButton("ریست آلارم")
    btn_clear_log = QPushButton("پاک کردن لیست")
    btn_row.addWidget(btn_reset)
    btn_row.addWidget(btn_clear_log)
    layout.addLayout(btn_row)

    win.setCentralWidget(central)

    blink_on = {"v": False}

    def set_alarm_visual(active):
        if active:
            status.setText("🔥 آلارم حریق فعال شد!")
            status.setStyleSheet("font-size: 22px; font-weight: bold; padding: 18px; "
                                 "background: #b71c1c; color: white; border-radius: 10px;")
        else:
            status.setText("⏳ در انتظار آلارم…")
            status.setStyleSheet("font-size: 22px; font-weight: bold; padding: 18px; "
                                 "background: #1c2b33; color: #9fb3bd; border-radius: 10px;")

    def on_event(payload):
        bridge.new_event.emit(payload)

    def handle_event(payload):
        state = payload.get("state", "trigger")
        test = " (تست)" if payload.get("test") else ""
        kind = {"fire": "آتش", "smoke": "دود"}.get(payload.get("kind"), payload.get("kind") or "—")
        cam = payload.get("camera") or "—"
        ts = payload.get("_received_at", "")
        if state == "clear":
            set_alarm_visual(False)
            item = QListWidgetItem(f"✅ [{ts}] ریست آلارم{test}")
            item.setForeground(QColor("#7cb342"))
        else:
            set_alarm_visual(True)
            item = QListWidgetItem(
                f"🔥 [{ts}] آلارم{test} — نوع: {kind} — دوربین: {cam}")
            item.setForeground(QColor("#ff5252"))
        lst.insertItem(0, item)
        # چشمک‌زن
        blink_on["v"] = True

    def blink():
        if blink_on["v"]:
            cur = status.styleSheet()
            if "#b71c1c" in cur:
                status.setStyleSheet(cur.replace("#b71c1c", "#7f0000"))
            else:
                status.setStyleSheet(cur.replace("#7f0000", "#b71c1c"))

    def do_reset():
        set_alarm_visual(False)
        blink_on["v"] = False
        ts = time.strftime("%H:%M:%S")
        item = QListWidgetItem(f"🔄 [{ts}] ریست دستی از شبیه‌ساز")
        item.setForeground(QColor("#64b5f6"))
        lst.insertItem(0, item)

    bridge.new_event.connect(handle_event)
    btn_reset.clicked.connect(do_reset)
    btn_clear_log.clicked.connect(lst.clear)

    global on_alarm
    on_alarm = on_event

    timer = QTimer()
    timer.timeout.connect(blink)
    timer.start(600)

    print(f"شبیه‌ساز پنل حریق روی http://127.0.0.1:{PORT}/api/fire-alarm بالا آمد.")
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

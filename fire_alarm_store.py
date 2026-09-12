# -*- coding: utf-8 -*-
"""ذخیره‌ی دائمی لیست پنل‌های اعلام حریق فیزیکی که کاربر اضافه کرده،
به همان الگوی camera_store.py (JSON روی دیسک، رمز عبور فقط در حافظه)."""

import json
import os
import uuid


class FireAlarmStore:
    def __init__(self, path: str = "fire_alarms.json"):
        self.path = path
        self.panels: list[dict] = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.panels = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری لیست پنل‌های اعلام حریق: {e}")
                self.panels = []
        # رفع درخواست امنیتی (مشابه camera_store.py): رمز عبور روی دیسک
        # ذخیره نمی‌شود؛ اگر فایل قدیمی رمزی داشته باشد، همین‌جا پاک می‌شود.
        if any(p.get("pass") for p in self.panels):
            for p in self.panels:
                p["pass"] = ""
            self.save()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._without_passwords(self.panels), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره‌ی لیست پنل‌های اعلام حریق: {e}")

    @staticmethod
    def _without_passwords(items):
        cleaned = []
        for item in items:
            item_copy = dict(item)
            item_copy["pass"] = ""
            cleaned.append(item_copy)
        return cleaned

    def clear_all_passwords(self):
        for panel in self.panels:
            panel["pass"] = ""

    def add_panel(self, name: str, protocol: str, ip: str, port: int,
                  user: str = "", pwd: str = "", **extra) -> dict:
        panel = {
            "id": str(uuid.uuid4()),
            "name": name or ip,
            "protocol": protocol,  # "isapi" | "cgi" | "modbus"
            "ip": ip,
            "port": port,
            "user": user,
            "pass": pwd,
            **extra,  # مثلاً input_count برای Modbus
        }
        self.panels.append(panel)
        self.save()
        return panel

    def update_panel(self, panel_id: str, **fields):
        for panel in self.panels:
            if panel["id"] == panel_id:
                panel.update(fields)
                self.save()
                return panel
        return None

    def remove_panel(self, panel_id: str):
        self.panels = [p for p in self.panels if p["id"] != panel_id]
        self.save()

    def get_panel(self, panel_id: str):
        for panel in self.panels:
            if panel["id"] == panel_id:
                return panel
        return None

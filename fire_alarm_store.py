import json
import os
import uuid

# ذخیره‌ی دائمی لیست پنل‌ها/سنسورهای فیزیکی اعلام حریق - دقیقاً همان الگوی
# camera_store.py برای NVRها (فایل JSON کنار cameras.json/nvrs.json)، ساده‌شده
# چون هر پنل فقط یک «ورودی» دارد (نه چند کانال مثل NVR).


class FireAlarmStore:
    def __init__(self, path="fire_alarms.json"):
        self.path = path
        self.panels = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.panels = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری لیست پنل‌های اعلام حریق: {e}")
                self.panels = []

        # رفع درخواست امنیتی: مثل camera_store.py، رمز عبور هرگز روی دیسک
        # نگه‌داشته نمی‌شود.
        if any(p.get("pass") for p in self.panels):
            for p in self.panels:
                p["pass"] = ""
            self.save()

    def save(self):
        try:
            cleaned = []
            for p in self.panels:
                c = dict(p)
                c["pass"] = ""
                cleaned.append(c)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره‌ی لیست پنل‌های اعلام حریق: {e}")

    def clear_all_passwords(self):
        for p in self.panels:
            p["pass"] = ""

    def add_panel(self, panel_data: dict):
        panel = dict(panel_data)
        panel["id"] = str(uuid.uuid4())
        self.panels.append(panel)
        self.save()
        return panel

    def update_panel(self, panel_id, **fields):
        for p in self.panels:
            if p["id"] == panel_id:
                p.update(fields)
                self.save()
                return p
        return None

    def remove_panel(self, panel_id):
        self.panels = [p for p in self.panels if p["id"] != panel_id]
        self.save()

    def get_panel(self, panel_id):
        for p in self.panels:
            if p["id"] == panel_id:
                return p
        return None

# -*- coding: utf-8 -*-
"""app_settings.py — تنظیمات عمومی برنامه (تم، زبان).

در app_settings.json کنار همین فایل ذخیره می‌شود؛ بدون نصب هیچ‌چیز.
"""

import json
import os

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "app_settings.json")

DEFAULTS = {
    "theme": "dark",      # dark | light | system
    "language": "fa",     # fa | en
}


def load_settings():
    cfg = dict(DEFAULTS)
    try:
        if os.path.exists(_SETTINGS_PATH):
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update(user)
    except Exception:
        pass
    if cfg.get("theme") not in ("dark", "light", "system"):
        cfg["theme"] = "dark"
    if cfg.get("language") not in ("fa", "en"):
        cfg["language"] = "fa"
    return cfg


def save_settings(cfg):
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_theme_mode():
    return load_settings().get("theme", "dark")


def set_theme_mode(mode):
    cfg = load_settings()
    cfg["theme"] = mode if mode in ("dark", "light", "system") else "dark"
    save_settings(cfg)


def get_language():
    return load_settings().get("language", "fa")


def set_language(lang):
    cfg = load_settings()
    cfg["language"] = lang if lang in ("fa", "en") else "fa"
    save_settings(cfg)

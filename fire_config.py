# -*- coding: utf-8 -*-
"""تنظیمات تشخیص تصویری آتش/دود (fire_config):

- سه سطح حساسیت «کم / متوسط / زیاد» که هم آستانه‌ی مدل YOLO، هم پارامترهای
  آشکارساز شعله‌ی کوچک (small_flame_detector.py) و هم منطق تأیید چندفریمی را
  کنترل می‌کند.
- انتخاب کاربر در فایل ``fire_settings.json`` کنار همین ماژول ذخیره می‌شود تا
  بین اجراها حفظ شود؛ خواندن/نوشتن thread-safe است چون تردهای تشخیص دوربین‌ها
  در هر دور تشخیص پارامترها را از اینجا می‌خوانند (پس تغییر حساسیت از UI بلافاصله
  و بدون ری‌استارت اعمال می‌شود).
"""

import json
import os
import threading

SENSITIVITY_LEVELS = {
    # yolo_conf: آستانه‌ی اطمینان مدل YOLO تمام‌فریم
    # confirm_k / confirm_n: تأیید چندفریمی - حداقل k فریم از n فریم آخر
    # flicker_std: حداقل انحراف معیار روشنایی (۰-۲۵۵) برای «سوسو زدن»
    # flicker_ratio: حداقل کسر پیکسل‌های سوسوزن داخل ناحیه‌ی کاندید
    # min_area: حداقل مساحت ناحیه (پیکسل، در تصویر ۳۲۰ پیکسلی) برای شعله‌ی کوچک
    "low": {
        "label": "کم",
        "yolo_conf": 0.50,
        "confirm_k": 4,
        "confirm_n": 6,
        "flicker_std": 22.0,
        "flicker_ratio": 0.35,
        "min_area": 60,
    },
    "medium": {
        "label": "متوسط",
        "yolo_conf": 0.35,
        "confirm_k": 3,
        "confirm_n": 6,
        "flicker_std": 16.0,
        "flicker_ratio": 0.25,
        "min_area": 25,
    },
    "high": {
        "label": "زیاد",
        "yolo_conf": 0.25,
        "confirm_k": 2,
        "confirm_n": 5,
        "flicker_std": 12.0,
        "flicker_ratio": 0.18,
        "min_area": 12,
    },
}

DEFAULT_SENSITIVITY = "medium"

_SETTINGS_FILENAME = "fire_settings.json"


def _settings_path():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        base = os.getcwd()
    return os.path.join(base, _SETTINGS_FILENAME)


_lock = threading.RLock()
_current = DEFAULT_SENSITIVITY


def _load():
    global _current
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        level = data.get("sensitivity")
        if level in SENSITIVITY_LEVELS:
            _current = level
    except Exception:
        pass


_load()


def get_sensitivity():
    """کلید سطح حساسیت فعلی: 'low' / 'medium' / 'high'."""
    with _lock:
        return _current


def set_sensitivity(level):
    """تغییر سطح حساسیت + ذخیره در فایل (برای ماندگاری بین اجراها)."""
    if level not in SENSITIVITY_LEVELS:
        raise ValueError(f"unknown sensitivity level: {level!r}")
    with _lock:
        global _current
        _current = level
        try:
            with open(_settings_path(), "w", encoding="utf-8") as f:
                json.dump({"sensitivity": level}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # ذخیره‌سازی اختیاری است؛ خطا نباید تشخیص را متوقف کند


def get_params():
    """دیکشنری پارامترهای سطح حساسیت فعلی (کپی، برای امنیت در تردها)."""
    with _lock:
        return dict(SENSITIVITY_LEVELS[_current])


def level_label(level):
    return SENSITIVITY_LEVELS.get(level, {}).get("label", level)

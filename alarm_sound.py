# -*- coding: utf-8 -*-
"""
alarm_sound.py — پخش صدای هشدار حریق در IAS-CMS.

- وقتی آتش/دود تشخیص داده می‌شود، آژیر به‌صورت لوپ پخش می‌شود تا کاربر
  آن را قطع کند (یا آلارم پاک شود).
- فایل پیش‌فرض: assets/fire_alarm.wav (آژیر دوتُن، همراه همین پکیج).
- کاربر می‌تواند فایل صوتی دلخواه (wav/mp3) انتخاب کند؛ مسیر در
  alarm_sound_config.json ذخیره می‌شود.
- اولویت پخش:
    1. QSoundEffect (PyQt6.QtMultimedia) اگر موجود باشد
    2. winsound روی ویندوز (PlaySound با SND_LOOP)
    3. QApplication.beep به‌عنوان آخرین راه
"""

import json
import os
import threading

def _config_dir():
    try:
        from app_paths import get_data_dir
        return get_data_dir()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


_CONFIG_PATH = os.path.join(_config_dir(), "alarm_sound_config.json")
_DEFAULT_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "assets", "fire_alarm.wav")

DEFAULT_CONFIG = {
    # هر صدا مستقل و جداگانه قابل فعال/غیرفعال‌سازی است:
    "fire_enabled": False,       # آژیر ممتد حریق (پیش‌فرض خاموش)
    "zone_enabled": False,        # بوق ورود به محدوده (پیش‌فرض خاموش)
    "violation_enabled": True,    # بوق تخلف طبقاتی (پیش‌فرض روشن، مثل قبل)
    "sound_file": "",        # خالی = فایل پیش‌فرض assets/fire_alarm.wav
    "volume": 0.9,           # 0.0 تا 1.0 (فقط برای QSoundEffect)
    "loop": True,            # تکرار تا قطع شدن
    "max_seconds": 120,      # سقف پخش خودکار (۰ = بدون سقف)
}

# نگاشت نوع صدا به کلید تنظیمات
_SOUND_KEYS = {
    "fire": "fire_enabled",
    "zone": "zone_enabled",
    "violation": "violation_enabled",
}


def _read_raw():
    """خواندن خام فایل json بدون اعمال پیش‌فرض‌ها (برای تشخیص کلیدهای موجود)."""
    try:
        if os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_raw(data):
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def migrate_legacy_violation(get_setting):
    """مهاجرت یک‌باره‌ی تنظیم قدیمی «floor_violation_sound» از person_store
    به کلید جدید «violation_enabled». get_setting: تابع (key, default)."""
    raw = _read_raw()
    if "violation_enabled" in raw:
        return
    try:
        enabled = str(get_setting("floor_violation_sound", "1")) == "1"
    except Exception:
        enabled = True
    raw["violation_enabled"] = enabled
    _write_raw(raw)


def sound_enabled(kind, cfg=None):
    """وضعیت فعال‌بودن یک نوع صدا: 'fire' | 'zone' | 'violation'."""
    key = _SOUND_KEYS.get(kind)
    if key is None:
        return False
    c = cfg if isinstance(cfg, dict) else load_config()
    return bool(c.get(key, DEFAULT_CONFIG.get(key, False)))


def set_sound_enabled(kind, value):
    """تغییر وضعیت یک نوع صدا و ذخیره."""
    key = _SOUND_KEYS.get(kind)
    if key is None:
        return False
    cfg = load_config()
    cfg[key] = bool(value)
    return save_config(cfg)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    user = _read_raw()
    if user:
        # مهاجرت از نسخه‌های قدیمی: کلید واحد «enabled» مربوط به آژیر حریق بود
        if "fire_enabled" not in user and "enabled" in user:
            user["fire_enabled"] = bool(user["enabled"])
        cfg.update(user)
    # کلید قدیمی را نگه ندار تا ابهام ایجاد نکند
    cfg.pop("enabled", None)
    return cfg


def save_config(cfg):
    try:
        data = dict(cfg)
        data.pop("enabled", None)  # کلید منسوخ نسخه‌های قدیمی
        return _write_raw(data)
    except Exception:
        return False


def resolve_sound_file(cfg=None):
    cfg = cfg or load_config()
    p = (cfg.get("sound_file") or "").strip()
    if p and os.path.exists(p):
        return p
    if os.path.exists(_DEFAULT_WAV):
        return _DEFAULT_WAV
    return ""


class AlarmSoundPlayer:
    """پخش‌کننده‌ی آژیر. یک نمونه بسازید و نگه دارید."""

    def __init__(self, config=None):
        self.config = config or load_config()
        self._effect = None
        self._playing = False
        self._lock = threading.Lock()
        self._stop_timer = None

    # -- API عمومی -------------------------------------------------------
    def start(self):
        """شروع آژیر (لوپ طبق تنظیمات). فقط آژیر حریق — با fire_enabled گیت می‌شود."""
        if not sound_enabled("fire", self.config):
            return
        with self._lock:
            if self._playing:
                return
            self._playing = True
        path = resolve_sound_file(self.config)
        loop = bool(self.config.get("loop", True))
        max_sec = int(self.config.get("max_seconds") or 0)

        if self._try_qsoundeffect(path, loop):
            pass
        elif not self._try_winsound(path, loop):
            self._fallback_beep()

        if max_sec > 0:
            import threading as _th
            if self._stop_timer is not None:
                self._stop_timer.cancel()
            self._stop_timer = _th.Timer(max_sec, self.stop)
            self._stop_timer.daemon = True
            self._stop_timer.start()

    def stop(self):
        """قطع آژیر."""
        with self._lock:
            self._playing = False
        if self._stop_timer is not None:
            try:
                self._stop_timer.cancel()
            except Exception:
                pass
            self._stop_timer = None
        try:
            if self._effect is not None:
                self._effect.stop()
        except Exception:
            pass
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except Exception:
            pass

    def play_once(self):
        """یک‌بار پخش (برای دکمه‌ی تست)."""
        old_loop = self.config.get("loop", True)
        self.config["loop"] = False
        try:
            self.stop()
            self.start()
        finally:
            self.config["loop"] = old_loop
            # بعد از ~۵ ثانیه قطع کن تا تک‌پخش واقعی باشد
            import threading as _th
            t = _th.Timer(5.0, self.stop)
            t.daemon = True
            t.start()

    @property
    def is_playing(self):
        with self._lock:
            return self._playing

    def reload(self):
        self.config = load_config()

    # -- backend ها ---------------------------------------------------------
    def _try_qsoundeffect(self, path, loop):
        if not path:
            return False
        try:
            from PyQt6.QtMultimedia import QSoundEffect
            from PyQt6.QtCore import QUrl
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(path))
            try:
                effect.setVolume(float(self.config.get("volume", 0.9)))
            except Exception:
                pass
            try:
                effect.setLoopCount(QSoundEffect.Loop.Infinite if loop
                                    else 1)
            except Exception:
                pass
            effect.play()
            self._effect = effect
            return True
        except Exception:
            return False

    def _try_winsound(self, path, loop):
        try:
            import winsound
        except ImportError:
            return False
        if path and os.path.exists(path):
            def _run():
                try:
                    flags = winsound.SND_FILENAME | winsound.SND_ASYNC
                    if loop:
                        flags |= winsound.SND_LOOP
                    winsound.PlaySound(path, flags)
                except Exception:
                    pass
            threading.Thread(target=_run, daemon=True).start()
            return True
        return False

    def _fallback_beep(self):
        def _run():
            try:
                import winsound
                import time
                # آژیر ساده با بیپ متناوب (اگر فایل صوتی پیدا نشد)
                for _ in range(12):
                    with self._lock:
                        if not self._playing:
                            break
                    winsound.Beep(880, 400)
                    time.sleep(0.15)
                    with self._lock:
                        if not self._playing:
                            break
                    winsound.Beep(660, 400)
                    time.sleep(0.15)
            except Exception:
                try:
                    from PyQt6.QtWidgets import QApplication
                    QApplication.beep()
                except Exception:
                    pass
        threading.Thread(target=_run, daemon=True).start()

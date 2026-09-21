# -*- coding: utf-8 -*-
"""credential_vault.py — ذخیره‌ی امن رمزهای عبور دوربین‌ها و NVRها.

به دستور کاربر (2.0.15-beta): رمزها باید بین اجراهای برنامه «سیو» بمانند تا
هر بار لازم نباشد دوباره وارد شوند — اما نه به‌صورت متن ساده روی دیسک.

راه‌حل بدون هیچ وابستگی جدید (قانون «بدون نصب روی ویندوز کاربر»):
روی ویندوز از DPAPI خود سیستم‌عامل (CryptProtectData / CryptUnprotectData
با حوزه‌ی کاربر جاری) از طریق ctypes استفاده می‌شود؛ یعنی فقط همان یوزر
ویندوزی که رمز را ذخیره کرده می‌تواند آن را بخواند و هیچ کلیدی در فایل‌های
برنامه نیست. روی غیر ویندوز (محیط توسعه) fallback ساده‌ی مبهم‌سازی با فایل
کلید 0o600 است که امن محسوب نمی‌شود و فقط برای تست است.

نحوه‌ی استفاده (camera_store.py):
    blob = encrypt("secret")      # -> رشته‌ی base64 برای ذخیره در JSON
    plain = decrypt(blob)         # -> "secret" یا None در صورت خطا
"""

import base64
import ctypes
import json
import os

try:
    from app_paths import get_data_dir
    _DATA_DIR = get_data_dir()
except Exception:
    _DATA_DIR = os.path.dirname(os.path.abspath(__file__))

_FALLBACK_KEY_PATH = os.path.join(_DATA_DIR, ".vault_key")


def is_windows() -> bool:
    return os.name == "nt"


def is_available() -> bool:
    """آیا ذخیره‌ی امن واقعی (DPAPI) در دسترس است؟"""
    if not is_windows():
        return False
    try:
        ctypes.windll.crypt32.CryptProtectData
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ DPAPI --
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi_protect(plain: bytes) -> bytes | None:
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        # بافر ورودی باید در متغیر مستقل زنده بماند تا بعد از فراخوانی
        # CryptProtectData توسط GC آزاد نشود (حتی اگر cast به‌ظاهر ارجاع نگه دارد).
        in_buf = ctypes.create_string_buffer(plain)
        in_blob = _DATA_BLOB()
        in_blob.cbData = len(plain)
        in_blob.pbData = ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_ubyte))
        out_blob = _DATA_BLOB()
        # CRYPTPROTECT_UI_FORBIDDEN = 0x1 : هیچ پنجره‌ای نشان نده
        ok = crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None,
                                      None, 0x1, ctypes.byref(out_blob))
        if not ok:
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return None


def _dpapi_unprotect(cipher: bytes) -> bytes | None:
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = ctypes.create_string_buffer(cipher)
        in_blob = _DATA_BLOB()
        in_blob.cbData = len(cipher)
        in_blob.pbData = ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_ubyte))
        out_blob = _DATA_BLOB()
        ok = crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None,
                                        None, None, 0x1, ctypes.byref(out_blob))
        if not ok:
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return None


# ------------------------------------------------------- fallback (dev) --
def _fallback_key() -> bytes:
    try:
        if os.path.exists(_FALLBACK_KEY_PATH):
            with open(_FALLBACK_KEY_PATH, "rb") as f:
                key = f.read().strip()
                if len(key) >= 16:
                    return key
        key = os.urandom(32)
        with open(_FALLBACK_KEY_PATH, "wb") as f:
            f.write(key)
        try:
            os.chmod(_FALLBACK_KEY_PATH, 0o600)
        except Exception:
            pass
        return key
    except Exception:
        return b"ias-cms-dev-fallback-key"


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


# ------------------------------------------------------------------ API --
def encrypt(plaintext: str) -> str | None:
    """رمزنگاری یک رشته و برگرداندن blob متنی (base64) برای ذخیره در JSON.
    ورودی خالی -> None. در صورت خطا -> None."""
    if not plaintext:
        return None
    raw = plaintext.encode("utf-8")
    if is_available():
        cipher = _dpapi_protect(raw)
        if cipher is None:
            return None
        return "dpapi1:" + base64.b64encode(cipher).decode("ascii")
    # فقط محیط توسعه
    cipher = _xor(raw, _fallback_key())
    return "xor1:" + base64.b64encode(cipher).decode("ascii")


def decrypt(blob: str | None) -> str | None:
    """برگرداندن متن اصلی از blob ساخته‌شده با encrypt؛ خطا -> None."""
    if not blob or not isinstance(blob, str):
        return None
    try:
        if blob.startswith("dpapi1:"):
            if not is_available():
                return None
            plain = _dpapi_unprotect(base64.b64decode(blob[7:]))
        elif blob.startswith("xor1:"):
            plain = _xor(base64.b64decode(blob[5:]), _fallback_key())
        else:
            return None
        return plain.decode("utf-8") if plain else None
    except Exception:
        return None


def backend_label() -> str:
    if is_available():
        return "DPAPI ویندوز (امن)"
    if is_windows():
        return "DPAPI در دسترس نیست"
    return "مبهم‌سازی محلی (فقط توسعه)"

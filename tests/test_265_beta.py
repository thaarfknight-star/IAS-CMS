# -*- coding: utf-8 -*-
"""تست 2.0.78-beta — رگرسیون گزارش طه (2026-09-29):
۱) پلاک موتورسیکلت در تصویر «گرفته» می‌شد (کادر رسم می‌شد) ولی هیچ رویدادی
   ثبت نمی‌شد؛ چون _looks_like_plate فقط قالب «۳-۴ رقم + ۱-۲ حرف» را موتور
   می‌دانست، در حالی که پلاک موتورسیکلت ایرانی «۳ رقم ردیف بالا + ۵ رقم ردیف
   پایین» است و حرف ندارد. خوانش OCR رد می‌شد و رویداد صادر نمی‌شد.
۲) فرم تعریف پلاک موتور هم غلط بود: ردیف پایین فقط ۱ رقم + حرف می‌گرفت.
حالا قالب درست: ۸ رقم (۳+۵)، بدون حرف.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plate_store import (
    detect_plate_kind,
    normalize_plate_text,
    prettify_plate,
    validate_motorcycle_plate,
)
from plate_detector import _looks_like_plate, canonicalize_ocr_text


# ---------- validate_motorcycle_plate ----------

def test_validate_motorcycle_ok():
    ok, err, canon = validate_motorcycle_plate("166", "17694")
    assert ok, err
    assert canon == "16617694"


def test_validate_motorcycle_persian_digits():
    ok, err, canon = validate_motorcycle_plate("۱۶۶", "۱۷۶۹۴")
    assert ok, err
    assert canon == "16617694"


def test_validate_motorcycle_rejects_short_bottom():
    ok, err, _ = validate_motorcycle_plate("166", "1769")
    assert not ok
    assert "۵ رقم" in err


def test_validate_motorcycle_rejects_short_top():
    ok, err, _ = validate_motorcycle_plate("16", "17694")
    assert not ok
    assert "۳ رقم" in err


def test_validate_motorcycle_rejects_old_single_digit():
    # قالب قدیمیِ غلط (۱ رقم پایین) دیگر معتبر نیست
    ok, _, _ = validate_motorcycle_plate("123", "4")
    assert not ok


# ---------- detect_plate_kind ----------

def test_detect_kind_motorcycle():
    assert detect_plate_kind("16617694") == "motorcycle"


def test_detect_kind_car_unchanged():
    assert detect_plate_kind(normalize_plate_text("12ب34567")) == "car"


def test_detect_kind_car_not_confused_by_8_digits():
    # پلاک ۸رقمی موتور نباید خودرو تشخیص داده شود
    assert detect_plate_kind("16617694") != "car"


# ---------- prettify_plate ----------

def test_prettify_motorcycle():
    assert prettify_plate("16617694") == "۱۶۶ ۱۷۶۹۴"


def test_prettify_car_unchanged():
    assert prettify_plate(normalize_plate_text("12ب34567")) == "۶۷ ۳۴۵ ب ۱۲"


# ---------- OCR pipeline: _looks_like_plate / canonicalize_ocr_text ----------

def test_looks_like_plate_motorcycle():
    assert _looks_like_plate("16617694") is True


def test_looks_like_plate_car_still_ok():
    assert _looks_like_plate(normalize_plate_text("12ب34567")) is True


def test_canonicalize_ocr_motorcycle_not_rejected():
    # این همان باگ اصلی بود: خوانش موتور «» برمی‌گرداند و رویداد صادر نمی‌شد
    assert canonicalize_ocr_text("16617694") == "16617694"


def test_canonicalize_ocr_garbage_still_rejected():
    assert canonicalize_ocr_text("ABC") == ""
    assert canonicalize_ocr_text("123") == ""

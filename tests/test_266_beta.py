# -*- coding: utf-8 -*-
"""تست 2.0.79-beta — رگرسیون گزارش طه (2026-09-29):
پلاک موتورسیکلت هنوز «انداخته» نمی‌شد (رویداد ثبت نمی‌شد).

علت ریشه‌ای دوم (بعد از فیکس قالب در 2.0.78): مدل هزار تک‌سطری آموزش دیده
و ورودی‌اش همیشه به (384, 32) تغییراندازه می‌دهد؛ کراپ مربعی پلاک موتور
یک‌جا له می‌شد و OCR چیزی درست خوانده نمی‌شد. حالا در PlateOCR.read اگر
کراپ مربعی‌شکل باشد (h/w > 0.45)، خوانش دوردیفه هم امتحان می‌شود: کراپ از
وسط نصف و هر نیمه جدا OCR می‌شود، بعد ۳ رقم بالا + ۵ رقم پایین ترکیب می‌شود.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plate_detector
from plate_detector import PlateOCR


def _make_ocr(top_text, bot_text, conf=0.9):
    """PlateOCR با _ocr_raw_line شبیه‌سازی‌شده (نیمه‌ی بالا بعد پایین)."""
    ocr = PlateOCR.__new__(PlateOCR)
    ocr._hezar_session = object()
    ocr._hezar_error = ""
    ocr._hezar_next_retry = 0.0
    texts = iter([top_text, bot_text])

    def fake_get_hezar():
        return ocr._hezar_session

    def fake_raw(sess, crop):
        assert crop.shape[0] > 0 and crop.shape[1] > 0
        return next(texts), conf

    ocr._get_hezar = fake_get_hezar
    ocr._ocr_raw_line = fake_raw
    return ocr


def _square_crop(h=60, w=60):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_tworow_combines_top_bottom():
    old_cv2 = plate_detector.cv2
    plate_detector.cv2 = object()
    try:
        ocr = _make_ocr("166", "17694")
        out = ocr._read_hezar_tworow(_square_crop())
    finally:
        plate_detector.cv2 = old_cv2
    assert len(out) == 1
    text, conf, engine = out[0]
    assert text == "16617694", text
    assert engine == "hezar-crnn-fa-2row"
    assert conf == 0.9


def test_tworow_swaps_reversed_halves():
    old_cv2 = plate_detector.cv2
    plate_detector.cv2 = object()
    try:
        ocr = _make_ocr("17694", "166")
        out = ocr._read_hezar_tworow(_square_crop())
    finally:
        plate_detector.cv2 = old_cv2
    assert len(out) == 1 and out[0][0] == "16617694", out


def test_tworow_persian_digits():
    old_cv2 = plate_detector.cv2
    plate_detector.cv2 = object()
    try:
        ocr = _make_ocr("۱۶۶", "۱۷۶۹۴")
        out = ocr._read_hezar_tworow(_square_crop())
    finally:
        plate_detector.cv2 = old_cv2
    assert len(out) == 1 and out[0][0] == "16617694", out


def test_tworow_rejects_wrong_lengths():
    old_cv2 = plate_detector.cv2
    plate_detector.cv2 = object()
    try:
        ocr = _make_ocr("16", "17694")
        assert ocr._read_hezar_tworow(_square_crop()) == []
        ocr = _make_ocr("166", "1769")
        assert ocr._read_hezar_tworow(_square_crop()) == []
    finally:
        plate_detector.cv2 = old_cv2


def test_tworow_rejects_garbage():
    old_cv2 = plate_detector.cv2
    plate_detector.cv2 = object()
    try:
        ocr = _make_ocr("ABC", "17694")
        assert ocr._read_hezar_tworow(_square_crop()) == []
    finally:
        plate_detector.cv2 = old_cv2


def test_tworow_tiny_crop_rejected():
    old_cv2 = plate_detector.cv2
    plate_detector.cv2 = object()
    try:
        ocr = _make_ocr("166", "17694")
        assert ocr._read_hezar_tworow(np.zeros((8, 60, 3), dtype=np.uint8)) == []
    finally:
        plate_detector.cv2 = old_cv2

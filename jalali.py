# -*- coding: utf-8 -*-
"""تبدیل دقیق تاریخ میلادی به شمسی (تقویم جلالی) - بدون وابستگی خارجی.

الگوریتم jalaali (پورت پایتون jalaali-js) - دقیق برای همه‌ی سال‌ها، برخلاف
نسخه‌ی قدیمیِ تقسیم‌محور که در person_store بود و بعضی تاریخ‌ها را چند روز
جا‌به‌جا نشان می‌داد. این ماژول تک‌منبع حقیقت است؛ person_store و plate_store
هر دو از همین‌جا ایمپورت می‌کنند.

توابع عمومی:
- gregorian_to_jalali(gy, gm, gd) -> (jy, jm, jd)
- jalali_now_str(ts=None)  -> "YYYY/MM/DD HH:MM:SS"
- jalali_date_str(ts=None) -> "YYYY/MM/DD"
"""

from datetime import datetime


# --------------------------------------------------------------------------
# تبدیل تاریخ میلادی به شمسی (بدون وابستگی خارجی - الگوریتم jalaali)
# --------------------------------------------------------------------------

def _div(a, b):
    return int(a / b)


def _mod(a, b):
    return a - _div(a, b) * b


_JALALI_BREAKS = [
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
]


def _jal_cal(jy):
    bl = len(_JALALI_BREAKS)
    gy = jy + 621
    leap_j = -14
    jp = _JALALI_BREAKS[0]
    jump = 0
    for i in range(1, bl):
        jm = _JALALI_BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return leap, gy, march


def _g2d(gy, gm, gd):
    d = (_div((gy + _div(gm - 8, 6) + 100100) * 1461, 4)
         + _div(153 * _mod(gm + 9, 12) + 2, 5)
         + gd - 34840408)
    d = d - _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4) + 752
    return d


def _d2g(jdn):
    j = 4 * jdn + 139361631
    j = j + _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _div(_mod(j, 1461), 4) * 5 + 308
    gd = _div(_mod(i, 153), 5) + 1
    gm = _mod(_div(i, 153), 12) + 1
    gy = _div(j, 1461) - 100100 + _div(8 - gm, 6)
    return gy, gm, gd


def _d2j(jdn):
    gy, gm, gd = _d2g(jdn)
    jy = gy - 621
    leap, _, march = _jal_cal(jy)
    jdn1f = _g2d(gy, 3, march)
    k = jdn - jdn1f
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


def gregorian_to_jalali(gy, gm, gd):
    """(سال، ماه، روز) میلادی -> (سال، ماه، روز) شمسی."""
    return _d2j(_g2d(gy, gm, gd))


def jalali_now_str(ts=None):
    """رشته‌ی تاریخ/ساعت شمسی «YYYY/MM/DD HH:MM:SS» برای نمایش در گزارش‌ها."""
    dt = datetime.fromtimestamp(ts) if ts else datetime.now()
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def jalali_date_str(ts=None):
    dt = datetime.fromtimestamp(ts) if ts else datetime.now()
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"



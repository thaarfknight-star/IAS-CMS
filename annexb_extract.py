"""استخراج واحدهای NAL از داده‌ی خام باینری WebSocket بدون نیاز به دانستن
فرمت دقیق هدر اختصاصی فروشنده.

چرا این روش (نه پارس کردن هدر باینری):
--------------------------------------------------------------------------
همان‌طور که در nvr_ws_protocol.py توضیح داده شد، فرمت دقیق هدر فریم برای
این پروتکل از تحلیل استاتیک JS قابل کشف نبود. اما تقریباً تمام این
پروتکل‌های اختصاصی، payload واقعی ویدیو را به‌صورت H.264/H.265 با Annex-B
start code (``00 00 00 01`` یا ``00 00 01``) قبل از هر NAL می‌فرستند --
چه بلافاصله بعد از یک هدر ثابت کوچک، چه با کمی padding/متادیتای دیگر.

این ماژول به‌جای فرض کردن یک طول هدر مشخص، کل بافر ورودی را برای start code
اسکن می‌کند و هر NAL را مستقل از طول/محتوای هدر اطراف آن استخراج می‌کند --
یعنی نسبت به هدر ناشناخته مقاوم است. اگر داده‌ی ورودی اصلاً Annex-B نباشد
(مثلاً کدک متفاوت یا کانتینر کاملاً متفاوت)، هیچ NAL معتبری پیدا نمی‌شود و
فراخوان (camera_stream.py) این را به‌عنوان شکست دیکود گزارش می‌کند.
"""

from __future__ import annotations

from typing import List, Tuple

START_CODE_4 = b"\x00\x00\x00\x01"
START_CODE_3 = b"\x00\x00\x01"


def find_start_codes(buf: bytes) -> List[int]:
    """اندیس بایت اول *بعد از* هر start code را برمی‌گرداند (یعنی شروع NAL)."""
    idxs = []
    i = 0
    n = len(buf)
    while i < n - 2:
        if buf[i] == 0 and buf[i + 1] == 0:
            if i + 3 < n and buf[i + 2] == 0 and buf[i + 3] == 1:
                idxs.append(i + 4)
                i += 4
                continue
            if buf[i + 2] == 1:
                idxs.append(i + 3)
                i += 3
                continue
        i += 1
    return idxs


def extract_nal_units(buf: bytes) -> List[bytes]:
    """تمام NAL unitهای کامل (شامل start code) داخل buf را برمی‌گرداند.

    آخرین NAL چون ممکن است هنوز کامل نرسیده باشد نادیده گرفته نمی‌شود --
    فراخوان مسئول تجمیع/parse تدریجی بافر است (رجوع کنید به AnnexBAssembler).
    """
    starts = find_start_codes(buf)
    if not starts:
        return []
    nals = []
    for k, s in enumerate(starts):
        # start code واقعی که این NAL با آن شروع می‌شود را هم اضافه می‌کن
        # (برای دیکودرهایی مثل PyAV که Annex-B کامل با start code می‌خواهند).
        sc_len = 4 if buf[s - 4:s - 1] == b"\x00\x00\x00" else 3
        nal_start = s - sc_len
        nal_end = starts[k + 1] - (4 if buf[starts[k + 1] - 4:starts[k + 1] - 1] == b"\x00\x00\x00" else 3) \
            if k + 1 < len(starts) else len(buf)
        nals.append(buf[nal_start:nal_end])
    return nals


class AnnexBAssembler:
    """بافر تدریجی: فریم‌های باینری WS را جمع می‌کند و هر NAL *کامل* (یعنی
    قبل از شروع NAL بعدی) را تحویل می‌دهد. آخرین بخش ناقص برای دفعه‌ی بعد
    نگه داشته می‌شود تا NAL روی مرز دو فریم WS قطع نشود."""

    def __init__(self, max_buffer: int = 4 * 1024 * 1024):
        self._buf = bytearray()
        self._max_buffer = max_buffer

    def feed(self, chunk: bytes) -> List[bytes]:
        self._buf.extend(chunk)
        if len(self._buf) > self._max_buffer:
            # محافظت در برابر نشتی حافظه اگر هیچ‌وقت start code معتبری پیدا
            # نشود (یعنی این پروتکل اصلاً Annex-B نیست).
            del self._buf[: len(self._buf) - self._max_buffer]

        starts = find_start_codes(bytes(self._buf))
        if len(starts) < 2:
            return []

        complete_nals = []
        for k in range(len(starts) - 1):
            s = starts[k]
            sc_len = 4 if self._buf[s - 4:s - 1] == b"\x00\x00\x00" else 3
            nal_start = s - sc_len
            nal_end_marker = starts[k + 1]
            sc_len_next = 4 if self._buf[nal_end_marker - 4:nal_end_marker - 1] == b"\x00\x00\x00" else 3
            nal_end = nal_end_marker - sc_len_next
            complete_nals.append(bytes(self._buf[nal_start:nal_end]))

        # فقط تا شروع آخرین start code (که ممکن است NAL آن هنوز کامل نرسیده
        # باشد) از بافر حذف می‌شود؛ بقیه برای دفعه‌ی بعد نگه داشته می‌شود.
        last_start = starts[-1]
        sc_len_last = 4 if self._buf[last_start - 4:last_start - 1] == b"\x00\x00\x00" else 3
        del self._buf[: last_start - sc_len_last]

        return complete_nals

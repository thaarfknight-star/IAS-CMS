# -*- coding: utf-8 -*-
"""license_crypto.py — رمزنگاری لایسنس IAS-CMS (امضای دیجیتال Ed25519).

پیاده‌سازی خالص پایتون از Ed25519 (فقط hashlib از کتابخانه‌ی استاندارد؛
هیچ وابستگی جدیدی به برنامه اضافه نمی‌کند) بر اساس پیاده‌سازی مرجع
دامنه‌ی عمومی (djb / Frank Braun).

مدل امنیتی:
- جفت‌کلید یک‌بار توسط فروشنده (طه) با `generate_keypair()` ساخته می‌شود.
- **کلید خصوصی** فقط نزد فروشنده می‌ماند و هرگز داخل ریپو/برنامه نیست؛
  با آن فایل لایسنس هر مشتری در `tools/make_license.py` امضا می‌شود.
- **کلید عمومی** داخل همین فایل (LICENSE_PUBLIC_KEY) در برنامه جاسازی
  شده و صحت امضای هر فایل لایسنس را بررسی می‌کند. چون ریپو عمومی است،
  فقط کلید عمومی اینجاست و جعل لایسنس بدون کلید خصوصی ممکن نیست.
"""

import hashlib
import os

# ----------------------------------------------------------------------------
# ثابت‌های منحنی Ed25519
# ----------------------------------------------------------------------------
_b = 256
_q = (1 << 255) - 19
_l = (1 << 252) + 27742317777372353535851937790883648493


def _H(m):
    return hashlib.sha512(m).digest()


def _inv(x):
    return pow(x, _q - 2, _q)


_d = (-121665 * _inv(121666)) % _q
_i = pow(2, (_q - 1) // 4, _q)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_d * y * y + 1) % _q
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _i) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = (4 * _inv(5)) % _q
_Bx = _xrecover(_By)
_B = (_Bx % _q, _By % _q)


def _edwards(P, Q):
    # قانون جمع Edwards پیچیده‌شده (twisted): با a=-1 در Ed25519،
    # داریم y3 = (y1*y2 + x1*x2) / (1 - d*x1*x2*y1*y2)
    x1, y1 = P
    x2, y2 = Q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _d * x1 * x2 * y1 * y2) % _q
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _d * x1 * x2 * y1 * y2) % _q
    return (x3, y3)


def _scalarmult(P, e):
    if e == 0:
        return (0, 1)
    Q = _scalarmult(P, e // 2)
    Q = _edwards(Q, Q)
    if e & 1:
        Q = _edwards(Q, P)
    return Q


def _encodeint(y):
    bits = [(y >> i) & 1 for i in range(_b)]
    return b"".join(
        bytes([sum(bits[i * 8 + j] << j for j in range(8))])
        for i in range(_b // 8))


def _encodepoint(P):
    x, y = P
    bits = [(y >> i) & 1 for i in range(_b - 1)] + [x & 1]
    return b"".join(
        bytes([sum(bits[i * 8 + j] << j for j in range(8))])
        for i in range(_b // 8))


def _bit(h, i):
    return (h[i // 8] >> (i % 8)) & 1


def _publickey(sk):
    h = _H(sk)
    a = 2 ** (_b - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _b - 2))
    A = _scalarmult(_B, a)
    return _encodepoint(A)


def _Hint(m):
    h = _H(m)
    return sum(2 ** i * _bit(h, i) for i in range(2 * _b))


def _signature(m, sk, pk):
    h = _H(sk)
    a = 2 ** (_b - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _b - 2))
    r = _Hint(bytes(h[i] for i in range(_b // 8, _b // 4)) + m)
    R = _scalarmult(_B, r)
    S = (r + _Hint(_encodepoint(R) + pk + m) * a) % _l
    return _encodepoint(R) + _encodeint(S)


def _isoncurve(P):
    x, y = P
    return (-x * x + y * y - 1 - _d * x * x * y * y) % _q == 0


def _decodeint(s):
    return sum(2 ** i * _bit(s, i) for i in range(_b))


def _decodepoint(s):
    y = sum(2 ** i * _bit(s, i) for i in range(_b - 1))
    sign = _bit(s, _b - 1)
    x = _xrecover(y)
    if x & 1 != sign:
        x = _q - x
    P = (x, y)
    if not _isoncurve(P):
        raise ValueError("decoded point is not on the curve")
    return P


def _checkvalid(sig, m, pk):
    if len(sig) != _b // 4:
        raise ValueError("bad signature length")
    if len(pk) != _b // 8:
        raise ValueError("bad public-key length")
    R = _decodepoint(sig[:_b // 8])
    A = _decodepoint(pk)
    S = _decodeint(sig[_b // 8:_b // 4])
    if S >= _l:
        raise ValueError("S out of range")
    h = _Hint(_encodepoint(R) + pk + m)
    P1 = _scalarmult(_B, S)
    P2 = _edwards(R, _scalarmult(A, h))
    return P1 == P2


# ----------------------------------------------------------------------------
# کلید عمومی جاسازی‌شده در برنامه (متناظر با کلید خصوصی نزد فروشنده).
# ----------------------------------------------------------------------------
LICENSE_PUBLIC_KEY = (
    "bb9a590e516d9fba114802e258ea3d25e3cc79b3c0a25f99086407befde2694a"
)


# ----------------------------------------------------------------------------
# API عمومی
# ----------------------------------------------------------------------------
def generate_keypair():
    """ساخت جفت‌کلید تازه؛ برمی‌گرداند (private_hex, public_hex).

    فقط روی سیستم فروشنده اجرا شود؛ کلید خصوصی را در فایلی امن نگه دارید
    و هرگز داخل ریپو یا برنامه قرار ندهید."""
    sk = os.urandom(32)
    pk = _publickey(sk)
    return sk.hex(), pk.hex()


def sign_message(private_hex, message: bytes) -> str:
    """امضای بایت‌ها با کلید خصوصی؛ خروجی hex امضا."""
    sk = bytes.fromhex(private_hex)
    if len(sk) != 32:
        raise ValueError("bad private key length")
    pk = _publickey(sk)
    return _signature(message, sk, pk).hex()


def verify_message(public_hex, message: bytes, signature_hex) -> bool:
    """بررسی امضا با کلید عمومی؛ True یعنی معتبر."""
    try:
        pk = bytes.fromhex(public_hex)
        sig = bytes.fromhex(signature_hex)
        return bool(_checkvalid(sig, message, pk))
    except Exception:
        return False

"""پروب سریع RTSP بدون نیاز به دیکود کامل جریان (بدون OpenCV/FFmpeg).

چرا این ماژول لازم است (بخشی از بازنویسی سیستم جست‌وجوی NVR):
--------------------------------------------------------------
تا قبل از این، تنها راه بررسی «آیا یک مسیر RTSP روی NVR واقعاً وجود دارد»
باز کردن کامل ``cv2.VideoCapture`` و صبر برای رسیدن اولین فریم واقعی بود
(``rtsp_utils.probe_stream``). این روش دو مشکل اصلی داشت:

  ۱) کند بود: باز کردن دیکودر FFmpeg و صبر برای I-frame چند صد میلی‌ثانیه
     تا چند ثانیه طول می‌کشد؛ برای اسکن کامل یک NVR (چند کانال × چند برند ×
     چند الگوی هرکدام)، حتی به‌صورت موازی، اسکن به‌طور محسوس کند بود.
  ۲) کم‌اطلاعات بود: فقط True/False برمی‌گرداند. اگر False می‌شد، معلوم
     نبود دلیلش رمز/کاربری اشتباه است (401)، مسیر اشتباه است (404)، یا
     اصلاً NVR پاسخ نمی‌دهد (timeout/connection refused) - همین ابهام
     باعث می‌شد کاربر نداند دقیقاً مشکل را کجا رفع کند.

این ماژول با ارسال مستقیم درخواست پروتکل RTSP «DESCRIBE» (که سرور بدون
شروع پخش واقعی جریان، فقط با یک کد وضعیت و اطلاعات SDP پاسخ می‌دهد) در
کسری از ثانیه تا حداکثر چند ثانیه جواب می‌گیرد و کد وضعیت دقیق را
برمی‌گرداند تا هم اسکن سریع‌تر شود و هم پیام خطای دقیق‌تری به کاربر
نمایش داده شود. این پیاده‌سازی همان کاری را می‌کند که VLC/FFmpeg داخلاً
انجام می‌دهند؛ صرفاً برای مرحله‌ی «تشخیص/کشف» سبک‌تر شده است.

نکته: این پروب فقط برای *تشخیص وجود و در دسترس‌بودن مسیر* استفاده می‌شود.
تصمیم نهایی برای پخش زنده‌ی واقعی همچنان از طریق ``rtsp_utils.open_capture``
(OpenCV/FFmpeg) انجام می‌شود.
"""

import base64
import hashlib
import socket

DEFAULT_TIMEOUT = 2.0


class RtspProbeResult:
    """نتیجه‌ی یک تلاش پروب.

    status یکی از این مقادیر است:
      "ok"           -> مسیر معتبر است و سرور 200 برگرداند.
      "unauthorized" -> نام کاربری/رمز عبور اشتباه یا ناکافی است (401/403).
      "not_found"    -> مسیر/کانال روی این دستگاه وجود ندارد (404/454).
      "timeout"      -> دستگاه در بازه‌ی زمانی مشخص پاسخ نداد.
      "refused"      -> اتصال به پورت RTSP رد شد (سرویس فعال نیست).
      "error"        -> خطای دیگر (مثلاً کد وضعیت غیرمنتظره).
    """

    __slots__ = ("status", "detail")

    def __init__(self, status, detail=""):
        self.status = status
        self.detail = detail

    def __bool__(self):
        return self.status == "ok"

    def __repr__(self):
        return f"RtspProbeResult({self.status!r}, {self.detail!r})"


def _recv_headers(sock, timeout):
    """فقط تا انتهای بلوک هدر (``\\r\\n\\r\\n``) می‌خواند؛ به بدنه‌ی SDP نیازی
    نیست چون فقط کد وضعیت برایمان مهم است."""
    sock.settimeout(timeout)
    data = b""
    try:
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data


def _parse_status_and_headers(raw: bytes):
    if not raw:
        return None, {}
    head = raw.split(b"\r\n\r\n", 1)[0]
    lines = head.split(b"\r\n")
    if not lines:
        return None, {}
    try:
        status_line = lines[0].decode("latin-1", errors="replace")
        parts = status_line.split(" ", 2)
        status_code = int(parts[1]) if len(parts) > 1 else None
    except (ValueError, IndexError):
        status_code = None
    headers = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        k, _, v = line.partition(b":")
        headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
    return status_code, headers


def _basic_auth_header(user, pwd):
    token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _parse_digest_challenge(value: str):
    if not value or not value.lower().startswith("digest"):
        return None
    body = value[len("digest"):].strip()
    params = {}
    for part in body.split(","):
        if "=" not in part:
            continue
        k, _, v = part.strip().partition("=")
        params[k.strip().lower()] = v.strip().strip('"')
    return params or None


def _digest_auth_header(user, pwd, method, uri, challenge, cnonce="ia5cms1", nc="00000001"):
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = challenge.get("qop", "")
    algorithm = challenge.get("algorithm", "MD5")

    def md5(s):
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    ha1 = md5(f"{user}:{realm}:{pwd}")
    ha2 = md5(f"{method}:{uri}")

    if qop:
        qop_value = qop.split(",")[0].strip()
        response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop_value}:{ha2}")
        return (
            f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", qop={qop_value}, nc={nc}, cnonce="{cnonce}", '
            f'response="{response}", algorithm={algorithm}'
        )
    response = md5(f"{ha1}:{nonce}:{ha2}")
    return (
        f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{response}", algorithm={algorithm}'
    )


def describe_probe(ip, port, path, user="", pwd="", timeout=DEFAULT_TIMEOUT) -> RtspProbeResult:
    """یک درخواست RTSP DESCRIBE می‌فرستد و بدون دانلود/دیکود جریان واقعی، از
    روی کد وضعیت پاسخ می‌فهمد مسیر/کانال معتبر است یا نه و دقیقاً چرا نیست.
    """
    uri = f"rtsp://{ip}:{port}/{path}" if path else f"rtsp://{ip}:{port}"

    try:
        sock = socket.create_connection((ip, int(port)), timeout=timeout)
    except socket.timeout:
        return RtspProbeResult("timeout", "اتصال به پورت RTSP timeout شد")
    except ConnectionRefusedError:
        return RtspProbeResult("refused", "اتصال به پورت RTSP رد شد (سرویس فعال نیست)")
    except OSError as exc:
        return RtspProbeResult("error", str(exc))

    try:
        cseq = 1
        req = (
            f"DESCRIBE {uri} RTSP/1.0\r\n"
            f"CSeq: {cseq}\r\n"
            f"Accept: application/sdp\r\n"
            f"User-Agent: IAS-CMS-Probe\r\n"
            f"\r\n"
        )
        sock.sendall(req.encode("utf-8"))
        status, headers = _parse_status_and_headers(_recv_headers(sock, timeout))

        if status == 200:
            return RtspProbeResult("ok", "200 OK")

        if status == 401 and (user or pwd):
            digest_params = _parse_digest_challenge(headers.get("www-authenticate", ""))
            cseq += 1
            auth_header = (
                _digest_auth_header(user, pwd, "DESCRIBE", uri, digest_params)
                if digest_params else _basic_auth_header(user, pwd)
            )
            req2 = (
                f"DESCRIBE {uri} RTSP/1.0\r\n"
                f"CSeq: {cseq}\r\n"
                f"Authorization: {auth_header}\r\n"
                f"Accept: application/sdp\r\n"
                f"User-Agent: IAS-CMS-Probe\r\n"
                f"\r\n"
            )
            sock.sendall(req2.encode("utf-8"))
            status2, _ = _parse_status_and_headers(_recv_headers(sock, timeout))
            if status2 == 200:
                return RtspProbeResult("ok", "200 OK (بعد از احراز هویت)")
            if status2 in (401, 403):
                return RtspProbeResult("unauthorized", "نام کاربری یا رمز عبور اشتباه است (401)")
            if status2 in (404, 454):
                return RtspProbeResult("not_found", f"مسیر/کانال یافت نشد ({status2})")
            if status2 is None:
                return RtspProbeResult("timeout", "بدون پاسخ بعد از احراز هویت")
            return RtspProbeResult("error", f"کد پاسخ غیرمنتظره: {status2}")

        if status == 401:
            return RtspProbeResult("unauthorized", "این مسیر نیاز به نام کاربری/رمز عبور دارد")

        if status in (404, 454):
            return RtspProbeResult("not_found", f"مسیر/کانال یافت نشد ({status})")

        if status is None:
            return RtspProbeResult("timeout", "بدون پاسخ از NVR (شاید سرویس RTSP روی این پورت فعال نیست)")

        return RtspProbeResult("error", f"کد پاسخ غیرمنتظره: {status}")
    except socket.timeout:
        return RtspProbeResult("timeout", "زمان پاسخ به پایان رسید")
    except OSError as exc:
        return RtspProbeResult("error", str(exc))
    finally:
        try:
            sock.close()
        except OSError:
            pass

import os

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

# --------------------------------------------------------------------------
# الگوهای استاندارد RTSP برای هر کانال یک NVR، به تفکیک برند.
# {ch}   -> شماره کانال یک‌مبنایی (1, 2, 3, ...)
# {ch0}  -> شماره کانال صفرمبنایی (0, 1, 2, ...)
# {ch2}  -> شماره کانال با دو رقم (01, 02, ...)
# --------------------------------------------------------------------------
CHANNEL_TEMPLATES = {
    "dahua_iap": [
        "cam/realmonitor?channel={ch}&subtype=0",   # main stream
        "cam/realmonitor?channel={ch}&subtype=1",   # sub stream (کم‌حجم‌تر)
    ],
    "hikvision": [
        "Streaming/Channels/{ch}01",   # e.g. 101, 201, 301 = کانال 1، 2، 3 استریم اصلی
        "Streaming/Channels/{ch}02",
    ],
    "sunell": [
        "live/ch{ch0}",
        "live/ch{ch}",
        "h264/ch{ch}/main/av_stream",
    ],
    "generic": [
        "cam/realmonitor?channel={ch}&subtype=0",
        "Streaming/Channels/{ch}01",
        "live/ch{ch0}",
        "h264Preview_{ch2}_main",
        "video{ch}",
        "onvif{ch}",
        "profile{ch}",
    ],
}

BRAND_LABELS = {
    "auto": "تشخیص خودکار (همه برندها)",
    "dahua_iap": "Dahua / IAP",
    "hikvision": "Hikvision",
    "sunell": "Sunell",
    "generic": "سایر / عمومی",
}


def _format_templates(templates, ch):
    return [t.format(ch=ch, ch0=ch - 1, ch2=f"{ch:02d}") for t in templates]


def try_onvif_discovery(ip, onvif_port, user, pwd, timeout=4):
    """تلاش برای کشف کانال‌های واقعی NVR از طریق پروتکل استاندارد ONVIF.

    اگر کتابخانه‌ی onvif-zeep نصب نباشد یا NVR از ONVIF پشتیبانی نکند، None
    برمی‌گرداند تا کد فراخوان به روش برندی (brute force) سوییچ کند.
    این روش دقیق‌ترین راه است چون تعداد و آدرس واقعی کانال‌ها را مستقیماً از
    خود دستگاه می‌گیرد (نه حدس زدن الگوی URL).
    """
    try:
        from onvif import ONVIFCamera
    except ImportError:
        return None

    try:
        cam = ONVIFCamera(ip, int(onvif_port), user, pwd)
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if not profiles:
            return None

        channels = []
        for idx, profile in enumerate(profiles, start=1):
            try:
                token = getattr(profile, "token", None) or getattr(profile, "_token", None)
                req = media.create_type("GetStreamUri")
                req.ProfileToken = token
                req.StreamSetup = {
                    "Stream": "RTP-Unicast",
                    "Transport": {"Protocol": "RTSP"},
                }
                uri = media.GetStreamUri(req).Uri
                name = getattr(profile, "Name", None) or f"کانال {idx}"
                channels.append({"channel": idx, "name": str(name), "url": uri})
            except Exception:
                continue

        return channels or None
    except Exception:
        return None


class NVRScanThread(QThread):
    """کانال‌های (دوربین‌های) متصل به یک NVR را شناسایی می‌کند.

    ابتدا ONVIF را امتحان می‌کند (سریع و دقیق، در صورت پشتیبانی NVR و نصب بودن
    کتابخانه)؛ در غیر این صورت هر کانال از 1 تا max_channels را با الگوهای
    RTSP شناخته‌شده‌ی برندهای رایج تست می‌کند (دقیقاً مشابه منطق auto-detect
    تک‌دوربین موجود در add_camera_dialog، اما برای هر کانال).
    """

    progress_signal = pyqtSignal(str)
    channel_found_signal = pyqtSignal(int, str, str)   # channel, name, path/url
    finished_signal = pyqtSignal(int)                  # تعداد کل کانال‌های یافت‌شده
    failed_signal = pyqtSignal(str)

    def __init__(self, ip, rtsp_port, onvif_port, user, pwd, brand="auto", max_channels=16, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.rtsp_port = rtsp_port
        self.onvif_port = onvif_port
        self.user = user
        self.pwd = pwd
        self.brand = brand
        self.max_channels = max_channels
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _build_url(self, path):
        if self.user and self.pwd:
            return f"rtsp://{self.user}:{self.pwd}@{self.ip}:{self.rtsp_port}/{path}"
        return f"rtsp://{self.ip}:{self.rtsp_port}/{path}"

    def _probe_path(self, path):
        url = self._build_url(path)
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    return True
        finally:
            cap.release()
        return False

    def run(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;2000000"
        found_count = 0

        # ۱) تلاش برای کشف دقیق از طریق ONVIF (در صورت پشتیبانی)
        if self.onvif_port and not self._is_cancelled:
            self.progress_signal.emit("در حال تلاش برای کشف کانال‌ها از طریق ONVIF...")
            onvif_channels = try_onvif_discovery(self.ip, self.onvif_port, self.user, self.pwd)
            if onvif_channels:
                for ch in onvif_channels:
                    if self._is_cancelled:
                        break
                    self.channel_found_signal.emit(ch["channel"], ch["name"], ch["url"])
                    found_count += 1
                self.finished_signal.emit(found_count)
                return

        # ۲) روش جایگزین: تست الگوهای RTSP شناخته‌شده برای هر کانال
        if self.brand == "auto":
            brands_to_try = ["dahua_iap", "hikvision", "sunell", "generic"]
        else:
            brands_to_try = [self.brand]

        for ch in range(1, self.max_channels + 1):
            if self._is_cancelled:
                break

            self.progress_signal.emit(f"در حال بررسی کانال {ch} از {self.max_channels}...")
            found_for_channel = False

            for brand in brands_to_try:
                if self._is_cancelled or found_for_channel:
                    break
                for path in _format_templates(CHANNEL_TEMPLATES[brand], ch):
                    if self._is_cancelled:
                        break
                    if self._probe_path(path):
                        self.channel_found_signal.emit(ch, f"کانال {ch}", path)
                        found_count += 1
                        found_for_channel = True
                        break

        if self._is_cancelled:
            self.failed_signal.emit("جستجو لغو شد.")
        elif found_count == 0:
            self.failed_signal.emit(
                "هیچ کانالی یافت نشد. IP، پورت RTSP، نام کاربری/رمز عبور را بررسی کنید "
                "یا برند NVR را به‌صورت دستی انتخاب کنید."
            )

        self.finished_signal.emit(found_count)

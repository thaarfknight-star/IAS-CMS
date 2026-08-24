import concurrent.futures
import os

from PyQt6.QtCore import QThread, pyqtSignal
from rtsp_utils import build_rtsp_url, probe_stream

CHANNEL_TEMPLATES = {
    "dahua_iap": [
        "cam/realmonitor?channel={ch}&subtype=0",
        "cam/realmonitor?channel={ch}&subtype=1",
        "cam/realmonitor?channel={ch0}&subtype=0",
    ],
    "hikvision": [
        "Streaming/Channels/{ch}01",
        "Streaming/Channels/{ch}02",
        "Streaming/Channels/{ch}",
        "ch{ch}/main/av_stream",
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
        "h264Preview_{ch}_main",
        "video{ch}",
        "onvif{ch}",
        "profile{ch}",
        "ch{ch0}",
        "ch{ch}",
    ],
}

BRAND_LABELS = {
    "auto": "تشخیص خودکار (تمام برندها)",
    "dahua_iap": "Dahua / IAP",
    "hikvision": "Hikvision",
    "sunell": "Sunell",
    "generic": "XM / سایر / استاندارد",
}

ONVIF_TIMEOUT_SEC = 4
COMMON_ONVIF_PORTS = [80, 8000, 8080, 8899, 2020, 5000, 37777]
CHANNEL_PROBE_WORKERS = max(4, min(8, (os.cpu_count() or 4)))


def _format_templates(templates, ch):
    return [t.format(ch=ch, ch0=ch - 1, ch2=f"{ch:02d}") for t in templates]


def _discover_onvif_channels(ip, onvif_port, user, pwd):
    from onvif import ONVIFCamera
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
            req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
            uri = media.GetStreamUri(req).Uri
            name = getattr(profile, "Name", None) or f"کانال {idx}"
            channels.append({"channel": idx, "name": str(name), "url": uri})
        except Exception:
            continue
    return channels or None


def _try_onvif_single_port(ip, onvif_port, user, pwd, timeout):
    try:
        import onvif
    except ImportError:
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_discover_onvif_channels, ip, onvif_port, user, pwd)
        try:
            return future.result(timeout=timeout)
        except Exception:
            return None


def try_onvif_discovery(ip, onvif_port, user, pwd, timeout=ONVIF_TIMEOUT_SEC):
    ports_to_try = [onvif_port] if onvif_port else []
    ports_to_try += [p for p in COMMON_ONVIF_PORTS if str(p) != str(onvif_port)]

    for port in ports_to_try:
        res = _try_onvif_single_port(ip, port, user, pwd, timeout)
        if res:
            return res
    return None


class NVRScanThread(QThread):
    progress_signal = pyqtSignal(str)
    channel_found_signal = pyqtSignal(int, str, str)
    finished_signal = pyqtSignal(int)
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
        return build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, path)

    def _probe_path(self, path):
        return probe_stream(self._build_url(path))

    def _probe_channel(self, ch, brands_to_try):
        for brand in brands_to_try:
            if self._is_cancelled:
                return None
            for path in _format_templates(CHANNEL_TEMPLATES[brand], ch):
                if self._is_cancelled:
                    return None
                try:
                    if self._probe_path(path):
                        return (ch, f"کانال {ch}", path)
                except Exception:
                    continue
        return None

    def run(self):
        found_count = 0

        # ۱) تلاش سریع با ONVIF
        self.progress_signal.emit("در حال بررسی پروتکل ONVIF...")
        onvif_channels = try_onvif_discovery(self.ip, self.onvif_port, self.user, self.pwd)
        if onvif_channels and not self._is_cancelled:
            for ch in onvif_channels:
                self.channel_found_signal.emit(ch["channel"], ch["name"], ch["url"])
                found_count += 1
            self.finished_signal.emit(found_count)
            return

        if self._is_cancelled:
            self.failed_signal.emit("لغو شد.")
            self.finished_signal.emit(found_count)
            return

        # ۲) بررسی موازی الگوهای RTSP
        brands_to_try = ["dahua_iap", "hikvision", "sunell", "generic"] if self.brand == "auto" else [self.brand]
        self.progress_signal.emit(f"در حال بررسی موازی {self.max_channels} کانال...")
        channels = list(range(1, self.max_channels + 1))

        with concurrent.futures.ThreadPoolExecutor(max_workers=CHANNEL_PROBE_WORKERS) as executor:
            future_to_ch = {
                executor.submit(self._probe_channel, ch, brands_to_try): ch for ch in channels
            }
            results = {}
            for future in concurrent.futures.as_completed(future_to_ch):
                ch = future_to_ch[future]
                if self._is_cancelled:
                    continue
                try:
                    res = future.result()
                    if res:
                        results[ch] = res
                except Exception:
                    pass

            for ch in sorted(results):
                found_ch, name, path = results[ch]
                self.channel_found_signal.emit(found_ch, name, path)
                found_count += 1

        if self._is_cancelled:
            self.failed_signal.emit("لغو شد.")
        elif found_count == 0:
            self.failed_signal.emit("هیچ کانالی پیدا نشد. یوزر/پسوورد، IP یا برند را دستی تعیین کنید.")

        self.finished_signal.emit(found_count)

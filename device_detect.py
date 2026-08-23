"""تشخیص خودکار نوع دستگاه (دوربین تکی یا NVR چندکاناله) بعد از اسکن شبکه.

قبلاً بعد از اسکن شبکه و دابل‌کلیک روی یک IP، از کاربر با یک پیام سوال
می‌شد که «این دستگاه دوربین تکی است یا NVR؟» و بر اساس پاسخ، یکی از دو
دیالوگ (AddCameraDialog / AddNVRDialog) به‌صورت دستی باز می‌شد. این ماژول
آن پرسش را حذف می‌کند: با اتصال آزمایشی به دستگاه، خودش تشخیص می‌دهد.

منطق تشخیص:
  1) ONVIF: اگر دستگاه از ONVIF پشتیبانی کند، تعداد «پروفایل‌های رسانه»
     دقیقاً برابر تعداد کانال‌های واقعی دستگاه است — یک دوربین تکی همیشه
     ۱ پروفایل دارد، NVR بیش از ۱ پروفایل (یکی به‌ازای هر کانال متصل).
  2) اگر ONVIF در دسترس نبود: با الگوهای شناخته‌شده‌ی برندها، ابتدا کانال ۱
     تست می‌شود. اگر کانال ۱ باز شد، کانال ۲ (با همان الگوی برند) هم تست
     می‌شود؛ وجود کانال ۲ یعنی دستگاه چندکاناله (NVR) است، نبودش یعنی
     دوربین تکی است.
"""

from PyQt6.QtCore import QThread, pyqtSignal

from rtsp_utils import build_rtsp_url, probe_stream
from nvr_scanner import CHANNEL_TEMPLATES, _format_templates, try_onvif_discovery


class DeviceDetectThread(QThread):
    """نتیجه از طریق detected_signal اعلام می‌شود:
      detected_signal.emit("camera", {"path": str, "full_url": str|None})
      detected_signal.emit("nvr", {"brand": str, "onvif_port": str|None})
    اگر هیچ‌کدام تشخیص داده نشد، failed_signal.emit(msg) صدا زده می‌شود
    (کاربر می‌تواند دستی افزودن را با اطلاعات کامل‌تر انجام دهد).
    """

    progress_signal = pyqtSignal(str)
    detected_signal = pyqtSignal(str, dict)
    failed_signal = pyqtSignal(str)

    def __init__(self, ip, open_ports=None, rtsp_port="554", user="admin", pwd="", parent=None):
        super().__init__(parent)
        self.ip = ip
        self.rtsp_port = rtsp_port
        self.user = user
        self.pwd = pwd
        # پورت‌های واقعاً باز که اسکن شبکه قبلاً پیدا کرده (554/80/8000/...)؛
        # برای اولویت‌بندی پورت‌های ONVIF کاندید استفاده می‌شود تا حدس‌زدن
        # کورکورانه کمتر و دقیق‌تر باشد.
        self.open_ports = open_ports or []
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _onvif_candidate_ports(self):
        # پورت‌های باز شناخته‌شده اولویت دارند (80/8000/8899 رایج‌اند)، سپس
        # چند پورت متداول دیگر که ممکن است در اسکن سریع پورت گزارش نشده باشند.
        candidates = [p for p in self.open_ports if p != self.rtsp_port]
        for p in (80, 8000, 8080, 2020):
            if p not in candidates:
                candidates.append(p)
        return candidates

    def _probe_channel(self, brand, ch):
        for path in _format_templates(CHANNEL_TEMPLATES[brand], ch):
            if self._is_cancelled:
                return None
            url = build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, path)
            if probe_stream(url):
                return path
        return None

    def run(self):
        # ۱) تلاش با ONVIF: تعداد پروفایل‌ها مستقیماً نوع دستگاه را مشخص می‌کند.
        self.progress_signal.emit("در حال تشخیص نوع دستگاه (ONVIF)...")
        for onvif_port in self._onvif_candidate_ports():
            if self._is_cancelled:
                return
            channels = try_onvif_discovery(self.ip, onvif_port, self.user, self.pwd)
            if channels:
                if len(channels) > 1:
                    self.detected_signal.emit(
                        "nvr", {"brand": "auto", "onvif_port": str(onvif_port)}
                    )
                else:
                    self.detected_signal.emit(
                        "camera", {"path": "", "full_url": channels[0]["url"]}
                    )
                return

        if self._is_cancelled:
            return

        # ۲) بدون ONVIF: کانال ۱ را با الگوهای هر برند تست می‌کن؛ به محض یافتن
        # الگوی برند درست، کانال ۲ همان برند را هم تست کن تا مشخص شود تک‌کاناله
        # است یا چندکاناله.
        self.progress_signal.emit("در حال بررسی کانال‌ها...")
        for brand in ("dahua_iap", "hikvision", "sunell", "generic"):
            if self._is_cancelled:
                return
            path_ch1 = self._probe_channel(brand, 1)
            if not path_ch1:
                continue

            self.progress_signal.emit("دستگاه یافت شد، در حال بررسی تعداد کانال...")
            path_ch2 = self._probe_channel(brand, 2)
            if path_ch2:
                self.detected_signal.emit("nvr", {"brand": brand, "onvif_port": ""})
            else:
                self.detected_signal.emit("camera", {"path": path_ch1, "full_url": None})
            return

        self.failed_signal.emit(
            "تشخیص خودکار نوع دستگاه ممکن نشد. لطفاً نام کاربری/رمز عبور را بررسی "
            "کنید یا دستگاه را به‌صورت دستی (دوربین تکی یا NVR) اضافه کنید."
        )

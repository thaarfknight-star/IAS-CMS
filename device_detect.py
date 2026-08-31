"""تشخیص خودکار نوع دستگاه (دوربین تکی یا NVR چندکاناله) بعد از اسکن شبکه.

قبلاً بعد از اسکن شبکه و دابل‌کلیک روی یک IP، از کاربر با یک پیام سوال
می‌شد که «این دستگاه دوربین تکی است یا NVR؟» و بر اساس پاسخ، یکی از دو
دیالوگ (AddCameraDialog / AddNVRDialog) به‌صورت دستی باز می‌شد. این ماژول
آن پرسش را حذف می‌کند: با اتصال آزمایشی به دستگاه، خودش تشخیص می‌دهد.

منطق تشخیص (بازنویسی‌شده - هماهنگ با معماری جدید nvr_scanner.py):
  1) API وب سازنده: اگر دستگاه API پیکربندی Hikvision/Dahua را پشتیبانی
     کند، تعداد کانال واقعی مستقیماً از پیکربندی خودش خوانده می‌شود - نه
     حدس. سریع‌ترین و دقیق‌ترین روش.
  2) ONVIF: اگر دستگاه از ONVIF پشتیبانی کند، تعداد «پروفایل‌های رسانه»
     دقیقاً برابر تعداد کانال‌های واقعی دستگاه است — یک دوربین تکی همیشه
     ۱ پروفایل دارد، NVR بیش از ۱ پروفایل (یکی به‌ازای هر کانال متصل).
  3) اگر هیچ‌کدام در دسترس نبود: با الگوهای شناخته‌شده‌ی برندها، ابتدا
     کانال ۱ با یک DESCRIBE سریع (rtsp_probe) تست می‌شود. اگر کانال ۱ باز
     شد، کانال ۲ به بعد (با همان الگوی برند) هم تست می‌شود؛ وجود کانال
     دیگر یعنی دستگاه چندکاناله (NVR) است، نبودش یعنی دوربین تکی است.
"""

from PyQt6.QtCore import QThread, pyqtSignal

from rtsp_utils import build_rtsp_url, frames_look_identical, probe_stream
from rtsp_probe import describe_probe
from nvr_http_api import discover_channels_http, COMMON_HTTP_PORTS
from nvr_scanner import CHANNEL_TEMPLATES, _format_templates, try_onvif_discovery
from nvr_ws_protocol import NVRWebSocketSession, NVRWebSocketError

# پورت‌های رایج برای پروتکل وب اختصاصی برخی NVRها (رجوع کنید به
# nvr_ws_protocol.py) -- معمولاً همان پورت رابط تحت‌وب دستگاه است.
_WS_CANDIDATE_PORTS = (80, 8080, 443, 7681)

# بعد از کانال ۱، این کانال‌ها هم برای تایید چندکاناله بودن دستگاه امتحان
# می‌شوند (نه فقط کانال ۲): برخی NVRها کانال ۲ خالی/غیرفعال دارند و فقط
# کانال‌های بعدی متصل‌اند؛ اگر فقط کانال ۲ چک شود، چنین NVRای به‌اشتباه
# «دوربین تکی» تشخیص داده می‌شود.
_EXTRA_CHANNELS_TO_CHECK = (2, 3, 4)


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
        # هر مسیر ابتدا با یک DESCRIBE سریع بررسی می‌شود (بدون نیاز به دیکود
        # کامل جریان)؛ فقط اگر نتیجه نامشخص بود (نه رد قطعی 401/404)، یک بار
        # هم با OpenCV/FFmpeg تایید می‌شود - رجوع کنید به rtsp_probe.py.
        for path in _format_templates(CHANNEL_TEMPLATES[brand], ch):
            if self._is_cancelled:
                return None
            result = describe_probe(self.ip, self.rtsp_port, path, self.user, self.pwd)
            if result:
                return path
            if result.status in ("timeout", "error", "refused"):
                url = build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, path)
                if probe_stream(url):
                    return path
        return None

    def _http_ports(self):
        ports = list(COMMON_HTTP_PORTS)
        for p in self.open_ports:
            if p not in ports:
                ports.append(p)
        return ports

    def _try_http_api(self):
        """رجوع کنید به nvr_http_api.py. در صورت موفقیت، سریع‌ترین و
        دقیق‌ترین راه برای فهمیدن دوربین‌تکی/NVR بودن دستگاه است: تعداد
        کانال واقعی مستقیماً از پیکربندی دستگاه خوانده می‌شود."""
        try:
            channels = discover_channels_http(self.ip, self.user, self.pwd, ports=self._http_ports())
        except Exception:
            channels = None
        if not channels:
            return None

        if len(channels) > 1:
            self.detected_signal.emit("nvr", {"brand": "auto", "onvif_port": ""})
        else:
            only = channels[0]
            full_url = build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, only["path"])
            self.detected_signal.emit("camera", {"path": only["path"], "full_url": full_url})
        return True

    def _try_ws_protocol(self):
        """رفع درخواست: لایه‌ی چهارم -- برخی NVRها (مثل دستگاهی که این پروتکل
        از رویش reverse-engineer شد) اصلاً RTSP/ONVIF/API وب استاندارد ندارند
        و فقط از طریق پروتکل وب اختصاصی WebSocket پخش زنده می‌دهند. اگر سه
        روش قبلی هیچ‌کدام جواب ندادند، هندشیک این پروتکل روی چند پورت رایج
        امتحان می‌شود؛ موفقیت در همان هندشیک (بدون نیاز به شمارش کامل کانال‌ها
        در این مرحله) کافی است تا دستگاه را به‌عنوان NVR با این پروتکل معرفی
        کند و کاربر را به دیالوگ افزودن NVR (با گزینه‌ی WS از پیش تیک‌خورده)
        بفرستد؛ شمارش دقیق کانال‌ها همان‌جا با AddNVRDialog انجام می‌شود.
        """
        candidate_ports = [p for p in self.open_ports if p]
        for p in _WS_CANDIDATE_PORTS:
            if p not in candidate_ports:
                candidate_ports.append(p)

        for port in candidate_ports:
            if self._is_cancelled:
                return None
            session = NVRWebSocketSession(self.ip, port, self.user, self.pwd, timeout=3.0)
            try:
                session.connect_and_auth()
            except NVRWebSocketError:
                continue
            finally:
                session.close()
            return port
        return None

    def run(self):
        # ۱) سریع‌ترین و دقیق‌ترین روش: API وب سازنده.
        self.progress_signal.emit("در حال بررسی API وب دستگاه...")
        if not self._is_cancelled and self._try_http_api():
            return

        if self._is_cancelled:
            return

        # ۲) تلاش با ONVIF: تعداد پروفایل‌ها مستقیماً نوع دستگاه را مشخص می‌کند.
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

        # ۳) بدون ONVIF: کانال ۱ را با الگوهای هر برند تست می‌کن؛ به محض یافتن
        # الگوی برند درست، چند کانال بعدی همان برند را هم تست کن تا مشخص شود
        # تک‌کاناله است یا چندکاناله.
        self.progress_signal.emit("در حال بررسی کانال‌ها...")
        for brand in ("dahua_iap", "hikvision", "sunell", "generic"):
            if self._is_cancelled:
                return
            path_ch1 = self._probe_channel(brand, 1)
            if not path_ch1:
                continue

            self.progress_signal.emit("دستگاه یافت شد، در حال بررسی تعداد کانال...")
            url_ch1 = build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, path_ch1)

            # رفع باگ «دوربین تکی به‌اشتباه NVR تشخیص داده می‌شود»: صرف باز
            # شدن URL کانال ۲ (یا بعدی) کافی نیست - خیلی از دوربین‌های تکی
            # پارامتر channel را نادیده می‌گیرند و همیشه همان یک فید را
            # برمی‌گردانند. اینجا محتوای واقعی تصویر کانال ۱ و کانال کاندید
            # مقایسه می‌شود (rtsp_utils.frames_look_identical)؛ فقط وقتی
            # تصویر واقعاً متفاوت باشد، کانال جداگانه (و در نتیجه NVR) تایید
            # می‌شود.
            is_multi_channel = False
            for ch in _EXTRA_CHANNELS_TO_CHECK:
                if self._is_cancelled:
                    return
                path_ch_n = self._probe_channel(brand, ch)
                if not path_ch_n:
                    continue
                url_ch_n = build_rtsp_url(self.ip, self.rtsp_port, self.user, self.pwd, path_ch_n)
                if not frames_look_identical(url_ch1, url_ch_n):
                    is_multi_channel = True
                    break

            if is_multi_channel:
                self.detected_signal.emit("nvr", {"brand": brand, "onvif_port": ""})
            else:
                self.detected_signal.emit("camera", {"path": path_ch1, "full_url": None})
            return

        # ۴) آخرین راه‌حل: پروتکل وب اختصاصی WebSocket (رجوع کنید به
        # nvr_ws_protocol.py) -- برای NVRهایی که هیچ‌کدام از سه روش استاندارد
        # بالا رویشان جواب نمی‌دهد.
        if self._is_cancelled:
            return
        self.progress_signal.emit("در حال بررسی پروتکل وب اختصاصی...")
        ws_port = self._try_ws_protocol()
        if ws_port:
            self.detected_signal.emit("nvr_ws", {"ws_port": str(ws_port)})
            return

        self.failed_signal.emit(
            "تشخیص خودکار نوع دستگاه ممکن نشد. لطفاً نام کاربری/رمز عبور را بررسی "
            "کنید یا دستگاه را به‌صورت دستی (دوربین تکی یا NVR) اضافه کنید."
        )

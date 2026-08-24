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

from rtsp_utils import build_rtsp_url, frames_look_identical, probe_stream
from nvr_scanner import CHANNEL_TEMPLATES, _format_templates, try_onvif_discovery

# بعد از کانال ۱، این کانال‌ها هم برای تایید چندکاناله بودن دستگاه امتحان
# می‌شوند (نه فقط کانال ۲): برخی NVRها کانال ۲ خالی/غیرفعال دارند و فقط
# کانال‌های بعدی متصل‌اند؛ اگر فقط کانال ۲ چک شود، چنین NVRای به‌اشتباه
# «دوربین تکی» تشخیص داده می‌شود.
_EXTRA_CHANNELS_TO_CHECK = (2, 3, 4)

# رفع باگ اصلیِ «NVR درست شناسایی/اسکن نمی‌شود» (پیام «هیچ کانالی یافت نشد» /
# «تشخیص ناموفق» با اینکه یوزرنیم/پسورد درست است): قبلاً این ماژول همیشه و
# فقط با پورت ثابت 554 برای RTSP تست می‌کرد (main.py._start_device_detect
# مقدار rtsp_port="554" را به‌صورت ثابت پاس می‌داد و هیچ راهی برای امتحان
# پورت دیگر وجود نداشت). خیلی از NVR/DVRهای ارزان‌قیمت و ژنریک (خصوصاً
# برندهای کمتر شناخته‌شده) پیش‌فرض RTSP را روی پورتی غیر از 554 (مثلاً 555،
# 8554، 1554) اجرا می‌کنند؛ روی چنین دستگاه‌هایی، تک‌تک probeهای کانال ۱ تا
# پایان لیست برندها با پورت اشتباه انجام می‌شد و همیشه شکست می‌خورد - دقیقاً
# رفتار «هیچ کانالی یافت نشد». حالا علاوه بر پورتی که کاربر/اسکن مشخص کرده،
# چند پورت رایج جایگزین RTSP هم امتحان می‌شوند.
COMMON_RTSP_PORT_FALLBACKS = ["554", "555", "8554", "1554"]


class DeviceDetectThread(QThread):
    """نتیجه از طریق detected_signal اعلام می‌شود:
      detected_signal.emit("camera", {"path": str, "full_url": str|None, "rtsp_port": str})
      detected_signal.emit("nvr", {"brand": str, "onvif_port": str|None, "rtsp_port": str})
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
        candidates = [p for p in self.open_ports if str(p) != str(self.rtsp_port)]
        for p in (80, 8000, 8080, 2020):
            if p not in candidates:
                candidates.append(p)
        return candidates

    def _rtsp_port_candidates(self):
        """لیست پورت‌های RTSP کاندید برای تست کانال‌ها: پورت اصلی (ورودی/اسکن)
        همیشه اول است؛ سپس هر پورت باز دیگری که در اسکن شبکه پیدا شده و از
        نظر عددی شبیه پورت‌های رایج RTSP است؛ در آخر چند پورت رایج جایگزین
        (555/8554/1554) که در اسکن سریع پورت ممکن است اصلاً چک نشده باشند."""
        candidates = [str(self.rtsp_port)] if self.rtsp_port else []
        for p in self.open_ports:
            p = str(p)
            if p in COMMON_RTSP_PORT_FALLBACKS and p not in candidates:
                candidates.append(p)
        for p in COMMON_RTSP_PORT_FALLBACKS:
            if p not in candidates:
                candidates.append(p)
        return candidates

    def _probe_channel(self, brand, ch, rtsp_port):
        for path in _format_templates(CHANNEL_TEMPLATES[brand], ch):
            if self._is_cancelled:
                return None
            url = build_rtsp_url(self.ip, rtsp_port, self.user, self.pwd, path)
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
                        "nvr", {"brand": "auto", "onvif_port": str(onvif_port), "rtsp_port": str(self.rtsp_port)}
                    )
                else:
                    self.detected_signal.emit(
                        "camera", {"path": "", "full_url": channels[0]["url"], "rtsp_port": str(self.rtsp_port)}
                    )
                return

        if self._is_cancelled:
            return

        # ۲) بدون ONVIF: کانال ۱ را با الگوهای هر برند تست می‌کن؛ به محض یافتن
        # الگوی برند درست، چند کانال بعدی همان برند را هم تست کن تا مشخص شود
        # تک‌کاناله است یا چندکاناله.
        #
        # رفع باگ «هیچ کانالی یافت نشد»: قبلاً اینجا فقط self.rtsp_port (که
        # همیشه "554" بود) امتحان می‌شد. حالا حلقه‌ی بیرونی روی چند پورت RTSP
        # کاندید (_rtsp_port_candidates) است تا NVRهایی که RTSP را روی پورتی
        # غیر از 554 اجرا می‌کنند هم پیدا شوند. برای جلوگیری از کند شدن بیش
        # از حد، پورت بعدی فقط وقتی امتحان می‌شود که پورت فعلی برای *همه‌ی*
        # برندها کامل شکست بخورد.
        self.progress_signal.emit("در حال بررسی کانال‌ها...")
        for rtsp_port in self._rtsp_port_candidates():
            if self._is_cancelled:
                return
            for brand in ("dahua_iap", "hikvision", "sunell", "generic"):
                if self._is_cancelled:
                    return
                path_ch1 = self._probe_channel(brand, 1, rtsp_port)
                if not path_ch1:
                    continue

                self.progress_signal.emit("دستگاه یافت شد، در حال بررسی تعداد کانال...")
                url_ch1 = build_rtsp_url(self.ip, rtsp_port, self.user, self.pwd, path_ch1)

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
                    path_ch_n = self._probe_channel(brand, ch, rtsp_port)
                    if not path_ch_n:
                        continue
                    url_ch_n = build_rtsp_url(self.ip, rtsp_port, self.user, self.pwd, path_ch_n)
                    if not frames_look_identical(url_ch1, url_ch_n):
                        is_multi_channel = True
                        break

                if is_multi_channel:
                    self.detected_signal.emit("nvr", {"brand": brand, "onvif_port": "", "rtsp_port": str(rtsp_port)})
                else:
                    self.detected_signal.emit(
                        "camera", {"path": path_ch1, "full_url": None, "rtsp_port": str(rtsp_port)}
                    )
                return

        self.failed_signal.emit(
            "تشخیص خودکار نوع دستگاه ممکن نشد. لطفاً نام کاربری/رمز عبور را بررسی "
            "کنید یا دستگاه را به‌صورت دستی (دوربین تکی یا NVR) اضافه کنید."
        )

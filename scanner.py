import os
import socket
import concurrent.futures

from PyQt6.QtCore import QThread, pyqtSignal

COMMON_CCTV_PORTS = [554, 555, 8554, 80, 8000, 37777, 8899]
# رفع درخواست «NVRها درست شناسایی و اسکن نمی‌شن»: پورت‌های 555 و 8554 اضافه
# شدند چون خیلی از NVR/DVRهای ارزان‌قیمت/ژنریک RTSP را روی این پورت‌ها به‌جای
# 554 پیش‌فرض اجرا می‌کنند - رجوع کنید به device_detect.COMMON_RTSP_PORT_FALLBACKS
# برای توضیح کامل و رفع مشابه در مرحله‌ی تشخیص نوع دستگاه.

# روی سیستم‌های ضعیف (کم‌رم/بدون کارت‌گرافیک) تعداد هسته‌ی CPU معمولاً کم است؛
# 50 ترد هم‌زمان روی چنین سیستمی باعث کندی شدید کل برنامه (از جمله پخش زنده‌ی
# دوربین‌های باز) در طول اسکن می‌شود. تعداد ترد پیش‌فرض به نسبت هسته‌های واقعی
# سیستم تنظیم می‌شود.
DEFAULT_SCAN_THREADS = max(16, min(50, (os.cpu_count() or 4) * 8))

def check_ip_port(ip: str, port: int, timeout=0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        return result == 0

def scan_single_host(ip: str):
    open_ports = []
    for port in COMMON_CCTV_PORTS:
        if check_ip_port(ip, port):
            open_ports.append(port)
    if open_ports:
        return {"ip": ip, "ports": open_ports}
    return None

def scan_subnet(base_subnet: str = "192.168.1", max_threads: int = DEFAULT_SCAN_THREADS):
    """اسکن شبکه: تمام IPهای یک ساب‌نت را برای پورت‌های رایج دوربین/NVR بررسی
    می‌کند (554=RTSP, 80=HTTP/ONVIF, 8000/37777/8899=مدیریت NVRهای رایج).
    این اسکن فقط پورت‌های باز را گزارش می‌دهد و به‌تنهایی نمی‌تواند تشخیص دهد
    دستگاه یک دوربین تکی است یا یک NVR چندکاناله؛ این تصمیم در UI از کاربر
    پرسیده می‌شود (رجوع کنید به MainWindow.on_scan_result_selected)."""
    active_devices = []
    ip_list = [f"{base_subnet}.{i}" for i in range(1, 255)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        results = executor.map(scan_single_host, ip_list)
        for res in results:
            if res:
                active_devices.append(res)

    return active_devices


class NetworkScanThread(QThread):
    """اجرای scan_subnet در یک ترد جدا.

    رفع باگ: قبلاً MainWindow.run_network_scan مستقیماً و به‌صورت همزمان
    (blocking) روی ترد UI اجرا می‌شد؛ در نتیجه در طول کل اسکن ساب‌نت (که
    می‌تواند چند ثانیه تا چند ده ثانیه طول بکشد)، کل رابط کاربری (از جمله
    پخش زنده‌ی دوربین‌های در حال حاضر باز) کاملاً قفل/فریز می‌شد و کاربر گمان
    می‌کرد برنامه هنگ کرده یا اسکن کار نمی‌کند. اجرای آن در QThread این مشکل
    را برطرف می‌کند.
    """

    finished_signal = pyqtSignal(list)

    def __init__(self, subnet, parent=None):
        super().__init__(parent)
        self.subnet = subnet

    def run(self):
        devices = scan_subnet(self.subnet)
        self.finished_signal.emit(devices)

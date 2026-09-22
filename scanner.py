import os
import socket
import concurrent.futures
import ipaddress

from PyQt6.QtCore import QThread, pyqtSignal

COMMON_CCTV_PORTS = [554, 80, 8000, 37777, 8899]

# سقف تعداد آدرس در یک اسکن؛ محافظت در برابر رنج‌های غول‌پیکر مثل ‎/8‎ که
# میلیون‌ها آدرس دارند و اسکن آن‌ها عملاً هنگ می‌کند.
MAX_SCAN_HOSTS = 65534


def _parse_single_range(part):
    """یک تکه رنج (بدون ویرگول) را به لیست IP تبدیل می‌کند."""
    part = part.strip()
    if "/" in part:
        # CIDR مثل 192.168.2.0/24
        try:
            net = ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise ValueError(f"رنج نامعتبر است: «{part}»")
        if net.version != 4:
            raise ValueError(f"فقط IPv4 پشتیبانی می‌شود: «{part}»")
        hosts = [str(h) for h in net.hosts()]
        if len(hosts) > MAX_SCAN_HOSTS:
            raise ValueError(
                f"رنج «{part}» خیلی بزرگ است (حداکثر {MAX_SCAN_HOSTS} آدرس).")
        if not hosts:
            raise ValueError(f"رنج «{part}» هیچ آدرسی ندارد.")
        return hosts
    if "-" in part:
        # بازه مثل 192.168.1.20-192.168.1.80 یا 192.168.1.20-80
        left, _, right = part.partition("-")
        left, right = left.strip(), right.strip()
        try:
            start_ip = ipaddress.ip_address(left)
        except ValueError:
            raise ValueError(f"ابتدای بازه نامعتبر است: «{left}»")
        if start_ip.version != 4:
            raise ValueError(f"فقط IPv4 پشتیبانی می‌شود: «{left}»")
        if "." in right:
            try:
                end_ip = ipaddress.ip_address(right)
            except ValueError:
                raise ValueError(f"انتهای بازه نامعتبر است: «{right}»")
        elif right.isdigit():
            base = ".".join(left.split(".")[:3])
            try:
                end_ip = ipaddress.ip_address(f"{base}.{right}")
            except ValueError:
                raise ValueError(f"انتهای بازه نامعتبر است: «{right}»")
        else:
            raise ValueError(f"انتهای بازه نامعتبر است: «{right}»")
        if end_ip.version != 4:
            raise ValueError(f"فقط IPv4 پشتیبانی می‌شود: «{right}»")
        lo, hi = int(start_ip), int(end_ip)
        if hi < lo:
            raise ValueError(
                f"انتهای بازه از ابتدایش کوچک‌تر است: «{part}»")
        if hi - lo + 1 > MAX_SCAN_HOSTS:
            raise ValueError(
                f"بازه «{part}» خیلی بزرگ است (حداکثر {MAX_SCAN_HOSTS} آدرس).")
        return [str(ipaddress.ip_address(i)) for i in range(lo, hi + 1)]
    # تک‌آدرس یا پیشوند سه‌اکتتی قدیمی (مثل 192.168.1)
    try:
        single = ipaddress.ip_address(part)
        if single.version == 4:
            return [part]
    except ValueError:
        pass
    octets = part.split(".")
    if len(octets) == 3:
        try:
            base = ipaddress.ip_address(".".join(octets) + ".0")
        except ValueError:
            raise ValueError(f"رنج نامعتبر است: «{part}»")
        if base.version == 4:
            # سازگار با رفتار قبلی: کل ساب‌نت ‎/24‎
            return [f"{part}.{i}" for i in range(1, 255)]
    raise ValueError(f"رنج نامعتبر است: «{part}»")


def parse_ip_range(text):
    """متن رنج IP را به لیست یکتای آدرس‌ها تبدیل می‌کند.

    قالب‌های پشتیبانی‌شده (چند رنج را می‌توان با «,» ترکیب کرد):
      - «192.168.1»                  → کل ساب‌نت 192.168.1.1 تا 192.168.1.254
      - «192.168.1.20»               → فقط همان یک آدرس
      - «192.168.1.20-192.168.1.80»  → بازه‌ی کامل
      - «192.168.1.20-80»            → بازه در همان ساب‌نت
      - «192.168.2.0/24»             → نماد CIDR
    در صورت نامعتبر بودن، ValueError با پیام فارسی پرتاب می‌شود.
    """
    if not text or not text.strip():
        raise ValueError("رنج IP خالی است.")
    ips = []
    seen = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        for ip in _parse_single_range(chunk):
            if ip not in seen:
                seen.add(ip)
                ips.append(ip)
    if not ips:
        raise ValueError("رنج IP معتبری وارد نشده است.")
    if len(ips) > MAX_SCAN_HOSTS:
        raise ValueError(
            f"رنج خیلی بزرگ است (حداکثر {MAX_SCAN_HOSTS} آدرس).")
    return ips

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

def scan_ip_list(ip_list, max_threads: int = DEFAULT_SCAN_THREADS,
               cancel_check=None):
    """اسکن لیست دلخواهی از IPها برای پورت‌های رایج دوربین/NVR.

    cancel_check: تابع اختیاری بدون آرگومان که اگر True برگرداند، اسکن
    بی‌درنگ لغو می‌شود (برای بستن امن دیالوگ حین اسکن)."""
    active_devices = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        future_to_ip = {executor.submit(scan_single_host, ip): ip for ip in ip_list}
        for future in concurrent.futures.as_completed(future_to_ip):
            if cancel_check is not None and cancel_check():
                # لغو: فیوچرهای باقی‌مانده دیگر خوانده نمی‌شوند؛ تردهای
                # کارگر فعلی تمام می‌شوند ولی نتیجه‌شان نادیده گرفته می‌شود.
                executor.shutdown(wait=False, cancel_futures=True)
                break
            try:
                res = future.result()
            except Exception:
                res = None
            if res:
                active_devices.append(res)

    return active_devices


def scan_subnet(base_subnet: str = "192.168.1", max_threads: int = DEFAULT_SCAN_THREADS,
               cancel_check=None):
    """اسکن شبکه: رنج IP داده‌شده را برای پورت‌های رایج دوربین/NVR بررسی
    می‌کند (554=RTSP, 80=HTTP/ONVIF, 8000/37777/8899=مدیریت NVRهای رایج).

    base_subnet می‌تواند هر قالب parse_ip_range باشد: پیشوند قدیمی سه‌اکتتی
    («192.168.1»)، بازه («192.168.1.20-192.168.1.80»)، CIDR («192.168.2.0/24»)
    یا ترکیب چند رنج با ویرگول.

    این اسکن فقط پورت‌های باز را گزارش می‌دهد و به‌تنهایی نمی‌تواند تشخیص دهد
    دستگاه یک دوربین تکی است یا یک NVR چندکاناله؛ این تصمیم در UI از کاربر
    پرسیده می‌شود (رجوع کنید به MainWindow.on_scan_result_selected).
    cancel_check: تابع اختیاری بدون آرگومان که اگر True برگرداند، اسکن
    بی‌درنگ لغو می‌شود (برای بستن امن دیالوگ حین اسکن)."""
    ip_list = parse_ip_range(base_subnet)
    return scan_ip_list(ip_list, max_threads, cancel_check)


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
        self._is_cancelled = False

    def cancel(self):
        """لغو اسکن (برای بستن امن دیالوگ حین اسکن، مثل دیالوگ افزودن NVR)."""
        self._is_cancelled = True

    def run(self):
        devices = scan_subnet(self.subnet,
                              cancel_check=lambda: self._is_cancelled)
        if not self._is_cancelled:
            self.finished_signal.emit(devices)

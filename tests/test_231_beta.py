# -*- coding: utf-8 -*-
"""تست‌های رگرسیون 2.0.31-beta — اسکن «رنج‌های IP مختلف» (2026-09-22):

درخواست طه: اسکن شبکه فقط به یک ساب‌نت ثابت محدود نباشد؛ کاربر بتواند
رنج‌های دلخواه (بازه، CIDR، تک‌آدرس، ترکیب با ویرگول) وارد کند.

اجرا:
  QT_QPA_PLATFORM=offscreen python3 tests/test_231_beta.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_passed = []
_failed = []


def check(name, cond, extra=""):
    (_passed if cond else _failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" | {extra}" if extra and not cond else ""))


for _mod in ("face_recognition", "cv2"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from scanner import parse_ip_range, scan_subnet, scan_ip_list, NetworkScanThread

# ---------- قالب قدیمی سه‌اکتتی (سازگاری با قبل) ----------
ips = parse_ip_range("192.168.1")
check("legacy prefix: 254 hosts", len(ips) == 254, f"got {len(ips)}")
check("legacy prefix: first/last",
      ips[0] == "192.168.1.1" and ips[-1] == "192.168.1.254",
      f"got {ips[0]}, {ips[-1]}")

# ---------- بازه‌ی کامل ----------
ips = parse_ip_range("192.168.1.20-192.168.1.25")
check("full range", ips == [f"192.168.1.{i}" for i in range(20, 26)],
      f"got {ips}")

# ---------- بازه با اکتت آخر ----------
ips = parse_ip_range("192.168.1.20-25")
check("short range", ips == [f"192.168.1.{i}" for i in range(20, 26)],
      f"got {ips}")

# ---------- CIDR ----------
ips = parse_ip_range("192.168.2.0/24")
check("cidr /24: 254 hosts", len(ips) == 254 and
      ips[0] == "192.168.2.1" and ips[-1] == "192.168.2.254",
      f"got {len(ips)}")
ips = parse_ip_range("10.0.0.5/30")
check("cidr /30", ips == ["10.0.0.5", "10.0.0.6"], f"got {ips}")

# ---------- تک‌آدرس ----------
ips = parse_ip_range("192.168.1.5")
check("single ip", ips == ["192.168.1.5"], f"got {ips}")

# ---------- ترکیب چند رنج ----------
ips = parse_ip_range("192.168.1.20-22, 10.0.0.1-10.0.0.2, 192.168.1.22")
check("multi range dedup",
      ips == ["192.168.1.20", "192.168.1.21", "192.168.1.22",
              "10.0.0.1", "10.0.0.2"], f"got {ips}")

# ---------- ورودی‌های نامعتبر ----------
for bad in ["", "   ", "abc", "192.168.1.300", "192.168.1.80-192.168.1.20",
            "192.168.1.20-abc", "10.0.0.0/8", "2001:db8::/32"]:
    try:
        parse_ip_range(bad)
        check(f"invalid rejected: {bad!r}", False, "no error raised")
    except ValueError:
        check(f"invalid rejected: {bad!r}", True)

# ---------- scan_subnet با رنج جدید، بدون شبکه‌ی واقعی ----------
import scanner as _sc
_calls = []
_orig = _sc.scan_single_host
_sc.scan_single_host = lambda ip: _calls.append(ip) or None
try:
    res = scan_subnet("192.168.9.10-192.168.9.12", max_threads=4)
    check("scan_subnet: custom range scanned",
          res == [] and _calls == ["192.168.9.10", "192.168.9.11",
                                   "192.168.9.12"], f"got {_calls}")
    res = scan_ip_list(["10.9.9.1", "10.9.9.2"], max_threads=2)
    check("scan_ip_list: custom list scanned",
          res == [] and _calls[-2:] == ["10.9.9.1", "10.9.9.2"],
          f"got {_calls[-2:]}")
finally:
    _sc.scan_single_host = _orig

# ---------- NetworkScanThread با رنج جدید ----------
thr = NetworkScanThread("192.168.7.0/30")
check("thread: keeps range text", thr.subnet == "192.168.7.0/30")

# ---------- main.py: ایمپورت و placeholder ----------
repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(repo, "main.py"), encoding="utf-8") as f:
    src = f.read()
check("main: parse_ip_range imported",
      "from scanner import NetworkScanThread, parse_ip_range" in src)
check("main: range validated before scan",
      'QMessageBox.warning(self, "رنج IP نامعتبر"' in src)

print(f"\n{len(_passed)} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)

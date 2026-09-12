# -*- coding: utf-8 -*-
"""شبیه‌سازی رویدادهای حریق/دود بدون نیاز به دوربین یا پنل فیزیکی واقعی.

این اسکریپت مستقیماً report_store را صدا می‌زند (نه سیگنال‌های Qt)، پس
حتی وقتی main.py باز نیست هم می‌توان با آن مسیر ثبت/ذخیره‌ی گزارش‌ها را
تایید کرد - بعد دیالوگ «گزارش‌ها» (Reports) در برنامه را باز کنید تا
ردیف‌های تازه‌ثبت‌شده را ببینید (رجوع کنید به چک‌لیست تایید در README.md).

اجرا:
    python tools/simulate_fire_events.py --mode visual
    python tools/simulate_fire_events.py --mode panel
    python tools/simulate_fire_events.py --mode both --count 5
"""

import argparse
import os
import sys
import time

# اجازه می‌دهد این اسکریپت هم از ریشه‌ی پروژه و هم از داخل پوشه‌ی tools/ اجرا شود.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_store import report_store  # noqa: E402


def simulate_visual(camera_name: str = "دوربین تستی", count: int = 3, delay: float = 1.0):
    """رویداد تشخیص تصویری آتش/دود را count بار (به‌جای fire و smoke)
    مستقیماً در report_store ثبت می‌کند - بدون فریم واقعی (frame=None)."""
    for i in range(count):
        label = "fire" if i % 2 == 0 else "smoke"
        confidence = 0.80 + 0.03 * (i % 5)
        report_store.log_fire_smoke_visual(camera_name, label, frame=None, confidence=confidence)
        print(f"[visual] ثبت شد: {camera_name} — {label} ({confidence:.0%})")
        time.sleep(delay)


def simulate_panel(panel_name: str = "پنل تستی", zone: str = "Zone-1", delay: float = 1.5):
    """یک چرخه‌ی کامل فعال‌شدن/رفع‌شدن یک پنل فیزیکی را شبیه‌سازی می‌کند."""
    report_store.log_fire_alarm_panel(panel_name, zone, state="triggered")
    print(f"[panel] فعال شد: {panel_name} — {zone}")
    time.sleep(delay)
    report_store.log_fire_alarm_panel(panel_name, zone, state="cleared")
    print(f"[panel] رفع شد: {panel_name} — {zone}")


def main():
    parser = argparse.ArgumentParser(description="شبیه‌ساز رویدادهای حریق/دود بدون سخت‌افزار")
    parser.add_argument("--mode", choices=["visual", "panel", "both"], default="both")
    parser.add_argument("--count", type=int, default=3, help="تعداد رویداد تصویری (فقط برای --mode visual/both)")
    parser.add_argument("--delay", type=float, default=1.0, help="فاصله‌ی زمانی بین رویدادها (ثانیه)")
    args = parser.parse_args()

    if args.mode in ("visual", "both"):
        simulate_visual(count=args.count, delay=args.delay)
    if args.mode in ("panel", "both"):
        simulate_panel(delay=max(args.delay, 1.5))

    print("انجام شد. برای دیدن ردیف‌های تازه‌ثبت‌شده، دیالوگ «گزارش‌ها» را در main.py باز کنید.")


if __name__ == "__main__":
    main()

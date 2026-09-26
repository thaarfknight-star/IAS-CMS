# -*- coding: utf-8 -*-
"""تست‌های رگرسیون 2.0.56-beta — «پنل رویدادها».

۱) نوع تخلف پلاک لیست سیاه «⛔ ورود غیرمجاز» تعریف شده است.
۲) plate_store.get_violation: ثبت و خواندن تخلف.
۳) ReportsPage تب «🚨 تخلفات پلاک» دارد و جست‌وجویش تخلف ثبت‌شده را
   نشان می‌دهد (آفسکرین).
۴) MainWindow._push_plate_violation_to_events تخلف را با برچسب
   «ورود غیرمجاز» و رنگ قرمز در لیست پنل رویدادها درج می‌کند (آفسکرین).

اجرا:
  QT_QPA_PLATFORM=offscreen python3 tests/test_247_beta.py
"""
import os
import sys
import tempfile
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# --- استاب وابستگی‌های سنگین (الگوی test_227_beta.py) ---
class _Dummy:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Dummy()

    def __getattr__(self, n):
        if n.startswith("__") and n.endswith("__"):
            raise AttributeError(n)
        return _Dummy()


fr = types.ModuleType("face_recognition")
fr.__getattr__ = lambda n: (lambda *a, **k: [])
sys.modules["face_recognition"] = fr

_cv2 = types.ModuleType("cv2")
_cv2.__getattr__ = lambda n: _Dummy()
_cv2.resize = lambda *a, **k: a[0] if a else None
_cv2.imwrite = lambda *a, **k: True
sys.modules["cv2"] = _cv2

_passed = []
_failed = []


def check(name, cond, extra=""):
    (_passed if cond else _failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" | {extra}" if extra and not cond else ""))


sys.argv = ["test"]
from PyQt6.QtWidgets import QApplication, QListWidget
from PyQt6.QtGui import QColor
app = QApplication(sys.argv)

TMP = tempfile.mkdtemp(prefix="ias247_")

# ============================ ۱) برچسب نوع تخلف ============================
from plate_store import PlateStore

check("watchlist_black label is ورود غیرمجاز",
      PlateStore.VIOLATION_LABELS.get("watchlist_black") == "⛔ ورود غیرمجاز",
      PlateStore.VIOLATION_LABELS.get("watchlist_black"))

# ============================ ۲) ثبت/خواندن تخلف ============================
db_path = os.path.join(TMP, "plates.db")
store = PlateStore(db_path=db_path)
store.add_watchlist_entry("۱۱ج۲۲۲", "black", "تست")
vid = store.log_violation("watchlist_black", "۱۱ج۲۲۲",
                          camera_name="ورودی اصلی",
                          detail="پلاک در لیست سیاه دیده شد")
check("log_violation returns id", bool(vid), vid)
v = store.get_violation(vid)
check("get_violation roundtrip",
      v is not None and v.get("violation_type") == "watchlist_black"
      and v.get("camera_name") == "ورودی اصلی", str(v)[:120])
check("get_violation unknown -> None", store.get_violation("nope") is None)

# ============================ ۳) تب گزارش‌ها ============================
import reports_dialog
reports_dialog.plate_store = store  # استور موقت به‌جای سینگلتون واقعی


class _FakeReportStore:
    def distinct_cameras(self):
        return []

    def query(self, **k):
        return []


page = reports_dialog.ReportsPage(_FakeReportStore(), None)
tab_titles = [page.tabs.tabText(i) for i in range(page.tabs.count())]
check("reports has plate violations tab", "🚨 تخلفات پلاک" in tab_titles,
      tab_titles)
page.run_plate_violation_search()
n_rows = page.pviol_table.rowCount()
check("plate violations tab shows logged violation", n_rows >= 1,
      f"rows={n_rows}")
found_label = any(
    "ورود غیرمجاز" in (page.pviol_table.item(r, 2).text()
                        if page.pviol_table.item(r, 2) else "")
    for r in range(n_rows))
check("violation type shown as ورود غیرمجاز in reports", found_label)
check("summary mentions count", "1" in page.pviol_summary.text(),
      page.pviol_summary.text())

# ============================ ۴) درج در پنل رویدادها ============================
import main as m

check("MainWindow has _push_plate_violation_to_events",
      hasattr(m.MainWindow, "_push_plate_violation_to_events"))
check("MainWindow has _recent_face_events concept",
      hasattr(m.MainWindow, "open_face_gallery")
      and hasattr(m.MainWindow, "on_face_event"))

m.plate_store = store  # استور موقت


class _FakeSelf:
    pass


fake = _FakeSelf()
fake._shown_violation_ids = set()
fake.events_panel_list = QListWidget()

m.MainWindow._push_plate_violation_to_events(fake, vid)
check("violation pushed to events panel",
      fake.events_panel_list.count() == 1,
      f"count={fake.events_panel_list.count()}")
item = fake.events_panel_list.item(0)
txt = item.text() if item else ""
check("panel item shows ورود غیرمجاز", "ورود غیرمجاز" in txt, txt)
check("panel item is red",
      item is not None and item.foreground().color() == QColor("#c0392b"))

# ضدتکرار: همان تخلف دوباره درج نشود
m.MainWindow._push_plate_violation_to_events(fake, vid)
check("duplicate violation not re-added",
      fake.events_panel_list.count() == 1)

# شناسه‌ی نامعتبر: چیزی درج نشود و خطا ندهد
m.MainWindow._push_plate_violation_to_events(fake, "nope")
check("unknown vid adds nothing", fake.events_panel_list.count() == 1)

# ---------- جمع‌بندی ----------
print(f"\n{len(_passed)} passed, {len(_failed)} failed")
if _failed:
    print("FAILED:", _failed)
    sys.exit(1)

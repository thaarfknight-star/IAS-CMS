# -*- coding: utf-8 -*-
"""Headless regression test for IAS-CMS 2.0.39-beta.

۱) رفع باگ: «تعریف این پلاک» از جزئیات عبور (گزارش عبور) حالا جدول تب
   «تعریف پلاک‌ها» را فوراً رفرش می‌کند (page.refresh_plates_table).
۲) رابط اصلی دوزبانه شد (فارسی RTL / English LTR):
   - i18n.py: زیرساخت مشترک (t/get_lang/is_rtl/apply_direction/tr_app)
   - main.py: ۸ دکمه‌ی هدر، عنوان پنجره، جهت کل برنامه
   - settings_page.py: صفحه‌ی تنظیمات
   - plate_library_dialog.py: فرم تعریف، جزئیات عبور، وضعیت زنده، هر ۵ تب
     (تعریف پلاک‌ها / گزارش عبور / مسیرها و قوانین / تخلفات تردد / لیست تحت‌نظر)
   - plate_stats_dialog.py: داشبورد آماری تردد
   - plate_store.py: لیبل‌های دوزبانه‌ی نوع/رنگ خودرو، تخلف، تحت‌نظر، عبور
   - plate_direction.py: متن جزئیات تخلف به زبان فعلی ثبت می‌شود
۳) در حالت English، کمبوباکس‌های نوع/رنگ خودرو متن انگلیسی نشان می‌دهند ولی
   مقدار فارسی (itemData) در دیتابیس ذخیره می‌شود.
"""
import os
import sys
import types
import tempfile
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr
sys.modules["cv2"] = MagicMock(name="cv2")

passed = []
failed = []


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                             QLabel)
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

import app_settings
import plate_store as ps
import plate_library_dialog as pld
import plate_stats_dialog as psd
import plate_direction as pd

# دیتابیس موقت تا دیتای واقعی کاربر دست‌نخورده بماند
TMP = tempfile.mkdtemp()
store = ps.PlateStore(db_path=os.path.join(TMP, "plates.db"))
pld.plate_store = store  # هدایت سینگلتون ماژول به دیتابیس موقت


def set_lang(l):
    app_settings.get_language = lambda: l


def make_page():
    cam_store = MagicMock()
    cam_store.standalone_cameras.return_value = []
    cam_store.nvrs = []
    return pld.PlateLibraryPage(lambda: None, cam_store)


def ok_button_text(dlg):
    bb = dlg.findChild(QDialogButtonBox)
    return (bb.button(QDialogButtonBox.StandardButton.Ok).text(),
            bb.button(QDialogButtonBox.StandardButton.Cancel).text())


# ============================ ۱) فارسی ============================
set_lang("fa")
page = make_page()
titles = [l.text() for l in page.findChildren(QLabel)]
check("fa: page title", "🚗 پلاک‌خوان - تشخیص و گزارش عبور پلاک‌ها" in titles)
check("fa: RTL", page.layoutDirection() == Qt.LayoutDirection.RightToLeft)
check("fa: tab0", page.tabs.tabText(0) == "📝 تعریف پلاک‌ها")
check("fa: tab1", page.tabs.tabText(1) == "📋 گزارش عبور")
check("fa: tab2", page.tabs.tabText(2) == "🛣 مسیرها و قوانین")
check("fa: tab3", page.tabs.tabText(3) == "🚨 تخلفات تردد")
check("fa: tab4", page.tabs.tabText(4) == "⭐ لیست تحت‌نظر")
check("fa: col0", page.plates_table.horizontalHeaderItem(0).text() == "پلاک")
check("fa: no stray EN in plates cols",
      not any("Plate" in page.plates_table.horizontalHeaderItem(i).text()
              for i in range(page.plates_table.columnCount())))

form = pld.PlateFormDialog(page, prefill_text="")
check("fa: form title", form.windowTitle() == "تعریف پلاک جدید")
check("fa: form tab0", form.kind_tabs.tabText(0) == "🚗 پلاک خودرو")
ok, cancel = ok_button_text(form)
check("fa: form ok", ok == "ثبت پلاک")
check("fa: form cancel", cancel == "انصراف")
idx = form.vehicle_type_combo.findText("سواری")
check("fa: vehicle type fa display+data",
      idx >= 0 and form.vehicle_type_combo.itemData(idx) == "سواری")

stats = psd.PlateStatsDialog(store, page)
check("fa: stats title", stats.windowTitle() == "📊 آمار تردد پلاک‌ها")
check("fa: stats RTL",
      stats.layoutDirection() == Qt.LayoutDirection.RightToLeft)

check("fa: violation_label",
      store.violation_label("wrong_way") == "تردد خلاف جهت مجاز مسیر")
check("fa: watchlist_label",
      store.watchlist_label("black") == "⛔ لیست سیاه")
check("fa: crossing_label", store.crossing_label("entry") == "ورود")
check("fa: travel_label", store.travel_label("going") == "رفت")

# ============================ ۲) English ============================
set_lang("en")
page2 = make_page()
titles2 = [l.text() for l in page2.findChildren(QLabel)]
check("en: page title",
      "🚗 Plate reader — plate detection & crossing reports" in titles2)
check("en: LTR", page2.layoutDirection() == Qt.LayoutDirection.LeftToRight)
check("en: tab0", page2.tabs.tabText(0) == "📝 Define plates")
check("en: tab1", page2.tabs.tabText(1) == "📋 Crossing report")
check("en: tab2", page2.tabs.tabText(2) == "🛣 Lanes & rules")
check("en: tab3", page2.tabs.tabText(3) == "🚨 Traffic violations")
check("en: tab4", page2.tabs.tabText(4) == "⭐ Watchlist")
check("en: col0", page2.plates_table.horizontalHeaderItem(0).text() == "Plate")
check("en: viol col2", page2.VIOLATION_COLUMNS[2] == "Violation type")

form2 = pld.PlateFormDialog(page2, prefill_text="")
check("en: form title", form2.windowTitle() == "Define new plate")
ok2, cancel2 = ok_button_text(form2)
check("en: form ok", ok2 == "Save plate")
check("en: form cancel", cancel2 == "Cancel")
check("en: vehicle type EN display",
      form2.vehicle_type_combo.itemText(0) == "Passenger car")
check("en: vehicle type data still FA",
      form2.vehicle_type_combo.itemData(0) == "سواری")
check("en: vehicle color data still FA",
      form2.vehicle_color_combo.itemData(0) == "سفید")

stats2 = psd.PlateStatsDialog(store, page2)
check("en: stats title",
      stats2.windowTitle() == "📊 Plate traffic statistics")
check("en: stats LTR",
      stats2.layoutDirection() == Qt.LayoutDirection.LeftToRight)

check("en: violation_label",
      store.violation_label("wrong_way") == "Driving against lane direction")
check("en: watchlist_label",
      store.watchlist_label("black") == "⛔ Blacklist")
check("en: crossing_label", store.crossing_label("entry") == "Entry")
check("en: travel_label", store.travel_label("going") == "Outbound")

# ============================ ۳) رفع باگ رفرش ============================
set_lang("fa")
page3 = make_page()
refreshed = []
page3.refresh_plates_table = lambda: refreshed.append(1)
ev = {"id": "ev1", "plate_text": "12ب345 ایران 11",
      "plate_display": "۱۲ ب ۳۴۵ | ایران ۱۱", "camera_name": "c1",
      "date_j": "1405/07/01", "time_g": "10:00", "kind": "unknown"}
dlg = pld.PlateEventDetailDialog(ev, page3)


class FakeForm(QDialog):
    def exec(self):
        return QDialog.DialogCode.Accepted

    def get_data(self):
        return dict(plate_text="12ب345 ایران 11", plate_kind="car",
                    plate_display="۱۲ ب ۳۴۵ | ایران ۱۱")


_orig_form = pld.PlateFormDialog
pld.PlateFormDialog = lambda *a, **k: FakeForm()
attached = []
store.attach_event_to_plate = lambda *a, **k: attached.append(1) or "p1"
store.get_plate = lambda pid: {"id": pid}
store.add_plate = lambda **kw: (True, "p1")
from PyQt6.QtWidgets import QMessageBox
_orig_info = QMessageBox.information
QMessageBox.information = lambda *a, **k: None
try:
    dlg.define_this_plate()
finally:
    pld.PlateFormDialog = _orig_form
    QMessageBox.information = _orig_info
check("bugfix: attach called", len(attached) == 1)
check("bugfix: refresh_plates_table called", len(refreshed) == 1)

# ============================ ۴) جزئیات تخلف دوزبانه ============================
eng = pd.PlateDirectionEngine(store)
set_lang("en")
d_en = pd._dt("wrong_way", crossing=store.crossing_label("entry"),
              travel=store.travel_label("going"), lane="L1",
              allowed=store.travel_label("going"))
check("en: wrong_way detail",
      d_en == "Entry (Outbound) in “L1” where only “Outbound” is allowed")
set_lang("fa")
d_fa = pd._dt("wrong_way", crossing=store.crossing_label("entry"),
              travel=store.travel_label("going"), lane="L1",
              allowed=store.travel_label("going"))
check("fa: wrong_way detail",
      d_fa == "ورود (رفت) در «L1» که فقط جهت «رفت» مجاز است")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

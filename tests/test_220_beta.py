# -*- coding: utf-8 -*-
"""Headless regression test for IAS-CMS 2.0.20-beta.

بازطراحی صفحه‌ی تنظیمات به دستور کاربر: صفحه اسکرول‌دار است و هر بخش
ارتفاع طبیعی خودش را دارد؛ لازم نیست همه‌ی گزینه‌ها هم‌زمان در یک نما
جا شوند.

۱) ساختار اسکرول: تنها آیتم layout بیرونی یک QScrollArea با widgetResizable
   و بدون فریم است؛ محتوا (تیتر + ۶ گروه) داخل آن با ارتفاع طبیعی چیده شده.
۲) هر ۶ گروه (تم، زبان، صداها، امنیت رمزها، آپدیت، حذف نصب) در محتوا هستند
   و انتهای layout یک stretch است تا گروه‌ها بالا بچینند.
۳) ردیف‌های صداهای هشدار: چک‌باکس بدون متن + لیبل wrapشونده (حفظ 2.0.19).
۴) دکمه‌های «اعمال آپدیت» و «حذف نصب»: متن کامل و minimumWidth >= اندازه‌ی
   طبیعی متن تا هیچ‌وقت «...» نشوند.
۵) در نمای کوچک، ارتفاع محتوا از viewport بیشتر است (یعنی اسکرول می‌خورد).
۶) تعویض زبان (بازسازی متن‌ها) و refresh بدون خطا کار می‌کند.
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr

passed = []
failed = []


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


from PyQt6.QtWidgets import (QApplication, QCheckBox, QFrame, QGroupBox,
                             QHBoxLayout, QLabel, QPushButton, QScrollArea)
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

from settings_page import SettingsPage

pg = SettingsPage()
pg.resize(900, 700)
pg.show()
app.processEvents()

check("settings page is RTL",
      pg.layoutDirection() == Qt.LayoutDirection.RightToLeft)

# ---------- ۱) ساختار اسکرول ----------
outer = pg.layout()
scrolls = pg.findChildren(QScrollArea)
check("exactly one QScrollArea", len(scrolls) == 1)
scroll = scrolls[0]
check("scroll area is the only outer-layout item",
      outer.count() == 1 and outer.itemAt(0).widget() is scroll)
check("scroll area is widget-resizable", scroll.widgetResizable())
check("scroll area is frameless", scroll.frameShape() == QFrame.Shape.NoFrame)

content = scroll.widget()
check("scroll content widget exists", content is not None)
clay = content.layout()
check("content layout is vertical with margins",
      clay is not None and clay.contentsMargins().left() >= 20)

# ---------- ۲) هر ۶ گروه ----------
groups = content.findChildren(QGroupBox)
check("6 groups in scroll content", len(groups) == 6)
titles = " ".join(g.title() for g in groups)
for needle in ("تم برنامه", "زبان", "صداهای هشدار",
               "امنیت رمزها", "به‌روزرسانی", "حذف نصب"):
    check("group present: " + needle, needle in titles)

# آخرین آیتم layout محتوا باید stretch باشد (گروه‌ها بالا می‌چینند)
last = clay.itemAt(clay.count() - 1)
check("content ends with stretch", last is not None and last.spacerItem() is not None)

# ---------- ۳) ردیف‌های صداهای هشدار (الگوی 2.0.19) ----------
check("sound checks has 3 keys",
      set(pg._sound_checks.keys()) == {"fire", "zone", "violation"})
expected_labels = {"🔥 آژیر حریق", "🚧 بوق ورود به محدوده", "🚨 بوق تخلف طبقاتی"}
found = {w.text() for w in content.findChildren(QLabel)
         if w.text() in expected_labels}
check("all 3 sound labels visible with full text", found == expected_labels)
rows_ok = True
for chk in pg._sound_checks.values():
    if not isinstance(chk, QCheckBox) or chk.text() != "":
        rows_ok = False
        break
check("sound checkboxes are textless", rows_ok)

# ---------- ۴) دکمه‌ها بریده نمی‌شوند ----------
for name, btn in (("update_btn", pg.update_btn),
                  ("uninstall_btn", pg.uninstall_btn)):
    ok_btn = (isinstance(btn, QPushButton) and btn.text().strip() != ""
              and btn.minimumWidth() >= btn.sizeHint().width())
    check(name + " full text, no elision", ok_btn)
check("update button text", pg.update_btn.text().strip() == "⬆️ اعمال آپدیت")
check("uninstall button text",
      pg.uninstall_btn.text().strip() == "🗑 حذف نصب برنامه")

# ---------- ۵) اسکرول واقعی در نمای کوچک ----------
pg.resize(900, 380)
pg.show()
app.processEvents()
content_h = content.sizeHint().height()
viewport_h = scroll.viewport().height()
check("content taller than small viewport (scrolls), "
      f"{content_h} > {viewport_h}", content_h > viewport_h)
check("vertical scrollbar appears when needed",
      scroll.verticalScrollBar().maximum() > 0)

# ---------- ۶) بازسازی متن‌ها (تعویض زبان) و refresh ----------
# نکته: deleteLater فقط وقتی حلقه‌ی رویداد واقعاً بچرخد اعمال می‌شود؛
# processEvents خشک در این محیط کافی نیست، پس با QTimer پمپ می‌کنیم.
from PyQt6.QtCore import QTimer


def _pump(ms=150):
    QTimer.singleShot(ms, app.quit)
    app.exec()


pg.resize(900, 700)
pg._lang = "en"
pg._rebuild_texts()
_pump()
scrolls2 = pg.findChildren(QScrollArea)
check("one scroll area after rebuild", len(scrolls2) == 1)
groups2 = scrolls2[0].widget().findChildren(QGroupBox)
check("6 groups after rebuild", len(groups2) == 6)
en_titles = " ".join(g.title() for g in groups2)
check("english titles after rebuild", "Alert sounds" in en_titles)
pg._lang = "fa"
pg._rebuild_texts()
_pump()
try:
    pg.refresh()
    check("refresh() runs clean", True)
except Exception:
    check("refresh() runs clean", False)

# تاگل صدا → کال‌بک on_sound_changed صدا زده می‌شود
calls = []
pg2 = SettingsPage(on_sound_changed=lambda: calls.append(1))
chk0 = pg2._sound_checks["fire"]
chk0.setChecked(not chk0.isChecked())
app.processEvents()
check("sound toggle fires callback", len(calls) >= 1)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

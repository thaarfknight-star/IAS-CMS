"""Headless regression test for IAS-CMS 2.0.6-beta (PyQt6 offscreen).

قانون «نذار دیگه خراب بشه»: این تست بعد از هر تغییر در main.py /
camera_stream.py / add_nvr_dialog.py اجرا می‌شود و موارد زیر را بررسی می‌کند:
 ۱) پنل‌ها: هیچ‌کدام collapsible نیستند، opaque resize فعال است،
    toggle_sidebar رفت‌وبرگشت، اندازه‌ی پنل راست را عوض نمی‌کند.
 ۲) تمام‌صفحه: toggle_fullscreen کرش نمی‌کند و اندازه‌های ذخیره‌شده برمی‌گردند.
 ۳) پنل حریق: بدون دوربین حریق مخفی، با حداقل یک دوربین حریق نمایان.
 ۴) فالبک تشخیص حرکت: با فریم مصنوعیِ دارای جسم متحرک، باکس برمی‌گرداند و
    زنجیره‌ی _PersonRegionTracker -> region_entered بدون مدل YOLO کار می‌کند.
 ۵) تطبیق IP اسکن NVR/شبکه: _subnet_of و _match_status.
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- stub سنگین‌ها قبل از import ---
fr = types.ModuleType("face_recognition")
fr.face_locations = lambda *a, **k: []
fr.face_encodings = lambda *a, **k: []
fr.face_distance = lambda *a, **k: []
fr.compare_faces = lambda *a, **k: []
sys.modules["face_recognition"] = fr

import numpy as np

passed = []
failed = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))


# ---------- ۵) منطق تطبیق IP (بدون نیاز به دیالوگ واقعی) ----------
sys.argv = ["test"]
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

import add_nvr_dialog as an

check("subnet_of valid", an.AddNVRDialog._subnet_of("192.168.1.50") == "192.168.1")
check("subnet_of invalid", an.AddNVRDialog._subnet_of("abc") is None)
check("subnet_of empty", an.AddNVRDialog._subnet_of("") is None)

dlg = an.AddNVRDialog.__new__(an.AddNVRDialog)
dlg._net_devices = [{"ip": "192.168.1.10"}, {"ip": "192.168.1.20"}]
dlg._net_scan_done = True
check("match_status matched", dlg._match_status("192.168.1.10") == "matched")
check("match_status not_found", dlg._match_status("192.168.1.99") == "not_found")
check("match_status pending(net running)", (lambda d: (setattr(d, "_net_scan_done", False), d._match_status("192.168.1.10"))[1])(dlg) == "pending")
dlg._net_scan_done = True
check("match_status empty ip", dlg._match_status("") is None)

# ---------- ۴) فالبک تشخیص حرکت ----------
import camera_stream as cs

md = cs._MotionRegionDetector()
h, w = 480, 640
bg = np.zeros((h, w, 3), dtype=np.uint8)
# چند فریم پس‌زمینه برای یادگیری مدل
for _ in range(5):
    md.detect(bg)
    md._last_run = 0  # دور زدن cooldown در تست
# فریم با یک مستطیل سفید متحرک (شبیه شخص)
frame = bg.copy()
frame[150:350, 280:360] = 255
md._last_run = 0
boxes = md.detect(frame)
check("motion detects moving blob", len(boxes) >= 1, f"boxes={boxes}")
if boxes:
    t, r, b, l = boxes[0]
    check("motion box sane coords", 0 <= l < r <= w and 0 <= t < b <= h, f"{boxes[0]}")
# فریم ثابت نباید باکس بدهد (بعد از یادگیری)
md._last_run = 0
for _ in range(8):
    md._last_run = 0
    still = md.detect(frame)
check("motion no box on static frame", len(still) == 0, f"boxes={still}")

# زنجیره‌ی کامل ردیاب محدوده با باکس فالبک (فرمت واقعی: points نرمال‌شده)
tracker = cs._PersonRegionTracker()
regions = [{"id": "r1", "number": 1, "name": "در",
            "points": [(0.4, 0.3), (0.6, 0.3), (0.6, 0.6), (0.4, 0.6)]}]
events = tracker.update(boxes, regions, w, h)
check("region tracker fires on motion box", len(events) >= 1, f"events={events}")
# فریم بعدی همان‌جا: نباید دوباره ایونت بدهد (یک‌بار ورود)
events2 = tracker.update(boxes, regions, w, h)
check("region tracker no repeat", len(events2) == 0)

# ---------- ۱/۲/۳) main window ----------
import main as m

win = m.MainWindow()
# در حالت offscreen، showMaximized هندسه‌ی واقعی نمی‌دهد؛ برای اینکه
# QSplitter اندازه‌های معنادار داشته باشد، اول سایز واقعی می‌دهیم.
win.resize(1600, 900)
win.show()
app.processEvents()

# ۱) پنل‌ها
for i in range(3):
    check(f"main splitter child {i} not collapsible", not win.splitter.isCollapsible(i))
for i in range(2):
    check(f"right splitter child {i} not collapsible", not win.right_splitter.isCollapsible(i))
check("main splitter opaque resize", win.splitter.opaqueResize())
check("right splitter opaque resize", win.right_splitter.opaqueResize())

# toggle_sidebar رفت‌وبرگشت (hide/show): اندازه‌ی پنل راست نباید عوض شود
before = list(win.splitter.sizes())
right_before = before[2]
win.toggle_sidebar()
app.processEvents()
check("sidebar hide -> left hidden", win.left_widget.isHidden())
mid = list(win.splitter.sizes())
check("sidebar hide -> left size 0", mid[0] == 0, f"sizes={mid}")
win.toggle_sidebar()
app.processEvents()
check("sidebar show -> left visible again", not win.left_widget.isHidden())
after = list(win.splitter.sizes())
check("sidebar show -> left width restored", abs(after[0] - before[0]) <= 2, f"before={before} after={after}")
check("sidebar roundtrip keeps right width", abs(after[2] - right_before) <= 2, f"before={before} after={after}")

# مکانیزم بازیابی اندازه‌ها (قانون «نذار دیگه خراب بشه»): بعد از roundtrip،
# وضعیت ذخیره‌شده باید با وضعیت واقعی یکی باشد (نه کهنه‌ی قبل از layout).
app.processEvents()
app.processEvents()
check("saved sizes match actual after roundtrip",
      all(abs(a - b) <= 2 for a, b in zip(win._saved_main_sizes, win.splitter.sizes())),
      f"saved={win._saved_main_sizes} actual={win.splitter.sizes()}")
win.splitter.setSizes([120, 900, 300])
win.right_splitter.setSizes([500, 100])
app.processEvents()
win._remember_splitter_sizes()  # همگام‌سازی صریح برای تست قطعی مکانیزم
win.splitter.setSizes([120, 900, 300])
app.processEvents()
win._restore_splitter_sizes()
app.processEvents()
restored = list(win.splitter.sizes())
check("restore brings back saved main sizes",
      all(abs(a - b) <= 2 for a, b in zip(restored, win._saved_main_sizes)),
      f"restored={restored} saved={win._saved_main_sizes}")

# ۲) تمام‌صفحه: کرش نکند و حالت عوض شود
win.toggle_fullscreen()
app.processEvents()
check("fullscreen entered", win.isFullScreen())
check("fullscreen button checked", win.fullscreen_btn.isChecked())
win.toggle_fullscreen()
app.processEvents()
check("fullscreen exited", not win.isFullScreen())
check("fullscreen button unchecked", not win.fullscreen_btn.isChecked())

# ۳) پنل حریق: در این محیط تست، دوربینی با fire_detection نیست -> باید مخفی باشد
check("fire panel hidden with no fire cams", not win.fire_panel_group.isVisible())
# یک دوربین حریق اضافه کن -> باید نمایان شود
cam = win.camera_store.add_camera("تست حریق", "192.168.1.99", "554", "u", "p", "/x")
win.camera_store.update_camera(cam["id"], fire_detection=True)
win.reload_camera_list()
app.processEvents()
check("fire panel visible with a fire cam", win.fire_panel_group.isVisible())
# حذف دوربین -> دوباره مخفی
win.camera_store.remove_camera(cam["id"])
win.reload_camera_list()
app.processEvents()
check("fire panel hidden after cam removed", not win.fire_panel_group.isVisible())

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

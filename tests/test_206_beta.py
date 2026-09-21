"""Headless regression test for IAS-CMS 2.0.6-beta (PyQt6 offscreen).

قانون «نذار دیگه خراب بشه»: این تست بعد از هر تغییر در main.py /
camera_stream.py / add_nvr_dialog.py اجرا می‌شود و موارد زیر را بررسی می‌کند:
 ۱) پنل‌ها (2.0.12-beta: برگشت به action #207): رفتار پیش‌فرض Qt -
    پنل‌ها collapsible هستند و هیچ ذخیره/بازیابی اندازه‌ای در کار نیست؛
    toggle_sidebar رفت‌وبرگشت با setSizes (عرض صفر و برگشت).
 ۲) تمام‌صفحه (2.0.14-beta: حذف کامل به دستور کاربر): دکمه‌ی «⛶ تمام‌صفحه»،
    متد toggle_fullscreen و شرتکات F11 کاملاً حذف شده‌اند.
 ۲-ب) دکمه‌های رسم محدوده (2.0.12-beta): فقط «🖊 رسم محدوده هشدار» و
    «📋 مدیریت محدوده‌ها» همیشه روی نوار ابزار و نمایان‌اند؛ بقیه
    (نوع رسم/تایید/لغو) فقط در حالت رسم یا وقتی محدوده‌ی در انتظار/در حال
    ویرایش هست دیده می‌شوند.
 ۳) پنل حریق: بدون دوربین حریق مخفی، با حداقل یک دوربین حریق نمایان.
 ۴) فالبک تشخیص حرکت: با فریم مصنوعیِ دارای جسم متحرک، باکس برمی‌گرداند و
    زنجیره‌ی _PersonRegionTracker -> region_entered بدون مدل YOLO کار می‌کند.
 ۵) تطبیق IP اسکن NVR/شبکه: _subnet_of و _match_status.
 ۶) موتور آپدیت پایتون (2.0.8-beta): جایگزینی updater.ps1 با update_apply؛
    اعمال کامل آپدیت روی دایرکتوری موقت، بکاپ، sha256، کدهای خطا،
    رهگیری --apply-update در main.py، بدون Qt در update_apply.
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

# ۱) پنل‌ها - برگشت به action #207: رفتار پیش‌فرض Qt؛ پنل‌ها collapsible
# هستند و هیچ مکانیزم ذخیره/بازیابی اندازه‌ای وجود ندارد.
for i in range(3):
    check(f"main splitter child {i} collapsible (default #207)", win.splitter.isCollapsible(i))
for i in range(2):
    check(f"right splitter child {i} collapsible (default #207)", win.right_splitter.isCollapsible(i))
check("no _remember_splitter_sizes (reverted to #207)", not hasattr(win, "_remember_splitter_sizes"))
check("no _restore_splitter_sizes (reverted to #207)", not hasattr(win, "_restore_splitter_sizes"))

# toggle_sidebar رفت‌وبرگشت با setSizes (نسخه‌ی #207): عرض صفر و برگشت به
# آخرین عرض؛ عرض آزادشده به خانه‌ی وسط (شبکه‌ی دوربین‌ها) می‌رسد و پنل راست
# دست‌نخورده می‌ماند.
before = list(win.splitter.sizes())
win.toggle_sidebar()
app.processEvents()
mid = list(win.splitter.sizes())
check("sidebar toggle -> left width 0", mid[0] == 0, f"sizes={mid}")
check("sidebar toggle -> freed width goes to middle",
      abs(mid[1] - (before[1] + before[0])) <= 2, f"before={before} mid={mid}")
check("sidebar toggle -> right keeps width",
      abs(mid[2] - before[2]) <= 2, f"before={before} mid={mid}")
win.toggle_sidebar()
app.processEvents()
after = list(win.splitter.sizes())
check("sidebar toggle back -> left width restored",
      abs(after[0] - before[0]) <= 2, f"before={before} after={after}")
check("sidebar toggle back -> right keeps width",
      abs(after[2] - before[2]) <= 2, f"before={before} after={after}")

# ۲) تمام‌صفحه (2.0.14-beta به دستور کاربر): دکمه‌ی «⛶ تمام‌صفحه»، متد
# toggle_fullscreen و شرتکات F11 کاملاً حذف شده‌اند.
check("no fullscreen_btn", not hasattr(win, "fullscreen_btn"))
check("no toggle_fullscreen", not hasattr(win, "toggle_fullscreen"))

# ۲-ب) دکمه‌های رسم محدوده (2.0.12-beta، اصلاح‌شده در 2.0.14-beta):
# «🖊 رسم محدوده هشدار» و «📋 مدیریت محدوده‌ها» همیشه نمایان‌اند؛
# دکمه‌های «نوع رسم» (خودکار/هوشمند) فقط در حالتِ خالصِ رسم، و «تایید»/«لغو»
# فقط وقتی محدوده‌ای رسم شده (در انتظار/در حال ویرایش) دیده می‌شوند.
# ردیف رسم، سطر دوم نوار ابزار است (_NoMinWidthRow) و ظاهر/مخفی شدنش نباید
# اندازه‌ی پنجره یا کادرها را عوض کند.
check("no region_menu_btn", not hasattr(win, "region_menu_btn"))
check("no region_type_row", not hasattr(win, "region_type_row"))
check("no region_action_row", not hasattr(win, "region_action_row"))
check("region_draw_row exists", hasattr(win, "region_draw_row"))
_region_btns = [win.draw_line_btn, win.auto_region_btn, win.ai_floor_btn,
                win.confirm_line_btn, win.redraw_line_btn, win.manage_regions_btn]
check("all 6 region buttons exist", all(b is not None for b in _region_btns))
check("draw buttons live in region_draw_row",
      all(b.parentWidget() is win.region_draw_row
          for b in (win.auto_region_btn, win.ai_floor_btn,
                    win.confirm_line_btn, win.redraw_line_btn)))
check("entry buttons not in draw row",
      win.draw_line_btn.parentWidget() is not win.region_draw_row
      and win.manage_regions_btn.parentWidget() is not win.region_draw_row)
check("draw btn portable label", win.draw_line_btn.text() == "🖊 رسم محدوده هشدار",
      win.draw_line_btn.text())
check("draw row is _NoMinWidthRow",
      type(win.region_draw_row).__name__ == "_NoMinWidthRow")
check("draw row min-width hint is 0",
      win.region_draw_row.minimumSizeHint().width() == 0)
check("people button renamed", win.people_toggle_btn.text() == "👥 شمارش افراد",
      win.people_toggle_btn.text())

class _FakeSlot:
    """خانه‌ی ساختگی برای تست منطق نمایش/مخفی‌شدن ردیف رسم."""
    def __init__(self, draw=False, pending=False, editing=False):
        self.cam = {"id": "x"}
        self.regions = []
        self._draw, self._pending, self._editing = draw, pending, editing
    def has_pending_region(self): return self._pending
    def is_editing_region(self): return self._editing
    def is_draw_mode(self): return self._draw

def _row_btn_visible(btn):
    # نمایان بودن دکمه نسبت به ردیف (صرف‌نظر از مخفی بودن خود ردیف)
    return btn.isVisibleTo(win.region_draw_row)

_orig_selected_slot = win._selected_slot
try:
    # بدون خانه‌ی انتخاب‌شده: ردیف رسم مخفی، دکمه‌های ورود نمایان
    win._selected_slot = lambda: None
    win._refresh_line_buttons()
    app.processEvents()
    check("draw row hidden with no selection", not win.region_draw_row.isVisible())
    check("entry buttons visible with no selection",
          win.draw_line_btn.isVisible() and win.manage_regions_btn.isVisible())
    # حالتِ خالصِ رسم: فقط دکمه‌های «نوع رسم» دیده می‌شوند، نه تایید/لغو
    win._selected_slot = lambda: _FakeSlot(draw=True)
    win._refresh_line_buttons()
    app.processEvents()
    check("draw row visible in draw mode", win.region_draw_row.isVisible())
    check("type buttons visible in draw mode",
          _row_btn_visible(win.auto_region_btn) and _row_btn_visible(win.ai_floor_btn))
    check("confirm/cancel hidden in pure draw mode",
          not _row_btn_visible(win.confirm_line_btn)
          and not _row_btn_visible(win.redraw_line_btn))
    # محدوده‌ی در انتظار (رسم شده): فقط تایید/لغو دیده می‌شوند
    win._selected_slot = lambda: _FakeSlot(pending=True)
    win._refresh_line_buttons()
    app.processEvents()
    check("draw row visible with pending region", win.region_draw_row.isVisible())
    check("confirm/cancel visible with pending region",
          _row_btn_visible(win.confirm_line_btn) and _row_btn_visible(win.redraw_line_btn))
    check("type buttons hidden with pending region",
          not _row_btn_visible(win.auto_region_btn)
          and not _row_btn_visible(win.ai_floor_btn))
    # ویرایش شکل: فقط تایید/لغو (با متن ویرایش)
    win._selected_slot = lambda: _FakeSlot(editing=True)
    win._refresh_line_buttons()
    app.processEvents()
    check("draw row visible when editing", win.region_draw_row.isVisible())
    check("confirm/cancel visible when editing",
          _row_btn_visible(win.confirm_line_btn) and _row_btn_visible(win.redraw_line_btn))
    # حالت عادی: ردیف مخفی
    win._selected_slot = lambda: _FakeSlot()
    win._refresh_line_buttons()
    app.processEvents()
    check("draw row hidden in normal state", not win.region_draw_row.isVisible())
finally:
    win._selected_slot = _orig_selected_slot

# ۲-ج) (2.0.14-beta) باگ «به‌هم‌ریختن اندازه‌ها با زدن دکمه‌ی رسم»: ظاهر و
# مخفی شدن ردیف رسم نباید اندازه‌ی پنجره، حداقل‌اندازه‌ی پنجره یا اندازه‌ی
# کادرها را عوض کند.
win.resize(1600, 900)
app.processEvents(); app.processEvents()
_win_before = (win.width(), win.height())
_min_before = (win.minimumWidth(), win.minimumHeight())
_tiles_before = sorted(set((s.width(), s.height()) for s in win.camera_grid.slots))
_slot0 = win.camera_grid.slots[0]
_slot0.cam = {"id": "t-draw", "name": "t", "ip": "192.168.1.50"}
win.camera_grid._on_slot_clicked(_slot0)
app.processEvents()
win.draw_line_btn.setChecked(True)  # ورود به حالت رسم -> ردیف نمایان
app.processEvents(); app.processEvents()
check("draw row really visible", win.region_draw_row.isVisible())
check("window size unchanged by draw row",
      (win.width(), win.height()) == _win_before, f"{(win.width(), win.height())}")
check("window min-size unchanged by draw row",
      (win.minimumWidth(), win.minimumHeight()) == _min_before,
      f"{(win.minimumWidth(), win.minimumHeight())}")
win.draw_line_btn.setChecked(False)  # خروج از حالت رسم -> ردیف مخفی
app.processEvents(); app.processEvents()
check("draw row hidden again", not win.region_draw_row.isVisible())
_tiles_after = sorted(set((s.width(), s.height()) for s in win.camera_grid.slots))
check("tiles restored after draw toggle", _tiles_after == _tiles_before,
      f"before={_tiles_before} after={_tiles_after}")
_slot0.cam = None
win.camera_grid._select_index(-1) if hasattr(win.camera_grid, "_select_index") else None
win._refresh_line_buttons()
app.processEvents()

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

# ---------- ۶) موتور آپدیت پایتون (2.0.8-beta، جایگزین updater.ps1) ----------
import json as _json
import hashlib as _hashlib
import shutil as _shutil
import subprocess as _sp
import tempfile as _tf
from pathlib import Path as _Path

import update_apply as _ua

_REPO = _Path(__file__).resolve().parent.parent


def _make_update_env(tmp, old_files, new_files, version="2.0.8-beta",
                     prev="2.0.7-beta", bad_hash=False):
    install = _Path(tmp) / "install"
    pending = install / "pending_update"
    (pending / "files").mkdir(parents=True)
    for rel, content in old_files.items():
        p = install / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    (install / "version.txt").write_text(prev, encoding="ascii")
    infos = []
    for rel, content in new_files.items():
        (pending / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
        (pending / "files" / rel).write_bytes(content)
        h = _hashlib.sha256(content).hexdigest()
        if bad_hash:
            h = "0" * 64
        infos.append({"path": rel, "size": len(content), "sha256": h})
    info = {"app": "IAS-CMS", "version": version, "prev_version": prev,
            "files": infos, "removed": [],
            "total_size": sum(len(c) for c in new_files.values())}
    (pending / "update_info.json").write_text(_json.dumps(info), encoding="utf-8")
    (pending / "manifest.json").write_text("{}", encoding="utf-8")
    return install, pending


_p = _sp.Popen(["true"])
_p.wait()
_dead_pid = _p.pid  # پی‌آیدیِ قطعاً مرده برای تست انتظار خروج والد

# سناریوی موفق کامل
tmp = _tf.mkdtemp()
install, pending = _make_update_env(
    tmp, {"app.txt": b"old", "sub/keep.txt": b"keep"},
    {"app.txt": b"new", "sub/extra.txt": b"extra"})
rc = _ua.apply_update(install, pending, _dead_pid, "CCTV_CMS.exe",
                      log_file=install / "update.log", relaunch=False)
check("updater rc==0", rc == 0, str(rc))
check("updater replaced file", (install / "app.txt").read_bytes() == b"new")
check("updater added file", (install / "sub" / "extra.txt").read_bytes() == b"extra")
check("updater kept untouched", (install / "sub" / "keep.txt").read_bytes() == b"keep")
check("updater backup old", (install / "backup" / "v2.0.7-beta" / "app.txt").read_bytes() == b"old")
check("updater version.txt", (install / "version.txt").read_text(encoding="ascii") == "2.0.8-beta")
check("updater pending removed", not pending.exists())
_log = (install / "update.log").read_text(encoding="utf-8")
check("updater handshake line", "updater started" in _log)
check("updater ok line", "update to v2.0.8-beta OK" in _log)
_shutil.rmtree(tmp, ignore_errors=True)

# عدم تطابق sha256 -> کد ۵
tmp = _tf.mkdtemp()
install, pending = _make_update_env(tmp, {"app.txt": b"old"}, {"app.txt": b"new"}, bad_hash=True)
rc = _ua.apply_update(install, pending, _dead_pid, "CCTV_CMS.exe", relaunch=False)
check("updater bad hash rc==5", rc == 5, str(rc))
_shutil.rmtree(tmp, ignore_errors=True)

# نبود update_info.json -> کد ۳
tmp = _tf.mkdtemp()
install = _Path(tmp) / "install"
install.mkdir()
pending = install / "pending_update"
pending.mkdir()
rc = _ua.apply_update(install, pending, _dead_pid, "CCTV_CMS.exe", relaunch=False)
check("updater missing info rc==3", rc == 3, str(rc))
_shutil.rmtree(tmp, ignore_errors=True)

# مسیر ناامن مسدود شود
check("safe_rel traversal", _ua._safe_rel("../../etc/passwd") == "")
check("safe_rel backslash traversal", _ua._safe_rel("..\\..\\x") == "")
check("safe_rel normal", _ua._safe_rel("sub/app.txt") == "sub/app.txt".replace("/", os.sep))

# main() با آرگومان کامل (relaunch واقعی با exe جعلی اجرایی)
tmp = _tf.mkdtemp()
install, pending = _make_update_env(tmp, {"a.txt": b"1"}, {"a.txt": b"2"})
fake_exe = install / "CCTV_CMS.exe"
fake_exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
fake_exe.chmod(0o755)
rc = _ua.main(["prog", "--apply-update", str(install), str(pending),
               str(_dead_pid), "CCTV_CMS.exe"])
check("updater main() rc==0", rc == 0, str(rc))
check("updater main() replaced", (install / "a.txt").read_bytes() == b"2")
check("updater main() bad usage", _ua.main(["prog", "--apply-update"]) == 2)
_shutil.rmtree(tmp, ignore_errors=True)

# cleanup در حالت غیر-frozen کاری نکند
check("cleanup not frozen", _ua.cleanup_updater_copy() is False)

# update_apply نباید هیچ ایمپورتی از Qt داشته باشد (سبک بماند)
_src = (_REPO / "update_apply.py").read_text(encoding="utf-8")
check("update_apply no Qt import", "PyQt" not in _src and "PySide" not in _src)

# main.py باید --apply-update را قبل از importهای سنگین رهگیری کند
_msrc = (_REPO / "main.py").read_text(encoding="utf-8")
_i_flag = _msrc.find("--apply-update")
check("main.py intercepts --apply-update",
      _i_flag != -1 and _i_flag < _msrc.find("import cv2")
      and _i_flag < _msrc.find("from PyQt6"))

# updater.py دیگر هیچ ارجاع «کدی» به updater.ps1/powershell نداشته باشد
# (اشاره‌ی توضیحی در کامنت‌ها اشکالی ندارد)
_usrc = (_REPO / "updater.py").read_text(encoding="utf-8")
check("updater.py no ps1 literal", '"updater.ps1"' not in _usrc)
check("updater.py no powershell literal", '"powershell"' not in _usrc.lower()
      and "'powershell'" not in _usrc.lower())
check("updater.py uses update_apply", "update_apply" in _usrc)

# ---------- ۷) دکمه‌ی «حذف نصب» در صفحه‌ی تنظیمات (2.0.9-beta بازسازی) ----------
import settings_page as _sp
_spage = _sp.SettingsPage()
check("settings has uninstall btn",
      hasattr(_spage, "uninstall_btn") and _spage.uninstall_btn.isVisibleTo(_spage)
      and _spage.uninstall_btn.text() != "")
# در این محیط uninstall.exe کنار پایتون نیست → مسیر None و بدون کرش
check("uninstall path missing -> None", _spage._uninstall_exe_path() is None)
_calls = []
_orig_info = _sp.QMessageBox.information
_sp.QMessageBox.information = lambda *a, **k: _calls.append(("info", a, k)) or None
try:
    _spage._on_uninstall_clicked()
finally:
    _sp.QMessageBox.information = _orig_info
check("uninstall missing shows info", len(_calls) == 1 and _calls[0][0] == "info")
# با فایل جعلی uninstall.exe مسیر پیدا شود (بدون اجرای واقعی)
_fake_dir = _tf.mkdtemp(prefix="fake_uninst_")
open(os.path.join(_fake_dir, "uninstall.exe"), "wb").write(b"x")
_orig_exe = sys.executable
sys.executable = os.path.join(_fake_dir, "CCTV_CMS.exe")
try:
    _found = _spage._uninstall_exe_path()
finally:
    sys.executable = _orig_exe
check("uninstall path found", _found == os.path.join(_fake_dir, "uninstall.exe"))
_shutil.rmtree(_fake_dir, ignore_errors=True)

# در این نسخه‌ی 2.0.9-beta فقط uninstall.exe داخل setup است — فایل مستقل
# IAS-CMS-Uninstall نباید در هیچ workflow ساخته/آپلود شود
for _wf in ["build.yml", ".github/workflows/build.yml"]:
    _wtxt = (_REPO / _wf).read_text(encoding="utf-8")
    check(f"{_wf} no UNINSTALLER_ONLY", "UNINSTALLER_ONLY" not in _wtxt)
    check(f"{_wf} no Uninstall artifact", "IAS-CMS-Uninstall" not in _wtxt)
# ولی uninstall.exe داخل NSI (WriteUninstaller) و میان‌بر «حذف برنامه» باشد
_nsi = (_REPO / "installer" / "installer.nsi").read_text(encoding="utf-8")
check("nsi WriteUninstaller", "WriteUninstaller" in _nsi)
check("nsi uninstall shortcut", "حذف برنامه" in _nsi)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

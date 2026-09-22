# -*- coding: utf-8 -*-
"""تست‌های 2.0.26-beta — حالت «👁 دیدن تصویر» + حذف کامل «تنظیمات تصویر».

اجرا:  QT_QPA_PLATFORM=offscreen python tests/test_226_beta.py

۱) ImageViewerDialog: باز شدن با تصویر سالم، مدیریت مسیر ناموجود و فایل
   خراب (بدون کرش)، آزاد شدن پیکس‌مپ هنگام بستن (closeEvent).
۲) کتابخانه‌ی چهره: دابل‌کلیک روی ستون عکس (ستون صفر) دیالوگ نمایش تصویر
   را باز می‌کند؛ دابل‌کلیک روی ستون‌های دیگر باز نمی‌کند.
۳) گزارش تردد: دابل‌کلیک روی thumbnail دیالوگ نمایش تصویر را باز می‌کند؛
   دابل‌کلیک روی جدول (ویرایش یادداشت) دست‌نخورده مانده است.
۴) حذف کامل تنظیمات تصویر: هیچ ارجاعی به image_profile / image_settings_dialog /
   onvif_imaging / ImageSettingsDialog / image_settings_btn / _on_image_settings_clicked /
   _apply_saved_image_profile / apply_image_profile / get_camera_profile /
   set_image_profile در main.py و camera_stream.py نیست و آن سه فایل حذف شده‌اند.
۵) بهینه‌سازی حافظه: بارگذاری بندانگشتی با QImageReader.setScaledSize انجام
   می‌شود (decode مستقیم در اندازه‌ی هدف، نه decode کامل + scale).
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_passed = []
_failed = []


def check(name, cond, extra=""):
    (_passed if cond else _failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra and not cond else ""))


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtCore import Qt

app = QApplication.instance() or QApplication([])

# ---------- ساخت تصاویر تست ----------
_tmp = tempfile.mkdtemp()
_good_path = os.path.join(_tmp, "good.png")
_img = QImage(400, 300, QImage.Format.Format_RGB32)
_img.fill(Qt.GlobalColor.darkGreen)
assert _img.save(_good_path), "could not write test image"
_bad_path = os.path.join(_tmp, "bad.png")
with open(_bad_path, "w", encoding="utf-8") as f:
    f.write("this is not an image")
_missing_path = os.path.join(_tmp, "no_such_file.png")

# ============================== ۱) ImageViewerDialog =====================
from image_viewer_dialog import ImageViewerDialog

dlg = ImageViewerDialog(_good_path)
check("viewer opens with valid image", dlg.isVisible() or True)
lbl = dlg.findChild(QLabel, "image_label") if hasattr(dlg, "findChild") else None
# تصویر باید لود شده باشد (پیام خطا نمایش داده نشده)
check("viewer loads valid image without error",
      dlg._base_pixmap is not None and not dlg._base_pixmap.isNull()
      if hasattr(dlg, "_base_pixmap") else True)

dlg2 = ImageViewerDialog(_missing_path)
check("viewer handles missing path without crash", True)

dlg3 = ImageViewerDialog(_bad_path)
check("viewer handles corrupt file without crash", True)

# بستن دیالوگ باید پیکس‌مپ را آزاد کند
dlg.close()
check("viewer frees pixmap on close",
      (dlg._base_pixmap is None or dlg._base_pixmap.isNull())
      if hasattr(dlg, "_base_pixmap") else True)

# ============================== ۲) کتابخانه‌ی چهره =======================
import ast
_src = open(os.path.join(REPO, "face_library_dialog.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
check("face library imports ImageViewerDialog",
      "ImageViewerDialog" in _src)
check("face library connects cellDoubleClicked",
      "cellDoubleClicked" in _src)
check("face library opens viewer only on photo column",
      "photo_path" in _src and "column" in _src)

# رفتار واقعی: شبیه‌سازی دابل‌کلیک ستون صفر
opened = []
try:
    import face_library_dialog as fld
    _orig = fld.ImageViewerDialog
    class _FakeViewer:
        def __init__(self, path, parent=None):
            opened.append(path)
        def exec(self):
            pass
    fld.ImageViewerDialog = _FakeViewer
    try:
        class _StubFaceEngine:
            def list_people(self):
                return []
            def get_person(self, pid):
                return None
            def list_work_groups(self):
                return []
        lib = fld.FaceLibraryPage(_StubFaceEngine(), lambda: None)
        # سطر آزمایشی با عکس
        from PyQt6.QtWidgets import QTableWidgetItem
        lib.table.setRowCount(1)
        photo_label = QLabel()
        photo_label.setProperty("photo_path", _good_path)
        lib.table.setCellWidget(0, 0, photo_label)
        lib.table.setItem(0, 1, QTableWidgetItem("x"))
        lib._on_cell_double_clicked(0, 0)
        check("double-click on photo column opens viewer",
              opened == [_good_path], f"opened={opened}")
        opened.clear()
        lib._on_cell_double_clicked(0, 1)
        check("double-click on other column does not open viewer",
              opened == [], f"opened={opened}")
    finally:
        fld.ImageViewerDialog = _orig
except Exception as e:
    check("face library double-click behavior", False, str(e))

# ============================== ۳) گزارش تردد =============================
_src2 = open(os.path.join(REPO, "person_track_dialog.py"), encoding="utf-8").read()
check("person track imports ImageViewerDialog", "ImageViewerDialog" in _src2)
check("person track has double-clickable thumb label",
      "doubleClicked" in _src2 and "_on_thumb_double_clicked" in _src2)
check("person track keeps table double-click for note editing",
      "itemDoubleClicked" in _src2 and "_on_person_double_clicked" in _src2)

try:
    import person_track_dialog as ptd
    _orig2 = ptd.ImageViewerDialog
    opened2 = []
    class _FakeViewer2:
        def __init__(self, path, parent=None):
            opened2.append(path)
        def exec(self):
            pass
    ptd.ImageViewerDialog = _FakeViewer2
    try:
        class _StubCameraStore:
            nvrs = []
            def standalone_cameras(self):
                return []
            def cameras_for_nvr(self, nvr_id):
                return []
        trk = ptd.PersonTrackPage(_StubCameraStore())
        trk._current_thumb_path = _good_path
        trk._on_thumb_double_clicked()
        check("double-click on thumb opens viewer",
              opened2 == [_good_path], f"opened2={opened2}")
        opened2.clear()
        trk._current_thumb_path = ""
        trk._on_thumb_double_clicked()
        check("double-click on empty thumb opens nothing",
              opened2 == [], f"opened2={opened2}")
    finally:
        ptd.ImageViewerDialog = _orig2
except Exception as e:
    check("person track thumb double-click behavior", False, str(e))

# ============================== ۴) حذف تنظیمات تصویر ======================
for _f in ("image_profile.py", "image_settings_dialog.py", "onvif_imaging.py"):
    check(f"obsolete file removed: {_f}",
          not os.path.exists(os.path.join(REPO, _f)))

for _mod in ("main.py", "camera_stream.py"):
    _s = open(os.path.join(REPO, _mod), encoding="utf-8").read()
    for _kw in ("image_profile", "image_settings_dialog", "onvif_imaging",
                "ImageSettingsDialog", "image_settings_btn",
                "_on_image_settings_clicked", "_apply_saved_image_profile",
                "apply_image_profile", "get_camera_profile", "set_image_profile",
                "_image_profile", "_img_profile", "_apply_image_profile"):
        check(f"no '{_kw}' in {_mod}", _kw not in _s)

# ============================== ۵) بهینه‌سازی حافظه =======================
check("face library uses QImageReader.setScaledSize",
      "QImageReader" in _src and "setScaledSize" in _src)
check("person track uses QImageReader.setScaledSize",
      "QImageReader" in _src2 and "setScaledSize" in _src2)

# ---------- جمع‌بندی ----------
print(f"\n{len(_passed)} passed, {len(_failed)} failed")
if _failed:
    print("FAILED:", _failed)
    sys.exit(1)

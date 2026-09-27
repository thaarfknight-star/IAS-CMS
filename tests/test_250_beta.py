"""تست 2.0.61-beta (رگرسیون کرش استارتاپ):
NameError: name 'cam_item' is not defined در reload_camera_list —
خطای مانده از بازنویسی درخت برای گروه‌بندی (همان کلاس باگ 2.0.59:
بعد از جابه‌جایی بلوک، خطی به متغیر حذف‌شده ارجاع می‌داد).

راهبرد: main با استاب وابستگی‌های سنگین ایمپورت می‌شود؛ سپس
MainWindow.reload_camera_list روی یک self جعلی با CameraStore واقعی
اجرا می‌شود — با QTreeWidgetItem واقعی ولی بدون QApplication.
اگر خطی به متغیر تعریف‌نشده ارجاع بدهد، همین‌جا NameError می‌گیریم
(دقیقاً همان کرشی که روی سیستم طه رخ داد).
"""
import io
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _catchall(name):
    m = types.ModuleType(name)
    m.__getattr__ = lambda attr: (lambda *a, **k: None)
    return m


for _n in ("cv2", "face_recognition", "numpy"):
    sys.modules[_n] = _catchall(_n)

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QTreeWidgetItem  # noqa: E402

import main as app_main  # noqa: E402
from camera_store import CameraStore  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeTree:
    def __init__(self):
        self.items = []

    def clear(self):
        self.items = []

    def addTopLevelItem(self, item):
        self.items.append(item)


class _FakeWindow:
    # متدهای واقعی ساخت آیتم درخت (بدون بقیه‌ی پنجره)
    _make_nvr_tree_item = app_main.MainWindow._make_nvr_tree_item
    _make_camera_tree_item = app_main.MainWindow._make_camera_tree_item

    def __init__(self, store):
        self.camera_list = _FakeTree()
        self.camera_store = store

    def _refresh_fire_panel_visibility(self):
        pass

    def _net_badge(self, cam):
        return "", None


def _make_store():
    tmp = tempfile.mkdtemp(prefix="ias_test_250_")
    return CameraStore(path=os.path.join(tmp, "cameras.json"),
                       nvr_path=os.path.join(tmp, "nvrs.json"))


def _reload(store):
    w = _FakeWindow(store)
    app_main.MainWindow.reload_camera_list(w)
    return w.camera_list.items


def _udata(item):
    return item.data(0, Qt.ItemDataRole.UserRole)


def test_reload_no_groups_no_crash(tmp_path):
    # سناریوی دقیق کرش طه: دوربین‌های مستقل بدون گروه
    store = _make_store()
    store.add_camera("c1", "192.168.1.10", "554", "a", "x", "/p")
    store.add_camera("c2", "192.168.1.11", "554", "a", "x", "/p")
    nvr = store.add_nvr(name="n1", ip="192.168.1.20", rtsp_port="554",
                        onvif_port="80", user="a", pwd="x")
    store.add_camera("ch1", "192.168.1.20", "554", "a", "x", "/p",
                     nvr_id=nvr["id"], channel=1)
    items = _reload(store)  # قبل از فیکس: NameError: cam_item
    types = [_udata(it)["type"] for it in items]
    assert types == ["nvr", "camera", "camera"], types
    nvr_item = items[0]
    assert nvr_item.childCount() == 1
    assert _udata(nvr_item.child(0))["type"] == "camera"


def test_reload_empty_store_no_crash(tmp_path):
    items = _reload(_make_store())
    assert items == []


def test_reload_groups_structure(tmp_path):
    store = _make_store()
    c1 = store.add_camera("c1", "192.168.1.10", "554", "a", "x", "/p",
                          group="پارکینگ")
    c2 = store.add_camera("c2", "192.168.1.11", "554", "a", "x", "/p")
    nvr = store.add_nvr(name="n1", ip="192.168.1.20", rtsp_port="554",
                        onvif_port="80", user="a", pwd="x", group="پارکینگ")
    items = _reload(store)
    top = [(_udata(it)["type"], _udata(it).get("name")) for it in items]
    assert top == [("group", "پارکینگ"), ("camera", None)], top
    folder = items[0]
    assert "📁 پارکینگ" in folder.text(0)
    child_types = sorted(_udata(folder.child(i))["type"]
                         for i in range(folder.childCount()))
    assert child_types == ["camera", "nvr"], child_types
    child_ids = {_udata(folder.child(i))["id"] for i in range(folder.childCount())}
    assert child_ids == {c1["id"], nvr["id"]}
    # دوربین بدون گروه در سطح بالا مانده
    assert _udata(items[1])["id"] == c2["id"]


def test_no_undefined_names_in_changed_files(tmp_path):
    # لایه‌ی دوم دفاع: pyflakes نباید «undefined name» در فایل‌های
    # تغییریافته گزارش بدهد (دقیقاً همان چیزی که کرش را می‌ساخت).
    try:
        from pyflakes import api, reporter
    except ImportError:
        print("SKIP: pyflakes نصب نیست")
        return
    files = ["main.py", "camera_store.py", "add_camera_dialog.py",
             "add_nvr_dialog.py", "alarm_sound.py", "settings_page.py",
             "video_loss.py", "plate_direction.py"]
    out, err = io.StringIO(), io.StringIO()
    rep = reporter.Reporter(out, err)
    total = 0
    for f in files:
        total += api.checkPath(os.path.join(REPO_ROOT, f), rep)
    text = out.getvalue() + err.getvalue()
    assert "undefined name" not in text, f"نام تعریف‌نشده پیدا شد:\n{text}"

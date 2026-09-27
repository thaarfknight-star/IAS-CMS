# -*- coding: utf-8 -*-
"""
تست‌های رگرسیون 2.0.63-beta — کادر ثبت لیست تحت‌نظر + ثبت پلاک از رویداد.

1. ماژول plate_library_dialog بدون خطا ایمپورت می‌شود (Signal/pyqtSignal).
2. کلاس PlateSegmentInput وجود دارد و PlateFormDialog و WatchlistFormDialog
   هر دو از آن استفاده می‌کنند (نه دو پیاده‌سازی جدا).
3. PlateEventDetailDialog دکمه‌ی «ثبت در لیست سیاه» و متد add_to_watchlist دارد.
4. PlateLibraryPage متدهای watchlist_selected_event_plate و _open_watchlist_form
   دارد و ارجاعی به فرم خطی قدیمی (watch_plate/watch_kind/watch_note یا
   _add_watchlist_entry) باقی نمانده است.
5. ثبت در لیست سیاه روی plate_store واقعی: add_watchlist_entry ->
   find_watchlist پیدا می‌کند؛ ثبت تکراری دوبل نمی‌شود؛ حذف کار می‌کند.
"""
import ast
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# cv2 روی این ماشین نصب نیست — مثل بقیه‌ی تست‌ها stub می‌کنیم.
_cv2 = types.ModuleType("cv2")
_cv2.imread = lambda *a, **k: None
_cv2.resize = lambda f, *a, **k: f
_cv2.cvtColor = lambda f, *a, **k: f
_cv2.INTER_AREA = 1
_cv2.COLOR_BGR2RGB = 4
sys.modules.setdefault("cv2", _cv2)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import plate_library_dialog as pld  # noqa: E402
import plate_store  # noqa: E402


def _classes(src):
    tree = ast.parse(src)
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef)}


def _methods(cls_node):
    return {n.name: n for n in cls_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_module_imports_clean():
    # ایمپورت بدون NameError/ImportError (کاور pyqtSignal در برابر Signal)
    assert hasattr(pld, "PlateSegmentInput")
    assert hasattr(pld, "PlateFormDialog")
    assert hasattr(pld, "WatchlistFormDialog")
    assert hasattr(pld, "PlateEventDetailDialog")
    assert hasattr(pld, "PlateLibraryPage")


def test_segment_input_has_expected_api():
    src = open(pld.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    cls = _classes(tree)["PlateSegmentInput"]
    meths = _methods(cls)
    for m in ("refresh_preview", "current_canonical", "prefill_text",
              "set_from_plate"):
        assert m in meths, f"PlateSegmentInput.{m} missing"
    # سیگنال باید pyqtSignal باشد نه Signal
    sig = [n for n in cls.body
           if isinstance(n, ast.Assign)
           and any(getattr(t, "id", "") == "changed" for t in n.targets)]
    assert sig and "pyqtSignal" in ast.dump(sig[0]), \
        "changed must be a pyqtSignal"
    assert "import Qt, QDate, QTimer, pyqtSignal" in src


def test_form_dialog_uses_shared_widget():
    src = open(pld.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    cls = _classes(tree)["PlateFormDialog"]
    meths = _methods(cls)
    init_src = ast.dump(meths["__init__"])
    assert "PlateSegmentInput" in init_src, \
        "PlateFormDialog must instantiate PlateSegmentInput"
    # تب‌ها و پیش‌نمایش از قبل نباید مستقیماً ساخته شوند
    assert "QTabWidget()" not in init_src, \
        "PlateFormDialog must not build its own tab widget"
    # متدهای تفویضی
    for m in ("_prefill_text", "_update_preview", "_current_canonical"):
        assert m in meths, f"PlateFormDialog.{m} missing"
    src_full = open(pld.__file__, encoding="utf-8").read()
    assert "def _prefill_text(self, text):\n        self.plate_input.prefill_text(text)" in src_full
    assert "def _update_preview(self):\n        self.plate_input.refresh_preview()" in src_full
    assert "def _current_canonical(self):" in src_full \
        and "return self.plate_input.current_canonical()" in src_full
    # بدنه‌ی جابه‌جاشده: نباید متن Regex کپی در _prefill_text مانده باشد
    assert "re.match" not in ast.dump(meths["_prefill_text"])


def test_watchlist_form_dialog_uses_shared_widget():
    src = open(pld.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    cls = _classes(tree)["WatchlistFormDialog"]
    meths = _methods(cls)
    init_src = ast.dump(meths["__init__"])
    assert "PlateSegmentInput" in init_src, \
        "WatchlistFormDialog must use the shared PlateSegmentInput"
    assert "QTabWidget()" not in init_src, \
        "WatchlistFormDialog must not build its own tab widget"
    for m in ("handle_accept", "get_data"):
        assert m in meths, f"WatchlistFormDialog.{m} missing"
    accept_src = ast.dump(meths["handle_accept"])
    assert "current_canonical" in accept_src, \
        "handle_accept must validate via current_canonical"
    # ترکیب‌های سیاه/سفید حفظ شده‌اند
    assert "'black'" in init_src and "'white'" in init_src


def test_event_detail_has_watchlist_button():
    src = open(pld.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    cls = _classes(tree)["PlateEventDetailDialog"]
    meths = _methods(cls)
    assert "add_to_watchlist" in meths, "add_to_watchlist missing"
    init_src = ast.dump(meths["__init__"])
    assert "watchlist_btn" in init_src, "watchlist_btn missing"
    add_src = ast.dump(meths["add_to_watchlist"])
    assert "WatchlistFormDialog" in add_src
    assert "add_watchlist_entry" in add_src
    # دکمه‌ی تعریف پلاک همچنان هست (پسرفت ندارد)
    assert "define_this_plate" in meths


def test_page_wiring_and_no_stale_refs():
    src = open(pld.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    cls = _classes(tree)["PlateLibraryPage"]
    meths = _methods(cls)
    assert "watchlist_selected_event_plate" in meths
    assert "_open_watchlist_form" in meths
    for stale in ("watch_plate", "watch_kind", "watch_note",
                  "_add_watchlist_entry"):
        assert stale not in src, f"stale reference remains: {stale}"
    wl_src = ast.dump(meths["watchlist_selected_event_plate"])
    assert "WatchlistFormDialog" in wl_src and "add_watchlist_entry" in wl_src
    open_src = ast.dump(meths["_open_watchlist_form"])
    assert "WatchlistFormDialog" in open_src and "_refresh_watchlist" in open_src


def test_watchlist_store_flow():
    store = plate_store.plate_store
    canon = plate_store.normalize_plate_text("12ب345 ایران11")
    assert canon, "normalize must produce a canonical plate"
    store.remove_watchlist_entry(canon, "black")
    store.remove_watchlist_entry(canon, "white")
    # ثبت سیاه -> پیدا می‌شود
    store.add_watchlist_entry(canon, "black", "تست رگرسیون 2.0.63")
    found = store.find_watchlist(canon)
    assert isinstance(found, list) and len(found) == 1 \
        and found[0].get("kind") == "black", \
        f"find_watchlist did not return the black entry: {found}"
    # ثبت تکراریِ هم‌نوع: آپدیت می‌شود نه دوبل (یادداشت جدید می‌نشیند)
    store.add_watchlist_entry(canon, "black", "بازنویسی تست")
    rows = store.list_watchlist()
    mine = [r for r in rows if r.get("plate_text") == canon
            and r.get("kind") == "black"]
    assert len(mine) == 1, f"duplicate watchlist rows: {mine}"
    assert mine[0]["note"] == "بازنویسی تست"
    # حذف
    store.remove_watchlist_entry(canon, "black")
    assert store.find_watchlist(canon) == []

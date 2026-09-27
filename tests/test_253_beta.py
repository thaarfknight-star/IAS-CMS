# -*- coding: utf-8 -*-
"""tests/test_253_beta.py — رگرسیون 2.0.65-beta.

۱) دکمه‌ی «افزودن دوربین تکی» به «افزودن دوربین» تغییر نام داده.
۲) باگ «افزودن دستی به لیست سیاه اصلا کار نمی‌کنه»: متد
   PlateSegmentInput.refresh_preview به self._current_canonical (ناموجود)
   صدا می‌زد و ساخت ویجت با AttributeError می‌ترکید؛ پس هیچ‌کدام از دو
   دیالوگ (تعریف پلاک / ثبت لیست تحت‌نظر) باز نمی‌شد. حالا باید
   current_canonical صدا زده شود.
۳) ارور ONVIF «No such file»: پکیج onvif_zeep فایل‌های WSDL را نصب نمی‌کند
   و --collect-all هم پوشه‌ی wsdl (همسایه‌ی پکیج) را باندل نمی‌کند؛ پس
   WSDLها در assets/onvif_wsdl وندور شدند و هر دو فراخوانی ONVIFCamera
   باید wsdl_dir صریح بگیرند.
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def _parse(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return ast.parse(f.read())


def test_add_camera_button_renamed():
    src = open(os.path.join(REPO, "main.py"), encoding="utf-8").read()
    assert 'QPushButton("افزودن دوربین")' in src
    assert "افزودن دوربین تکی" not in src


def test_plate_segment_input_refresh_calls_existing_method():
    tree = _parse("plate_library_dialog.py")
    widget = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PlateSegmentInput":
            widget = node
            break
    assert widget is not None
    methods = {n.name for n in widget.body if isinstance(n, ast.FunctionDef)}
    assert "current_canonical" in methods
    assert "_current_canonical" not in methods, (
        "PlateSegmentInput نباید متد _current_canonical داشته باشد")
    # refresh_preview باید current_canonical را صدا بزند، نه _current_canonical
    calls = set()
    for node in ast.walk(widget):
        if isinstance(node, ast.FunctionDef) and node.name == "refresh_preview":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute):
                    calls.add(sub.attr)
    assert "current_canonical" in calls
    assert "_current_canonical" not in calls


def test_onvif_wsdl_vendored():
    from app_paths import get_onvif_wsdl_dir
    d = get_onvif_wsdl_dir()
    assert os.path.isdir(d), d
    # همان چکی که onvif/client.py در get_definition می‌کند:
    # wsdlpath = os.path.join(wsdl_dir, wsdl_file) باید فایل باشد
    for must in ("devicemgmt.wsdl", "media.wsdl"):
        wsdlpath = os.path.join(d, must)
        assert os.path.isfile(wsdlpath), wsdlpath
    # کل ست WSDL از sdist رسمی وندور شده، نه فقط دو فایل
    wsdls = [f for f in os.listdir(d) if f.endswith(".wsdl")]
    assert len(wsdls) >= 15, f"فقط {len(wsdls)} فایل wsdl وندور شده"


def test_onvif_camera_gets_wsdl_dir():
    for mod in ("bandwidth.py", "nvr_scanner.py"):
        tree = _parse(mod)
        found = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ONVIFCamera"):
                kws = {kw.arg for kw in node.keywords}
                assert "wsdl_dir" in kws, f"{mod}: ONVIFCamera بدون wsdl_dir"
                found = True
        assert found, f"{mod}: فراخوانی ONVIFCamera پیدا نشد"

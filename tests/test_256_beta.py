# -*- coding: utf-8 -*-
"""تست 2.0.68-beta: کنترل PTZ/لنز موتورایزد (ONVIF)."""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def test_ptz_module_api():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ptz_control", os.path.join(REPO, "ptz_control.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.detect_ptz_support)
    assert callable(mod.describe_support)
    assert isinstance(mod.COMMON_ONVIF_PORTS, list) and 80 in mod.COMMON_ONVIF_PORTS
    for m in ("continuous_move", "stop", "relative_move", "get_presets",
              "goto_preset", "set_preset", "remove_preset", "goto_home",
              "focus_continuous", "focus_stop"):
        assert hasattr(mod.PTZController, m), f"PTZController.{m} نیست"


def test_detect_no_ip_shape():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ptz_control", os.path.join(REPO, "ptz_control.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    info = mod.detect_ptz_support({"ip": "", "user": "", "pass": ""})
    assert info["supported"] is False
    for k in ("pan", "tilt", "zoom", "focus", "presets", "home",
              "node_token", "profile_token", "onvif_port", "error"):
        assert k in info, f"کلید {k} در خروجی detect نیست"


def test_describe_support_texts():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ptz_control", os.path.join(REPO, "ptz_control.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    full = {"supported": True, "pan": True, "tilt": True, "zoom": True,
            "focus": True, "presets": True, "home": True}
    assert "دوربین PTZ" in mod.describe_support(full)
    assert "پریست" in mod.describe_support(full)
    zoom_only = {"supported": True, "pan": False, "tilt": False, "zoom": True,
                 "focus": True, "presets": False, "home": False}
    assert "لنز موتورایزد" in mod.describe_support(zoom_only)
    assert "شناسایی نشد" in mod.describe_support(
        {"supported": False, "error": "x"})


def test_ptz_wsdl_vendored():
    assert os.path.isfile(os.path.join(REPO, "assets", "onvif_wsdl", "ptz.wsdl"))
    assert os.path.isfile(os.path.join(REPO, "assets", "onvif_wsdl", "imaging.wsdl"))


def test_camera_store_ptz_field():
    src = _read("camera_store.py")
    assert '"ptz"' in src, "فیلد ptz در camera_store نیست"
    assert "ptz=None" in src, "پارامتر ptz در add_camera نیست"


def test_main_ptz_wiring():
    src = _read("main.py")
    assert "کنترل PTZ" in src, "آیتم منوی کنترل PTZ در main.py نیست"
    assert "def open_ptz_control" in src
    assert "def _autodetect_ptz_async" in src
    assert "_autodetect_ptz_async(cam[\"id\"])" in src, \
        "شناسایی خودکار PTZ پس از افزودن دوربین وصل نیست"


def test_ptz_dialog_structure():
    src = _read("ptz_dialog.py")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "PTZDialog")
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    for m in ("_make_dpad", "_make_hold_button", "_refresh_presets",
              "_goto_selected_preset", "_add_preset", "_delete_selected_preset",
              "_retry_detect"):
        assert m in methods, f"PTZDialog.{m} نیست"
    # صفحه‌جهت ۳×۳ و اسلایدر سرعت باید باشند
    assert "↖" in src and "↘" in src
    assert "QSlider" in src
    assert "QThread" in src, "فراخوانی شبکه باید در ترد جدا باشد"

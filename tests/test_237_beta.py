# -*- coding: utf-8 -*-
"""تست‌های 2.0.37-beta — پوشش نقاط رسم‌شده‌ی مسیر + به‌روزرسانی زنده حین درگ.

- lane_coverage: build_camera_sectors و analyze_waypoint_coverage
- building_map_dialog (بازبینی ساختاری با AST — محیط headless بدون Qt):
  DeviceItem حین درگ moved_live را صدا می‌زند؛ _refresh_coverage_live
  cam_override می‌پذیرد؛ نقاط رسم‌شده در _draw_lane_coverage رسم می‌شوند.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lane_coverage import (  # noqa: E402
    analyze_waypoint_coverage,
    build_camera_sectors,
    point_in_sector,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DLG_SRC = open(os.path.join(REPO, "building_map_dialog.py"),
               encoding="utf-8").read()


def _cams():
    return build_camera_sectors(
        [{"name": "C1", "x": 0.0, "y": 0.0, "angle": 0.0,
          "fov": 90.0, "view_distance": 10.0}], to_meter=1.0)


def test_01_sector_geometry():
    c = _cams()[0]
    kw = dict(cx=c["x"], cy=c["y"], angle_deg=c["angle"], fov_deg=c["fov"],
              range_scene=c["range_scene"])
    assert point_in_sector(5, 0, **kw) is True      # روبه‌رو
    assert point_in_sector(-5, 0, **kw) is False    # پشت دوربین
    assert point_in_sector(0, 8, **kw) is False     # بیرون از FOV
    assert point_in_sector(50, 0, **kw) is False    # بیرون از برد
    assert point_in_sector(0, 0, **kw) is True      # روی خود دوربین
    print("PASS 01: point_in_sector geometry")


def test_02_waypoint_coverage():
    wps = analyze_waypoint_coverage([(5, 0), (-5, 0), (0, 8)], _cams())
    assert len(wps) == 3
    assert wps[0]["covered_by"] == ["C1"] and wps[0]["index"] == 0
    assert wps[1]["covered_by"] == [] and wps[2]["covered_by"] == []
    print("PASS 02: waypoint covered/uncovered")


def test_03_waypoint_empty_and_bad_input():
    assert analyze_waypoint_coverage([], _cams()) == []
    assert analyze_waypoint_coverage(None, _cams()) == []
    wps = analyze_waypoint_coverage([("bad", 1), (3, 4)], _cams())
    assert len(wps) == 1 and wps[0]["x"] == 3.0
    print("PASS 03: waypoint edge inputs")


def test_04_waypoint_respects_fov_narrow():
    cams = build_camera_sectors(
        [{"name": "C2", "x": 0, "y": 0, "angle": 90,
          "fov": 30, "view_distance": 10}], 1.0)
    wps = analyze_waypoint_coverage([(0, -5), (5, -5)], cams)
    # زاویه ۹۰ با وارونگی y صحنه یعنی رو به بالا (y منفی)
    assert wps[0]["covered_by"] == ["C2"], wps
    assert wps[1]["covered_by"] == [], wps
    print("PASS 04: narrow FOV + angle convention")


def _tree():
    return ast.parse(DLG_SRC)


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def test_05_device_item_live_drag():
    tree = _tree()
    src = DLG_SRC
    # فلگ ItemSendsGeometryChanges برای دریافت ItemPositionHasChanged
    assert "ItemSendsGeometryChanges" in src
    item_change = _find_func(tree, "itemChange")
    assert item_change is not None
    dump = ast.dump(item_change)
    assert "moved_live" in dump, "itemChange باید moved_live را صدا بزند"
    assert "ItemPositionHasChanged" in dump
    assert "_dragging" in dump, "فقط حین درگ باید زنده رفرش شود"
    print("PASS 05: DeviceItem live-drag wiring")


def test_06_moved_live_handler():
    tree = _tree()
    fn = _find_func(tree, "_on_device_moved_live")
    assert fn is not None, "_on_device_moved_live باید وجود داشته باشد"
    dump = ast.dump(fn)
    assert "cam_override" in dump
    # throttling برای سبک ماندن حین درگ
    assert "0.08" in dump or "_live_move_ts" in dump
    print("PASS 06: _on_device_moved_live with throttle")


def test_07_refresh_accepts_override():
    tree = _tree()
    fn = _find_func(tree, "_refresh_coverage_live")
    assert fn is not None
    args = [a.arg for a in fn.args.args] + \
           [a.arg for a in fn.args.kwonlyargs]
    assert "cam_override" in args, \
        "_refresh_coverage_live باید cam_override بپذیرد"
    dump = ast.dump(fn)
    assert "analyze_waypoint_coverage" in dump
    assert "build_camera_sectors" in dump
    print("PASS 07: _refresh_coverage_live override + waypoints")


def test_08_draw_waypoints():
    tree = _tree()
    fn = _find_func(tree, "_draw_lane_coverage")
    assert fn is not None
    dump = ast.dump(fn)
    assert "waypoints" in dump, "نقاط رسم‌شده باید رسم شوند"
    assert "setToolTip" in dump, "تولتیپ نقطه باید باشد"
    print("PASS 08: _draw_lane_coverage draws waypoints")


def test_09_report_waypoint_summary():
    tree = _tree()
    fn = _find_func(tree, "_render_coverage_report")
    assert fn is not None
    assert "waypoints" in ast.dump(fn), "گزارش باید خلاصه‌ی نقاط رسم‌شده داشته باشد"
    print("PASS 09: report includes waypoint summary")


def test_10_both_callback_dicts_wired():
    count = DLG_SRC.count('"moved_live": self._on_device_moved_live')
    assert count == 2, f"هر دو دیکشنری کال‌بک باید moved_live داشته باشند ({count})"
    print("PASS 10: both DeviceItem callback dicts wired")


if __name__ == "__main__":
    test_01_sector_geometry()
    test_02_waypoint_coverage()
    test_03_waypoint_empty_and_bad_input()
    test_04_waypoint_respects_fov_narrow()
    test_05_device_item_live_drag()
    test_06_moved_live_handler()
    test_07_refresh_accepts_override()
    test_08_draw_waypoints()
    test_09_report_waypoint_summary()
    test_10_both_callback_dicts_wired()
    print("ALL 2.0.37-beta TESTS PASSED (10/10)")

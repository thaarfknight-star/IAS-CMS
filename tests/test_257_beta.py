# -*- coding: utf-8 -*-
"""تست 2.0.69-beta: شناسایی مستقل PTZ و Imaging در detect_ptz_support.

سناریوی واقعی: دوربین‌های لنز موتورایزد ارزان (بردهای OEM) معمولاً سرویس
PTZ ندارند و فوکوس/زومشان فقط از طریق Imaging در دسترس است. قبل از فیکس،
چک Imaging فقط وقتی اجرا می‌شد که نود PTZ پیدا شده باشد و چنین دوربینی
همیشه «پشتیبانی نشد» می‌گرفت.
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import ptz_control


class _FakeSpaces:
    ContinuousPanTiltVelocitySpace = [object()]
    ContinuousZoomVelocitySpace = [object()]


class _FakeNode:
    MaximumNumberOfPresets = 0
    HomeSupported = False
    SupportedPTZSpaces = _FakeSpaces()


class _FakePTZService:
    def __init__(self, nodes):
        self._nodes = nodes

    def GetNodes(self):
        return self._nodes


class _FakeFocus:
    pass


class _FakeOptions:
    def __init__(self, focus):
        if focus:
            self.Focus = _FakeFocus()


class _FakeImaging:
    def __init__(self, focus):
        self._focus = focus

    def GetOptions(self, _req):
        return _FakeOptions(self._focus)


class _FakeSource:
    token = "vs1"


class _FakeMedia:
    def GetVideoSources(self):
        return [_FakeSource()]


class _FakeCam:
    """دوربین ONVIF جعلی: has_ptz / has_focus قابل تنظیم."""

    def __init__(self, has_ptz, has_focus):
        self._has_ptz = has_ptz
        self._has_focus = has_focus

    def create_ptz_service(self):
        if not self._has_ptz:
            raise Exception("no PTZ service")
        return _FakePTZService([_FakeNode()])

    def create_imaging_service(self):
        return _FakeImaging(self._has_focus)

    def create_media_service(self):
        return _FakeMedia()


def _patch(monkeypatch, has_ptz, has_focus):
    monkeypatch.setattr(
        ptz_control, "_new_onvif_camera",
        lambda ip, port, user, pwd: _FakeCam(has_ptz, has_focus))
    monkeypatch.setattr(ptz_control, "_iter_onvif_ports", lambda cam: [80])


def test_motorized_lens_without_ptz_service_detected_via_imaging(monkeypatch):
    """لنز موتورایزد بدون سرویس PTZ: فقط از طریق Imaging شناسایی شود."""
    _patch(monkeypatch, has_ptz=False, has_focus=True)
    info = ptz_control.detect_ptz_support(
        {"ip": "192.168.1.10", "user": "admin", "pass": "x"})
    assert info["supported"] is True
    assert info["focus"] is True
    assert info["pan"] is False and info["tilt"] is False
    assert info["zoom"] is False


def test_camera_without_ptz_and_focus_not_supported(monkeypatch):
    """دوربین ثابت معمولی: نه PTZ نه فوکوس → پشتیبانی نشد."""
    _patch(monkeypatch, has_ptz=False, has_focus=False)
    info = ptz_control.detect_ptz_support(
        {"ip": "192.168.1.10", "user": "admin", "pass": "x"})
    assert info["supported"] is False
    assert info["error"]


def test_full_ptz_still_detected(monkeypatch):
    """دوربین PTZ کامل بدون فوکوس Imaging: از طریق PTZ شناسایی شود."""
    _patch(monkeypatch, has_ptz=True, has_focus=False)
    info = ptz_control.detect_ptz_support(
        {"ip": "192.168.1.10", "user": "admin", "pass": "x"})
    assert info["supported"] is True
    assert info["pan"] is True and info["tilt"] is True
    assert info["zoom"] is True
    assert info["focus"] is False

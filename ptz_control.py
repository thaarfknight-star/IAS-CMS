# -*- coding: utf-8 -*-
"""کنترل PTZ دوربین‌ها از طریق ONVIF (نسخه‌ی 2.0.69-beta).

- `detect_ptz_support(cam)`: شناسایی خودکار پشتیبانی PTZ/لنز موتورایزد
  (چرخش افقی/عمودی، زوم، فوکوس، پریست) با چند پورت کاندید ONVIF.
- `PTZController`: حرکت پیوسته/نسبی، توقف، پریست‌ها، خانه، فوکوس.

همه‌ی فراخوانی‌های شبکه بلاکینگ‌اند؛ صداکننده باید آن‌ها را در ترد جدا
اجرا کند (مثل bandwidth.py). شناسه‌های داخلی/دیتا تغییر نکرده است.
"""

from app_paths import get_onvif_wsdl_dir

COMMON_ONVIF_PORTS = [80, 8080, 8000, 8899, 8008, 8081]


def _iter_onvif_ports(cam):
    """پورت‌های کاندید ONVIF: اول onvif_port، بعد port، بعد پورت‌های رایج."""
    seen = []
    for key in ("onvif_port", "port"):
        try:
            v = int(cam.get(key) or 0)
        except Exception:
            v = 0
        if v > 0 and v not in seen:
            seen.append(v)
            yield v
    for p in COMMON_ONVIF_PORTS:
        if p not in seen:
            yield p


def _cam_ip(cam):
    """برای کانال‌های NVR، آی‌پی واقعی دوربین (camera_ip) ارجح است."""
    return (cam.get("camera_ip") or cam.get("ip") or "").strip()


def _new_onvif_camera(ip, port, user, pwd):
    from onvif import ONVIFCamera
    return ONVIFCamera(ip, int(port), user, pwd,
                       wsdl_dir=get_onvif_wsdl_dir())


def _node_token(node):
    for attr in ("token", "_token"):
        v = getattr(node, attr, None)
        if v:
            return str(v)
    try:
        return str(node["token"])
    except Exception:
        return ""


def _spaces_of(node):
    """نام فضای‌های پشتیبانی‌شده‌ی نود PTZ (رشته)."""
    out = set()
    try:
        spaces = node.SupportedPTZSpaces
    except Exception:
        return out
    for grp in ("AbsolutePanTiltPositionSpace", "RelativePanTiltTranslationSpace",
                "ContinuousPanTiltVelocitySpace",
                "AbsoluteZoomPositionSpace", "RelativeZoomTranslationSpace",
                "ContinuousZoomVelocitySpace",
                "PanTiltSpeedSpace", "ZoomSpeedSpace"):
        try:
            items = getattr(spaces, grp, None) or []
        except Exception:
            items = []
        if items:
            out.add(grp)
    return out


def _try_ptz_node(ptz):
    """تلاش برای خواندن نود PTZ؛ (node, spaces) یا (None, set())."""
    try:
        nodes = ptz.GetNodes() or []
    except Exception:
        return None, set()
    if not nodes:
        return None, set()
    node = nodes[0]
    return node, _spaces_of(node)


def _try_imaging_focus(onvif_cam):
    """بررسی پشتیبانی فوکوس از طریق سرویس Imaging — مستقل از PTZ."""
    try:
        imaging = onvif_cam.create_imaging_service()
        media = onvif_cam.create_media_service()
        sources = media.GetVideoSources() or []
        if not sources:
            return False
        try:
            vsrc_tok = str(sources[0].token)
        except Exception:
            return False
        opts = imaging.GetOptions({"VideoSourceToken": vsrc_tok})
        return getattr(opts, "Focus", None) is not None
    except Exception:
        return False


def _try_profile_token(onvif_cam, node):
    """پروفایل مدیای متناظر با نود PTZ (یا اولین پروفایل)."""
    try:
        media = onvif_cam.create_media_service()
        profiles = media.GetProfiles() or []
    except Exception:
        return None
    node_tok = _node_token(node) if node is not None else ""
    profile_token = None
    for pr in profiles:
        try:
            cfg = pr.PTZConfiguration
        except Exception:
            cfg = None
        if cfg is not None:
            try:
                nt = str(cfg.NodeToken)
            except Exception:
                nt = ""
            if (node_tok and nt == node_tok) or not profile_token:
                try:
                    profile_token = str(pr.token)
                except Exception:
                    profile_token = None
            if node_tok and nt == node_tok:
                break
    if not profile_token and profiles:
        try:
            profile_token = str(profiles[0].token)
        except Exception:
            profile_token = None
    return profile_token
def detect_ptz_support(cam, timeout=10):
    """شناسایی قابلیت PTZ/لنز موتورایزد دوربین.

    PTZ و Imaging مستقل از هم بررسی می‌شوند: دوربین‌های لنز موتورایزد
    ارزان (مثل بردهای OEM) معمولاً سرویس PTZ ندارند و فوکوس/زومشان فقط
    از طریق Imaging در دسترس است.

    خروجی: dict با کلیدهای supported/pan/tilt/zoom/focus/presets/
    home/node_token/profile_token/onvif_port/error
    """
    result = {"supported": False, "pan": False, "tilt": False, "zoom": False,
              "focus": False, "presets": False, "home": False,
              "node_token": None, "profile_token": None, "onvif_port": None,
              "error": None}
    ip = _cam_ip(cam)
    user = cam.get("user", "") or ""
    pwd = cam.get("pass", "") or ""
    if not ip:
        result["error"] = "IP ندارد"
        return result
    last_err = ""
    for port in _iter_onvif_ports(cam):
        try:
            onvif_cam = _new_onvif_camera(ip, port, user, pwd)
            # ۱) سرویس PTZ (اختیاری — ممکن است وجود نداشته باشد)
            node, spaces = None, set()
            try:
                ptz = onvif_cam.create_ptz_service()
                node, spaces = _try_ptz_node(ptz)
            except Exception:
                node, spaces = None, set()
            pan_tilt = bool({"AbsolutePanTiltPositionSpace",
                             "RelativePanTiltTranslationSpace",
                             "ContinuousPanTiltVelocitySpace"} & spaces)
            zoom = bool({"AbsoluteZoomPositionSpace",
                         "RelativeZoomTranslationSpace",
                         "ContinuousZoomVelocitySpace"} & spaces)
            # ۲) فوکوس از طریق Imaging — مستقل از PTZ
            focus = _try_imaging_focus(onvif_cam)
            if not (pan_tilt or zoom or focus):
                last_err = f"پورت {port}: قابلیت PTZ/فوکوس ندارد"
                continue
            try:
                max_presets = int(getattr(node, "MaximumNumberOfPresets", 0) or 0)
            except Exception:
                max_presets = 0
            try:
                home = bool(getattr(node, "HomeSupported", False))
            except Exception:
                home = False
            result.update({
                "supported": True,
                "pan": pan_tilt, "tilt": pan_tilt, "zoom": zoom,
                "focus": focus,
                "presets": max_presets > 0,
                "home": home,
                "node_token": _node_token(node) or None,
                "profile_token": _try_profile_token(onvif_cam, node),
                "onvif_port": int(port),
            })
            return result
        except Exception as e:
            last_err = f"پورت {port}: {str(e)[:70]}"
    result["error"] = last_err or "ONVIF پاسخ نداد"
    return result


class PTZController:
    """کنترلر PTZ یک دوربین. هر متد (ok, message) برمی‌گرداند."""

    def __init__(self, cam):
        self.cam = cam
        self._ptz = None
        self._profile_token = None
        self._port = None

    # ---------------------------------------------------------- اتصال ---
    def _connect(self):
        if self._ptz is not None:
            return True, ""
        ip = _cam_ip(self.cam)
        user = self.cam.get("user", "") or ""
        pwd = self.cam.get("pass", "") or ""
        if not ip:
            return False, "IP ندارد"
        stored = self.cam.get("ptz") or {}
        if stored.get("profile_token"):
            self._profile_token = stored["profile_token"]
        last_err = ""
        for port in _iter_onvif_ports(self.cam):
            try:
                onvif_cam = _new_onvif_camera(ip, port, user, pwd)
                self._ptz = onvif_cam.create_ptz_service()
                # اعتبارسنجی سبک: اگر نود نداشت، پورت بعدی
                if not (self._ptz.GetNodes() or []):
                    self._ptz = None
                    last_err = f"پورت {port}: نود PTZ ندارد"
                    continue
                if not self._profile_token:
                    try:
                        media = onvif_cam.create_media_service()
                        profiles = media.GetProfiles() or []
                        if profiles:
                            self._profile_token = str(profiles[0].token)
                    except Exception:
                        pass
                if not self._profile_token:
                    self._ptz = None
                    last_err = f"پورت {port}: پروفایل پیدا نشد"
                    continue
                self._port = int(port)
                return True, ""
            except Exception as e:
                self._ptz = None
                last_err = f"پورت {port}: {str(e)[:70]}"
        return False, last_err or "ONVIF پاسخ نداد"

    @staticmethod
    def _clamp(v):
        return max(-1.0, min(1.0, float(v)))

    # ----------------------------------------------------------- حرکت ---
    def continuous_move(self, pan=0.0, tilt=0.0, zoom=0.0):
        """حرکت پیوسته با سرعت ۱- تا ۱؛ برای توقف از stop استفاده کنید."""
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("ContinuousMove")
            req.ProfileToken = self._profile_token
            req.Velocity = {
                "PanTilt": {"x": self._clamp(pan), "y": self._clamp(tilt)},
                "Zoom": {"x": self._clamp(zoom)},
            }
            self._ptz.ContinuousMove(req)
            return True, ""
        except Exception as e:
            return False, str(e)[:90]

    def stop(self):
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("Stop")
            req.ProfileToken = self._profile_token
            req.PanTilt = True
            req.Zoom = True
            self._ptz.Stop(req)
            return True, ""
        except Exception as e:
            return False, str(e)[:90]

    def relative_move(self, pan=0.0, tilt=0.0, zoom=0.0):
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("RelativeMove")
            req.ProfileToken = self._profile_token
            req.Translation = {
                "PanTilt": {"x": self._clamp(pan), "y": self._clamp(tilt)},
                "Zoom": {"x": self._clamp(zoom)},
            }
            self._ptz.RelativeMove(req)
            return True, ""
        except Exception as e:
            return False, str(e)[:90]

    # ---------------------------------------------------------- پریست ---
    def get_presets(self):
        """لیست [(token, name)]"""
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("GetPresets")
            req.ProfileToken = self._profile_token
            presets = self._ptz.GetPresets(req) or []
            out = []
            for p in presets:
                try:
                    tok = str(p.token)
                except Exception:
                    continue
                try:
                    name = str(p.Name or tok)
                except Exception:
                    name = tok
                out.append((tok, name))
            return True, out
        except Exception as e:
            return False, str(e)[:90]

    def goto_preset(self, preset_token):
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("GotoPreset")
            req.ProfileToken = self._profile_token
            req.PresetToken = str(preset_token)
            self._ptz.GotoPreset(req)
            return True, ""
        except Exception as e:
            return False, str(e)[:90]

    def set_preset(self, name):
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("SetPreset")
            req.ProfileToken = self._profile_token
            req.PresetName = str(name)
            token = self._ptz.SetPreset(req)
            return True, str(token)
        except Exception as e:
            return False, str(e)[:90]

    def remove_preset(self, preset_token):
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("RemovePreset")
            req.ProfileToken = self._profile_token
            req.PresetToken = str(preset_token)
            self._ptz.RemovePreset(req)
            return True, ""
        except Exception as e:
            return False, str(e)[:90]

    def goto_home(self):
        ok, msg = self._connect()
        if not ok:
            return False, msg
        try:
            req = self._ptz.create_type("GotoHomePosition")
            req.ProfileToken = self._profile_token
            self._ptz.GotoHomePosition(req)
            return True, ""
        except Exception as e:
            return False, str(e)[:90]

    # ---------------------------------------------------------- فوکوس ---
    def focus_continuous(self, speed=0.5):
        """فوکوس پیوسته‌ی لنز موتورایزد (از طریق سرویس Imaging)."""
        ip = _cam_ip(self.cam)
        user = self.cam.get("user", "") or ""
        pwd = self.cam.get("pass", "") or ""
        last_err = ""
        for port in _iter_onvif_ports(self.cam):
            try:
                onvif_cam = _new_onvif_camera(ip, port, user, pwd)
                imaging = onvif_cam.create_imaging_service()
                media = onvif_cam.create_media_service()
                sources = media.GetVideoSources() or []
                vsrc_tok = str(sources[0].token)
                req = imaging.create_type("Move")
                req.VideoSourceToken = vsrc_tok
                req.Focus = {"Continuous": {"Speed": self._clamp(speed)}}
                imaging.Move(req)
                return True, ""
            except Exception as e:
                last_err = str(e)[:80]
        return False, last_err or "سرویس Imaging پاسخ نداد"

    def focus_stop(self):
        ip = _cam_ip(self.cam)
        user = self.cam.get("user", "") or ""
        pwd = self.cam.get("pass", "") or ""
        for port in _iter_onvif_ports(self.cam):
            try:
                onvif_cam = _new_onvif_camera(ip, port, user, pwd)
                imaging = onvif_cam.create_imaging_service()
                media = onvif_cam.create_media_service()
                sources = media.GetVideoSources() or []
                vsrc_tok = str(sources[0].token)
                imaging.Stop({"VideoSourceToken": vsrc_tok})
                return True, ""
            except Exception:
                continue
        return False, "توقف فوکوس ناموفق بود"


def describe_support(info):
    """توضیح فارسی قابلیت‌های شناسایی‌شده برای نمایش در UI."""
    if not info.get("supported"):
        return "پشتیبانی PTZ شناسایی نشد" + \
            (f" ({info.get('error')})" if info.get("error") else "")
    parts = []
    if info.get("pan"):
        parts.append("چرخش افقی/عمودی")
    if info.get("zoom"):
        parts.append("زوم")
    if info.get("focus"):
        parts.append("فوکوس")
    if info.get("presets"):
        parts.append("پریست")
    if info.get("home"):
        parts.append("خانه")
    kind = "دوربین PTZ" if info.get("pan") else "لنز موتورایزد (زوم)"
    return kind + " — " + ("، ".join(parts) if parts else "قابلیت پایه")

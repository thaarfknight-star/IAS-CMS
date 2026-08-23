import json
import os
import uuid


class CameraStore:
    """Persists the list of cameras the user has connected to before,
    each with a custom display name (درب اصلی، حیاط، راهرو و ...).

    از این نسخه به بعد، NVRها هم نگهداری می‌شوند (در فایل جدای nvrs.json).
    هر کانال کشف‌شده از یک NVR، به‌صورت یک «دوربین» عادی در cameras.json ذخیره
    می‌شود (تا کل بقیه‌ی برنامه - پخش زنده، Face Library و ... بدون تغییر با آن
    کار کند)، اما فیلدهای اضافه‌ی nvr_id و channel به آن اضافه می‌شود تا در UI
    زیر همان NVR گروه‌بندی شود.
    """

    def __init__(self, path="cameras.json", nvr_path="nvrs.json"):
        self.path = path
        self.nvr_path = nvr_path
        self.cameras = []
        self.nvrs = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.cameras = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری لیست دوربین‌ها: {e}")
                self.cameras = []
        if os.path.exists(self.nvr_path):
            try:
                with open(self.nvr_path, "r", encoding="utf-8") as f:
                    self.nvrs = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری لیست NVRها: {e}")
                self.nvrs = []

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.cameras, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره لیست دوربین‌ها: {e}")

    def save_nvrs(self):
        try:
            with open(self.nvr_path, "w", encoding="utf-8") as f:
                json.dump(self.nvrs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره لیست NVRها: {e}")

    # ------------------------------------------------------------ cameras --

    def add_camera(self, name, ip, port, user, pwd, path, nvr_id=None, channel=None, full_url=None):
        cam = {
            "id": str(uuid.uuid4()),
            "name": name or ip,
            "ip": ip,
            "port": port,
            "user": user,
            "pass": pwd,
            "path": path,
            "nvr_id": nvr_id,
            "channel": channel,
            "full_url": full_url,
        }
        self.cameras.append(cam)
        self.save()
        return cam

    def update_camera(self, cam_id, **fields):
        for cam in self.cameras:
            if cam["id"] == cam_id:
                cam.update(fields)
                self.save()
                return cam
        return None

    def remove_camera(self, cam_id):
        self.cameras = [c for c in self.cameras if c["id"] != cam_id]
        self.save()

    def get_camera(self, cam_id):
        for cam in self.cameras:
            if cam["id"] == cam_id:
                return cam
        return None

    def cameras_for_nvr(self, nvr_id):
        return [c for c in self.cameras if c.get("nvr_id") == nvr_id]

    def standalone_cameras(self):
        """دوربین‌هایی که به هیچ NVR متصل نیستند (اتصال مستقیم)."""
        return [c for c in self.cameras if not c.get("nvr_id")]

    # --------------------------------------------------------------- nvrs --

    def add_nvr(self, name, ip, rtsp_port, onvif_port, user, pwd, brand=""):
        nvr = {
            "id": str(uuid.uuid4()),
            "name": name or ip,
            "ip": ip,
            "rtsp_port": rtsp_port,
            "onvif_port": onvif_port,
            "user": user,
            "pass": pwd,
            "brand": brand,
        }
        self.nvrs.append(nvr)
        self.save_nvrs()
        return nvr

    def update_nvr(self, nvr_id, **fields):
        for nvr in self.nvrs:
            if nvr["id"] == nvr_id:
                nvr.update(fields)
                self.save_nvrs()
                return nvr
        return None

    def remove_nvr(self, nvr_id, cascade=True):
        """حذف NVR؛ در صورت cascade=True تمام کانال‌های ثبت‌شده‌ی زیر آن هم حذف می‌شوند."""
        self.nvrs = [n for n in self.nvrs if n["id"] != nvr_id]
        self.save_nvrs()
        if cascade:
            self.cameras = [c for c in self.cameras if c.get("nvr_id") != nvr_id]
            self.save()

    def get_nvr(self, nvr_id):
        for nvr in self.nvrs:
            if nvr["id"] == nvr_id:
                return nvr
        return None

    def add_channel_camera(self, nvr: dict, channel: int, name: str, path: str = "", full_url: str = None):
        """یک کانال کشف‌شده‌ی NVR را به‌عنوان دوربین جدید (متصل به آن NVR) ثبت می‌کند."""
        return self.add_camera(
            name=name,
            ip=nvr["ip"],
            port=nvr.get("rtsp_port", "554"),
            user=nvr.get("user", ""),
            pwd=nvr.get("pass", ""),
            path=path,
            nvr_id=nvr["id"],
            channel=channel,
            full_url=full_url,
        )

    @staticmethod
    def build_rtsp_url(cam: dict) -> str:
        # اگر آدرس کامل استریم (مثلاً از طریق کشف ONVIF) از قبل مشخص شده، همان استفاده
        # می‌شود؛ فقط در صورت نبود نام‌کاربری/رمز در خود URL، این اطلاعات اضافه می‌شوند.
        full_url = cam.get("full_url")
        if full_url:
            user, pwd = cam.get("user", ""), cam.get("pass", "")
            if user and pwd and "@" not in full_url:
                scheme_sep = "://"
                idx = full_url.find(scheme_sep)
                if idx != -1:
                    scheme = full_url[:idx + len(scheme_sep)]
                    rest = full_url[idx + len(scheme_sep):]
                    return f"{scheme}{user}:{pwd}@{rest}"
            return full_url

        ip, port = cam["ip"], cam["port"]
        user, pwd, path = cam.get("user", ""), cam.get("pass", ""), cam.get("path", "")
        if user and pwd:
            return f"rtsp://{user}:{pwd}@{ip}:{port}/{path}" if path else f"rtsp://{user}:{pwd}@{ip}:{port}"
        return f"rtsp://{ip}:{port}/{path}" if path else f"rtsp://{ip}:{port}"

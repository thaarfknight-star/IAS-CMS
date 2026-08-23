import json
import os
import uuid


class CameraStore:
    """Persists the list of cameras the user has connected to before,
    each with a custom display name (درب اصلی، حیاط، راهرو و ...)."""

    def __init__(self, path="cameras.json"):
        self.path = path
        self.cameras = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.cameras = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری لیست دوربین‌ها: {e}")
                self.cameras = []

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.cameras, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره لیست دوربین‌ها: {e}")

    def add_camera(self, name, ip, port, user, pwd, path):
        cam = {
            "id": str(uuid.uuid4()),
            "name": name or ip,
            "ip": ip,
            "port": port,
            "user": user,
            "pass": pwd,
            "path": path,
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

    @staticmethod
    def build_rtsp_url(cam: dict) -> str:
        ip, port = cam["ip"], cam["port"]
        user, pwd, path = cam.get("user", ""), cam.get("pass", ""), cam.get("path", "")
        if user and pwd:
            return f"rtsp://{user}:{pwd}@{ip}:{port}/{path}" if path else f"rtsp://{user}:{pwd}@{ip}:{port}"
        return f"rtsp://{ip}:{port}/{path}" if path else f"rtsp://{ip}:{port}"

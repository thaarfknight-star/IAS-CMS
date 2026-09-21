# -*- coding: utf-8 -*-
"""plate_direction.py — موتور قوانین جهت تردد پلاک‌خوان (2.0.15-beta).

به دستور کاربر، برای دوربین‌های پلاک‌خوان تعریف می‌شود هر دوربین برای کدام
مسیر است (ورود/خروج) و هر مسیر فقط یک جهت مجاز دارد (رفت/برگشت):

  قانون الف) خروجِ بدون ورودِ ثبت‌شده -> تخلف «خروج بدون ورود ثبت‌شده»
  قانون ب) ورودِ مجددِ بدون خروجِ قبلی -> تخلف «ورود مجدد بدون خروج قبلی»
  قانون ج) تردد در مسیر خلاف جهت مجاز آن -> تخلف «تردد خلاف جهت مجاز مسیر»

نکته‌ی استخراج جهت از یک دوربین ثابت: دوربین پلاک را یک‌بار می‌بیند، پس
«ورود/خروج» از نقش دوربین (plate_role) و «رفت/برگشت» از نگاشت قراردادی
(ورود=رفت، خروج=برگشت) به‌دست می‌آید و با جهت مجاز مسیر (lane.allowed)
مقایسه می‌شود.

وضعیت داخل/خارج هر پلاک در جدول plate_states نگه‌داشته می‌شود؛ همه‌ی
عبورها (با جهت و مسیر) در plate_crossings و تخلفات در plate_violations ثبت
می‌شوند (رجوع کنید به plate_store.py).

نقطه‌ی فراخوانی: MainWindow.on_plate_event در main.py، بلافاصله بعد از
plate_store.log_event.
"""

import time

from plate_store import normalize_plate_text

# نقش‌های مجاز دوربین (فیلد plate_role رکورد دوربین در cameras.json)
ROLE_ENTRY = "entry"
ROLE_EXIT = "exit"

# جهت‌های سفر
TRAVEL_GOING = "going"    # رفت
TRAVEL_RETURN = "return"  # برگشت

ROLE_LABELS = {"entry": "دوربین ورود", "exit": "دوربین خروج", "": "غیرپلاکی"}


class PlateDirectionEngine:
    def __init__(self, store, camera_store=None):
        self.store = store
        self.camera_store = camera_store
        # بوق تخلف: همان کانال «violation» صداهای هشدار (مثل تخلفات طبقاتی)
        self._beep = None

    def set_violation_beep(self, beep_callable):
        self._beep = beep_callable

    def _beep_violation(self):
        try:
            if callable(self._beep):
                self._beep()
        except Exception:
            pass

    # ------------------------------------------------------------ lanes --
    def get_lane(self, lane_id):
        if not lane_id:
            return None
        lanes = self.store.get_lanes()
        lane = lanes.get(lane_id)
        if not isinstance(lane, dict):
            return None
        return lane

    # ---------------------------------------------------------- process --
    def process(self, cam, data, event):
        """پردازش یک رویداد پلاک تأییدشده.

        cam: دیکشنری دوربین (باید plate_role و lane_id داشته باشد)
        data: دیکشنری رویداد از plate_event_signal ترد استریم
        event: دیکشنری خروجی plate_store.log_event
        خروجی: دیکشنری {"crossing_id", "violations": [ids...]} یا None
        """
        try:
            return self._process_inner(cam, data, event)
        except Exception as e:
            print(f"خطا در موتور جهت پلاک: {e}")
            return None

    def _process_inner(self, cam, data, event):
        cam = cam or {}
        role = (cam.get("plate_role") or "").strip()
        if role not in (ROLE_ENTRY, ROLE_EXIT):
            return None  # دوربین نقش ورود/خروج ندارد -> فقط لاگ عادی

        text = normalize_plate_text((data or {}).get("plate_text", ""))
        if not text:
            return None
        event = event or {}
        event_id = event.get("id", "")
        plate_display = event.get("plate_display", "")
        plate_obj = event.get("plate")
        plate_id = plate_obj.get("id") if isinstance(plate_obj, dict) else None
        owner_name = event.get("owner_name", "")
        snapshot = event.get("snapshot_path", "")
        cam_id = str(cam.get("id", ""))
        cam_name = cam.get("name", "")
        lane_id = (cam.get("lane_id") or "").strip()

        crossing = "entry" if role == ROLE_ENTRY else "exit"
        travel = TRAVEL_GOING if crossing == "entry" else TRAVEL_RETURN
        now = time.time()
        violations = []

        # قانون ج) خلاف جهت مسیر - مستقل از وضعیت داخل/خارج
        lane = self.get_lane(lane_id)
        if lane:
            allowed = (lane.get("allowed") or "").strip()
            if allowed in (TRAVEL_GOING, TRAVEL_RETURN) and travel != allowed:
                lane_name = lane.get("name") or lane_id
                detail = (
                    f"{self.store.CROSSING_LABELS[crossing]} "
                    f"({self.store.TRAVEL_LABELS[travel]}) در «{lane_name}» "
                    f"که فقط جهت «{self.store.TRAVEL_LABELS[allowed]}» مجاز است"
                )
                violations.append(self.store.log_violation(
                    "wrong_way", text, camera_id=cam_id, camera_name=cam_name,
                    lane_id=lane_id, detail=detail, snapshot_path=snapshot,
                    plate_display=plate_display, plate_id=plate_id,
                    owner_name=owner_name))

        # قوانین الف/ب بر اساس وضعیت داخل/خارج
        st = self.store.get_plate_state(text)
        prev_state = (st or {}).get("state") or "outside"
        last_ts = (st or {}).get("last_ts") or 0

        if crossing == "exit" and prev_state != "inside":
            # قانون الف) خروج بدون ورود ثبت‌شده
            detail = "خروج ثبت شد در حالی که ورود قبلی برای این پلاک ثبت نشده است"
            violations.append(self.store.log_violation(
                "exit_without_entry", text, camera_id=cam_id,
                camera_name=cam_name, lane_id=lane_id, detail=detail,
                snapshot_path=snapshot, plate_display=plate_display,
                plate_id=plate_id, owner_name=owner_name))
            # وضعیت عوض نمی‌شود (هنوز خارج است)
        elif crossing == "entry" and prev_state == "inside":
            # قانون ب) ورود مجدد بدون خروج - با پنجره‌ی اغماض برای خوانش
            # تکراری هم‌جهت در یک گیت (مثلاً دو دوربین روی یک ورودی)
            grace = self.store.reentry_grace_seconds
            if now - last_ts < grace:
                pass  # خوانش تکراری همان ورود؛ فقط لاگ عبور
            else:
                detail = "ورود مجدد ثبت شد در حالی که خروج قبلی ثبت نشده است"
                violations.append(self.store.log_violation(
                    "reentry_without_exit", text, camera_id=cam_id,
                    camera_name=cam_name, lane_id=lane_id, detail=detail,
                    snapshot_path=snapshot, plate_display=plate_display,
                    plate_id=plate_id, owner_name=owner_name))
            # وضعیت همان داخل می‌ماند
        else:
            # عبور سالم: چرخه‌ی وضعیت
            new_state = "inside" if crossing == "entry" else "outside"
            self.store.set_plate_state(text, new_state,
                                       last_event_id=event_id,
                                       last_camera_id=cam_id, last_ts=now)

        # ثبت عبور (همیشه، حتی با تخلف) + لینک دوربین/مسیر روی رویداد اصلی
        crossing_id = self.store.log_crossing(
            text, camera_id=cam_id, camera_name=cam_name, lane_id=lane_id,
            crossing_type=crossing, travel=travel, event_id=event_id,
            snapshot_path=snapshot, plate_display=plate_display,
            plate_id=plate_id, owner_name=owner_name)
        for vid in violations:
            try:
                with self.store._lock:
                    self.store._conn.execute(
                        "UPDATE plate_violations SET crossing_id=? WHERE id=?",
                        (crossing_id, vid))
                    self.store._conn.commit()
            except Exception:
                pass
        if violations:
            self._beep_violation()
        return {"crossing_id": crossing_id, "violations": violations}

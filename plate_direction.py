# -*- coding: utf-8 -*-
"""plate_direction.py — موتور قوانین جهت تردد پلاک‌خوان (2.0.17-beta).

قوانین تردد (تحقیق و تدوین نهایی):

  قانون الف) خروجِ بدون ورودِ ثبت‌شده -> تخلف «خروج بدون ورود ثبت‌شده»
             (استثنا: ادامه‌ی حرکت رو به جلو/درجا در همان مسیرِ خروج، ادامه‌ی
             همان رویداد خروج است و تخلف نیست — مثلاً دوربین دوم مسیر خروج)
  قانون ب) ورودِ مجددِ بدون خروجِ قبلی -> تخلف «ورود مجدد بدون خروج قبلی»
             (بدون هیچ اغماضی؛ کول‌داون ۱۵ثانیه‌ای دتکتور برای خوانش تکراری
             کافی است و اغماض ۱۲۰ثانیه‌ای حذف شده است)
  قانون ج) تردد در مسیر خلاف جهت مجاز آن -> تخلف «تردد خلاف جهت مجاز مسیر»
             (نقش دوربین: ورود=رفت، خروج=برگشت؛ مقایسه با lane.allowed)
  قانون د) حرکت معکوس در مسیر رسم‌شده -> تخلف «تردد خلاف جهت مجاز مسیر»
             (فقط وقتی پلاک «داخل» است و عبور قبلی‌اش در همان مسیر بوده:
             در مسیر فقط-رفت باید ترتیب دوربین‌ها صعودی باشد، در مسیر
             فقط-برگشت نزولی؛ حرکت رو به جلو، تخلف «ورود مجدد» قانون ب را
             هم خنثی می‌کند چون تردد سالم در امتداد مسیر است)

قوانین رسم و اتصال مسیر روی نقشه (رجوع به lane_geometry.py):
  ۱) حداقل ۲ نقطه؛ جهت رسم (اول -> آخر) = جهت «رفت» مسیر.
  ۲) فقط دوربینِ دارای نقش پلاکی (ورود/خروج) و با فاصله‌ی حداکثر ۵ متر از
     خط مسیر قابل اتصال است.
  ۳) هر دوربین فقط عضو یک مسیر است.
  ۴) ترتیب دوربین‌ها = فاصله‌ی طولی پروجکشن از ابتدای مسیر.

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


def lane_direction(allowed, prev_order, cur_order):
    """جهت حرکت میان دو ترتیب دوربین در یک مسیر.

    خروجی: "forward" | "backward" | "same" | None (نامشخص)
    """
    if prev_order is None or cur_order is None:
        return None
    if allowed not in ("going", "return"):
        return None
    if cur_order == prev_order:
        return "same"
    if allowed == "going":
        return "forward" if cur_order > prev_order else "backward"
    return "forward" if cur_order < prev_order else "backward"


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

        # قوانین الف/ب/د بر اساس وضعیت داخل/خارج و ترتیب مسیر
        st = self.store.get_plate_state(text)
        prev_state = (st or {}).get("state") or "outside"
        prev_lane = (st or {}).get("last_lane_id") or ""
        try:
            prev_order = (int((st or {}).get("last_lane_order"))
                          if (st or {}).get("last_lane_order") is not None else None)
        except (TypeError, ValueError):
            prev_order = None
        cam_order = self.store.lane_camera_order(lane, cam_id)
        allowed = ((lane or {}).get("allowed") or "").strip()

        direction = None  # forward/backward/same/None — فقط در همان مسیر
        if lane and lane_id and lane_id == prev_lane:
            direction = lane_direction(allowed, prev_order, cam_order)
            if (direction is None and prev_order is not None
                    and cam_order is not None and not allowed):
                direction = "forward"  # جهت مسیر تعریف‌نشده: سخت‌گیری نکن
        forward_progress = direction in ("forward", "same")
        reverse_move = (direction == "backward" and prev_state == "inside"
                        and crossing == "entry")

        if crossing == "exit" and prev_state != "inside":
            if forward_progress:
                # استثنای قانون الف: ادامه‌ی حرکت رو به جلو/درجا در همان
                # مسیرِ خروج (مثلاً دوربین دوم مسیر خروج) = ادامه‌ی همان
                # رویداد خروج است، نه تخلف.
                pass
            else:
                # قانون الف) خروج بدون ورود ثبت‌شده
                detail = ("خروج ثبت شد در حالی که ورود قبلی برای این پلاک "
                          "ثبت نشده است")
                violations.append(self.store.log_violation(
                    "exit_without_entry", text, camera_id=cam_id,
                    camera_name=cam_name, lane_id=lane_id, detail=detail,
                    snapshot_path=snapshot, plate_display=plate_display,
                    plate_id=plate_id, owner_name=owner_name))
            # وضعیت عوض نمی‌شود (هنوز خارج است)
        elif reverse_move:
            # قانون د) حرکت معکوس در مسیر رسم‌شده — تخلفِ خاص‌تر، پس قانون
            # ب اجرا نمی‌شود تا تخلف تکراری ثبت نشود.
            lane_name = (lane or {}).get("name") or lane_id
            detail = (
                f"حرکت معکوس در مسیر «{lane_name}»: دوربین «{cam_name}» "
                f"بعد از دوربینی با ترتیب بالاتر/پایین‌تر دیده شد")
            violations.append(self.store.log_violation(
                "wrong_way", text, camera_id=cam_id, camera_name=cam_name,
                lane_id=lane_id, detail=detail, snapshot_path=snapshot,
                plate_display=plate_display, plate_id=plate_id,
                owner_name=owner_name))
            # وضعیت همان داخل می‌ماند
        elif crossing == "entry" and prev_state == "inside":
            if forward_progress:
                pass  # تردد سالم در امتداد مسیر؛ تخلف «ورود مجدد» نیست
            else:
                # قانون ب) ورود مجدد بدون خروج — بدون اغماض؛ کول‌داون
                # ۱۵ثانیه‌ای دتکتور برای خوانش تکراری کافی است.
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

        # به‌روزرسانی آخرین مسیر/ترتیب دوربین (برای قانون د در عبورهای بعدی)
        try:
            self.store.set_plate_lane(text, lane_id, cam_order)
        except Exception:
            pass

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

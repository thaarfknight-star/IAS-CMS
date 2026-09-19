"""کنترل تردد طبقاتی اشخاص.

معماری طبقه‌ی دوربین (تک‌منبع حقیقت):
- فیلد `floor_id` روی رکورد دوربین در camera_store (cameras.json).
- وقتی دوربین روی «نقشه‌ی ساختمان» در طبقه‌ای قرار/لینک می‌شود، خودکار سینک
  می‌شود؛ در «تنظیمات دوربین» هم می‌توان دستی ست/پاک کرد.
- در زمان اجرا: اول floor_id دوربین، و اگر خالی بود fallback روی جست‌وجوی
  نقشه (device از نوع camera با ref_id برابر).

قوانین تردد:
- افراد تعریف‌نشده (بدون چهره‌ی شناخته‌شده و بدون قانون تکی): قانون سراسری
  `undefined_allowed_floors` («*» = همه‌ی طبقات).
- افراد تعریف‌شده (چهره‌محور): قانون تکی هر شخص؛ اگر قانونی ثبت نشده باشد
  یعنی آزاد (بدون محدودیت).
"""

ALL_FLOORS = "*"


def get_floor_list(building_map=None):
    """لیست (floor_id, floor_name) طبقات تعریف‌شده در نقشه‌ی ساختمان."""
    if building_map is None:
        try:
            from building_map import MapStore
            building_map = MapStore()
        except Exception:
            return []
    try:
        fls = building_map.floors() if callable(
            getattr(building_map, "floors", None)) else building_map.floors
        return [(fl.get("id"), fl.get("name") or fl.get("id")) for fl in fls]
    except Exception:
        return []


def get_floor_name(floor_id, building_map=None):
    for fid, name in get_floor_list(building_map):
        if fid == floor_id:
            return name
    return floor_id or "—"


def resolve_camera_floor(cam_id, camera_store=None, building_map=None):
    """floor_id دوربین؛ «» یعنی طبقه نامشخص (چک تردد رد می‌شود)."""
    if not cam_id:
        return ""
    # ۱) فیلد دستی/سینک‌شده روی رکورد دوربین
    if camera_store is not None:
        try:
            fid = camera_store.get_camera_floor_id(cam_id)
            if fid:
                return fid
        except Exception:
            pass
    # ۲) fallback: جست‌وجو در نقشه‌ی ساختمان
    if building_map is None:
        try:
            from building_map import MapStore
            building_map = MapStore()
        except Exception:
            building_map = None
    if building_map is not None:
        try:
            fid = building_map.find_camera_floor(cam_id)
            if fid:
                return str(fid)
        except Exception:
            pass
    return ""


def evaluate_floor_access(person_id, face_person_id, floor_id, person_store,
                        face_engine=None):
    """ارزیابی قانون تردد. خروجی: (مجاز؟, دلیل, is_defined)
    - دلیل: 'undefined' / 'defined' / 'work_group' / 'no_floor' / 'free'
    - ترتیب اولویت برای افراد تعریف‌شده: ۱) قانون تکی شخص ۲) قانون «گروه
      کاری» شخص (از بانک چهره) ۳) آزاد. قانون تکیِ پیدا‌شده نسبت به قانون
      سراسری اولویت دارد."""
    if not floor_id:
        return True, "no_floor", False
    person = None
    try:
        person = person_store.get_person(person_id)
    except Exception:
        person = None
    known_face = bool(face_person_id or (person and person.get("face_person_id")))
    # قانون تکی شخص (اگر ثبت شده باشد، برای هر دو حالت اولویت دارد):
    # اول کلید بانک چهره، بعد کلید رکورد ردیابی (سازگاری با قبل)
    personal = None
    try:
        if face_person_id:
            personal = person_store.get_person_allowed_floors(face_person_id)
        if personal is None:
            personal = person_store.get_person_allowed_floors(person_id)
    except Exception:
        personal = None
    if personal is not None:
        if personal == ALL_FLOORS:
            return True, "defined", known_face
        return (floor_id in personal), "defined", known_face
    if known_face:
        # قانون «گروه کاری»: اگر برای work_group شخص قانونی ثبت شده باشد
        group_rule = None
        try:
            wg = ""
            if face_engine is not None and face_person_id:
                rec = face_engine.get_person(face_person_id)
                if rec:
                    wg = (rec.get("work_group") or "").strip()
            if wg:
                group_rule = person_store.get_work_group_allowed_floors(wg)
        except Exception:
            group_rule = None
        if group_rule is not None:
            if group_rule == ALL_FLOORS:
                return True, "work_group", True
            return (floor_id in group_rule), "work_group", True
        # تعریف‌شده بدون قانون تکی و بدون قانون گروه = آزاد
        return True, "free", True
    # تعریف‌نشده: قانون سراسری
    try:
        allowed = person_store.get_undefined_allowed_floors()
    except Exception:
        allowed = ALL_FLOORS
    if allowed == ALL_FLOORS:
        return True, "undefined", False
    return (floor_id in allowed), "undefined", False

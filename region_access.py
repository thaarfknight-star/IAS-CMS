"""کنترل تردد محدوده‌های هشدار اشخاص.

قوانین تردد (دقیقاً همان الگوی کنترل تردد طبقاتی، ولی برای محدوده‌ها):
- افراد تعریف‌نشده (بدون چهره‌ی شناخته‌شده): ورودشان همیشه «گزارش ورود به
  محدوده» ثبت می‌شود (رفتار قبلی zone_entry)؛ قانون ممنوعیت ندارند.
- افراد تعریف‌شده (چهره‌محور، از بانک چهره‌ها): قانون «محدوده‌های ممنوعه»
  به‌صورت تکی برای هر شخص؛ اگر قانونی ثبت نشده باشد، قانون «گروه کاری»
  شخص (از بانک چهره‌ها) بررسی می‌شود؛ اگر هیچ‌کدام نباشد یعنی آزاد.
- ترتیب اولویت: ۱) قانون تکی شخص ۲) قانون گروه کاری ۳) آزاد.

کلید یکتا‌ی هر محدوده: «camera_id:region_id» (محدوده‌ها per-camera هستند).
"""


def region_key(camera_id, region_id):
    """کلید یکتای یک محدوده در قوانین."""
    return f"{camera_id or ''}:{region_id or ''}"


def evaluate_region_access(face_person_id, camera_id, region_id, person_store,
                           face_engine=None):
    """ارزیابی قانون تردد محدوده. خروجی: (مجاز؟, دلیل, is_defined)
    - دلیل: 'free' / 'person_rule' / 'work_group' / 'no_region'
    - فقط افراد تعریف‌شده (face_person_id غیرخالی) قانون دارند؛ بقیه آزادند
      (گزارش ورودشان جداگانه و همیشه ثبت می‌شود)."""
    if not region_id:
        return True, "no_region", False
    if not face_person_id:
        return True, "free", False
    # ۱) قانون تکی شخص
    try:
        denied = person_store.get_person_denied_regions(face_person_id)
    except Exception:
        denied = None
    if denied is not None:
        key = region_key(camera_id, region_id)
        if key in denied:
            return False, "person_rule", True
        return True, "person_rule", True
    # ۲) قانون گروه کاری شخص (از بانک چهره‌ها)
    work_group = ""
    if face_engine is not None:
        try:
            person = None
            getter = getattr(face_engine, "get_person", None)
            if callable(getter):
                person = getter(face_person_id)
            if person:
                work_group = str(person.get("work_group") or "").strip()
        except Exception:
            work_group = ""
    if work_group:
        try:
            gdenied = person_store.get_work_group_denied_regions(work_group)
        except Exception:
            gdenied = None
        if gdenied is not None:
            key = region_key(camera_id, region_id)
            if key in gdenied:
                return False, "work_group", True
            return True, "work_group", True
    # ۳) آزاد
    return True, "free", True

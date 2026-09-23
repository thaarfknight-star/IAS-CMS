"""Headless regression test for IAS-CMS 2.0.34-beta.

۱) پلاک‌خوانِ آگاه از حرکت:
   - ترکِ تازه با ۲ خوانشِ هم‌طولِ معتبر در همان تیک اول تأیید می‌شود
     (پلاکِ خودروی تندگذر که فقط یک تیک دیده می‌شود).
   - ترکِ متحرک: دو دتکشن با IoU صفر ولی مرکز نزدیک به همان ترک لینک
     می‌شوند (ترک نمی‌شکند) و پرچم moving می‌گیرد.
   - برای ترکِ متحرک گیت تاریِ بازتر (۱۲) به OCR پاس داده می‌شود.
۲) کول‌داون پیش‌فرض پلاک ۱۵ ثانیه است (قانون ثابت پروژه).
۳) ردیابی اشخاصِ آگاه از حرکت:
   - شخصِ سریع (IoU صفر بین دو تیک) با لینکِ مرکز-نزدیک به همان رد وصل
     و با ۲ هیت تأیید می‌شود.
   - کراپِ کوچک (شخصِ دور) با بزرگ‌نمایی توصیف‌گر می‌گیرد و تأیید می‌شود.
۴) آستانه‌ی پیش‌فرض تشخیص شخص ۰٫۳۰ است.
۵) زمان‌بندی متناوب تیک: چهره فقط در تیک‌های زوج اجرا می‌شود.
"""
import os
import sys
import types
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_encodings = lambda *a, **k: []
fr.face_locations = lambda *a, **k: []
sys.modules["face_recognition"] = fr

_cv2 = types.ModuleType("cv2")


def _fake_resize(img, dsize, **k):
    # نزدیک‌ترین همسایه با numpy (فقط برای تست بزرگ‌نمایی کراپ)
    nw, nh = int(dsize[0]), int(dsize[1])
    h, w = img.shape[:2]
    yi = (np.arange(nh) * h / max(1, nh)).astype(int)
    xi = (np.arange(nw) * w / max(1, nw)).astype(int)
    return img[yi[:, None], xi]


_cv2.resize = _fake_resize
_cv2.imwrite = lambda *a, **k: True
_cv2.INTER_NEAREST = 0
_cv2.INTER_LINEAR = 1
_cv2.INTER_CUBIC = 2
_cv2.VideoCapture = type("VideoCapture", (), {})
sys.modules["cv2"] = _cv2

passed = []
failed = []


def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)


import numpy as np

# ---------- ۱) پلاک‌خوانِ آگاه از حرکت ----------
from plate_detector import PlateTracker, _looks_like_plate

PLATE_TEXT = "12ب34567"
check("plate: test text is valid", _looks_like_plate(PLATE_TEXT))


class FakeOcr:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []  # blur_heavy هر فراخوانی

    def read(self, crop, blur_heavy=None):
        self.calls.append(blur_heavy)
        idx = min(len(self.calls) - 1, len(self.texts) - 1)
        return [(self.texts[idx], 0.9, "fake")]


frame = np.zeros((480, 640, 3), dtype=np.uint8)

# ۱-الف) ترک تازه: ۲ خوانش هم‌طول در همان تیک اول -> رویداد
tr1 = PlateTracker()
ev = tr1.update([(100, 100, 220, 140, 0.92)], frame,
                FakeOcr([PLATE_TEXT, PLATE_TEXT]))
check("plate: new track confirms in single tick (2 reads)",
      len(ev) == 1 and ev[0][1] == PLATE_TEXT)

# ۱-ب) ترک متحرک: جابه‌جایی زیاد (IoU=۰) ولی مرکز نزدیک -> همان ترک
ocr2 = FakeOcr([PLATE_TEXT, PLATE_TEXT])
tr2 = PlateTracker()
tr2.update([(100, 100, 220, 140, 0.92)], frame, ocr2)
t = tr2._tracks[0]
t["last_ocr_ts"] = 0.0  # اجازه‌ی OCR دوباره در تیک بعد
ev2 = tr2.update([(260, 100, 380, 140, 0.90)], frame, ocr2)
check("plate: fast-moving detection links to same track",
      tr2.diag["tracks_created"] == 1)
check("plate: moving flag set", bool(tr2._tracks[0].get("moving")))
check("plate: moving track uses relaxed blur gate (12.0)",
      ocr2.calls[-1] == 12.0)
check("plate: static track uses default blur gate (None)",
      ocr2.calls[0] is None)

# ۱-ج) دتکشنِ خیلی دور به ترکِ دیگری وصل نمی‌شود (ترک تازه می‌سازد)
tr3 = PlateTracker()
tr3.update([(100, 100, 220, 140, 0.92)], frame, FakeOcr([]))
tr3.update([(500, 300, 620, 340, 0.90)], frame, FakeOcr([]))
check("plate: far detection creates new track",
      tr3.diag["tracks_created"] == 2)

# ---------- ۲) کول‌داون پیش‌فرض ۱۵ ثانیه ----------
from plate_store import PlateStore

tmpd = tempfile.mkdtemp(prefix="ias_plate_")
ps = PlateStore(db_path=os.path.join(tmpd, "plates.db"))
check("plate: default cooldown is 15s", ps.cooldown_seconds == 15)
ps.set_setting("cooldown_seconds", "45")
check("plate: cooldown override respected", ps.cooldown_seconds == 45)

# ---------- ۳) ردیابی اشخاصِ آگاه از حرکت ----------
import person_reid
from person_reid import PersonLocalTracker

pframe = np.zeros((480, 640, 3), dtype=np.uint8)

# توصیف‌گر واقعی stub می‌شود تا منطق لینک/تأیید تست شود (نه هیستوگرام)؛
# شکل کراپِ رسیده به describe_person ثبت می‌شود تا بزرگ‌نمایی بررسی شود.
describe_shapes = []


def fake_describe_person(crop):
    describe_shapes.append(crop.shape)
    return {"vector": np.ones(8, dtype=np.float64) / 8.0,
            "face_vector": None, "shirt_color": "آبی",
            "pants_color": "مشکی", "hair_color": "مشکی",
            "hair_length": "کوتاه", "face_person_id": "", "face_name": ""}


person_reid.describe_person = fake_describe_person

# ۳-الف) شخص سریع: باکس دوم ۱۵۰ پیکسل جابه‌جا شده (IoU=۰)
pt = PersonLocalTracker(confirm_frames=2)
e1 = pt.update([(100, 200, 300, 100)], pframe, ts=1000.0)   # trbl
e2 = pt.update([(100, 350, 300, 250)], pframe, ts=1003.0)   # +۱۵۰px در x
kinds = [k for k, _ in (e1[0] + e2[0])]
check("person: fast-moving person links across ticks (no new track)",
      pt._diag["tracks_created"] == 1)
check("person: fast-moving person confirmed with 2 hits",
      "confirmed" in kinds)
trk = pt._tracks[0]
check("person: kalman velocity estimated after match",
      abs(float(trk["kf"].x[4])) > 1.0)

# ۳-ب) جابه‌جایی خیلی زیاد -> رد تازه (ادغام اشتباه رخ نمی‌دهد)
pt2 = PersonLocalTracker(confirm_frames=2)
pt2.update([(100, 200, 300, 100)], pframe, ts=2000.0)
pt2.update([(100, 700, 300, 600)], pframe, ts=2003.0)  # +۵۰۰px
check("person: far detection starts new track",
      pt2._diag["tracks_created"] == 2)

# ۳-ج) کراپ کوچک (شخص دور، ۱۰×۳۰) با بزرگ‌نمایی توصیف‌گر می‌گیرد و تأیید می‌شود
describe_shapes.clear()
pt3 = PersonLocalTracker(confirm_frames=2)
pt3.update([(10, 60, 40, 50)], pframe, ts=3000.0)
ev3 = pt3.update([(10, 62, 42, 52)], pframe, ts=3001.0)
kinds3 = [k for k, _ in ev3[0]]
check("person: tiny crop upscaled before describe (>=40x20)",
      bool(describe_shapes) and describe_shapes[0][0] >= 40
      and describe_shapes[0][1] >= 20)
check("person: tiny crop still confirms",
      "confirmed" in kinds3)

# ---------- ۴) آستانه‌ی تشخیص شخص ----------
from person_detector import PersonDetector

check("person: default conf threshold is 0.30",
      PersonDetector().conf_threshold == 0.30)

# ---------- ۵) زمان‌بندی متناوب تیک ----------
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
import camera_stream as cs

cs._get_person_detector = lambda: None
cs._get_fire_detector = lambda: None


class FakeFaceEngine:
    def __init__(self):
        self.calls = 0

    def recognize(self, frame):
        self.calls += 1
        return [], None, []


fe = FakeFaceEngine()
bt = cs.CameraStreamThread("dummy", fe, process_every_n=5)
tframe = np.zeros((240, 320, 3), dtype=np.uint8)
for _ in range(4):
    bt._run_recognition(tframe)
check("stagger: face runs only on even ticks (2 of 4)",
      fe.calls == 2)
check("stagger: tick counter advances", bt._tick_idx == 4)

# ---------- جمع‌بندی ----------
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)

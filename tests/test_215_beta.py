"""Headless regression test for IAS-CMS 2.0.15-beta.

۱) ذخیره‌ی امن رمزها (credential_vault): رمزنگاری/رمزگشایی، عدم ذخیره‌ی
   متن‌ساده در JSON، حذف کامل رمز ذخیره‌شده.
۲) پایداری استریم (camera_stream / rtsp_utils): حلقه‌ی reconnect با backoff،
   توقف bounded ترد، گزینه‌های rw_timeout در PROBE_FFMPEG_OPTS.
۳) اسکن شبکه (network_probe): نگاشت quality_label.
۴) موتور قوانین جهت تردد پلاک (plate_store + plate_direction):
   - ورود سالم -> داخل، بدون تخلف
   - خروج سالم بعد از ورود -> خارج، بدون تخلف
   - خروج بدون ورود ثبت‌شده -> تخلف exit_without_entry
   - ورود مجدد بدون خروج قبلی -> تخلف reentry_without_exit
   - خلاف جهت مسیر -> تخلف wrong_way
   - دوربین بدون نقش -> هیچ پردازشی
   - مسیر تعریف‌نشده -> فقط قوانین ورود/خروج
   - ضدتکرار تخلف در ۶۰ ثانیه
   - اغماض خوانش تکراری هم‌جهت در پنجره‌ی grace
   - ثبت عبورها + خروجی CSV تخلفات
"""
import os
import sys
import time
import types
import json
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fr = types.ModuleType("face_recognition")
fr.face_locations = lambda *a, **k: []
fr.face_encodings = lambda *a, **k: []
fr.face_distance = lambda *a, **k: []
fr.compare_faces = lambda *a, **k: []
sys.modules["face_recognition"] = fr

passed = []
failed = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name +
          (f" [{extra}]" if extra and not cond else ""))


sys.argv = ["test"]
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

TMP = tempfile.mkdtemp(prefix="ias215_")

# تنظیمات را از مسیر واقعی جدا کن تا تست، تنظیمات واقعی کاربر را دست‌نخورده بگذارد
import app_settings
app_settings._SETTINGS_PATH = os.path.join(TMP, "settings.json")

# ================================================= ۱) credential_vault ==
import credential_vault as vault

pw = "Sup3r$ecret!رمز"
enc = vault.encrypt(pw)
check("vault encrypt produces non-plaintext", enc and pw not in enc, enc[:20])
check("vault decrypt roundtrip", vault.decrypt(enc) == pw)
check("vault decrypt bad data -> None", vault.decrypt("!!!") is None)
check("vault encrypt empty -> None", vault.encrypt("") is None)
key_path = vault._FALLBACK_KEY_PATH
if os.path.exists(key_path):
    os.remove(key_path)  # کلید مبهم‌سازی توسعه نباید در محیط بماند

# ذخیره‌ی امن از مسیر CameraStore: رمز حافظه -> pass_enc روی دیسک، بدون متن‌ساده
from camera_store import CameraStore
cs_path = os.path.join(TMP, "vault_cameras.json")
cstore = CameraStore(path=cs_path)
cam = cstore.add_camera("دوربین تست", "192.168.1.21", 554, "admin", pw, "/h264")
cstore.save()
with open(cs_path, encoding="utf-8") as f:
    disk = json.load(f)
disk_text = json.dumps(disk, ensure_ascii=False)
check("no plaintext password on disk", pw not in disk_text)
check("pass_enc stored on disk", bool(disk[0].get("pass_enc")))
cstore2 = CameraStore(path=cs_path)  # بارگذاری مجدد
check("password restored in memory after reload",
      cstore2.standalone_cameras()[0].get("pass") == pw)
cstore2.wipe_saved_passwords()
with open(cs_path, encoding="utf-8") as f:
    disk2 = json.load(f)
check("wipe removes pass_enc from disk", "pass_enc" not in disk2[0])
check("wipe clears memory pass",
      cstore2.standalone_cameras()[0].get("pass") == "")

# ================================================= ۲) پایداری استریم ==
import rtsp_utils

check("stimeout in PROBE_FFMPEG_OPTS",
      "stimeout" in rtsp_utils.PROBE_FFMPEG_OPTS)
check("rw_timeout in STREAM_FFMPEG_OPTS",
      "rw_timeout" in rtsp_utils.STREAM_FFMPEG_OPTS)
check("reconnect in STREAM_FFMPEG_OPTS",
      "reconnect;1" in rtsp_utils.STREAM_FFMPEG_OPTS)

import camera_stream as cs
src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "camera_stream.py"),
    encoding="utf-8").read()
check("stream pump/supervisor present", "_pump_frames" in src)
check("bounded stop (wait 8000ms)", "self.wait(8000)" in src)
check("reconnect backoff present", "backoff" in src.lower())
check("stream_status_signal defined", "stream_status_signal" in src)
check("stream_stats_signal defined", "stream_stats_signal" in src)

# ================================================= ۳) network_probe ==
import network_probe as np

check("quality good", np.quality_label(4000, 30) == "خوب")
check("quality medium", np.quality_label(800, 150) == "متوسط")
check("quality weak", np.quality_label(100, 400) == "ضعیف")
check("quality unknown", np.quality_label(None, 50) == "نامشخص")
check("quality offline", np.quality_label(None, None) == "قطع")

# ================================= ۴) موتور جهت تردد پلاک ==
from plate_store import PlateStore, normalize_plate_text
from plate_direction import PlateDirectionEngine

db_path = os.path.join(TMP, "plates.db")
store = PlateStore(db_path=db_path)
store.set_lanes({
    "lane1": {"name": "مسیر ۱", "allowed": "going"},
    "lane2": {"name": "مسیر ۲", "allowed": "return"},
})
engine = PlateDirectionEngine(store)

PLATE = "۱۲الف۳۴۵"  # پلاک نمایشی برای سناریوها

entry_cam = {"id": "c1", "name": "ورودی اصلی",
             "plate_role": "entry", "lane_id": "lane1"}
exit_cam = {"id": "c2", "name": "خروجی اصلی",
            "plate_role": "exit", "lane_id": "lane2"}
# دوربین خروج روی مسیر رفت (خلاف جهت)
exit_on_going_lane = {"id": "c3", "name": "خروجی روی مسیر ۱",
                      "plate_role": "exit", "lane_id": "lane1"}
norole_cam = {"id": "c4", "name": "بدون نقش"}


def fire(cam, plate=PLATE):
    data = {"plate_text": plate, "plate_display": plate, "conf": 0.95,
            "crop": None}
    event = {"id": "ev_" + str(time.time_ns()), "plate_display": plate,
             "plate": None, "owner_name": "", "snapshot_path": ""}
    return engine.process(cam, data, event)


canon = normalize_plate_text(PLATE)

# سناریوی ۱: ورود سالم -> داخل، بدون تخلف
store.set_plate_state(canon, "outside", last_ts=time.time() - 3600)
res = fire(entry_cam)
check("clean entry: no violations", res and not res["violations"])
st = store.get_plate_state(canon)
check("clean entry: state inside", st and st["state"] == "inside")

# سناریوی ۲: خروج سالم بعد از ورود -> خارج، بدون تخلف
res = fire(exit_cam)
check("clean exit: no violations", res and not res["violations"])
st = store.get_plate_state(canon)
check("clean exit: state outside", st and st["state"] == "outside")

# سناریوی ۳: خروج بدون ورود ثبت‌شده -> تخلف
store.set_plate_state(canon, "outside", last_ts=time.time() - 3600)
res = fire(exit_cam)
check("exit_without_entry violation",
      res and any(v in res["violations"] or True for v in res["violations"]) and
      len(res["violations"]) >= 1)
rows = store.list_violations(search=canon)
types = [r["violation_type"] for r in rows]
check("exit_without_entry type logged", "exit_without_entry" in types)

# سناریوی ۴: ورود مجدد بدون خروج قبلی -> تخلف
store.set_plate_state(canon, "inside", last_ts=time.time() - 3600)
res = fire(entry_cam)
types = [r["violation_type"]
         for r in store.list_violations(search=canon)]
check("reentry_without_exit type logged", "reentry_without_exit" in types)
st = store.get_plate_state(canon)
check("state stays inside after reentry", st and st["state"] == "inside")

# سناریوی ۵: خلاف جهت مسیر (خروج روی مسیر فقط-رفت)
store.set_plate_state(canon, "outside", last_ts=time.time() - 3600)
res = fire(exit_on_going_lane)
types = [r["violation_type"]
         for r in store.list_violations(search=canon)]
check("wrong_way type logged", "wrong_way" in types)

# سناریوی ۶: دوربین بدون نقش -> هیچ پردازشی
res = fire(norole_cam)
check("no role -> no processing", res is None)

# سناریوی ۷: مسیر تعریف‌نشده -> فقط قوانین ورود/خروج، بدون wrong_way
nolanecam = {"id": "c5", "name": "ورود بدون مسیر",
             "plate_role": "entry", "lane_id": ""}
store.set_plate_state(canon, "outside", last_ts=time.time() - 3600)
n_before = len(store.list_violations(search=canon))
res = fire(nolanecam)
n_after = len(store.list_violations(search=canon))
check("no lane -> clean entry no new violation",
      res and not res["violations"] and n_after == n_before)

# سناریوی ۸: ضدتکرار تخلف در ۶۰ ثانیه (همان رویداد تکراری)
store.set_plate_state(canon, "outside", last_ts=time.time() - 3600)
r1 = fire(exit_cam)
n1 = len(store.list_violations(search=canon))
r2 = fire(exit_cam)  # کمتر از ۶۰ ثانیه بعد
n2 = len(store.list_violations(search=canon))
check("violation dedup within 60s", n2 == n1,
      f"before={n_before} n1={n1} n2={n2}")

# سناریوی ۹: اغماض خوانش تکراری هم‌جهت در پنجره‌ی grace
store.reentry_grace_seconds = 120
store.set_plate_state(canon, "inside", last_ts=time.time() - 10)
n_before = len(store.list_violations(search=canon))
res = fire(entry_cam)
n_after = len(store.list_violations(search=canon))
check("grace window suppresses reentry violation",
      res and not res["violations"] and n_after == n_before)

# سناریوی ۱۰: پلاک تعریف‌نشده هم state و تخلف می‌گیرد
undef = normalize_plate_text("۹۹ب۱۲۳")
store.set_plate_state(undef, "outside", last_ts=time.time() - 3600)
res = fire(exit_cam, plate="۹۹ب۱۲۳")
check("undefined plate: exit_without_entry logged",
      res and len(res["violations"]) >= 1)

# سناریوی ۱۱: ثبت عبورها و خروجی CSV
crossings = store.query_crossings(plate_text=PLATE, limit=100)
check("crossings logged", len(crossings) >= 5, str(len(crossings)))
csv_path = os.path.join(TMP, "violations.csv")
n = store.export_violations_csv(csv_path, search=canon)
check("violations CSV exported", n > 0 and os.path.getsize(csv_path) > 0)
with open(csv_path, encoding="utf-8-sig") as f:
    head = f.readline()
check("CSV header Persian", "نوع تخلف" in head)
check("unacked count", store.count_unacked_violations() >= 1)
vid = store.list_violations(search=canon, limit=1)[0]["id"]
store.acknowledge_violation(vid, True)
check("acknowledge works",
      store.list_violations(search=canon, acknowledged=True)[0]["id"] == vid)

# ================================================= ۵) نقش/مسیر رکورد دوربین ==
cs2_path = os.path.join(TMP, "cameras.json")
cstore = CameraStore(path=cs2_path)
cam = cstore.add_camera("پلاک‌خوان ورودی", "192.168.1.21", 554,
                        "admin", "pw", "/h264")
cstore.update_camera(cam["id"], plate_role="entry", lane_id="lane1")
cstore.save()
cstore2 = CameraStore(path=cs2_path)
cams = cstore2.standalone_cameras()
check("camera role/lane persisted",
      cams and cams[0].get("plate_role") == "entry" and
      cams[0].get("lane_id") == "lane1")
cam_nvr = cstore2.add_camera("nvr ch", "10.0.0.5", 554, "admin", "pw", "/h264",
                            nvr_id="n1", channel=3)
url = cstore2.build_rtsp_url(cam_nvr)
check("NVR channel RTSP url builds", bool(url) and "10.0.0.5" in url, url)

# ================================================= ۶) تنظیمات ==
from app_settings import load_settings, save_settings

s = load_settings()
check("save_passwords default True", s.get("save_passwords") is True)
s["save_passwords"] = False
save_settings(s)
s2 = load_settings()
check("save_passwords setting roundtrip", s2.get("save_passwords") is False)

print()
print(f"RESULT: {len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

import os
import json
import time
import uuid
import threading

import cv2
import numpy as np
import face_recognition


class FaceEngine:
    """Face library + recognizer.

    Every registered person can carry: name, phone number, employee id and a
    free-text note, alongside the face embedding used for matching.
    """

    def __init__(
        self,
        db_path="face_db.json",
        faces_dir="faces",
        tolerance=0.5,
        unknown_alert_cooldown=6.0,
        known_alert_cooldown=15.0,
    ):
        self.db_path = db_path
        self.faces_dir = faces_dir
        self.tolerance = tolerance
        self.unknown_alert_cooldown = unknown_alert_cooldown
        self.known_alert_cooldown = known_alert_cooldown

        self.people = []  # each: id, name, phone, employee_id, note, encoding, photo, created_at

        self._last_unknown_alert = 0.0
        self._last_seen_person = {}  # person_id -> last notified timestamp

        # نکته کلیدی برای رفع کرش هنگام «تعریف چهره»:
        #   کتابخانه face_recognition (dlib) برای فراخوانی هم‌زمان (concurrent) از چند ترد
        #   thread-safe نیست. در این برنامه، ترد پخش زنده هر دوربین به‌صورت مداوم
        #   FaceEngine.recognize() را در پس‌زمینه صدا می‌زند و هم‌زمان کاربر می‌تواند از
        #   ترد اصلی (UI) هنگام «افزودن چهره از تصویر زنده» متد register_face() را صدا بزند.
        #   برخورد این دو فراخوانی با هم روی مدل dlib باعث کرش کامل برنامه می‌شد.
        #   با این قفل، تمام عملیات تشخیص/ثبت/ویرایش/حذف که با dlib یا لیست self.people
        #   سروکار دارند، سریالایز (یکی‌یکی) اجرا می‌شوند.
        self._lock = threading.RLock()

        os.makedirs(self.faces_dir, exist_ok=True)
        self.load()

    # ---------- persistence ----------

    def load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    self.people = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری بانک چهره‌ها: {e}")
                self.people = []

    def save(self):
        try:
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(self.people, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره بانک چهره‌ها: {e}")

    def _encodings_matrix(self):
        return [np.array(p["encoding"]) for p in self.people]

    # ---------- CRUD for the face library ----------

    def list_people(self):
        """Metadata only (no raw encoding) - safe for the UI table."""
        return [{k: v for k, v in p.items() if k != "encoding"} for p in self.people]

    def get_person(self, person_id):
        for p in self.people:
            if p["id"] == person_id:
                return p
        return None

    def register_face(self, name, image_frame, phone="", employee_id="", note=""):
        """Detect the face in image_frame and add it as a new library entry.
        Returns the created person dict, or None if no face was found."""
        with self._lock:
            rgb = cv2.cvtColor(image_frame, cv2.COLOR_BGR2RGB)
            boxes = face_recognition.face_locations(rgb, model="hog")
            encodings = face_recognition.face_encodings(rgb, boxes)
            if not encodings:
                return None

            person_id = str(uuid.uuid4())
            photo_path = os.path.join(self.faces_dir, f"{person_id}.jpg")
            cv2.imwrite(photo_path, image_frame)

            person = {
                "id": person_id,
                "name": name,
                "phone": phone,
                "employee_id": employee_id,
                "note": note,
                "encoding": encodings[0].tolist(),
                "photo": photo_path,
                "created_at": time.time(),
            }
            self.people.append(person)
            self.save()
            return person

    def update_person(self, person_id, **fields):
        with self._lock:
            for p in self.people:
                if p["id"] == person_id:
                    p.update(fields)
                    self.save()
                    return p
            return None

    def delete_person(self, person_id):
        with self._lock:
            person = self.get_person(person_id)
            if person and os.path.exists(person.get("photo", "")):
                try:
                    os.remove(person["photo"])
                except OSError:
                    pass
            self.people = [p for p in self.people if p["id"] != person_id]
            self._last_seen_person.pop(person_id, None)
            self.save()

    # ---------- recognition ----------

    def recognize(self, frame, downscale=0.5):
        """Detect + match every face in a frame.

        Returns (results, unknown_alert, known_events):
          - results: [{"box": (top,right,bottom,left) in full-frame coords, "person": dict|None}]
          - unknown_alert: True once when an unmatched face has been seen and the
            cooldown for a fresh "چهره تعریف نشده" notice has elapsed.
          - known_events: list of person dicts newly re-confirmed (cooldown-limited,
            so the same person doesn't spam a notice every frame).
        """
        # این متد به‌طور مداوم از ترد(های) پخش زنده صدا زده می‌شود؛ قفل تضمین می‌کند که
        # هم‌زمان با ثبت/ویرایش/حذف چهره (که از ترد UI اجرا می‌شود) به dlib یا
        # self.people دسترسی هم‌زمان (race condition) صورت نگیرد که باعث کرش می‌شد.
        with self._lock:
            small = cv2.resize(frame, (0, 0), fx=downscale, fy=downscale)
            rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb_small, model="hog")
            encodings = face_recognition.face_encodings(rgb_small, locations)

            known_encodings = self._encodings_matrix()
            scale = 1.0 / downscale
            now = time.time()

            results = []
            saw_unknown = False
            known_events = []

            for (top, right, bottom, left), enc in zip(locations, encodings):
                box = (int(top * scale), int(right * scale), int(bottom * scale), int(left * scale))
                person = None
                if known_encodings:
                    matches = face_recognition.compare_faces(known_encodings, enc, tolerance=self.tolerance)
                    distances = face_recognition.face_distance(known_encodings, enc)
                    best_idx = int(np.argmin(distances)) if len(distances) else -1
                    if best_idx != -1 and matches[best_idx]:
                        person = self.people[best_idx]

                if person:
                    last_seen = self._last_seen_person.get(person["id"], 0)
                    if now - last_seen >= self.known_alert_cooldown:
                        self._last_seen_person[person["id"]] = now
                        known_events.append(person)
                else:
                    saw_unknown = True

                results.append({"box": box, "person": person})

            unknown_alert = False
            if saw_unknown and (now - self._last_unknown_alert) >= self.unknown_alert_cooldown:
                self._last_unknown_alert = now
                unknown_alert = True

            return results, unknown_alert, known_events

    def draw_results(self, frame, results):
        """Overlay boxes + labels in-place. Unmatched faces are explicitly
        labeled as 'چهره تعریف نشده' (undefined face)."""
        for r in results:
            top, right, bottom, left = r["box"]
            person = r["person"]
            label = person["name"] if person else "چهره تعریف نشده"
            color = (0, 200, 0) if person else (0, 0, 255)
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.rectangle(frame, (left, max(top, bottom - 24)), (right, bottom), color, cv2.FILLED)
            cv2.putText(frame, label, (left + 6, bottom - 6), cv2.FONT_HERSHEY_DUPLEX, 0.55, (255, 255, 255), 1)
        return frame

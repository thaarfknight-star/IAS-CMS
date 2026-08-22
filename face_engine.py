import os
import pickle
import cv2
import face_recognition
import numpy as np

class FaceEngine:
    def __init__(self, db_path="encodings.pkl", faces_dir="faces"):
        self.db_path = db_path
        self.faces_dir = faces_dir
        self.known_face_encodings = []
        self.known_face_names = []
        
        if not os.path.exists(self.faces_dir):
            os.makedirs(self.faces_dir)
            
        self.load_encodings()

    def load_encodings(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "rb") as f:
                    data = pickle.load(f)
                    self.known_face_encodings = data.get("encodings", [])
                    self.known_face_names = data.get("names", [])
            except Exception as e:
                print(f"Error loading encodings: {e}")
                self.known_face_encodings = []
                self.known_face_names = []

    def save_encodings(self):
        try:
            with open(self.db_path, "wb") as f:
                pickle.dump({
                    "encodings": self.known_face_encodings,
                    "names": self.known_face_names
                }, f)
        except Exception as e:
            print(f"Error saving encodings: {e}")

    def register_face(self, name: str, image_frame: np.ndarray) -> bool:
        """Register a new face from an image frame and save embeddings."""
        rgb_frame = cv2.cvtColor(image_frame, cv2.COLOR_BGR2RGB)
        boxes = face_recognition.face_locations(rgb_frame, model="hog")
        encodings = face_recognition.face_encodings(rgb_frame, boxes)
        
        if len(encodings) == 0:
            return False
        
        self.known_face_encodings.append(encodings[0])
        self.known_face_names.append(name)
        self.save_encodings()
        
        file_path = os.path.join(self.faces_dir, f"{name}.jpg")
        cv2.imwrite(file_path, image_frame)
        return True

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Detect and recognize faces in live video frame."""
        small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

        face_locations = face_recognition.face_locations(rgb_small, model="hog")
        face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

        for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
            name = "Unknown"
            if self.known_face_encodings:
                matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=0.5)
                face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                best_match_index = np.argmin(face_distances) if len(face_distances) > 0 else -1
                
                if best_match_index != -1 and matches[best_match_index]:
                    name = self.known_face_names[best_match_index]

            top, right, bottom, left = top * 2, right * 2, bottom * 2, left * 2
            
            color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.rectangle(frame, (left, bottom - 25), (right, bottom), color, cv2.FILLED)
            cv2.putText(frame, name, (left + 6, bottom - 6), cv2.FONT_HERSHEY_DUPLEX, 0.6, (255, 255, 255), 1)

        return frame

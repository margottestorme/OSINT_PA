"""
Worker de capture webcam temps réel — OSINT Platform.

Un thread unique capture la webcam, détecte les visages (HOG), les annote et
compare les embeddings à la base (throttlé à 1/s). Le dernier frame annoté est
gardé en mémoire (JPEG) pour le flux MJPEG, puis écrasé au frame suivant.
Aucune frame n'est écrite sur le disque.

Instance singleton : `stream_worker` (une seule capture à la fois).
"""
import os
import threading
import time
from contextlib import contextmanager

import cv2
import face_recognition
from loguru import logger

from app.facial.engine import compare_embedding

# Réduit la verbosité interne d'OpenCV (best effort selon la version)
try:
    cv2.setLogLevel(0)
except Exception:  # pragma: no cover
    pass


@contextmanager
def _suppress_stderr():
    """Redirige temporairement le fd 2 (stderr) vers /dev/null.

    Fait taire les messages C++ d'OpenCV (V4L2 "can't open camera", etc.)
    autour des appels cv2 sans masquer les logs applicatifs (hors de ce bloc).
    """
    saved_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_fd, 2)
        os.close(devnull_fd)
        os.close(saved_fd)


class StreamWorker:
    """Capture + détection + reconnaissance faciale en continu, dans un thread."""

    def __init__(self, camera_index: int = 0, max_fps: int = 30, match_interval: float = 1.0):
        self._camera_index = camera_index
        self._max_fps = max_fps
        self._match_interval = match_interval  # secondes entre 2 comparaisons DB

        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

        self._latest_jpeg: bytes | None = None
        self._known: list[tuple[int, str, list]] = []  # (person_id, label, embedding)

        # Statistiques exposées via status()
        self._fps = 0.0
        self._faces_detected = 0
        self._matches = 0
        self._last_match: dict | None = None
        self._last_labels: list[str] = []
        self._error: str | None = None

    # --- Configuration ---
    def set_known_faces(self, faces: list[tuple[int, str, list]]) -> None:
        """Renseigne les visages connus (chargés depuis la DB par la route async)."""
        with self._lock:
            self._known = faces

    # --- Cycle de vie ---
    def start(self) -> bool:
        if self._running:
            return False
        self._error = None
        self._matches = 0
        self._last_match = None
        self._running = True
        self._thread = threading.Thread(target=self._run, name="stream-worker", daemon=True)
        self._thread.start()
        logger.info("[worker] Capture démarrée")
        return True

    def stop(self) -> bool:
        if not self._running:
            return False
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        with self._lock:
            self._latest_jpeg = None  # libère le dernier frame
            self._faces_detected = 0
            self._fps = 0.0
            self._last_labels = []
        logger.info("[worker] Capture arrêtée")
        return True

    def is_running(self) -> bool:
        return self._running

    def get_frame(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "fps": round(self._fps, 1),
                "faces_detected": self._faces_detected,
                "matches": self._matches,
                "last_match": self._last_match,
                "error": self._error,
            }

    # --- Boucle de capture (thread) ---
    def _run(self) -> None:
        with _suppress_stderr():
            cap = cv2.VideoCapture(self._camera_index)
            opened = cap is not None and cap.isOpened()

        if not opened:
            self._error = "Webcam indisponible"
            self._running = False
            logger.error("[worker] Webcam indisponible — arrêt")
            if cap is not None:
                cap.release()
            return

        frame_period = 1.0 / self._max_fps
        last_match_time = 0.0
        prev_ts = time.monotonic()
        try:
            while self._running:
                loop_start = time.monotonic()

                with _suppress_stderr():
                    ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                locations = face_recognition.face_locations(rgb, model="hog")
                self._faces_detected = len(locations)

                # Comparaison DB throttlée (1/s max) et seulement si visage présent
                now = time.monotonic()
                if locations and (now - last_match_time) >= self._match_interval:
                    self._last_labels = self._match_faces(rgb, locations)
                    last_match_time = now

                self._draw(frame, locations)

                with _suppress_stderr():
                    ret, buf = cv2.imencode(".jpg", frame)
                if ret:
                    with self._lock:
                        self._latest_jpeg = buf.tobytes()

                del frame, rgb  # libère la mémoire du frame courant

                elapsed = time.monotonic() - loop_start
                remaining = frame_period - elapsed
                if remaining > 0:
                    time.sleep(remaining)

                # FPS effectif : débit réel entre 2 frames (plafonné à max_fps)
                now_ts = time.monotonic()
                delta = now_ts - prev_ts
                prev_ts = now_ts
                self._fps = 1.0 / delta if delta > 0 else 0.0
        except Exception as e:  # pragma: no cover - dépend du matériel
            self._error = "Erreur de capture"
            logger.error(f"[worker] Erreur boucle : {e}")
        finally:
            with _suppress_stderr():
                cap.release()
            with self._lock:
                self._latest_jpeg = None
            self._running = False

    def _match_faces(self, rgb, locations) -> list[str]:
        """Compare chaque visage aux embeddings connus. Retourne les libellés."""
        with self._lock:
            known = list(self._known)
        db_list = [(pid, emb) for pid, _label, emb in known]
        names = {pid: label for pid, label, _emb in known}

        encodings = face_recognition.face_encodings(rgb, locations)
        labels: list[str] = []
        for enc in encodings:
            match = compare_embedding(enc.tolist(), db_list)
            if match:
                pid, distance = match
                confidence = round(1.0 - distance, 3)
                name = names.get(pid, f"#{pid}")
                labels.append(f"{name} {int(confidence * 100)}%")
                self._matches += 1
                self._last_match = {"person_id": pid, "name": name, "score": confidence}
            else:
                labels.append("Inconnu")
        return labels

    def _draw(self, frame, locations) -> None:
        """Dessine les rectangles + libellés (vert si connu, rouge sinon)."""
        for i, (top, right, bottom, left) in enumerate(locations):
            label = self._last_labels[i] if i < len(self._last_labels) else "..."
            known = label not in ("Inconnu", "...")
            color = (16, 185, 129) if known else (68, 68, 239)  # BGR (vert / rouge)
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.rectangle(frame, (left, bottom - 22), (right, bottom), color, cv2.FILLED)
            cv2.putText(frame, label, (left + 6, bottom - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)


# Instance singleton
stream_worker = StreamWorker()

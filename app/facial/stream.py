"""
Détection et introspection de la webcam (module flux temps réel).

Aucune frame n'est stockée : ces fonctions ouvrent brièvement le périphérique
pour lire ses capacités puis le relâchent immédiatement.
"""
import cv2
from loguru import logger


def detect_camera(index: int = 0) -> bool:
    """Retourne True si une webcam est disponible à l'index donné."""
    cap = None
    try:
        cap = cv2.VideoCapture(index)
        return bool(cap is not None and cap.isOpened())
    except Exception as e:
        logger.warning(f"[stream] detect_camera({index}) : {e}")
        return False
    finally:
        if cap is not None:
            cap.release()


def get_camera_info(index: int = 0) -> dict:
    """Retourne les caractéristiques de la webcam.

    {available, index, width, height, fps} — available=False si aucune caméra.
    """
    cap = None
    try:
        cap = cv2.VideoCapture(index)
        if cap is None or not cap.isOpened():
            logger.info(f"[stream] Aucune webcam disponible à l'index {index}")
            return {"available": False, "index": index, "width": 0, "height": 0, "fps": 0.0}

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = round(float(cap.get(cv2.CAP_PROP_FPS)) or 0.0, 1)
        logger.info(f"[stream] Webcam détectée : {width}x{height} @ {fps} fps")
        return {"available": True, "index": index, "width": width, "height": height, "fps": fps}
    except Exception as e:
        logger.warning(f"[stream] get_camera_info({index}) : {e}")
        return {"available": False, "index": index, "width": 0, "height": 0, "fps": 0.0}
    finally:
        if cap is not None:
            cap.release()

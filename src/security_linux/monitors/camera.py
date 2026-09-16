"""Moniteur webcam : détection de visage 100 % locale (OpenCV, Haar cascade).

Aucune image n'est envoyée hors de la machine. Une image peut être
conservée localement uniquement si `capture_on_lock` est actif (événement).
"""
from __future__ import annotations

import os
import time

import security_linux.config as config
from security_linux.monitors.base import Monitor, MonitorResult


def _cv2():
    import cv2  # noqa: PLC0415

    return cv2


class CameraMonitor(Monitor):
    name = "camera"

    def __init__(self, cfg: dict) -> None:
        monitored = dict(cfg)
        monitored.setdefault("enabled", True)
        super().__init__(monitored)
        self._device = cfg.get("device", "/dev/video0")
        self._confirm_frames = int(cfg.get("absent_confirmations", 3))
        self._capture_on_lock = bool(cfg.get("capture_on_lock", True))
        self._cap: object | None = None
        self._last_capture_path: str | None = None
        try:
            _cv2()
            self._cv2_available = True
        except Exception:
            self._cv2_available = False

    def resolve_device(self) -> str | None:
        """Device configuré, sinon auto-détection générique (/dev/video*)."""
        if os.path.exists(self._device):
            return self._device
        for index in range(16):
            candidate = f"/dev/video{index}"
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def list_devices() -> list[str]:
        """Périphériques vidéo présents (/dev/video*)."""
        return [d for i in range(16) if os.path.exists(d := f"/dev/video{i}")]

    @staticmethod
    def device_label(device: str) -> str:
        """Nom lisible d'un périphérique (via sysfs v4l), sinon le chemin."""
        base = os.path.basename(device)
        root = f"/sys/class/video4linux/{base}/name"
        try:
            with open(root, encoding="utf-8", errors="replace") as fh:
                name = fh.read().strip()
            if name:
                return name
        except OSError:
            pass
        return device

    def supported(self) -> bool:
        return self._cv2_available and self.resolve_device() is not None

    def _open_cap(self):
        if self._cap is not None:
            return self._cap
        device = self.resolve_device()
        if device is None:
            return None
        cv2 = _cv2()
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap = cap
        return cap

    def _close_cap(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def face_detected(self) -> bool:
        if not self.supported():
            return True
        cv2 = _cv2()
        cascade = cv2.CascadeClassifier(
            os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        )
        cap = self._open_cap()
        try:
            ok, frame = cap.read()
            if not ok or frame is None or frame.size == 0:
                return True
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60))
            return len(faces) > 0
        except Exception:
            return True
        finally:
            pass

    def capture_snapshot(self, reason: str) -> str | None:
        """Sauvegarde locale d'une image (pas de cloud)."""
        if not self.supported():
            return None
        cv2 = _cv2()
        cap = self._open_cap()
        try:
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
            drop = config.data_dir() / "captures"
            os.makedirs(drop, exist_ok=True)
            os.chmod(drop, 0o700)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(str(drop), f"{reason}_{ts}.jpg")
            cv2.imwrite(path, frame)
            self._last_capture_path = path
            return path
        except Exception:
            return None
        finally:
            self._close_cap()

    def tick(self) -> MonitorResult:
        import time as _time

        if not self.enabled():
            return MonitorResult(self.name, "disabled", "moniteur coupé")
        if not self.supported():
            return MonitorResult(self.name, "unavailable", "webcam ou OpenCV indisponible")
        self._device = self.cfg.get("device", self._device)
        faces_seen = 0
        for _ in range(max(1, self._confirm_frames)):
            if self.face_detected():
                faces_seen += 1
            if faces_seen > 0:
                break
        present = faces_seen > 0
        self._start_absent_if(not present, _time.time())
        if present:
            return MonitorResult(self.name, "present", "visage détecté")
        elapsed = int(self.absent_elapsed_seconds)
        return MonitorResult(
            self.name,
            "absent",
            f"aucun visage depuis {elapsed}s",
            absent_since=self.absent_since,
        )
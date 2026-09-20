"""Validation faciale 100 % locale : photo de référence ↔ visage devant la caméra.

La GUI permet d'enregistrer une photo de référence puis de cliquer
« Vérifier visage » : l'image alors capturée est comparée au visage de la
photo enregistrée. Le résultat (correspondance + score de similarité) est
affiché immédiatement, ce qui permet de tester si le passage face à la
caméra matche. Aucune donnée ne sort de la machine.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import security_linux.config as config
import security_linux.led as led
from security_linux.i18n import _

_FACE_SIZE = 128
_CASCADE = "haarcascade_frontalface_default.xml"
_DEFAULT_THRESHOLD = 0.45


def default_threshold() -> float:
    """Seuil de correspondance configuré (Réglages → Webcam), sinon 0.45."""
    try:
        value = float(config.load_config().get("faces", {}).get("threshold", _DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD
    return min(max(value, 0.0), 1.0)


def _cv2():
    import cv2  # noqa: PLC0415

    return cv2


def _cascade(cv2):
    return cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, _CASCADE))


@dataclass
class FaceCheckResult:
    ok: bool = False
    matched: bool = False
    score: float = 0.0
    message: str = ""
    path: str = ""
    details: dict = field(default_factory=dict)


def reference_path() -> Path:
    """Chemin de la photo de référence (visage enregistré par l'utilisateur)."""
    return config.data_dir() / "faces" / "reference.jpg"


def resolve_device(device: str | None = None) -> str | None:
    """Périphérique demandé, sinon celui de la config, sinon le premier /dev/video*."""
    if device and os.path.exists(device):
        return device
    try:
        from security_linux.monitors.camera import CameraMonitor  # noqa: PLC0415

        cfg_device = config.load_config().get("camera", {}).get("device")
        if cfg_device and os.path.exists(cfg_device):
            return cfg_device
        devices = CameraMonitor.list_devices()
        if devices:
            return devices[0]
    except Exception:  # noqa: BLE001
        pass
    return None


def normalize_face(crop_bgr: object) -> object | None:
    """Préparation du visage pour comparaison : gris + contour-effacement + taille fixe."""
    import numpy as np

    cv2 = _cv2()
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    gray = cv2.resize(gray, (_FACE_SIZE, _FACE_SIZE), interpolation=cv2.INTER_AREA)
    return gray.astype(np.float32) / 255.0


def _ncc(a, b):
    """Corrélation croisée normalisée entre deux vecteurs lissage à zéro."""
    import numpy as np

    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if denom == 0.0:
        return 0.0
    return float(np.sum(a * b) / denom)


def _gradient_magnitude(gray: object):
    """Profil géométrique : norme du gradient du visage (yeux, nez, bouche)."""
    import numpy as np

    gx, gy = np.gradient(np.asarray(gray, dtype=np.float32))
    return np.sqrt(gx * gx + gy * gy)


def similarity_components(crop_a: object, crop_b: object) -> tuple[float, float, float]:
    """Score global et ses deux composantes (score, intensité, géométrie)."""
    ga = _gradient_magnitude(crop_a)
    gb = _gradient_magnitude(crop_b)
    intensity = _ncc(crop_a, crop_b)
    geometry = _ncc(ga, gb)
    return (0.5 * intensity + 0.5 * geometry, intensity, geometry)


def similarity(crop_a: object, crop_b: object) -> float:
    """Score de similarité global entre deux visages normalisés (0..1 d'usage)."""
    return similarity_components(crop_a, crop_b)[0]


def _largest_face(frame_bgr, cascade):
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return None
    cv2 = _cv2()
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60))
    if not len(faces):
        return None
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    x, y, w, h = faces[0]
    return frame_bgr[max(0, y - 10) : y + h + 10, max(0, x - 10) : x + w + 10]


def _open_cap(cv2, device: str):
    for flag in (cv2.CAP_V4L2, None):
        if flag is None:
            cap = cv2.VideoCapture(device)
        else:
            cap = cv2.VideoCapture(device, flag)
        try:
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                return cap
            cap.release()
        except Exception:  # noqa: BLE001
            cap.release()
    return None


def capture_frame(device: str, timeout: float = 8.0):
    """Capture une image. Bloque jusqu'à obtenir une image lisible."""
    cv2 = _cv2()
    cap = _open_cap(cv2, device)
    if cap is None:
        return None
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                led.blink()
                return frame
            time.sleep(0.05)
        return None
    finally:
        cap.release()


def save_reference(device: str | None = None, timeout: float = 10.0) -> tuple[bool, str]:
    """Enregistre la photo (avec visage) comme référence.

    Attend qu'un visage apparaisse devant la caméra, puis se place sur un
    visage et resserre : filme jusqu'à ce que la zone faciale soit stable.
    """
    dev = resolve_device(device)
    if dev is None:
        return False, _("Aucun périphérique vidéo détecté.")
    cv2 = _cv2()
    cascade = _cascade(cv2)
    best = None
    best_roi = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = capture_frame(dev, timeout=2.0)
        if frame is None:
            return False, _("Aucune image capturée ({dev}).").format(dev=dev)
        face = _largest_face(frame, cascade)
        if face is not None:
            roi = face.shape[0] * face.shape[1]
            if roi >= best_roi:
                best_roi = roi
                best = frame
        time.sleep(0.1)
    if best is None:
        return False, _("Aucun visage détecté pendant la capture. Réessayez face à la caméra.")
    path = reference_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        cv2.imwrite(str(path), best)
        return True, _("Photo de référence enregistrée : {path}").format(path=path)
    except Exception as exc:  # noqa: BLE001
        return False, _("Erreur d'écriture : {erreur}").format(erreur=exc)


def verify_face(device: str | None = None, threshold: float = _DEFAULT_THRESHOLD) -> FaceCheckResult:
    """Vérifie si le visage devant la caméra correspond à la photo de référence."""
    ref = reference_path()
    if not ref.exists():
        return FaceCheckResult(
            ok=False,
            message=_("Aucune photo de référence. Enregistrez-la d'abord."),
            path=str(ref),
        )
    cv2 = _cv2()
    cascade = _cascade(cv2)
    ref_img = cv2.imread(str(ref))
    ref_face = _largest_face(ref_img, cascade)
    if ref_face is None:
        return FaceCheckResult(
            ok=False,
            message=_("Aucun visage détecté sur la photo de référence."),
            path=str(ref),
        )
    ref_norm = normalize_face(ref_face)
    dev = resolve_device(device)
    if dev is None:
        return FaceCheckResult(ok=False, message=_("Aucun périphérique vidéo détecté."), path=str(ref))

    # Plusieurs captures : le score est la moyenne des visages validés. Une
    # seule image peut être bruitée (similitude artificiellement haute) ; la
    # moyenne rend le verdict stable et les échecs reproductibles.
    n_frames = 3
    scores: list[float] = []
    intents: list[float] = []
    geoms: list[float] = []
    deadline = time.monotonic() + 8.0
    while len(scores) < n_frames and time.monotonic() < deadline:
        frame = capture_frame(dev, timeout=2.0)
        if frame is None:
            break
        live_face = _largest_face(frame, cascade)
        if live_face is None:
            continue
        sc, it_c, gm_c = similarity_components(ref_norm, normalize_face(live_face))
        scores.append(sc)
        intents.append(it_c)
        geoms.append(gm_c)
    if not scores:
        return FaceCheckResult(
            ok=False,
            message=_("Aucun visage détecté devant la caméra. Approchez votre visage."),
            path=str(ref),
            details={"reference": str(ref)},
        )
    score = sum(scores) / len(scores)
    intensity = sum(intents) / len(intents)
    geometry = sum(geoms) / len(geoms)
    matched = score >= threshold
    if matched:
        message = _(
            "Correspondance confirmée (score {score:.2f} — intensité {it:.2f} / géométrie {geom:.2f})."
        ).format(score=score, it=intensity, geom=geometry)
    else:
        message = _(
            "Pas de correspondance (score {score:.2f} < {seuil:.2f} — intensité {it:.2f} / géométrie {geom:.2f})."
        ).format(score=score, seuil=threshold, it=intensity, geom=geometry)
    return FaceCheckResult(
        ok=True,
        matched=matched,
        score=score,
        message=message,
        path=str(ref),
        details={
            "reference": str(ref),
            "threshold": threshold,
            "scores": scores,
            "intensity": intensity,
            "geometry": geometry,
        },
    )
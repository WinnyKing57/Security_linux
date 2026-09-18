"""Tests de la validation faciale (photo de référence ↔ caméra).

Aucun accès au matériel : on n'utilise que des entrées synthétiques et les
chemins de sortie du mode debug (config/état isolés).
"""
import numpy as np

from security_linux import faces
from security_linux.config import data_dir


def test_reference_path_in_data_dir():
    assert faces.reference_path().is_relative_to(data_dir())


def test_similarity_identical_face_is_perfect():
    crop = np.full((128, 128), 0.5, dtype=np.float32)
    crop[32:96, 48:80] = 0.9
    assert faces.similarity(crop, crop.copy()) > 0.99


def test_similarity_different_faces_is_low():
    rng = np.random.default_rng(42)
    a = rng.random((128, 128)).astype(np.float32)
    b = rng.random((128, 128)).astype(np.float32)
    assert faces.similarity(a, b) < faces._DEFAULT_THRESHOLD


def test_similarity_constant_flate_does_not_crash():
    const = np.full((128, 128), 0.5, dtype=np.float32)
    assert faces.similarity(const, const) == 0.0


def test_normalize_face_shape_and_range():
    img = (np.random.default_rng(1).random((200, 150, 3)) * 255).astype(np.uint8)
    out = faces.normalize_face(img)
    assert out is not None
    assert out.shape == (faces._FACE_SIZE, faces._FACE_SIZE)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_verify_without_reference_fails():
    ref = faces.reference_path()
    ref.unlink(missing_ok=True)
    assert not ref.exists()
    result = faces.verify_face()
    assert not result.ok
    assert "référence" in result.message or "reference" in result.message


def test_resolve_device_returns_none_when_absent(monkeypatch):
    import os

    monkeypatch.setattr(os.path, "exists", lambda *_a: False)
    monkeypatch.setattr(faces.config, "load_config", lambda *_a, **_k: {"camera": {"device": "/dev/video0"}})
    monkeypatch.setattr("security_linux.monitors.camera.CameraMonitor.list_devices", staticmethod(lambda: []))
    assert faces.resolve_device() is None


def test_resolve_device_prefers_requested(monkeypatch):
    import os

    monkeypatch.setattr(os.path, "exists", lambda p: p == "/dev/video0")
    assert faces.resolve_device("/dev/video0") == "/dev/video0"
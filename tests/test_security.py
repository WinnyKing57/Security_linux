"""Sécurité : canal de commande HMAC, détection de tamper, code admin,
rotation + intégrité du journal, verrouillage par inactivité."""
from __future__ import annotations

import copy
import json

from security_linux.config import DEFAULT_CONFIG, set_admin_code, valid_admin_code
from security_linux.engine import Engine
from security_linux.monitors import MonitorResult


def _clean_events_files():
    import security_linux.events as events

    for name in ("command.json", "command.key", "command.key.tmp"):
        (events.command_path().with_name(name)).unlink(missing_ok=True)
    events.events_path().unlink(missing_ok=True)
    for idx in range(1, 6):
        events.events_path().with_name(f"events.log.{idx}").unlink(missing_ok=True)


# ------------------------------------------------------------ canal de commande
def test_command_hmac_roundtrip():
    _clean_events_files()
    import security_linux.events as events

    events.write_command({"type": "arm", "value": True})
    cmd = events.consume_command()
    assert cmd is not None
    assert cmd["type"] == "arm" and cmd["value"] is True
    assert "sig" not in cmd  # la signature ne sort jamais du canal


def test_command_invalid_signature_rejected():
    _clean_events_files()
    import security_linux.events as events

    forged = {"type": "arm", "value": False, "ts": "2026-01-01T00:00:00", "expires": 9999999999}
    events.command_path().write_text(
        json.dumps({**forged, "sig": "fausse-signature"}), "utf-8"
    )
    cmd = events.consume_command()
    assert cmd is None  # commande non signée correctement : ignorée


def test_command_missing_signature_rejected():
    _clean_events_files()
    import security_linux.events as events
    import time

    events.command_path().write_text(
        json.dumps({"type": "arm", "value": False, "ts": "now", "expires": time.time() + 30}),
        "utf-8",
    )
    assert events.consume_command() is None


def test_command_key_permissions():
    _clean_events_files()
    import security_linux.events as events

    events.write_command({"type": "arm", "value": True})
    assert events.command_key_path().exists()
    assert events.command_key_path().stat().st_mode & 0o777 == 0o600
    assert len(events.command_key_path().read_bytes()) >= 32


# ------------------------------------------------------------ tamper (capteur)
def _make_engine(cfg):
    locks = {"n": 0}

    def lock_fn():
        locks["n"] += 1
        return True

    return Engine(cfg, lock_fn, lambda: False), locks


def _home_state():
    return {
        "camera": MonitorResult("camera", "present"),
        "bluetooth": MonitorResult("bluetooth", "present"),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }


def test_engine_tamper_detection_on_sensor_loss():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    engine, _ = _make_engine(cfg)

    states = _home_state()
    d1 = engine.tick(states, manual_armed=True)
    assert d1["tamper"] is False

    states["camera"] = MonitorResult("camera", "unavailable", "webcam débranchée")
    d2 = engine.tick(states, manual_armed=True)
    assert d2["tamper"] is True

    # une fois rétablie, plus de signal de tamper
    states["camera"] = MonitorResult("camera", "present")
    d3 = engine.tick(states, manual_armed=True)
    assert d3["tamper"] is False


def test_engine_no_tamper_when_unarmed():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    engine, _ = _make_engine(cfg)

    states = _home_state()
    engine.tick(states, manual_armed=True)
    states["camera"] = MonitorResult("camera", "unavailable")
    d = engine.tick(states, manual_armed=False)
    assert d["tamper"] is False


def test_engine_no_tamper_on_first_cycle():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    engine, _ = _make_engine(cfg)
    states = _home_state()
    states["camera"] = MonitorResult("camera", "unavailable")
    d = engine.tick(states, manual_armed=True)
    assert d["tamper"] is False  # premier cycle : pas de transition connue


# ------------------------------------------------------------ code admin
def test_admin_code_min_length():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    assert not valid_admin_code("12345")
    assert valid_admin_code("123456")
    assert not valid_admin_code("  ab ")
    import pytest

    with pytest.raises(ValueError):
        set_admin_code(cfg, "1234")


# ------------------------------------------------------------ journal (rotation)
def test_event_log_rotation(monkeypatch):
    _clean_events_files()
    import security_linux.events as events

    monkeypatch.setattr(events, "MAX_EVENT_LOG_BYTES", 200)
    for index in range(60):
        events.log_event("test", f"ligne {index}")
    assert events.events_path().with_name("events.log.1").exists()
    # la chaîne d'intégrité reste valide après rotation
    assert events.verify_event_log()["ok"] is True


def test_event_log_integrity_chain():
    _clean_events_files()
    import security_linux.events as events

    events.log_event("test", "a")
    events.log_event("test", "b")
    assert events.verify_event_log()["ok"] is True

    path = events.events_path()
    lines = path.read_text("utf-8").splitlines()
    forged = json.loads(lines[1])
    forged["message"] = "trace falsifiée"
    lines[1] = json.dumps(forged, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", "utf-8")
    assert events.verify_event_log()["ok"] is False


def test_event_log_has_chain_field():
    _clean_events_files()
    import security_linux.events as events

    events.log_event("test", "chaîne")
    record = events.read_events(limit=1)[-1]
    assert len(record["chain"]) == 64


# ------------------------------------------------------------ inactivité
def test_engine_idle_fallback_locks(monkeypatch):
    import security_linux.engine as engmod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["idle_lock_minutes"] = 5
    cfg["general"]["lock_grace_seconds"] = 0
    engine, locks = _make_engine(cfg)
    monkeypatch.setattr(engmod.idle, "idle_seconds", lambda: 6 * 60)

    states = _home_state()
    d = engine.tick(states, manual_armed=True)
    assert d["action"] == "lock"
    assert locks["n"] == 1


def test_engine_idle_disabled_by_default():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    assert cfg["general"]["idle_lock_minutes"] == 0
    engine, locks = _make_engine(cfg)
    states = _home_state()
    d = engine.tick(states, manual_armed=True)
    assert d["action"] is None


def test_engine_idle_no_lock_below_threshold(monkeypatch):
    import security_linux.engine as engmod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["idle_lock_minutes"] = 5
    engine, locks = _make_engine(cfg)
    monkeypatch.setattr(engmod.idle, "idle_seconds", lambda: 60)
    d = engine.tick(_home_state(), manual_armed=True)
    assert d["action"] is None
    assert locks["n"] == 0


# ------------------------------------------------------------ défauts
def test_security_defaults():
    assert DEFAULT_CONFIG["braquage"]["on_tamper"] is True
    assert "idle_lock_minutes" in DEFAULT_CONFIG["general"]
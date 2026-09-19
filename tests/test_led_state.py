"""Tests du voyant webcam dans les états publiés (state.json).

Vérifie que _publish expose `led_active` / `led_last_ts` selon le dernier
clignotement, sans matériel (led.last_blink est mocké).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import security_linux.daemon as daemon_module
import security_linux.events as events
from security_linux.daemon import Daemon
from security_linux.monitors import MonitorResult


def _publish_with_led(monkeypatch, blink_ts):
    """Exécute Daemon._publish avec un clignotement donné, retourne l'état publié."""
    monkeypatch.setattr(daemon_module.led, "last_blink", lambda: blink_ts)
    monkeypatch.setattr(daemon_module.events, "write_state", lambda payload: None)
    daemon = Daemon(one_shot=True)
    states = {"camera": MonitorResult("camera", "absent", "aucun visage")}
    decision = {"armed": {"armed": True}, "conditions": {}, "time_to_lock": 0.0}
    seen = {}
    monkeypatch.setattr(daemon_module.events, "write_state", lambda payload: seen.update(payload))
    daemon._publish(states, decision)
    return seen


def test_publish_led_active_recent(monkeypatch):
    now = datetime.now(timezone.utc)
    out = _publish_with_led(monkeypatch, now - timedelta(seconds=10))
    assert out["led_active"] is True
    assert out["led_last_ts"] is not None


def test_publish_led_inactive_after_one_minute(monkeypatch):
    now = datetime.now(timezone.utc)
    out = _publish_with_led(monkeypatch, now - timedelta(seconds=120))
    assert out["led_active"] is False
    assert out["led_last_ts"] is not None


def test_publish_no_blink_yet(monkeypatch):
    out = _publish_with_led(monkeypatch, None)
    assert out["led_active"] is False
    assert out["led_last_ts"] is None


def test_read_state_exposes_led(tmp_path):
    path = events.state_path()
    payload = {
        "mode": "debug",
        "machine_state": "debug",
        "admin_set": False,
        "armed_state": {"armed": False, "manual": False, "forced": False},
        "condition": "n/a",
        "time_to_lock": 0.0,
        "silentium_active": False,
        "led_active": True,
        "led_last_ts": "2026-01-01T00:00:00+00:00",
        "monitors": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".tmp"), "w", encoding="utf-8") as fh:
        import json

        fh.write(json.dumps(payload))
    import os

    os.replace(path.with_suffix(".tmp"), path)
    assert events.read_state()["led_active"] is True


@pytest.fixture(autouse=True)
def _clean_state(tmp_path=None):
    p = events.state_path()
    if p.exists():
        p.unlink()
    yield
    if p.exists():
        p.unlink()
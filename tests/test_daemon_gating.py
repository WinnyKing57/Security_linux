"""Tests de la boucle de surveillance du démon (vie privée webcam)."""
from __future__ import annotations

from security_linux.daemon import Daemon
from security_linux.monitors import MonitorResult


def _daemon():
    d = Daemon(one_shot=True)
    d.cfg["camera"]["enabled"] = True
    d.cfg["bluetooth"]["enabled"] = False
    d.cfg["general"]["armed"] = False
    return d


def _home(d):
    d.location.tick = lambda *_: MonitorResult("location", "home", at_home=True, offline=False)


def _away(d):
    d.location.tick = lambda *_: MonitorResult("location", "away", at_home=False, offline=False)


def test_camera_not_read_when_disarmed_with_tick_flag(monkeypatch):
    d = _daemon()
    _home(d)
    called: list[bool] = [False]

    def fake_tick(*_):
        called[0] = True
        return MonitorResult("camera", "present", "test")

    d.camera.tick = fake_tick
    states = d._poll_monitors({})
    assert called[0] is False, "la webcam ne doit pas être sondée quand le système est désarmé"
    assert states["camera"].status == "disabled"
    assert "désarmé" in states["camera"].detail


def test_camera_read_when_manually_armed(monkeypatch):
    d = _daemon()
    d.cfg["general"]["armed"] = True
    _home(d)
    called: list[bool] = [False]

    def fake_tick(*_):
        called[0] = True
        return MonitorResult("camera", "present", "test")

    d.camera.tick = fake_tick
    states = d._poll_monitors({})
    assert called[0] is True
    assert states["camera"].status == "present"


def test_camera_read_when_forced_rearm_away(monkeypatch):
    d = _daemon()
    d.cfg["general"]["armed"] = False
    _away(d)
    called: list[bool] = [False]

    def fake_tick(*_):
        called[0] = True
        return MonitorResult("camera", "present", "test")

    d.camera.tick = fake_tick
    states = d._poll_monitors({})
    assert called[0] is True
    assert states["camera"].status == "present"


def test_monitor_crash_logs_full_traceback(monkeypatch):
    d = _daemon()
    d.cfg["general"]["armed"] = True
    _home(d)
    calls: list[tuple] = []
    monkeypatch.setattr("security_linux.events.log_event", lambda *a, **k: calls.append((a, k)))

    def boom(*_):
        raise RuntimeError("panne-camera-test")

    d.camera.tick = boom
    states = d._poll_monitors({})
    errors = [a for a, _k in calls if a[0] == "error"]
    assert errors, "une erreur moniteur doit être journalisée"
    assert "Traceback" in errors[0][1] and "panne-camera-test" in errors[0][1]
    assert states["camera"].status == "unavailable"
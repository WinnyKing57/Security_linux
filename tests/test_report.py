"""Tests du rapport d'intrusion consolidé (mode braquage / tamper).

Aucun matériel : états de capteurs injectés, écriture uniquement dans le
répertoire de données du mode debug.
"""
from __future__ import annotations

import copy

from security_linux import report
from security_linux.config import DEFAULT_CONFIG
from security_linux.monitors import MonitorResult


def _states() -> dict[str, MonitorResult]:
    return {
        "camera": MonitorResult("camera", "absent", "aucun visage depuis 30s", extra={"absent_elapsed_seconds": 30}),
        "bluetooth": MonitorResult(
            "bluetooth",
            "absent",
            "téléphone non joignable",
            extra={"rssi": -75, "monitored_name": "téléphone", "other_connected": None},
        ),
        "location": MonitorResult("location", "home", "Wi-Fi : maison", at_home=True, offline=False),
    }


def _clean() -> None:
    for item in report.list_reports():
        import os

        try:
            os.unlink(item["path"])
        except OSError:
            pass


def test_build_report_writes_file_and_fields():
    _clean()
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    path = report.build_report(
        "braquage",
        cfg,
        _states(),
        capture_path="captures/lock_event_x.jpg",
        machine_state="armed",
    )
    assert path is not None and path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert report.reports_dir().stat().st_mode & 0o777 == 0o700

    payload = report.read_report(path)
    assert payload["kind"] == "braquage"
    assert payload["machine_state"] == "armed"
    assert payload["capture"] == "captures/lock_event_x.jpg"
    assert payload["monitors"]["camera"]["status"] == "absent"
    assert payload["monitors"]["bluetooth"]["extra"]["rssi"] == -75
    assert payload["config"]["decision_mode"] == "AND"


def test_build_report_leaks_no_secret():
    _clean()
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["admin_code"]["salt"] = "secret-sel"
    cfg["admin_code"]["hash"] = "secret-hachage"
    cfg["admin_code"]["failed_attempts"] = [1, 2, 3]
    path = report.build_report("tamper", cfg, _states(), machine_state="armed")
    raw = path.read_text("utf-8").lower()
    assert "admin_code" not in raw
    assert "secret-sel" not in raw
    assert "secret-hachage" not in raw


def test_report_does_not_expose_home_ssids():
    _clean()
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["location"]["home_ssids"] = ["SecretWifiABC", "AutreWifiXYZ"]
    path = report.build_report("braquage", cfg, _states(), machine_state="disarmed")
    raw = path.read_text("utf-8")
    assert "SecretWifiABC" not in raw
    assert "AutreWifiXYZ" not in raw
    payload = report.read_report(path)
    assert payload["config"]["location"]["home_ssids_count"] == 2


def test_list_reports_roundtrip():
    _clean()
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    path = report.build_report("braquage", cfg, _states(), machine_state="armed")
    items = report.list_reports()
    assert items
    assert any(item["path"] == str(path) for item in items)
    assert all(item["kind"] in ("braquage", "tamper") for item in items)
    assert items == sorted(items, key=lambda i: i["ts"], reverse=True)


# ------------------------------------------------------------ daemon (câblage)
def test_daemon_builds_report_on_braquage(monkeypatch):
    import security_linux.daemon as daemon_module
    from security_linux.daemon import Daemon

    calls: list[dict] = []

    def fake_build_report(kind, cfg, states, capture_path=None, machine_state=None, extra=None):
        calls.append({"kind": kind, "capture": capture_path, "machine_state": machine_state})
        return None

    class _NoThread:
        def __init__(self, _target=None, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(daemon_module.report, "build_report", fake_build_report)
    monkeypatch.setattr(daemon_module, "alerts", type("_A", (), {"play_alarm": staticmethod(lambda *a, **k: None)})())
    monkeypatch.setattr(daemon_module.threading, "Thread", _NoThread)

    daemon = Daemon(one_shot=True)
    daemon.cfg["braquage"]["enabled"] = True
    daemon._trigger_braquage(_states(), capture_path="captures/x.jpg", armed_state={"armed": True})
    assert calls and calls[-1]["kind"] == "braquage"
    assert calls[-1]["capture"] == "captures/x.jpg"
    assert calls[-1]["machine_state"] == "debug"  # tests forcés en mode debug


def test_daemon_no_report_when_braquage_disabled(monkeypatch):
    import security_linux.daemon as daemon_module
    from security_linux.daemon import Daemon

    calls: list[dict] = []

    def fake_build_report(kind, cfg, states, capture_path=None, machine_state=None, extra=None):
        calls.append(kind)
        return None

    monkeypatch.setattr(daemon_module.report, "build_report", fake_build_report)
    daemon = Daemon(one_shot=True)
    daemon.cfg["braquage"]["enabled"] = False
    daemon._trigger_braquage(_states(), capture_path=None, armed_state={})
    assert not calls


def test_daemon_tamper_writes_report(monkeypatch):
    import security_linux.daemon as daemon_module
    from security_linux.daemon import Daemon

    calls: list[dict] = []

    def fake_build_report(kind, cfg, states, capture_path=None, machine_state=None, extra=None):
        calls.append({"kind": kind, "machine_state": machine_state})
        return None

    class _Alert:
        @staticmethod
        def send_intrusion_alert(_t):
            return True

    monkeypatch.setattr(daemon_module.report, "build_report", fake_build_report)
    monkeypatch.setattr(daemon_module.alerts, "send_intrusion_alert", staticmethod(lambda _t: True))
    daemon = Daemon(one_shot=True)
    daemon.cfg["braquage"]["enabled"] = False
    daemon._handle_tamper(_states(), armed_state={"armed": True})
    assert calls and calls[-1]["kind"] == "tamper"
    assert calls[-1]["machine_state"] == "debug"  # tests forcés en mode debug
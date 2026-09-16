"""Tests du noyau (sans GUI) : hachage, config, moteur, moniteur localisation."""
from __future__ import annotations

import copy

from security_linux import hashing
from security_linux.config import DEFAULT_CONFIG, load_config, save_config, set_admin_code, verify_admin_code
from security_linux.engine import Engine, effective_armed
from security_linux.monitors import MonitorResult
from security_linux.monitors.location import LocationMonitor


class FakeMon:
    def __init__(self, status, elapsed):
        self.status = status
        self._elapsed = elapsed

    @property
    def absent_elapsed_seconds(self):
        return self._elapsed


# ---------------------------------------------------------------- hachage
def test_hashing_roundtrip():
    salt = hashing.generate_salt()
    h = hashing.hash_code("mon-code-123", salt)
    assert hashing.verify_code("mon-code-123", salt, h)
    assert not hashing.verify_code("autre-code", salt, h)


def test_config_admin_code():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    assert not verify_admin_code(cfg, "1234")
    set_admin_code(cfg, "1234")
    assert verify_admin_code(cfg, "1234")
    assert not verify_admin_code(cfg, "9999")


def test_config_merge_and_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["camera"]["device"] = "/dev/video9"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded["camera"]["device"] == "/dev/video9"
    assert loaded["general"]["decision_mode"] == "AND"  # défauts préservés
    assert path.stat().st_mode & 0o777 == 0o600


# ------------------------------------------------------------ moteur
def _make_engine(cfg):
    locks = {"n": 0}

    def lock_fn():
        locks["n"] += 1
        return True

    return Engine(cfg, lock_fn, lambda: False), locks


def test_effective_armed_matrix():
    home = MonitorResult("location", "home", at_home=True, offline=False)
    away = MonitorResult("location", "away", at_home=False, offline=False)
    offline = MonitorResult("location", "away", at_home=False, offline=True)

    r = effective_armed(False, home, secure_when_offline=True)
    assert r["armed"] is False  # manuel off + à la maison => désarmé

    r = effective_armed(False, away, secure_when_offline=True)
    assert r["armed"] is True and r["forced"] is True  # hors domicile => réarmé

    r = effective_armed(False, offline, secure_when_offline=True)
    assert r["armed"] is True and r["forced"] is True  # hors ligne sécurisé => réarmé

    r = effective_armed(False, offline, secure_when_offline=False)
    assert r["armed"] is True  # le fait d'être "away" réarme, indépendamment du mode hors-ligne

    unknown_offline = MonitorResult("location", "unavailable", at_home=None, offline=True)
    r = effective_armed(False, unknown_offline, secure_when_offline=True)
    assert r["armed"] is True and r["forced"] is True
    r = effective_armed(False, unknown_offline, secure_when_offline=False)
    assert r["armed"] is False

    r = effective_armed(True, home, secure_when_offline=True)
    assert r["armed"] is True and r["forced"] is False


def test_engine_and_mode():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 20
    engine, locks = _make_engine(cfg)

    states = {
        "camera": FakeMon("absent", 30),
        "bluetooth": FakeMon("absent", 30),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    decision = engine.tick(states, manual_armed=True)
    assert decision["action"] == "lock"
    assert locks["n"] == 1


def test_engine_and_mode_needs_both():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 20
    engine, locks = _make_engine(cfg)

    states = {
        "camera": FakeMon("absent", 30),
        "bluetooth": FakeMon("present", 0),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    decision = engine.tick(states, manual_armed=True)
    assert decision["action"] is None
    assert locks["n"] == 0


def test_engine_or_mode():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "OR"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 20
    engine, locks = _make_engine(cfg)

    states = {
        "camera": FakeMon("absent", 30),
        "bluetooth": FakeMon("present", 0),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    decision = engine.tick(states, manual_armed=True)
    assert decision["action"] == "lock"


def test_engine_not_armed_no_lock():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["lock_grace_seconds"] = 0
    engine, locks = _make_engine(cfg)
    states = {
        "camera": FakeMon("absent", 100),
        "bluetooth": FakeMon("absent", 100),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    decision = engine.tick(states, manual_armed=False)
    assert decision["action"] is None
    assert locks["n"] == 0


def test_engine_grace_cancels():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 60
    cfg["general"]["min_absent_seconds"] = 5
    engine, locks = _make_engine(cfg)
    states = {
        "camera": FakeMon("absent", 10),
        "bluetooth": FakeMon("absent", 10),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    d1 = engine.tick(states, manual_armed=True)
    assert d1["action"] is None and d1["time_to_lock"] > 0
    # la condition disparaît avant la fin du délai de grâce
    states["camera"] = FakeMon("present", 0)
    d2 = engine.tick(states, manual_armed=True)
    assert d2["action"] is None
    assert locks["n"] == 0


# ------------------------------------------------------ localisation
def test_location_wifi_at_home(monkeypatch):
    mon = LocationMonitor({"method": "wifi", "home_ssids": ["MaBox"], "secure_when_offline": True})

    def fake_run(cmd, timeout=8):
        if cmd[:1] == ["nmcli"] or (cmd and cmd[0] == "nmcli"):
            return 0, "oui:MaBox"
        if cmd[0] == "ip":
            return 0, "default via 192.168.1.1"
        return 0, ""

    monkeypatch.setattr(LocationMonitor, "_run", staticmethod(fake_run))
    res = mon.tick()
    assert res.at_home is True
    assert res.offline is False


def test_location_wifi_away(monkeypatch):
    mon = LocationMonitor({"method": "wifi", "home_ssids": ["MaBox"], "secure_when_offline": True})

    def fake_run(cmd, timeout=8):
        if cmd[0] == "nmcli":
            return 0, "oui:HotspotTelephone"
        if cmd[0] == "ip":
            return 0, "default via 10.0.0.1"
        return 0, ""

    monkeypatch.setattr(LocationMonitor, "_run", staticmethod(fake_run))
    res = mon.tick()
    assert res.at_home is False


def test_location_offline(monkeypatch):
    mon = LocationMonitor({"method": "wifi", "home_ssids": [], "secure_when_offline": True})

    def fake_run(cmd, timeout=8):
        if cmd[0] == "nmcli":
            return 0, "oui:QuelquePart"
        if cmd[0] == "ip":
            return 0, ""  # pas de route par défaut
        return 0, ""

    monkeypatch.setattr(LocationMonitor, "_run", staticmethod(fake_run))
    res = mon.tick()
    assert res.offline is True


# ----------------------------------------------------------- mode debug
def test_runtime_mode_default(monkeypatch):
    monkeypatch.delenv("SECURITY_LINUX_MODE", raising=False)
    import security_linux.runtime as runtime

    assert runtime.mode() == "production"
    assert runtime.is_debug() is False


def test_runtime_mode_debug_env(monkeypatch):
    monkeypatch.setenv("SECURITY_LINUX_MODE", "debug")
    import security_linux.runtime as runtime

    assert runtime.is_debug() is True
    assert runtime.mode() == "debug"


def test_debug_config_dirs_are_isolated(monkeypatch):
    monkeypatch.setenv("SECURITY_LINUX_MODE", "debug")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/cfg-test")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/cache-test")
    import security_linux.config as cfgmod

    assert str(cfgmod.config_dir()).startswith("/tmp/cache-test/security-linux/debug")
    assert str(cfgmod.data_dir()).startswith("/tmp/cache-test/security-linux/debug")
    assert "cfg-test" not in str(cfgmod.config_dir())


def test_runtime_simulate_lock_noop_in_debug(monkeypatch):
    monkeypatch.setenv("SECURITY_LINUX_MODE", "debug")
    import security_linux.runtime as runtime

    fired = {"n": 0}

    def real_lock():
        fired["n"] += 1
        return True

    wrapper = runtime.simulate_lock(real_lock)
    assert wrapper() is True
    assert fired["n"] == 0  # aucune action réelle


def test_machine_state(monkeypatch):
    from security_linux.daemon import machine_state

    assert machine_state({"armed": True, "away": False, "offline_secure": False}, "production") == "armed"
    assert machine_state({"armed": True, "away": True, "offline_secure": False}, "production") == "armed_away"
    assert machine_state({"armed": True, "away": True, "offline_secure": True}, "production") == "armed_offline"
    assert machine_state({"armed": False, "away": False, "offline_secure": False}, "production") == "disarmed"
    assert machine_state({"armed": True}, "debug") == "debug"


def test_camera_resolve_autodetects(monkeypatch):
    from security_linux.monitors.camera import CameraMonitor

    monkeypatch.setattr(CameraMonitor, "_cv2_available", True, raising=False)
    if __import__("os").path.exists("/dev/video0"):
        mon = CameraMonitor({"enabled": True, "device": "/dev/does-not-exist"})
        assert mon.resolve_device() is not None
    # device configuré prioritaire
    mon2 = CameraMonitor({"enabled": True, "device": "/dev/video0"})
    expected = "/dev/video0" if __import__("os").path.exists("/dev/video0") else None
    assert mon2.resolve_device() == expected


def test_camera_list_devices():
    from security_linux.monitors.camera import CameraMonitor

    devs = CameraMonitor.list_devices()
    assert isinstance(devs, list)
    assert all(dev.startswith("/dev/video") for dev in devs)
    if __import__("os").path.exists("/dev/video0"):
        assert "/dev/video0" in devs


def test_camera_device_label_fallback():
    from security_linux.monitors.camera import CameraMonitor

    # chemin inexistant -> fallback sur le chemin
    assert CameraMonitor.device_label("/dev/video99") == "/dev/video99"
    # périphérique réel -> nom sysfs (ou fallback, mais toujours une chaîne)
    label = CameraMonitor.device_label("/dev/video0")
    assert isinstance(label, str) and label.strip() != ""


def test_howdy_detect_device_path():
    from security_linux import howdy_ctrl

    path = howdy_ctrl.detect_device_path()
    assert path.startswith("/dev/video") if __import__("os").path.exists("/dev/video0") else path == "none"
    # config_device_path refuse de lire un fichier absent sans planter
    assert howdy_ctrl.config_device_path() in ("none", "/dev/video0", "/dev/video1")
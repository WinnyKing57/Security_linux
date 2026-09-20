"""Tests du noyau (sans GUI) : hachage, config, moteur, moniteur localisation."""
from __future__ import annotations

import copy

import pytest

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


def test_argon2id_roundtrip_if_available(monkeypatch):
    if not hashing.argon2_available():
        pytest.skip("argon2-cffi absent")
    encoded = hashing.argon2id_hash("mon-code-secret")
    assert hashing.argon2id_verify("mon-code-secret", encoded)
    assert not hashing.argon2id_verify("autre-code", encoded)
    assert hashing.verify_code("mon-code-secret", "", encoded, "argon2id")
    assert not hashing.verify_code("autre-code", "", encoded, "argon2id")


def test_verify_code_rejects_argon2id_without_lib(monkeypatch):
    monkeypatch.setattr(hashing, "_argon2", lambda: None)
    assert hashing.argon2id_verify("x", "n'importe-quoi") is False


def test_verify_code_unknown_algorithm_falls_back_pbkdf2():
    salt = hashing.generate_salt()
    h = hashing.hash_code("code", salt)
    assert hashing.verify_code("code", salt, h, algorithm="inconnu")


def test_admin_code_stores_pbkdf2_without_argon2(monkeypatch):
    monkeypatch.setattr(hashing, "argon2_available", lambda: False)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    set_admin_code(cfg, "123456")
    assert cfg["admin_code"]["alg"] == "pbkdf2"
    assert cfg["admin_code"]["salt"]
    assert verify_admin_code(cfg, "123456")
    assert not verify_admin_code(cfg, "999999")


def test_admin_code_uses_argon2id_when_available(monkeypatch):
    if not hashing.argon2_available():
        pytest.skip("argon2-cffi absent")
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    set_admin_code(cfg, "123456")
    assert cfg["admin_code"]["alg"] == "argon2id"
    assert verify_admin_code(cfg, "123456")


def test_migration_pbkdf2_to_argon2id(monkeypatch):
    if not hashing.argon2_available():
        pytest.skip("argon2-cffi absent")
    orig = hashing.argon2_available
    monkeypatch.setattr(hashing, "argon2_available", lambda: False)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    set_admin_code(cfg, "123456")
    assert cfg["admin_code"]["alg"] == "pbkdf2"
    monkeypatch.setattr(hashing, "argon2_available", orig)
    # La vérification réussie migre le hash PBKDF2 vers Argon2id.
    assert verify_admin_code(cfg, "123456")
    assert cfg["admin_code"]["alg"] == "argon2id"
    assert verify_admin_code(cfg, "123456")


def test_config_admin_code():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    assert not verify_admin_code(cfg, "123456")
    set_admin_code(cfg, "123456")
    assert verify_admin_code(cfg, "123456")
    assert not verify_admin_code(cfg, "999999")


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


def test_engine_uses_monitorresult_elapsed_extra():
    """Régression : les vrais moniteurs produisent des MonitorResult, leur
    durée d'absence circule dans ``extra["absent_elapsed_seconds"]``. Sans
    cela, elapsed vaut toujours 0 et le verrouillage ne se déclenche jamais.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 20
    engine, locks = _make_engine(cfg)

    states = {
        # bluetooth désactivé (statut "disabled") : ignoré par le moteur
        "camera": MonitorResult("camera", "absent", absent_since="2026-09-18T00:00:00",
                                extra={"absent_elapsed_seconds": 30}),
        "bluetooth": MonitorResult("bluetooth", "disabled"),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    decision = engine.tick(states, manual_armed=True)
    assert decision["action"] == "lock"
    assert locks["n"] == 1


def test_engine_falls_back_to_absent_since():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 1
    engine, locks = _make_engine(cfg)

    states = {
        "camera": MonitorResult("camera", "absent", absent_since="2026-09-18T00:00:00"),
        "bluetooth": MonitorResult("bluetooth", "disabled"),
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


# ----------------------------------------------------------- Silentium
def _absent_states():
    return {
        "camera": FakeMon("absent", 100),
        "bluetooth": FakeMon("absent", 100),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }


def test_config_defaults_include_features():
    assert "silentium" in DEFAULT_CONFIG
    assert "braquage" in DEFAULT_CONFIG
    assert "notifications" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["notifications"]["rearm"] is True
    assert DEFAULT_CONFIG["braquage"]["alarm_duration"] == 5


def test_is_silentium_active_disabled():
    import security_linux.config as cfgmod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    assert cfgmod.is_silentium_active(cfg) is False
    cfg["silentium"]["enabled"] = True
    cfg["silentium"]["start_hour"] = 23
    cfg["silentium"]["end_hour"] = 7
    # plage désactivée vs horaire non testé ici (dépend de l'heure courante)


def _patch_hour(monkeypatch, hour_value: int):
    import datetime as _dt

    class _FakeClock:
        hour = hour_value

        @classmethod
        def now(cls):
            return cls()

    monkeypatch.setattr(_dt, "datetime", _FakeClock)


def test_is_silentium_active_night_range(monkeypatch):
    import security_linux.config as cfgmod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["silentium"]["enabled"] = True
    cfg["silentium"]["start_hour"] = 23
    cfg["silentium"]["end_hour"] = 7
    _patch_hour(monkeypatch, 1)   # 1h du matin : dans la plage
    assert cfgmod.is_silentium_active(cfg) is True
    _patch_hour(monkeypatch, 16)  # 16h : en dehors
    assert cfgmod.is_silentium_active(cfg) is False


def test_is_silentium_active_day_range(monkeypatch):
    import security_linux.config as cfgmod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["silentium"]["enabled"] = True
    cfg["silentium"]["start_hour"] = 14
    cfg["silentium"]["end_hour"] = 16
    _patch_hour(monkeypatch, 15)
    assert cfgmod.is_silentium_active(cfg) is True
    _patch_hour(monkeypatch, 10)
    assert cfgmod.is_silentium_active(cfg) is False


def test_engine_silentium_suppresses_lock(monkeypatch):
    import security_linux.engine as engmod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 20
    engine, locks = _make_engine(cfg)
    monkeypatch.setattr(engmod.config, "is_silentium_active", lambda _cfg: True)
    decision = engine.tick(_absent_states(), manual_armed=True)
    assert decision["action"] is None
    assert decision["silentium_active"] is True
    assert locks["n"] == 0


def test_engine_silentium_inactive_locks(monkeypatch):
    import security_linux.engine as engmod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 20
    engine, locks = _make_engine(cfg)
    monkeypatch.setattr(engmod.config, "is_silentium_active", lambda _cfg: False)
    decision = engine.tick(_absent_states(), manual_armed=True)
    assert decision["action"] == "lock"
    assert decision["silentium_active"] is False
    assert locks["n"] == 1


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


def test_camera_tick_no_shadowed_underscore(monkeypatch):
    """Régression : ``for _ in range(...)`` dans tick() ne doit pas écraser la
    fonction gettext ``_`` (sinon -> TypeError 'int' object is not callable).
    """
    from security_linux.monitors.camera import CameraMonitor

    mon = CameraMonitor({"enabled": True, "device": "/dev/video0", "absent_confirmations": 3})
    mon.supported = lambda: True
    mon.face_detected = lambda: False
    res = mon.tick()
    assert res.status == "absent"
    assert "aucun visage" in res.detail


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


def test_howdy_enroll_command_uses_configured_device(monkeypatch):
    from security_linux import howdy_ctrl

    if not howdy_ctrl.is_installed():
        import pytest

        pytest.skip("howdy non installé")
    import os

    monkeypatch.setattr(howdy_ctrl.shutil, "which", lambda *_a, **_k: "/usr/bin/konsole")
    # device configuré existant -> injecté dans la commande
    cmd = howdy_ctrl.enroll_command("/dev/video0")
    assert "device_path = /dev/video0" in cmd[-1]
    # device configuré inexistant -> repli sur le périphérique détecté
    cmd2 = howdy_ctrl.enroll_command("/dev/video99")
    if os.path.exists("/dev/video0"):
        assert "device_path = /dev/video0" in cmd2[-1] or "device_path = /dev/video1" in cmd2[-1]
    # aucun device -> repli
    cmd3 = howdy_ctrl.enroll_command()
    assert "device_path =" in cmd3[-1]


def test_howdy_models_status_detects_dat_file(monkeypatch):
    from security_linux import howdy_ctrl

    import os
    import tempfile

    model_dir = os.path.join(tempfile.mkdtemp(), "models")
    os.makedirs(model_dir, exist_ok=True)
    monkeypatch.setenv("USER", "testuser")
    # répertoire vide -> none (pas d'exception)
    assert howdy_ctrl.models_status(model_dir) == "none"
    # fichier <user>.dat présent -> ok
    with open(os.path.join(model_dir, "testuser.dat"), "w"):
        pass
    assert howdy_ctrl.models_status(model_dir) == "ok"
    # répertoire <user>/ contenant des fichiers -> ok
    os.remove(os.path.join(model_dir, "testuser.dat"))
    os.makedirs(os.path.join(model_dir, "testuser"))
    with open(os.path.join(model_dir, "testuser", "encodings"), "w"):
        pass
    assert howdy_ctrl.models_status(model_dir) == "ok"
    # répertoire absent -> none
    assert howdy_ctrl.models_status("/tmp/does-not-exist-xyz") == "none"


# ------------------------------------------------------------ voyant webcam
def test_led_marker_roundtrip(monkeypatch):
    from datetime import datetime, timezone as _tz

    monkeypatch.setenv("SECURITY_LINUX_MODE", "debug")
    import security_linux.led as led
    from security_linux.config import data_dir

    marker = data_dir() / "capture_led.json"
    marker.unlink(missing_ok=True)

    # aucun clignotement encore émis -> None
    assert led.last_blink() is None
    led.blink()
    ts = led.last_blink()
    assert ts is not None
    assert (datetime.now(_tz.utc) - ts).total_seconds() < 5


def test_led_marker_config_default():
    import security_linux.config as cfgmod

    assert "led" in cfgmod.DEFAULT_CONFIG
    assert cfgmod.DEFAULT_CONFIG["led"]["enabled"] is False


# ------------------------------------------------- verrouillage robustesse
def test_engine_lock_fn_error_no_spam():
    """Régression : si le verrouilleur plante, le moteur ne doit PAS
    ré-essayer à chaque cycle (l'absence de mise à jour de ``_last_lock_ts``
    causait un verrouillage d'écran en boucle, 1×/seconde)."""
    import security_linux.events as events_mod

    def boom():
        raise RuntimeError("lock_failed_simulé")

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["decision_mode"] = "AND"
    cfg["general"]["lock_grace_seconds"] = 0
    cfg["general"]["min_absent_seconds"] = 1
    engine = Engine(cfg, boom, lambda: False)

    states = {
        "camera": FakeMon("absent", 5),
        "bluetooth": FakeMon("absent", 5),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    first = engine.tick(states, manual_armed=True)
    assert first["action"] == "lock_failed"
    assert engine._last_lock_ts is not None
    # cycle suivant dans la même fenêtre de répétition : aucune nouvelle tentative
    second = engine.tick(states, manual_armed=True)
    assert second["action"] is None


def test_engine_idle_lock_fn_error_no_spam(monkeypatch):
    """Même garantie pour le filet de sécurité (verrouillage par inactivité)."""
    from security_linux import idle as idle_mod

    def boom():
        raise RuntimeError("lock_failed_simulé")

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["idle_lock_minutes"] = 1
    engine = Engine(cfg, boom, lambda: False)
    monkeypatch.setattr(idle_mod, "idle_seconds", lambda: 300)

    states = {
        "camera": FakeMon("present", 0),
        "bluetooth": FakeMon("present", 0),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    first = engine.tick(states, manual_armed=True)
    assert first["action"] == "lock_failed"
    second = engine.tick(states, manual_armed=True)
    assert second["action"] is None


def test_lock_screen_uses_loginctl_session(monkeypatch):
    """Loginctl : colonnes SESSION UID USER SEAT — l'UID est parts[1]."""
    import security_linux.lock as lockmod

    calls: list[list[str]] = []
    logged: list[tuple[str, str]] = []

    def fake_run(cmd, timeout=10):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if joined.startswith("loginctl show-session"):
            return (0, "Type=x11\nActive=yes\n")
        if joined.startswith("loginctl lock-session"):
            return (0, "")
        if joined.startswith("loginctl list-sessions"):
            return (0, "3 1000 winny seat0\n")
        return (0, "")

    def fake_log_event(kind, message):
        logged.append((kind, message))

    monkeypatch.setattr(lockmod, "_run", fake_run)
    monkeypatch.setattr(lockmod.events, "log_event", fake_log_event)
    assert not hasattr(lockmod, "_")
    assert callable(getattr(lockmod, "_t"))

    assert lockmod.lock_screen() is True
    assert any(cmd[1] == "lock-session" for cmd in calls)
    assert any(kind == "lock" and "session 3" in msg for kind, msg in logged)


def test_lock_screen_qdbus_fallback_shadowed_gettext(monkeypatch):
    """Régression du bug production : même si ``_`` (gettext) était écrasé
    par une chaîne, la ligne « écran verrouillé via ... » (tombée du crash
    'str' object is not callable) passe par l'alias ``_t`` — non masqué."""
    import security_linux.lock as lockmod

    calls: list[list[str]] = []
    logged: list[tuple[str, str]] = []

    def fake_run(cmd, timeout=10):
        calls.append(list(cmd))
        return (0, "")

    def fake_log_event(kind, message):
        logged.append((kind, message))

    monkeypatch.setattr(lockmod, "_run", fake_run)
    monkeypatch.setattr(lockmod.events, "log_event", fake_log_event)
    # Durcissement : plus aucun symbole global ``_`` dans lock.py (l'ancien
    # binding gettext pouvait être écrasé par une chaîne à l'exécution).
    assert not hasattr(lockmod, "_")
    assert callable(getattr(lockmod, "_t"))

    assert lockmod.lock_screen() is True
    assert any(cmd[0] == "qdbus6" for cmd in calls)
    # le journal « verrouillé via ... » passe par l'alias _t (non masqué)
    assert any(kind == "lock" and "écran verrouillé" in msg for kind, msg in logged)


def test_engine_idle_lock_keeps_conditions_unmet(monkeypatch):
    """Le seuil « braquage » repose sur cond.met : le verrouillage par
    inactivité ne doit jamais remplir les conditions d'absence."""
    import security_linux.idle as idle_mod

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["general"]["idle_lock_minutes"] = 1
    engine = Engine(cfg, lambda: True, lambda: False)
    monkeypatch.setattr(idle_mod, "idle_seconds", lambda: 300)

    states = {
        "camera": FakeMon("present", 0),
        "bluetooth": FakeMon("present", 0),
        "location": MonitorResult("location", "home", at_home=True, offline=False),
    }
    decision = engine.tick(states, manual_armed=True)
    assert decision["action"] == "lock"
    assert decision["conditions"]["met"] is False


def test_should_trigger_braquage_only_on_real_absence(monkeypatch):
    """Régression « braquage sans raison » : pas d'alarme sur le seul
    verrouillage d'inactivité, seulement sur une absence réelle (conditions)."""
    import security_linux.daemon as daemonmod
    from security_linux.daemon import should_trigger_braquage

    monkeypatch.setattr(daemonmod.runtime, "is_debug", lambda: False)
    assert should_trigger_braquage({"action": "lock", "conditions": {"met": True}})
    assert should_trigger_braquage({"action": "relock", "conditions": {"met": True}})
    # verrouillage par inactivité : conditions non remplies -> pas d'alarme
    assert not should_trigger_braquage({"action": "lock", "conditions": {"met": False}})
    assert not should_trigger_braquage({"action": "relock", "conditions": {"met": False}})
    assert not should_trigger_braquage({"action": "lock_failed", "conditions": {"met": True}})
    assert not should_trigger_braquage({"action": None, "conditions": {"met": True}})
    assert not should_trigger_braquage({})

    monkeypatch.setattr(daemonmod.runtime, "is_debug", lambda: True)
    assert not should_trigger_braquage({"action": "lock", "conditions": {"met": True}})
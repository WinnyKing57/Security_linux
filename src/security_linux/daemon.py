"""Démon de sécurité (boucle de surveillance arrière-plan).

Lancement : python -m security_linux.daemon [--one-shot]
"""
from __future__ import annotations

import argparse
import atexit
import fcntl
import os
import signal
import sys
import threading
import time
from pathlib import Path

import security_linux.alerts as alerts
import security_linux.config as config
import security_linux.events as events
import security_linux.howdy_ctrl as howdy_ctrl
import security_linux.lock as lock
import security_linux.runtime as runtime
from security_linux.i18n import _
from security_linux.engine import Engine
from security_linux.monitors import MonitorResult
from security_linux.monitors.bluetooth import BluetoothMonitor
from security_linux.monitors.camera import CameraMonitor
from security_linux.monitors.location import LocationMonitor
from security_linux.version import __version__

_WATCH = 1.0
_LOCK_FILE = None


def _daemon_lock_path() -> Path:
    """Verrou d'instance unique : XDG_RUNTIME_DIR sinon répertoire de config."""
    if runtime.is_debug():
        return runtime.debug_root() / "daemon.lock"
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or config.config_dir())
    return base / "security-linuxd.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_daemon_lock() -> bool:
    """Instance unique du démon (flock). Retourne False si déjà en cours."""
    global _LOCK_FILE
    path = _daemon_lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Instance précédente morte (crash) : son flock est libéré mais le
        # fichier reste. On supprime l'orphelin avant de verrouiller.
        try:
            stale_pid = int(path.read_text("utf-8").strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            stale_pid = 0
        if stale_pid and not _pid_alive(stale_pid):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        # "a+" : ne tronque pas un fichier verrouillé par une autre instance.
        _LOCK_FILE = open(path, "a+")
        fcntl.flock(_LOCK_FILE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE.seek(0)
        _LOCK_FILE.truncate()
        _LOCK_FILE.write(f"{os.getpid()}\n")
        _LOCK_FILE.flush()
        return True
    except (IOError, OSError):
        if _LOCK_FILE:
            try:
                _LOCK_FILE.close()
            except OSError:
                pass
            _LOCK_FILE = None
        return False


def _release_daemon_lock() -> None:
    global _LOCK_FILE
    if _LOCK_FILE:
        try:
            fcntl.flock(_LOCK_FILE.fileno(), fcntl.LOCK_UN)
            _LOCK_FILE.close()
        except (IOError, OSError):
            pass
        try:
            _daemon_lock_path().unlink(missing_ok=True)
        except OSError:
            pass
        _LOCK_FILE = None


def machine_state(armed: dict, mode: str) -> str:
    """État métier de l'application (visible dans state.json et la GUI)."""
    if mode == "debug":
        return "debug"
    if armed.get("away") and (armed.get("offline_secure")):
        return "armed_offline"
    if armed.get("away"):
        return "armed_away"
    if not armed.get("armed"):
        return "disarmed"
    return "armed"


class Daemon:
    def __init__(self, one_shot: bool = False) -> None:
        self.one_shot = one_shot
        self.cfg = config.load_config()
        self.camera = CameraMonitor(self.cfg["camera"])
        self.bluetooth = BluetoothMonitor(self.cfg["bluetooth"])
        self.location = LocationMonitor(self.cfg["location"])
        lock_fn = runtime.simulate_lock(lock.lock_screen)
        self.engine = Engine(self.cfg, lock_fn, lock.is_locked)
        self._schedules = {
            "camera": 0.0,
            "bluetooth": 0.0,
            "location": 0.0,
        }
        # État précédent d'armement forcé (détection des transitions de réarmement)
        self._prev_forced: bool | None = None
        # Config précédente des moniteurs (journal des changements de réglages)
        self._prev_mon_cfg: dict | None = None

    def _is_due(self, name: str, mon) -> bool:
        now = time.time()
        if now < self._schedules[name]:
            return False
        self._schedules[name] = now + mon.poll_seconds()
        return True

    def _refresh_monitor_configs(self) -> None:
        """Reprise de la config sur disque à chaque cycle (réglages à chaud)."""
        self.engine.cfg = self.cfg
        self.camera.cfg = self.cfg["camera"]
        self.bluetooth.cfg = self.cfg["bluetooth"]
        self.location.cfg = self.cfg["location"]
        keys = {
            "camera": (self.cfg["camera"].get("enabled"), self.cfg["camera"].get("device")),
            "bluetooth": (
                self.cfg["bluetooth"].get("enabled"),
                self.cfg["bluetooth"].get("device_addr"),
                self.cfg["bluetooth"].get("min_rssi"),
            ),
            "location": (
                self.cfg["location"].get("method"),
                tuple(sorted(self.cfg["location"].get("home_ssids", []))),
                self.cfg["location"].get("secure_when_offline"),
            ),
        }
        labels = {
            "camera": (_("actif"), _("périphérique")),
            "bluetooth": (_("actif"), _("appareil"), _("seuil RSSI")),
            "location": (_("méthode"), _("SSID maison"), _("hors-ligne sécurisé")),
        }
        if self._prev_mon_cfg is not None:
            for name, now in keys.items():
                prev = self._prev_mon_cfg[name]
                if now != prev:
                    changed = [lbl for lbl, a, b in zip(labels[name], now, prev) if a != b]
                    events.log_event("config", _("moniteur {name} reconfiguré : {changements}").format(name=name, changements=", ".join(changed)))
        self._prev_mon_cfg = keys

    def _consume_commands(self) -> None:
        cmd = events.consume_command()
        if not cmd:
            return
        kind = cmd.get("type")
        if kind == "arm" and isinstance(cmd.get("value"), bool):
            self.cfg["general"]["armed"] = bool(cmd["value"])
            config.save_config(self.cfg)
            events.log_event("arm", _("interrupteur manuel ") + ("ARMÉ" if cmd["value"] else "DÉSARMÉ (code admin)"))
        elif kind == "set_many" and isinstance(cmd.get("values"), dict):
            changed = False
            for dotted, value in cmd["values"].items():
                if not isinstance(dotted, str) or "." not in dotted:
                    continue
                section, field = dotted.split(".", 1)
                if section in self.cfg and field in self.cfg[section]:
                    self.cfg[section][field] = value
                    changed = True
            if changed:
                config.save_config(self.cfg)
                details = ", ".join(f"{k}={v}" for k, v in cmd["values"].items())
                events.log_event("config", _("réglages appliqués : {details}").format(details=details))

    def run(self) -> None:
        if not _acquire_daemon_lock():
            events.log_event("daemon", _("démarrage refusé : un démon de sécurité tourne déjà"))
            print(_("security-linuxd : un démon tourne déjà (voir le journal)."), file=sys.stderr)
            sys.exit(1)
        atexit.register(_release_daemon_lock)
        cam = self.cfg["camera"]
        bt = self.cfg["bluetooth"]
        loc = self.cfg["location"]
        cam_state = _("activée") if cam.get("enabled") else _("coupée")
        bt_state = bt.get("device_addr") or _("aucun appareil")
        events.log_event(
            "daemon",
            _("démarrage du démon de sécurité — v{version} (mode {mode}), "
              "décision {decision}, webcam {cam} ({device}), "
              "bluetooth {bt}, localisation {loc}").format(
                version=__version__, mode=runtime.mode(),
                decision=self.cfg["general"].get("decision_mode"),
                cam=cam_state, device=cam.get("device"), bt=bt_state, loc=loc.get("method"),
            ),
        )
        cached: dict[str, MonitorResult] = {
            "camera": MonitorResult("camera", "pending"),
            "bluetooth": MonitorResult("bluetooth", "pending"),
            "location": MonitorResult("location", "pending"),
        }

        def _stop(signum, _frame):
            events.log_event("daemon", _("arrêt du démon"))
            sys.exit(0)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while True:
            try:
                self.cfg = config.load_config()
                self._consume_commands()
                # après consommation : les réglages 'set_many' sont repris par
                # les moniteurs dès le cycle courant (immédiateté des changements)
                self._refresh_monitor_configs()
                for name, mon in (
                    ("camera", self.camera),
                    ("bluetooth", self.bluetooth),
                    ("location", self.location),
                ):
                    scraps = {
                        "camera": runtime.sim_camera(),
                        "bluetooth": runtime.sim_bluetooth(),
                        "location": runtime.sim_location(),
                    }
                    if runtime.is_debug() and scraps[name]:
                        continue  # capteur simulé : pas d'accès au matériel
                    if name != "location" and not mon.enabled():
                        cached[name] = MonitorResult(name, "disabled")
                        continue
                    if self._is_due(name, mon):
                        try:
                            cached[name] = mon.tick()
                        except Exception as exc:  # noqa: BLE001
                            events.log_event("error", _("moniteur {name} : {erreur}").format(name=name, erreur=exc))
                            cached[name] = MonitorResult(name, "unavailable", str(exc))

                cached = self._apply_simulations(cached)

                decision = self.engine.tick(cached, self.cfg["general"]["armed"])
                cam_simulated = runtime.is_debug() and runtime.sim_camera() is not None
                if decision.get("action") in ("lock", "relock") and not cam_simulated:
                    cam_cfg = self.cfg["camera"]
                    if cam_cfg.get("capture_on_lock", True) and cam_cfg.get("enabled", True):
                        try:
                            snap = self.camera.capture_snapshot("lock_event")
                            if snap:
                                events.log_event("capture", _("image locale conservée : {snap}").format(snap=snap))
                        except Exception as exc:  # noqa: BLE001
                            events.log_event("error", _("capture : {erreur}").format(erreur=exc))
                if decision.get("action") in ("lock", "relock") and not runtime.is_debug():
                    self._trigger_braquage()
                if decision.get("tamper") and not runtime.is_debug():
                    self._handle_tamper()
                self._maybe_notify_rearm(decision)
                self._publish(cached, decision)
                if self.one_shot:
                    runtime.debug_msg(f"one-shot terminé : {decision.get('armed', {})}")
                    return
            except Exception as exc:  # noqa: BLE001
                events.log_event("error", f"boucle principale: {exc}")
            time.sleep(_WATCH)

    def _trigger_braquage(self) -> None:
        """Mode braquage : alarme sonore sur verrouillage automatique.

        L'alarme joue dans un thread détaché pour ne pas bloquer la boucle
        de surveillance (sinon le démon ne répondrait plus pendant toute la
        durée de l'alarme, y compris aux commandes de désarmement).
        """
        brq = self.cfg.get("braquage", {})
        if not brq.get("enabled", False):
            return
        duration = int(brq.get("alarm_duration", 5))

        def _play():
            try:
                alerts.play_alarm(duration)
                events.log_event("alarm", _("alarme sonore déclenchée ({duration}s, mode braquage)").format(duration=duration))
            except Exception as exc:  # noqa: BLE001
                events.log_event("error", _("alarme : {erreur}").format(erreur=exc))

        threading.Thread(target=_play, daemon=True).start()

    def _handle_tamper(self) -> None:
        """Disparition d'un capteur armé : notification critique + alarme
        (si le mode braquage est actif et ``on_tamper`` activé)."""
        brq = self.cfg.get("braquage", {})
        try:
            alerts.send_intrusion_alert("intrusion_detectee")
        except Exception as exc:  # noqa: BLE001
            events.log_event("error", _("alerte intrusion : {erreur}").format(erreur=exc))
        if brq.get("enabled", False) and brq.get("on_tamper", True):
            events.log_event("tamper", _("alarme déclenchée suite à la disparition d'un capteur armé"))
            self._trigger_braquage()

    def _maybe_notify_rearm(self, decision: dict) -> None:
        """Notification bureau quand le système se réarme automatiquement."""
        if runtime.is_debug():
            return
        notif = self.cfg.get("notifications", {})
        if not notif.get("rearm", True):
            self._prev_forced = bool(decision.get("armed", {}).get("forced"))
            return
        armed = decision.get("armed", {})
        forced = bool(armed.get("forced"))
        if self._prev_forced is None:
            # Premier cycle : on mémorise l'état sans notifier au démarrage du démon.
            self._prev_forced = forced
            return
        if forced and not self._prev_forced:
            away = bool(armed.get("away"))
            offline = bool(armed.get("offline_secure"))
            reason = "hors_ligne" if offline else ("hors_domicile" if away else "manuel")
            try:
                alerts.send_rearm_notification(reason)
                events.log_event("notification", _("réarmement automatique notifié ({raison})").format(raison=reason))
            except Exception as exc:  # noqa: BLE001
                events.log_event("error", _("notification réarmement : {erreur}").format(erreur=exc))
        self._prev_forced = forced

    def _apply_simulations(self, states: dict[str, MonitorResult]) -> dict[str, MonitorResult]:
        """En debug uniquement : injecte des états de capteurs pour les tests."""
        if not runtime.is_debug():
            return states
        specs = {
            "camera": runtime.sim_camera(),
            "bluetooth": runtime.sim_bluetooth(),
            "location": runtime.sim_location(),
        }
        for name, value in specs.items():
            if not value:
                continue
            if name == "location":
                if value == "home":
                    states[name] = MonitorResult("location", "home", at_home=True, offline=False)
                elif value == "away":
                    states[name] = MonitorResult("location", "away", at_home=False, offline=False)
                elif value == "offline":
                    states[name] = MonitorResult("location", "away", at_home=None, offline=True)
            else:
                states[name] = MonitorResult(name, value, extra={"absent_elapsed_seconds": 999})
            runtime.debug_msg(_("capteur {name} SIMULÉ = {valeur}").format(name=name, valeur=value))
        return states

    def _publish(self, states: dict[str, MonitorResult], decision: dict) -> None:
        recent = events.read_events(limit=1)
        mode = runtime.mode()
        payload = {
            "mode": mode,
            "machine_state": machine_state(decision.get("armed", {}), mode),
            "admin_set": config.has_admin_code(self.cfg),
            "armed_state": decision.get("armed", {}),
            "conditions": decision.get("conditions", {}),
            "action": decision.get("action"),
            "time_to_lock": decision.get("time_to_lock", 0.0),
            "silentium_active": decision.get("silentium_active", False),
            "monitors": {name: mon.to_dict() for name, mon in states.items()},
            "howdy": {
                "installed": howdy_ctrl.is_installed(),
                "models_status": howdy_ctrl.models_status(),
            },
            "last_event": recent[-1] if recent else None,
        }
        events.write_state(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=_("Démon de sécurité"))
    parser.add_argument("--one-shot", action="store_true", help=_("exécute une seule itération puis s'arrête"))
    parser.add_argument("--debug", action="store_true", help=_("mode développement temporaire (équivaut à SECURITY_LINUX_MODE=debug)"))
    parser.add_argument("--simulate-camera", choices=["present", "absent", "disabled"], help=_("injecter l'état webcam (debug)"))
    parser.add_argument("--simulate-bluetooth", choices=["present", "absent", "disabled"], help=_("injecter l'état Bluetooth (debug)"))
    parser.add_argument("--simulate-location", choices=["home", "away", "offline"], help=_("injecter la localisation (debug)"))
    args = parser.parse_args()
    if args.debug:
        os.environ["SECURITY_LINUX_MODE"] = "debug"
    for attr, env in (("simulate_camera", "SECURITY_LINUX_SIM_CAMERA"),
                      ("simulate_bluetooth", "SECURITY_LINUX_SIM_BLUETOOTH"),
                      ("simulate_location", "SECURITY_LINUX_SIM_LOCATION")):
        value = getattr(args, attr, None)
        if value:
            os.environ[env] = value
    Daemon(one_shot=args.one_shot).run()


if __name__ == "__main__":
    main()
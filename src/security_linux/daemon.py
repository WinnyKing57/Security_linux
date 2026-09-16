"""Démon de sécurité (boucle de surveillance arrière-plan).

Lancement : python -m security_linux.daemon [--one-shot]
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

import security_linux.config as config
import security_linux.events as events
import security_linux.howdy_ctrl as howdy_ctrl
import security_linux.lock as lock
import security_linux.runtime as runtime
from security_linux.engine import Engine
from security_linux.monitors import MonitorResult
from security_linux.monitors.bluetooth import BluetoothMonitor
from security_linux.monitors.camera import CameraMonitor
from security_linux.monitors.location import LocationMonitor

_WATCH = 1.0


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

    def _consume_commands(self) -> None:
        cmd = events.consume_command()
        if not cmd:
            return
        kind = cmd.get("type")
        if kind == "arm" and isinstance(cmd.get("value"), bool):
            self.cfg["general"]["armed"] = bool(cmd["value"])
            config.save_config(self.cfg)
            events.log_event("arm", "interrupteur manuel " + ("ARMÉ" if cmd["value"] else "DÉSARMÉ (code admin)"))
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
                events.log_event("config", f"{len(cmd['values'])} réglage(s) appliqué(s)")

    def run(self) -> None:
        events.log_event("daemon", "démarrage du démon de sécurité")
        cached: dict[str, MonitorResult] = {
            "camera": MonitorResult("camera", "pending"),
            "bluetooth": MonitorResult("bluetooth", "pending"),
            "location": MonitorResult("location", "pending"),
        }

        def _stop(signum, _frame):
            events.log_event("daemon", "arrêt du démon")
            sys.exit(0)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while True:
            try:
                self.cfg = config.load_config()
                self._refresh_monitor_configs()
                self._consume_commands()
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
                            events.log_event("error", f"moniteur {name}: {exc}")
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
                                events.log_event("capture", f"image locale conservée: {snap}")
                        except Exception as exc:  # noqa: BLE001
                            events.log_event("error", f"capture: {exc}")
                self._publish(cached, decision)
                if self.one_shot:
                    runtime.debug_msg(f"one-shot terminé : {decision.get('armed', {})}")
                    return
            except Exception as exc:  # noqa: BLE001
                events.log_event("error", f"boucle principale: {exc}")
            time.sleep(_WATCH)

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
            runtime.debug_msg(f"capteur {name} SIMULÉ = {value}")
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
            "monitors": {name: mon.to_dict() for name, mon in states.items()},
            "howdy": {
                "installed": howdy_ctrl.is_installed(),
                "models_status": howdy_ctrl.models_status(),
            },
            "last_event": recent[-1] if recent else None,
        }
        events.write_state(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Démon de sécurité")
    parser.add_argument("--one-shot", action="store_true", help="exécute une seule itération puis s'arrête")
    parser.add_argument("--debug", action="store_true", help="mode développement temporaire (équivaut à SECURITY_LINUX_MODE=debug)")
    parser.add_argument("--simulate-camera", choices=["present", "absent", "disabled"], help="injecter l'état webcam (debug)")
    parser.add_argument("--simulate-bluetooth", choices=["present", "absent", "disabled"], help="injecter l'état Bluetooth (debug)")
    parser.add_argument("--simulate-location", choices=["home", "away", "offline"], help="injecter la localisation (debug)")
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
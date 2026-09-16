"""Interface en ligne de commande.

  security-linux           → lance l'application graphique
  security-linuxd          → lance le démon (démarrage automatique)
  security-linux status    → affiche l'état courant
  security-linux arm       → active le système (sans code)
  security-linux disarm    → désactive (code admin requis)
  security-linux lock-now  → verrouille l'écran immédiatement
  security-linux set-code  → définit/chgange le code admin
  security-linux list-bt   → liste les appareils Bluetooth appariés
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

import security_linux.config as config
import security_linux.events as events


def _load_or_default() -> tuple[dict, str]:
    path = config.default_config_path()
    cfg = config.load_config(path) if path.exists() else config.load_config()
    return cfg, str(path)


def cmd_status(_args) -> int:
    state = events.read_state()
    if not state:
        print("Pas de démon actif (état inconnu).")
        return 1
    armed = state.get("armed_state", {})
    print(f"Mode : {state.get('mode', 'production')}")
    print(f"État machine : {state.get('machine_state')}")
    print(f"Armé manuellement : {armed.get('manual')}")
    print(f"Armé (forcé, hors domicile/hors-ligne) : {armed.get('forced')}")
    print(f"Armement effectif : {armed.get('armed')}")
    print("Moniteurs :")
    for name, mon in state.get("monitors", {}).items():
        print(f"  {name:<10} {mon.get('status'):<16} {mon.get('detail', '')}")
    return 0


def cmd_arm(_args) -> int:
    events.write_command({"type": "arm", "value": True})
    print("Commande ARMÉ envoyée au démon.")
    return 0


def cmd_disarm(_args) -> int:
    _cfg, _path = _load_or_default()
    if not config.has_admin_code(_cfg):
        print("Aucun code admin défini. Lancez l'application graphique pour en créer un.")
        return 1
    code = getpass.getpass("Code administrateur : ")
    if not config.verify_admin_code(_cfg, code):
        print("Code invalide.")
        return 1
    events.write_command({"type": "arm", "value": False})
    print("Commande DÉSARMÉ envoyée au démon.")
    return 0


def cmd_lock_now(_args) -> int:
    from security_linux.lock import lock_screen

    return 0 if lock_screen() else 1


def cmd_set_code(_args) -> int:
    cfg, _path = _load_or_default()
    if config.has_admin_code(cfg):
        current = getpass.getpass("Code admin actuel : ")
        if not config.verify_admin_code(cfg, current):
            print("Code actuel invalide.")
            return 1
    code = getpass.getpass("Nouveau code admin : ")
    confirm = getpass.getpass("Confirmez : ")
    if code != confirm:
        print("Les codes ne correspondent pas.")
        return 1
    if len(code) < 4:
        print("Le code doit contenir au moins 4 caractères.")
        return 1
    config.set_admin_code(cfg, code)
    config.save_config(cfg)
    print("Code admin mis à jour.")
    return 0


def cmd_list_bt(_args) -> int:
    from security_linux.monitors.bluetooth import BluetoothMonitor

    if not config.default_config_path().exists():
        print("Config par défaut utilisée.")
    mon = BluetoothMonitor({"enabled": True, "device_addr": ""})
    for dev in mon.list_known_devices():
        marker = "*" if dev["address"] == config.load_config()["bluetooth"]["device_addr"] else " "
        print(f"{marker} {dev['address']}  {dev['name']}")
    return 0


def cmd_gui(_args) -> int:
    from security_linux.gui.app import main as gui_main

    gui_main()
    return 0


def cmd_daemon(args) -> int:
    if getattr(args, "debug", False):
        os.environ["SECURITY_LINUX_MODE"] = "debug"
    sim_map = {
        "simulate_camera": "SECURITY_LINUX_SIM_CAMERA",
        "simulate_bluetooth": "SECURITY_LINUX_SIM_BLUETOOTH",
        "simulate_location": "SECURITY_LINUX_SIM_LOCATION",
    }
    for attr, env in sim_map.items():
        value = getattr(args, attr, None)
        if value:
            os.environ[env] = value
    from security_linux.daemon import Daemon

    Daemon(one_shot=getattr(args, "one_shot", False)).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="security-linux")
    parser.add_argument("--debug", action="store_true", help="mode développement temporaire (config/état isolés, verrouillage simulé)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="état courant")
    sub.add_parser("arm", help="armer")
    sub.add_parser("disarm", help="désarmer (code admin)")
    sub.add_parser("lock-now", help="verrouiller l'écran tout de suite")
    sub.add_parser("set-code", help="définir/changer le code admin")
    sub.add_parser("list-bt", help="liste des appareils Bluetooth")
    sub.add_parser("gui", help="ouvrir l'application graphique")
    daemon = sub.add_parser("daemon", help="démarrer le démon")
    daemon.add_argument("--one-shot", action="store_true", help="une seule itération puis s'arrête")
    daemon.add_argument("--simulate-camera", choices=["present", "absent", "disabled"], help="injecter l'état webcam (debug)")
    daemon.add_argument("--simulate-bluetooth", choices=["present", "absent", "disabled"], help="injecter l'état Bluetooth (debug)")
    daemon.add_argument("--simulate-location", choices=["home", "away", "offline"], help="injecter la localisation (debug)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "debug", False) and args.command in ("set-code", "disarm", "arm", "list-bt"):
        os.environ["SECURITY_LINUX_MODE"] = "debug"
    if args.command is None:
        if args.debug:
            os.environ["SECURITY_LINUX_MODE"] = "debug"
        return cmd_gui(args)
    handlers = {
        "status": cmd_status,
        "arm": cmd_arm,
        "disarm": cmd_disarm,
        "lock-now": cmd_lock_now,
        "set-code": cmd_set_code,
        "list-bt": cmd_list_bt,
        "gui": cmd_gui,
        "daemon": cmd_daemon,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
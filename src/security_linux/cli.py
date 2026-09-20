"""Interface en ligne de commande.

  security-linux           → lance l'application graphique
  security-linuxd          → lance le démon (démarrage automatique)
  security-linux status    → affiche l'état courant
  security-linux arm       → active le système (sans code)
  security-linux disarm    → désactive (code admin requis)
  security-linux lock-now  → verrouille l'écran immédiatement
  security-linux set-code  → définit/chgange le code admin
  security-linux list-bt   → liste les appareils Bluetooth appariés
security-linux face-save → enregistre la photo de référence (visage)
   security-linux face-check→ vérifie le visage devant la caméra (passe au test)
   security-linux autostart → état du démarrage automatique au logon
   security-linux autostart on|off → l'activer/désactiver
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time

import security_linux.config as config
import security_linux.events as events
from security_linux.i18n import _


def _load_or_default() -> tuple[dict, str]:
    path = config.default_config_path()
    cfg = config.load_config(path) if path.exists() else config.load_config()
    return cfg, str(path)


def cmd_status(_args) -> int:
    state = events.read_state()
    if not state:
        print(_("Pas de démon actif (état inconnu)."))
        return 1
    armed = state.get("armed_state", {})
    print(_("Mode : {}").format(state.get("mode", "production")))
    print(_("État machine : {}").format(state.get("machine_state")))
    print(_("Armé manuellement : {}").format(armed.get("manual")))
    print(_("Armé (forcé, hors domicile/hors-ligne) : {}").format(armed.get("forced")))
    print(_("Armement effectif : {}").format(armed.get("armed")))
    print(_("Moniteurs :"))
    for name, mon in state.get("monitors", {}).items():
        print(f"  {name:<10} {mon.get('status'):<16} {mon.get('detail', '')}")
    if state.get("led_last_ts") is not None:
        led = _("ALLUMÉ (captures récentes)") if state.get("led_active") else _("éteint")
        print(_("Voyant webcam : {} (dernier clignotement {})").format(led, state.get("led_last_ts")))
    return 0


def _persist_armed(value: bool) -> int:
    """Persiste l'état armé dans la config ET l'envoie au démon.

    Le double-écriture garantit que la commande survit même si le démon
    ne tourne pas (le fichier de commande expire après 30 s).
    """
    cfg, _path = _load_or_default()
    cfg["general"]["armed"] = bool(value)
    config.save_config(cfg)
    events.write_command({"type": "arm", "value": bool(value)})
    return 0


def cmd_arm(_args) -> int:
    _persist_armed(True)
    print(_("Système ARMÉ."))
    return 0


def cmd_disarm(_args) -> int:
    _cfg, _path = _load_or_default()
    if not config.has_admin_code(_cfg):
        print(_("Aucun code admin défini. Lancez l'application graphique pour en créer un."))
        return 1
    code = getpass.getpass(_("Code administrateur : "))
    if not config.verify_admin_code(_cfg, code):
        print(_("Code invalide."))
        return 1
    _persist_armed(False)
    print(_("Système DÉSARMÉ."))
    return 0


def cmd_lock_now(_args) -> int:
    from security_linux.lock import lock_screen

    return 0 if lock_screen() else 1


def cmd_set_code(_args) -> int:
    cfg, _path = _load_or_default()
    if config.has_admin_code(cfg):
        current = getpass.getpass(_("Code admin actuel : "))
        if not config.verify_admin_code(cfg, current):
            print(_("Code actuel invalide."))
            return 1
    code = getpass.getpass(_("Nouveau code admin : "))
    confirm = getpass.getpass(_("Confirmez : "))
    if code != confirm:
        print(_("Les codes ne correspondent pas."))
        return 1
    if not config.valid_admin_code(code):
        print(_("Le code doit contenir au moins {n} caractères.").format(n=config.MIN_ADMIN_CODE_LENGTH))
        return 1
    config.set_admin_code(cfg, code)
    config.save_config(cfg)
    print(_("Code admin mis à jour."))
    return 0


def cmd_list_bt(_args) -> int:
    from security_linux.monitors.bluetooth import BluetoothMonitor

    if not config.default_config_path().exists():
        print(_("Config par défaut utilisée."))
    mon = BluetoothMonitor({"enabled": True, "device_addr": ""})
    for dev in mon.list_known_devices():
        marker = "*" if dev["address"] == config.load_config()["bluetooth"]["device_addr"] else " "
        print(f"{marker} {dev['address']}  {dev['name']}")
    return 0


def _bt_sample(mon) -> dict:
    """Un échantillon Bluetooth (ts, connected, rssi)."""
    from datetime import datetime  # noqa: PLC0415

    info = mon._probe()
    return {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "connected": info["connected"] is True,
        "rssi": info["rssi"],
    }


def _bt_summarize(samples: list[dict]) -> dict:
    """Synthèse d'une série d'échantillons + seuil RSSI recommandé."""
    total = len(samples)
    connected = sum(1 for s in samples if s["connected"])
    rssi = [s["rssi"] for s in samples if s["rssi"] is not None]
    summary = {
        "total": total,
        "connected": connected,
        "connected_pct": (100.0 * connected / total) if total else 0.0,
        "rssi_count": len(rssi),
        "rssi_min": min(rssi) if rssi else None,
        "rssi_avg": round(sum(rssi) / len(rssi), 1) if rssi else None,
        "rssi_max": max(rssi) if rssi else None,
        "recommended_threshold": None,
    }
    # Le seuil conseillé repose sur les niveaux observés "connecté" (ou tous
    # les échantillons si l'appareil n'a jamais été vu connecté), avec une marge
    # de 8 dBm contre les faux négatifs.
    present = [s["rssi"] for s in samples if s["connected"] and s["rssi"] is not None]
    base = present or rssi
    if base:
        summary["recommended_threshold"] = min(-30, max(-100, round(sum(base) / len(base) - 8)))
    return summary


def _bt_write_csv(path: str, samples: list[dict]) -> None:
    import csv  # noqa: PLC0415

    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ts", "connected", "rssi"])
        for sample in samples:
            writer.writerow([sample["ts"], sample["connected"], sample["rssi"]])


def cmd_bluetooth_test(args) -> int:
    from security_linux.monitors.bluetooth import BluetoothMonitor  # noqa: PLC0415

    cfg = config.load_config()
    addr = (cfg["bluetooth"].get("device_addr") or "").strip().lower()
    if not addr:
        print(_("Aucun appareil Bluetooth configuré (Réglages → Bluetooth)."))
        return 1
    duration = max(1, int(getattr(args, "duration", 120)))
    interval = max(1, int(getattr(args, "interval", 5)))
    out = getattr(args, "out", None)

    mon = BluetoothMonitor({"enabled": True, "device_addr": addr, "min_rssi": -100})
    print(
        _("Test de présence Bluetooth — {appareil} — {duration} s, échantillon toutes les {interval} s").format(
            appareil=mon._device_name(), duration=duration, interval=interval
        )
    )
    deadline = time.monotonic() + duration
    samples: list[dict] = []
    while time.monotonic() < deadline:
        sample = _bt_sample(mon)
        samples.append(sample)
        conn = _("connecté") if sample["connected"] else _("absent")
        rssi = sample["rssi"] if sample["rssi"] is not None else "—"
        print(f"{sample['ts']}  {conn:<10} RSSI {rssi}")
        time.sleep(interval)

    summary = _bt_summarize(samples)
    if out:
        _bt_write_csv(out, samples)
    print(_("— Synthèse —"))
    print(_("  échantillons     : {total}").format(total=summary["total"]))
    print(_("  connecté         : {connected}/{total} ({pct:.0f}%)").format(
        connected=summary["connected"], total=summary["total"], pct=summary["connected_pct"]))
    if summary["rssi_count"]:
        print(_("  RSSI min/moy/max : {min}/{avg}/{max} dBm").format(
            min=summary["rssi_min"], avg=summary["rssi_avg"], max=summary["rssi_max"]))
    if summary["recommended_threshold"] is not None:
        print(_("  seuil RSSI conseillé : {seuil} dBm (réglage min_rssi)").format(seuil=summary["recommended_threshold"]))
    else:
        print(_("  appareil jamais vu connecté : seuil par défaut -70 dBm conseillé"))
    if out:
        print(_("  échantillons enregistrés : {out}").format(out=out))

    events.log_event(
        "bluetooth-test",
        _("test Bluetooth terminé : {connected}/{total} connectés, RSSI min/moy/max {min}/{avg}/{max}").format(
            connected=summary["connected"], total=summary["total"],
            min=summary["rssi_min"] if summary["rssi_min"] is not None else "?", avg=summary["rssi_avg"], max=summary["rssi_max"] if summary["rssi_max"] is not None else "?"),
        samples=len(samples),
    )
    return 0


def cmd_face_save(args) -> int:
    from security_linux import faces

    ok, message = faces.save_reference(device=getattr(args, "device", None))
    print(message)
    return 0 if ok else 1


def cmd_face_check(args) -> int:
    from security_linux import faces

    threshold = getattr(args, "threshold", None)
    if threshold is None:
        threshold = faces.default_threshold()
    result = faces.verify_face(device=getattr(args, "device", None), threshold=threshold)
    print(result.message)
    return 0 if result.ok and result.matched else 1


def cmd_autostart(args) -> int:
    from security_linux import autostart

    state = getattr(args, "state", None)
    if state == "on":
        mechanism = autostart.set_enabled(True)
        print(_("Démarrage au logon activé ({mecanisme}).").format(mecanisme=mechanism))
        return 0
    if state == "off":
        mechanism = autostart.set_enabled(False)
        print(_("Démarrage au logon désactivé ({mecanisme}).").format(mecanisme=mechanism))
        return 0
    print(_("Démarrage au logon : {etat} (mécanisme {mecanisme})").format(
        etat=_("actif") if autostart.is_enabled() else _("inactif"),
        mecanisme=autostart.mechanism(),
    ))
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
    parser.add_argument("--debug", action="store_true", help=_("mode développement temporaire (config/état isolés, verrouillage simulé)"))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help=_("état courant"))
    sub.add_parser("arm", help=_("armer"))
    sub.add_parser("disarm", help=_("désarmer (code admin)"))
    sub.add_parser("lock-now", help=_("verrouiller l'écran tout de suite"))
    sub.add_parser("set-code", help=_("définir/changer le code admin"))
    sub.add_parser("list-bt", help=_("liste des appareils Bluetooth"))
    bt_test = sub.add_parser("bluetooth-test", help=_("test de présence Bluetooth (échantillonnage RSSI)"))
    bt_test.add_argument("--duration", type=int, default=120, help=_("durée du test en secondes (défaut : 120)"))
    bt_test.add_argument("--interval", type=int, default=5, help=_("intervalle d'échantillonnage en secondes (défaut : 5)"))
    bt_test.add_argument("--out", default=None, help=_("fichier CSV de sortie (optionnel)"))
    face_save = sub.add_parser("face-save", help=_("enregistrer la photo de référence"))
    face_save.add_argument("--device", default=None, help=_("périphérique vidéo (défaut : /dev/video0)"))
    face_check = sub.add_parser("face-check", help=_("vérifier le visage devant la caméra"))
    face_check.add_argument("--device", default=None, help=_("périphérique vidéo (défaut : /dev/video0)"))
    face_check.add_argument("--threshold", type=float, default=None, help=_("seuil de correspondance 0..1 (défaut : réglage configuré, 0.45)"))
    autostart = sub.add_parser("autostart", help=_("gérer le démarrage automatique au logon"))
    autostart.add_argument("state", nargs="?", choices=["on", "off"], help=_("on : activer, off : désactiver"))
    sub.add_parser("gui", help=_("ouvrir l'application graphique"))
    daemon = sub.add_parser("daemon", help=_("démarrer le démon"))
    daemon.add_argument("--one-shot", action="store_true", help=_("une seule itération puis s'arrête"))
    daemon.add_argument("--simulate-camera", choices=["present", "absent", "disabled"], help=_("injecter l'état webcam (debug)"))
    daemon.add_argument("--simulate-bluetooth", choices=["present", "absent", "disabled"], help=_("injecter l'état Bluetooth (debug)"))
    daemon.add_argument("--simulate-location", choices=["home", "away", "offline"], help=_("injecter la localisation (debug)"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "debug", False) and args.command in ("set-code", "disarm", "arm", "list-bt", "face-save", "face-check"):
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
        "bluetooth-test": cmd_bluetooth_test,
        "face-save": cmd_face_save,
        "face-check": cmd_face_check,
        "autostart": cmd_autostart,
        "gui": cmd_gui,
        "daemon": cmd_daemon,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

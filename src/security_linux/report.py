"""Rapport d'intrusion consolidé (mode braquage / tamper).

À chaque déclenchement du mode braquage (verrouillage automatique du fait
d'une absence détectée) ou d'un signal de tamper (capteur armé qui disparaît),
le démon rassemble dans un rapport horodaté unique :

  * la raison (``braquage`` / ``tamper``) et l'heure exacte ;
  * l'état d'armement et ``machine_state`` ;
  * les snapshots des moniteurs (webcam, Bluetooth, localisation) avec leurs
    détails (RSSI, appareil surveillé, SSID…) ;
  * le chemin de la capture image si elle a été prise au moment du verrouillage ;
  * les derniers événements de sécurité (journal) ;
  * une synthèse de configuration — jamais les secrets (champs ``admin_code``
    exclus explicitement).

Écriture : ``data_dir()/reports/intrusion_<ts>.json``
(dossier 0700, fichier 0600). Utilitaires ``list_reports()`` /
``read_report()`` pour la visionneuse de l'interface.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import security_linux.config as config
import security_linux.events as events
from security_linux.i18n import _

# Champs de configuration inclus dans le rapport. Tout ce qui n'y figure pas
# (ex. sel/hash du code admin, clé HMAC, positions LED) reste hors du rapport.
_CONFIG_FIELDS = {
    "decision_mode",
    "lock_grace_seconds",
    "auto_lock_repeat_minutes",
    "min_absent_seconds",
    "idle_lock_minutes",
    "braquage",
    "silentium",
    "notifications",
}
_CONFIG_SUBSECTIONS = {
    "location": {"method", "secure_when_offline"},
}


def reports_dir() -> Path:
    return config.data_dir() / "reports"


def _now_ts() -> str:
    return _utcnow()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _config_summary(cfg: dict) -> dict:
    """Synthèse de configuration sans aucune donnée secrète."""
    gen = cfg.get("general", {})
    summary: dict = {}
    for key in _CONFIG_FIELDS:
        if key in cfg:
            summary[key] = cfg[key]
        elif key in gen:
            summary[key] = gen[key]
    loc = cfg.get("location", {})
    loc_summary = {k: loc.get(k) for k in _CONFIG_SUBSECTIONS.get("location", set()) if k in loc}
    if loc_summary:
        summary["location"] = loc_summary
        # nombre de SSID 'maison' sans jamais exposer leur valeur
        home = loc.get("home_ssids", [])
        summary["location"]["home_ssids_count"] = len(home) if isinstance(home, list) else 0
    return summary


def _monitor_snapshot(mon) -> dict:
    """Snapshot sûr d'un moniteur (un objet MonitorResult)."""
    if mon is None:
        return {}
    if hasattr(mon, "to_dict"):
        return mon.to_dict()
    return {"status": str(getattr(mon, "status", "")), "detail": str(getattr(mon, "detail", ""))}


def _linked_events(limit: int = 20) -> list[dict]:
    """Derniers événements du journal, sans leur champ d'intégrité 'chain'."""
    return [
        {k: v for k, v in record.items() if k != "chain"}
        for record in events.read_events(limit=limit)
    ]


def build_report(
    kind: str,
    cfg: dict,
    states: dict,
    capture_path: str | None = None,
    machine_state: str | None = None,
    extra: dict | None = None,
) -> Path | None:
    """Écrit un rapport d'intrusion consolidé. Retourne son chemin (None si échec)."""
    try:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = reports_dir() / f"intrusion_{stamp}.json"
        reports_dir().mkdir(parents=True, exist_ok=True)
        os.chmod(reports_dir(), 0o700)

        payload = {
            "id": f"intrusion_{stamp}",
            "ts": _now_ts(),
            "kind": kind,
            "machine_state": machine_state,
            "capture": capture_path,
            "extra": extra or {},
            "monitors": {name: _monitor_snapshot(mon) for name, mon in states.items()},
            "events": _linked_events(),
            "config": _config_summary(cfg),
        }
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        events.log_event("report", _("rapport d'intrusion écrit : {path}").format(path=target))
        return target
    except OSError as exc:  # noqa: BLE001
        events.log_event("error", _("rapport d'intrusion : {erreur}").format(erreur=exc))
        return None


def list_reports() -> list[dict]:
    """Rapports présents, du plus récent au plus ancien."""
    if not reports_dir().exists():
        return []
    out = []
    for path in sorted(reports_dir().glob("intrusion_*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {}
        out.append(
            {
                "path": str(path),
                "ts": payload.get("ts", path.stat().st_mtime),
                "kind": payload.get("kind", "?"),
                "capture": payload.get("capture"),
            }
        )
    return out


def read_report(path: Path | str) -> dict | None:
    """Contenu d'un rapport, ou None s'il est illisible/absent."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
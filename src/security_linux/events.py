"""Journalisation des événements + état partagé daemon/GUI.

Ecritures dans:
  ~/.local/share/security-linux/events.log       (journal JSON, append)
  ~/.local/share/security-linux/state.json       (état courant, écrit atomiquement)

Fichier de commande (daemon/GUI) :
  ~/.local/share/security-linux/command.json     (consommé et supprimé par le daemon)

Sécurité :
  * chaque ligne du journal porte une empreinte ``chain`` (SHA-256 de la ligne
    précédente) : toute édition/suppression intermédiaire est détectable via
    ``verify_event_log()`` ;
  * le journal est rotaté automatiquement par taille (``MAX_EVENT_LOG_BYTES``),
    MAX_BACKUPS archives conservées ;
  * le canal de commande command.json est authentifié par HMAC-SHA256 : la
    clé de session vit dans ``command.key`` (0600), le démon rejette toute
    commande mal signée et journalise la tentative.
"""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import security_linux.config as config
from security_linux.i18n import _

MAX_EVENT_LOG_BYTES = 1024 * 1024  # 1 Mio avant rotation
MAX_BACKUPS = 5
_SESSION_KEY_NAME = "command.key"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(obj) -> str:
    """Représentation JSON canonique (ordre stable) pour hachage / HMAC."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _ensure_data_dir() -> Path:
    d = config.data_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def events_path() -> Path:
    return config.data_dir() / "events.log"


def state_path() -> Path:
    return config.data_dir() / "state.json"


def command_path() -> Path:
    return config.data_dir() / "command.json"


def command_key_path() -> Path:
    return config.data_dir() / _SESSION_KEY_NAME


# ------------------------------------------------------------ journal (rotation)
def _rotate_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size < MAX_EVENT_LOG_BYTES:
            return
    except OSError:
        return
    for index in range(MAX_BACKUPS - 1, 0, -1):
        src = path.with_name(f"events.log.{index}")
        dst = path.with_name(f"events.log.{index + 1}")
        try:
            dst.unlink(missing_ok=True)
            os.replace(src, dst)
        except OSError:
            pass
    try:
        os.replace(path, path.with_name("events.log.1"))
    except OSError:
        pass


def _last_chain(path: Path) -> str:
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    try:
        return str(json.loads(lines[-1]).get("chain", ""))
    except json.JSONDecodeError:
        return ""


def log_event(kind: str, message: str, **extra) -> None:
    record = {
        "ts": _utcnow(),
        "kind": kind,
        "message": message,
        **extra,
    }
    path = events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(path)
    prev = _last_chain(path)
    record["chain"] = hashlib.sha256(
        (prev + ":" + _canonical(record)).encode("utf-8")
    ).hexdigest()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def verify_event_log(path: Path | None = None) -> dict:
    """Vérifie la chaîne d'intégrité du journal (lignes éditées/supprimées).

    Retourne {'ok': True, 'record': n} ou {'ok': False, 'record': idx, 'reason': ...}.
    """
    path = path or events_path()
    if not path.exists():
        return {"ok": True, "record": 0}
    prev = ""
    for idx, line in enumerate(path.read_text("utf-8", errors="replace").splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return {"ok": False, "record": idx, "reason": "ligne illisible"}
        chain = str(record.get("chain", ""))
        expected = hashlib.sha256(
            (prev + ":" + _canonical({k: v for k, v in record.items() if k != "chain"})).encode("utf-8")
        ).hexdigest()
        if chain != expected:
            return {"ok": False, "record": idx, "reason": "chaîne d'intégrité rompue"}
        prev = chain
    return {"ok": True, "record": idx}


def read_events(limit: int = 100) -> list[dict]:
    path = events_path()
    if not path.exists():
        return []
    lines = path.read_text("utf-8").strip().splitlines()
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_state(state: dict) -> None:
    state.setdefault("ts", _utcnow())
    _atomic_write(state_path(), state)


def read_state() -> dict | None:
    path = state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ------------------------------------------------------------ canal de commande
def _load_session_key() -> bytes:
    """Clé HMAC de session (créée à la demande, 0600).

    Créée par le premier émetteur OU par le démon ; tous les acteurs du
    même utilisateur partagent le même secret (fichier en 0600).
    """
    _ensure_data_dir()
    path = command_key_path()
    if path.exists():
        try:
            raw = path.read_bytes()
            if len(raw) >= 32:
                return raw
        except OSError:
            pass
    key = secrets.token_bytes(32)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(key)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def _sign(payload: dict) -> str:
    msg = _canonical(payload).encode("utf-8")
    return hmac.new(_load_session_key(), msg, hashlib.sha256).hexdigest()


def write_command(cmd: dict) -> None:
    import time

    cmd.setdefault("ts", _utcnow())
    cmd["expires"] = time.time() + 30
    cmd["sig"] = _sign({k: v for k, v in cmd.items() if k != "sig"})
    _atomic_write(command_path(), cmd)
    try:
        command_path().chmod(0o600)
    except OSError:
        pass


def consume_command() -> dict | None:
    """Lit la commande signée, vérifie son HMAC, puis la supprime.

    En cas de signature invalide (falsification) ou de garde non expirée,
    l'événement est journalisé et la commande est rejetée.
    """
    import time

    path = command_path()
    if not path.exists():
        return None
    try:
        raw = path.read_text("utf-8")
    except OSError:
        return None
    try:
        cmd = json.loads(raw)
    except json.JSONDecodeError:
        cmd = None
    try:
        path.unlink()
    except OSError:
        pass
    if not isinstance(cmd, dict):
        return None
    sig = cmd.pop("sig", None)
    expected = _sign(cmd)
    if not sig or not isinstance(sig, str) or not hmac.compare_digest(sig, expected):
        log_event("security", _("commande rejetée : signature invalide (tentative de falsification)"))
        return None
    if cmd.get("expires", 0) >= time.time():
        return cmd
    return None
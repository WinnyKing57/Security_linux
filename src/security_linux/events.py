"""Journalisation des événements + état partagé daemon/GUI.

Ecritures dans:
  ~/.local/share/security-linux/events.log     (journal JSON, append)
  ~/.local/share/security-linux/state.json      (état courant, écrit atomiquement)

Fichier de commande (daemon/GUI) :
  ~/.local/share/security-linux/command.json    (consommé et supprimé par le daemon)
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import security_linux.config as config


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def events_path() -> Path:
    return config.data_dir() / "events.log"


def state_path() -> Path:
    return config.data_dir() / "state.json"


def command_path() -> Path:
    return config.data_dir() / "command.json"


def log_event(kind: str, message: str, **extra) -> None:
    record = {
        "ts": _utcnow(),
        "kind": kind,
        "message": message,
        **extra,
    }
    path = events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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


def consume_command() -> dict | None:
    """Lit et supprime la commande en attente (moins de 30 s)."""
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
    if isinstance(cmd, dict) and cmd.get("expires", 0) >= time.time():
        return cmd
    return None


def write_command(cmd: dict) -> None:
    import time

    cmd.setdefault("ts", _utcnow())
    cmd["expires"] = time.time() + 30
    _atomic_write(command_path(), cmd)
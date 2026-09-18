"""Voyant webcam : marqueur inter-processus partagé daemon/GUI.

À chaque lecture réelle d'une image issue de la webcam, l'application écrit un
horodatage dans un petit fichier (``data_dir()/capture_led.json``). La GUI
allume son point flottant tant que ce dernier clignotement est récent.
Aucune information ne sort de la machine.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from security_linux.config import data_dir

_MARKER = "capture_led.json"


def _marker_path() -> Path:
    return data_dir() / _MARKER


def blink() -> None:
    """Enregistre une lecture d'image webcam (appelée à chaque capture réelle)."""
    try:
        payload = json.dumps({"ts": datetime.now(timezone.utc).isoformat()})
        path = _marker_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, "utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def last_blink() -> datetime | None:
    """Horodatage du dernier clignotement, None si aucun encore émis."""
    try:
        data = json.loads(_marker_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    try:
        ts = datetime.fromisoformat(str(data.get("ts", "")).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except ValueError:
        return None
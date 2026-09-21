"""Temps d'inactivité utilisateur (repli en cas de capteurs indisponibles).

Compatibilité :
  * KDE / X11 → org.freedesktop.ScreenSaver.GetSessionIdleTime (qdbus6/qdbus)
  * GNOME (Wayland/X11) → org.gnome.Mutter.IdleMonitor.GetIdletime (dbus-send)

Piège documenté : ces deux interfaces renvoient la durée en **millisecondes**
même si la spécification ``org.freedesktop.ScreenSaver`` parle de secondes —
KDE et Mutter ignorent la spécification et renvoient des ms. On divise donc
par 1000. Une interface qui renverrait réellement des secondes donne alors une
valeur plus petite : le repli se désactive silencieusement (jamais de faux
verrouillage, contrairement à l'inverse).

Aucune de ces interfaces n'étant fiable partout, ``idle_seconds()`` renvoie
``None`` si l'inactivité est impossible à mesurer (repli silencieusement
désactivé).
"""
from __future__ import annotations

import re
import subprocess

_MS_TO_SECONDS = 1000.0


def _run(cmd: list[str], timeout: float = 5) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout + proc.stderr
    except (subprocess.TimeoutExpired, OSError):
        return -1, ""


def _parse_number(text: str) -> int | None:
    match = re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else None


def idle_seconds() -> float | None:
    """Renvoie la durée d'inactivité en secondes, ou None si indisponible."""
    candidates = (
        ["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "GetSessionIdleTime"],
        ["qdbus", "org.freedesktop.ScreenSaver", "/ScreenSaver", "GetSessionIdleTime"],
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.gnome.Mutter.IdleMonitor",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "org.gnome.Mutter.IdleMonitor.GetIdletime",
        ],
    )
    for cmd in candidates:
        rc, out = _run(cmd)
        if rc != 0:
            continue
        value = _parse_number(out)
        if value is not None and value >= 0:
            return float(value) / _MS_TO_SECONDS
    return None

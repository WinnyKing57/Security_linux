"""Déclenchement du verrouillage d'écran (KDE/X11) et état verrouillé."""
from __future__ import annotations

import os
import subprocess

import security_linux.events as events


def _run(cmd: list[str], timeout: float = 10) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout + proc.stderr
    except (subprocess.TimeoutExpired, OSError):
        return -1, ""


def _active_user_session_id() -> str | None:
    uid = os.geteuid()
    rc, out = _run(["loginctl", "list-sessions", "--no-legend"])
    if rc != 0:
        return None
    candidates: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            session_id, _, session_uid = parts[0], parts[1], parts[2]
            if session_uid.isdigit() and int(session_uid) == uid:
                candidates.append(session_id)
    if not candidates:
        return None
    # privilégier une session graphique (x11/wayland) puis une active
    def _score(sid: str) -> tuple[int, int]:
        rc_t, out_t = _run(["loginctl", "show-session", sid, "-p", "Type", "-p", "Active"])
        info = out_t.lower()
        if "type=wayland" in info or "type=x11" in info:
            type_score = 1
        elif "type=tty" in info:
            type_score = 0
        else:
            type_score = 0
        active = 1 if "active=yes" in info else 0
        return (type_score, active)

    return max(candidates, key=_score)


def lock_screen() -> bool:
    """Verrouille l'écran. Essaie plusieurs mécanismes compatibles KDE."""
    session_id = _active_user_session_id()
    if session_id:
        rc, msg = _run(["loginctl", "lock-session", session_id])
        if rc == 0:
            events.log_event("lock", f"écran verrouillé (session {session_id})")
            return True
    for cmd_prefix in (
        ["qdbus6", "org.kde.screensaver", "/ScreenSaver", "Lock"],
        ["qdbus", "org.kde.screensaver", "/ScreenSaver", "Lock"],
        ["xdg-screensaver", "lock"],
    ):
        rc, _ = _run(list(cmd_prefix), timeout=8)
        if rc == 0:
            events.log_event("lock", f"écran verrouillé via {' '.join(cmd_prefix)}")
            return True
    events.log_event("error", "échec du verrouillage d'écran")
    return False


def is_locked() -> bool:
    session_id = _active_user_session_id()
    if not session_id:
        return False
    rc, out = _run(["loginctl", "show-session", session_id, "-p", "LockedHint"])
    if rc != 0:
        return False
    return "yes" in out.lower()
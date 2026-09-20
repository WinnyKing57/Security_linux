"""Gestion du démarrage automatique au logon.

Deux mécanismes cohérents avec l'installation (`scripts/install.sh` /
`scripts/install_user_service.sh`) :
  * service `systemd --user` durci `security-linuxd.service` (préféré) ;
  * repli autostart XDG : `~/.config/autostart/security-linux.desktop`.

En mode debug (tests), aucun fichier réel n'est touché : le fichier XDG est
créé dans le bac à sable du mode debug, et systemd est ignoré.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import security_linux.runtime as runtime

AUTOSTART_FILENAME = "security-linux.desktop"
SYSTEMD_UNIT = "security-linuxd.service"


def autostart_dir() -> Path:
    """Répertoire des entrées autostart XDG (isolé en mode debug)."""
    if runtime.is_debug():
        return runtime.debug_root() / "config" / "autostart"
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "autostart"


def _xdg_path() -> Path:
    return autostart_dir() / AUTOSTART_FILENAME


def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _systemd_available() -> bool:
    """Le service systemd utilisateur est-il installé ?"""
    if runtime.is_debug():
        return False
    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "systemd" / "user"
    if (unit_dir / SYSTEMD_UNIT).exists():
        return True
    rc, out = _run(["systemctl", "--user", "show", SYSTEMD_UNIT, "-p", "UnitFileState", "--no-pager"])
    return rc == 0 and "not-found" not in out


def _systemd_enabled() -> bool:
    if not _systemd_available():
        return False
    rc, out = _run(["systemctl", "--user", "is-enabled", SYSTEMD_UNIT])
    return rc == 0 and "enabled" in out


def _desktop_entry() -> str:
    exe = shutil.which("security-linuxd")
    if not exe:
        exe = f"{sys.executable} -m security_linux.daemon"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Security-Linux\n"
        "Name[fr]=Security-Linux\n"
        "Comment=Verrouillage automatique et détection de présence\n"
        f"Exec={exe}\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
        "X-KDE-autostart-after=panel\n"
    )


def _set_xdg(enabled: bool) -> None:
    path = _xdg_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_desktop_entry(), "utf-8")
    else:
        path.unlink(missing_ok=True)


def is_enabled() -> bool:
    """Le système se lance-t-il au logon ?"""
    if _systemd_enabled():
        return True
    return _xdg_path().exists()


def mechanism() -> str:
    """Mécanisme actif : "systemd", "xdg" ou "none"."""
    if _systemd_enabled():
        return "systemd"
    if _xdg_path().exists():
        return "xdg"
    return "none"


def set_enabled(enabled: bool) -> str:
    """Active/désactive le démarrage au logon. Retourne le mécanisme utilisé."""
    # systemd est privilégié quand il est disponible (cohérent avec install.sh).
    if not runtime.is_debug() and _systemd_available():
        verb = "enable" if enabled else "disable"
        rc, _ = _run(["systemctl", "--user", verb, "--now", SYSTEMD_UNIT], timeout=30.0)
        if rc == 0:
            # Évite un double démarrage au prochain logon si le fichier
            # autostart XDG avait été posé auparavant.
            _set_xdg(False)
            return "systemd"
    _set_xdg(enabled)
    return "xdg"
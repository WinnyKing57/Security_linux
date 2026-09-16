"""Module Howdy (déverrouillage facial) — DORMANT par défaut.

En v1 le déverrouillage d'écran reste le mot de passe de session. Howdy est
détecté s'il est installé et l'application n'autorise l'activation de la
reconnaissance faciale qu'une fois un visage enregistré (contrainte produit :
"un visage doit être défini avant d'activer Howdy").
"""
from __future__ import annotations

import os
import shutil
import subprocess


def is_installed() -> bool:
    return shutil.which("howdy") is not None


def _models_dir() -> str:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    return f"/etc/howdy/models/{user}"


def models_status() -> str:
    """Retourne "ok" (visages présents), "none" (aucun), ou "unknown" (non lisible)."""
    if not is_installed():
        return "not_installed"
    path = _models_dir()
    if not os.path.isdir(path):
        return "none"
    try:
        entries = [e for e in os.listdir(path) if os.path.isfile(os.path.join(path, e))]
        if entries:
            return "ok"
        return "none"
    except PermissionError:
        return "unknown"


def detect_device_path() -> str:
    """Premier /dev/video* présent, sinon "none" (reste la valeur défaut de howdy)."""
    for index in range(16):
        if os.path.exists(f"/dev/video{index}"):
            return f"/dev/video{index}"
    return "none"


def config_device_path() -> str:
    """device_path lu dans /etc/howdy/config.ini ("none" si absent/lisible)."""
    try:
        with open("/etc/howdy/config.ini", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("device_path") and "=" in line:
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "none"


def enroll_command() -> list[str] | None:
    """Lancer l'enregistrement du visage (mot de passe root demandé).

    Corrige d'abord device_path (Howdy refuse d'enregistrer quand il vaut
    ``none`` ou un périphérique inexistant), puis lance ``howdy add``.
    """
    if not is_installed():
        return None
    terminal = shutil.which("x-terminal-emulator") or shutil.which("konsole") or shutil.which("gnome-terminal")
    if not terminal:
        return None
    cmd = (
        "dev=/dev/video0; "
        "for i in 0 1 2 3 4 5 6 7; do [ -e /dev/video$i ] && dev=/dev/video$i && break; done; "
        "sed -i \"s|^device_path.*|device_path = $dev|\" /etc/howdy/config.ini 2>/dev/null || true; "
        "echo \"device_path -> $dev\"; "
        "howdy add"
    )
    return [terminal, "-e", "sudo", "sh", "-c", cmd]


def install_script_path() -> str | None:
    """Chemin du script d'installation de Howdy fourni dans le dépôt."""
    repo = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    candidate = os.path.join(repo, "scripts", "install_howdy.sh")
    return candidate if os.path.isfile(candidate) else None


def install_command(script: str) -> list[str] | None:
    """Commande de lancement de l'installation Howdy (root via pkexec, GUI)."""
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return None
    terminal = shutil.which("x-terminal-emulator") or shutil.which("konsole") or shutil.which("gnome-terminal")
    if terminal:
        return [terminal, "-e", pkexec, script]
    return [pkexec, script]
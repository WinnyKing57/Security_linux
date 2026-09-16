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


def enroll_command() -> list[str] | None:
    """Commande lancer l'enregistrement du visage (demande le mot de passe root)."""
    if not is_installed():
        return None
    terminal = shutil.which("x-terminal-emulator") or shutil.which("konsole") or shutil.which("gnome-terminal")
    if terminal:
        return [terminal, "-e", "sudo", "howdy", "add"]
    return None


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
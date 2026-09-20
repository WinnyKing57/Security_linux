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


def models_status(model_dir: str | None = None) -> str:
    """Retourne "ok" (visages présents), "none" (aucun) ou "unknown" (non lisible).

    Howdy stocke le visage de l'utilisateur soit dans
    ``/etc/howdy/models/<user>.dat`` (fichier unique), soit dans un répertoire
    ``/etc/howdy/models/<user>/`` (styles d'encodage anciens). Les deux formes
    sont détectées. ``model_dir`` permet de tester un autre emplacement.
    """
    if not is_installed():
        return "not_installed"
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    model_dir = model_dir or "/etc/howdy/models"
    try:
        if not os.path.isdir(model_dir):
            return "none"
        if os.path.isfile(os.path.join(model_dir, f"{user}.dat")):
            return "ok"
        user_dir = os.path.join(model_dir, user)
        if os.path.isdir(user_dir):
            entries = [e for e in os.listdir(user_dir) if os.path.isfile(os.path.join(user_dir, e))]
            if entries:
                return "ok"
        return "none"
    except (PermissionError, OSError):
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


def enroll_command(device: str | None = None) -> list[str] | None:
    """Lancer l'enregistrement du visage (mot de passe root demandé).

    Utilise le périphérique passé en argument (celui sélectionné dans les
    réglages de l'application) et corrige ``device_path`` dans
    /etc/howdy/config.ini avant ``howdy add`` (Howdy refuse d'enregistrer
    quand il vaut ``none`` ou un périphérique inexistant).
    """
    if not is_installed():
        return None
    terminal = shutil.which("x-terminal-emulator") or shutil.which("konsole") or shutil.which("gnome-terminal")
    if not terminal:
        return None
    if not device or not os.path.exists(device):
        device = detect_device_path()
    cmd = (
        "sed -i \"s|^device_path.*|device_path = {}|\" /etc/howdy/config.ini 2>/dev/null || true; "
        "echo \"device_path -> {}\"; "
        "howdy add"
    ).format(device, device)
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


# ----------------------------------------------------- fiabilité Howdy
DEFAULT_CONFIG_PATH = "/etc/howdy/config.ini"
_DEFAULT_CERTAINTY = 3.5


def config_path() -> str:
    """Chemin du fichier de configuration Howdy."""
    return DEFAULT_CONFIG_PATH


def config_value(key: str, path: str | None = None) -> str | None:
    """Valeur d'une clé dans config.ini (première occurrence non commentée).

    Lit ``/etc/howdy/config.ini`` (lisible par tous) ; ``None`` si le fichier
    est absent ou la clé inconnue.
    """
    try:
        with open(path or DEFAULT_CONFIG_PATH, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if not line.startswith("[") and "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() == key:
                        return v.strip()
    except OSError:
        return None
    return None


def certainty(path: str | None = None) -> float:
    """Seuil de correspondance Howdy (plus bas = plus strict)."""
    try:
        return float(config_value("certainty", path) or _DEFAULT_CERTAINTY)
    except (TypeError, ValueError):
        return _DEFAULT_CERTAINTY


def use_cnn(path: str | None = None) -> bool:
    """True si la détection CNN (plus précise) est activée dans Howdy."""
    value = (config_value("use_cnn", path) or "false").strip().lower()
    return value in ("1", "true", "yes", "on")


def disabled(path: str | None = None) -> bool:
    """True si Howdy est désactivé (clé ``disabled`` du fichier config.ini).

    Howdy lit la clé ``[core] disabled`` : quand elle vaut ``true``, la
    reconnaissance faciale est coupée (le mot de passe reprend seul la main).
    """
    value = (config_value("disabled", path) or "false").strip().lower()
    return value in ("1", "true", "yes", "on")


def tune_script_path() -> str | None:
    """Chemin du script de réglage de la fiabilité (exécuté en root)."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidate = os.path.join(repo, "scripts", "tune_howdy.sh")
    return candidate if os.path.isfile(candidate) else None


def tune_command(certainty_value: float | None = None, use_cnn_value: bool | None = None) -> list[str] | None:
    """Commande de lancement du réglage de fiabilité (root, terminal).

    Applique ``certainty`` et/ou ``use_cnn`` dans /etc/howdy/config.ini.
    Les valeurs ``None`` ne modifient pas la clé correspondante.
    """
    script = tune_script_path()
    if not script:
        return None
    args = []
    if certainty_value is not None:
        args += ["--certainty", f"{float(certainty_value):g}"]
    if use_cnn_value is not None:
        args.append("--use-cnn" if use_cnn_value else "--no-use-cnn")
    terminal = shutil.which("x-terminal-emulator") or shutil.which("konsole") or shutil.which("gnome-terminal")
    if terminal:
        return [terminal, "-e", "sudo", "sh", script, *args]
    return ["sudo", "sh", script, *args]


def toggle_command(enable: bool) -> list[str] | None:
    """Commande pour activer/désactiver réellement Howdy (root, terminal).

    Écrit la clé ``[core] disabled`` du config.ini Howdy : ``true`` (OFF)
    ou ``false`` (ON). Retourne ``None`` si le script de réglage est absent.
    """
    script = tune_script_path()
    if not script:
        return None
    flag = "--enable" if enable else "--disable"
    terminal = shutil.which("x-terminal-emulator") or shutil.which("konsole") or shutil.which("gnome-terminal")
    if terminal:
        return [terminal, "-e", "sudo", "sh", script, flag]
    return ["sudo", "sh", script, flag]
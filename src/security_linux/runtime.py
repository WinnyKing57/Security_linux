"""Mode d'exécution de l'application.

  production  : comportement normal (verrouillage réel, config utilisateur).
  debug       : mode développement TEMPORAIRE.

Activation du mode debug :
  * variable d'environnement  SECURITY_LINUX_MODE=debug
  * ou drapeau en ligne de commande  --debug

En mode debug :
  - configuration et données ISOLÉES dans ~/.cache/security-linux/debug
    (aucune modification de la configuration/de l'état de production) ;
  - le verrouillage d'écran est SIMULÉ (rien n'est verrouillé réellement) ;
  - les journaux sont redondants sur la sortie standard ;
  - l'état des capteurs peut être INJECTÉ (scénarios de test) via
    SECURITY_LINUX_SIM_CAMERA / _BT / _LOCATION.
"""
from __future__ import annotations

import os
from pathlib import Path


def is_debug() -> bool:
    value = os.environ.get("SECURITY_LINUX_MODE", "").strip().lower()
    return value in ("1", "true", "yes", "debug", "dev")


def mode() -> str:
    return "debug" if is_debug() else "production"


def debug_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser()
    return base / "security-linux" / "debug"


def sim_camera() -> str | None:
    return os.environ.get("SECURITY_LINUX_SIM_CAMERA", "") or None


def sim_bluetooth() -> str | None:
    return os.environ.get("SECURITY_LINUX_SIM_BLUETOOTH", "") or None


def sim_location() -> str | None:
    return os.environ.get("SECURITY_LINUX_SIM_LOCATION", "") or None


def simulate_lock(fn):
    """Enveloppe un verrouilleur réel : en debug, il est simulé."""

    def wrapped():
        if is_debug():
            print("[debug] 🔒 verrouillage d'écran SIMULÉ (aucune action réelle)")
            return True
        return fn()

    return wrapped


def debug_msg(msg: str) -> None:
    if is_debug():
        print(f"[debug] {msg}")
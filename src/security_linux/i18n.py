"""Localisation de l'application via gettext.

La langue SOURCE des chaînes est le français (les ``msgid`` sont en
français). Les traductions (``.po``/``.mo``) sont embarquées dans le paquet
sous ``locales/<lang>/LC_MESSAGES/security-linux.mo``.

Langue choisie (premier trouvé) :
  1. variable ``SECURITY_LINUX_LANG`` (forcée, ex. ``en``)
  2. ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG`` (locale système)
  3. français (défaut — les ``msgid`` sont déjà en français)
"""
from __future__ import annotations

import gettext
import os
from pathlib import Path

_LOCALEDIR = Path(__file__).resolve().parent / "locales"
_DEFAULT_LANG = "fr"
_DOMAIN = "security-linux"


def current_lang() -> str:
    """Langue effective (code court : 'fr', 'en', 'de', …)."""
    for var in ("SECURITY_LINUX_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        raw = os.environ.get(var)
        if raw:
            code = raw.split(".")[0].replace("-", "_")
            if "_" in code:
                code = code.split("_")[0]
            return code.strip() or _DEFAULT_LANG
    return _DEFAULT_LANG


_translation = gettext.translation(
    _DOMAIN,
    localedir=str(_LOCALEDIR),
    languages=[current_lang(), _DEFAULT_LANG],
    fallback=True,
)


def _(message: str) -> str:
    """Traduit une chaîne (source : français)."""
    return _translation.gettext(message)


def _p(message: str) -> str:
    """Alias pour les chaînes à pluralisation non gérée (retourne l'original)."""
    return _translation.gettext(message)
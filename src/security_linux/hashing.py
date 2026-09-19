"""Hachage du code administrateur.

Algorithme par défaut : **Argon2id** (via ``argon2-cffi``, si le module est
installé), sinon repli sur **PBKDF2-SHA256** (stdlib, 200 000 itérations).
PBKDF2 reste pris en charge en lecture pour les comptes créés avant Argon2id
(verrouillage/application sur machine sans argon2-cffi).

Interface :
  * ``generate_salt()``            — sel aléatoire (16 octets, hex) pour PBKDF2 ;
  * ``argon2_available()``         — argon2-cffi est-il installé ?
  * ``argon2id_hash(code)``        — empreinte Argon2id au format PHC ;
  * ``hash_code(code, salt)``      — PBKDF2 (compat historique) ;
  * ``verify_code(...)``           — vérification selon l'algorithme choisi.
"""
from __future__ import annotations

import hashlib
import hmac
import os

_ITERATIONS = 200_000
_ALGO = "sha256"

# Paramètres Argon2id (cohérents avec les recommandations OWASP 2023).
_ARGON_TIME_COST = 3
_ARGON_MEMORY_KIB = 64 * 1024  # 64 Mio
_ARGON_PARALLELISM = 1


def _argon2():
    """Module argon2 (argon2-cffi) ou None s'il n'est pas installé."""
    try:
        import argon2  # noqa: PLC0415

        return argon2
    except ImportError:
        return None


def argon2_available() -> bool:
    return _argon2() is not None


def generate_salt() -> str:
    return os.urandom(16).hex()


def argon2id_hash(code: str) -> str:
    """Empreinte Argon2id au format PHC (sel inclus dans la chaîne)."""
    mod = _argon2()
    if mod is None:
        raise RuntimeError("argon2-cffi n'est pas installé")
    hasher = mod.PasswordHasher(
        time_cost=_ARGON_TIME_COST,
        memory_cost=_ARGON_MEMORY_KIB,
        parallelism=_ARGON_PARALLELISM,
    )
    return hasher.hash(code)


def argon2id_verify(code: str, encoded: str) -> bool:
    mod = _argon2()
    if mod is None:
        return False
    try:
        mod.PasswordHasher().verify(encoded, code)
        return True
    except (mod.exceptions.VerifyMismatchError, mod.exceptions.InvalidHashError):
        return False


def hash_code(code: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(_ALGO, code.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return digest.hex()


def verify_code(code: str, salt: str, expected: str, algorithm: str = "pbkdf2") -> bool:
    """Vérifie un code selon ``algorithm`` (``argon2id`` ou ``pbkdf2``)."""
    if algorithm == "argon2id":
        return argon2id_verify(code, expected)
    candidate = hash_code(code, salt)
    return hmac.compare_digest(candidate, expected)
"""Hachage du code administrateur (pbkdf2, stdlib uniquement)."""
import hashlib
import hmac
import os

_ITERATIONS = 200_000
_ALGO = "sha256"


def generate_salt() -> str:
    return os.urandom(16).hex()


def hash_code(code: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(_ALGO, code.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return digest.hex()


def verify_code(code: str, salt: str, expected_hex: str) -> bool:
    candidate = hash_code(code, salt)
    return hmac.compare_digest(candidate, expected_hex)
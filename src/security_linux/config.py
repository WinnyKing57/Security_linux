"""Configuration de l'application (JSON, permissions restreintes).

Emplacement par défaut : ~/.config/security-linux/config.json
"""
import copy
import json
import os
import time
from pathlib import Path

import security_linux.events as events
import security_linux.hashing as hashing
import security_linux.runtime as runtime

DEFAULT_CONFIG = {
    "general": {
        "armed": True,
        "decision_mode": "AND",
        "lock_grace_seconds": 10,
        "auto_lock_repeat_minutes": 3,
        "min_absent_seconds": 20,
    },
    "camera": {
        "enabled": True,
        "device": "/dev/video0",
        "poll_seconds": 5,
        "absent_confirmations": 3,
        "capture_on_lock": True,
    },
    "bluetooth": {
        "enabled": True,
        "device_addr": "",
        "poll_seconds": 10,
        "absent_confirmations": 3,
        "min_rssi": -70,
    },
    "location": {
        "method": "wifi",
        "home_ssids": [],
        "secure_when_offline": True,
        "home_lat": 0.0,
        "home_lon": 0.0,
        "radius_km": 1.0,
    },
    "howdy": {
        "enabled": False,
        "require_face_before_enable": True,
    },
    "admin_code": {
        "salt": "",
        "hash": "",
        "failed_attempts": [],
    },
    "silentium": {
        "enabled": False,
        "start_hour": 23,
        "end_hour": 7,
    },
    "braquage": {
        "enabled": False,
        "alarm_duration": 5,
    },
    "notifications": {
        "rearm": True,
    },
}

CONFIG_DIR_ENV = "SECURITY_LINUX_CONFIG_DIR"


def config_dir() -> Path:
    if runtime.is_debug():
        return runtime.debug_root() / "config"
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "security-linux"


def data_dir() -> Path:
    if runtime.is_debug():
        return runtime.debug_root() / "data"
    base = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    return base / "security-linux"


def default_config_path() -> Path:
    return config_dir() / "config.json"


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(path: Path | None = None) -> dict:
    path = path or default_config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: dict, path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)


def has_admin_code(cfg: dict) -> bool:
    return bool(cfg["admin_code"]["hash"]) and bool(cfg["admin_code"]["salt"])


def set_admin_code(cfg: dict, code: str) -> None:
    cfg["admin_code"]["salt"] = hashing.generate_salt()
    cfg["admin_code"]["hash"] = hashing.hash_code(code, cfg["admin_code"]["salt"])


def verify_admin_code(cfg: dict, code: str) -> bool:
    """Vérifie le code admin avec protection anti-bruteforce.

    Limite : 3 tentatives maximum par fenêtre de 10 minutes (600s).
    Retourne False si la limite est atteinte.
    """
    if not has_admin_code(cfg):
        return False

    now = time.time()
    window_seconds = 600  # 10 minutes
    max_attempts = 3

    failed = cfg["admin_code"].get("failed_attempts", [])
    recent = [t for t in failed if now - t < window_seconds]

    if len(recent) >= max_attempts:
        oldest = min(recent) if recent else 0
        wait = int(window_seconds - (now - oldest))
        events.log_event("security", f"trop de tentatives échouées, attendez {wait}s")
        return False

    if hashing.verify_code(code, cfg["admin_code"]["salt"], cfg["admin_code"]["hash"]):
        cfg["admin_code"]["failed_attempts"] = recent
        save_config(cfg)
        return True

    recent.append(now)
    cfg["admin_code"]["failed_attempts"] = recent
    save_config(cfg)
    events.log_event("security", f"échec authentification admin ({len(recent)}/{max_attempts})")
    return False


def is_silentium_active(cfg: dict) -> bool:
    """Vérifie si le mode Silentium est actif (heures nocturnes).
    
    Le mode Silentium désactive le verrouillage automatique pendant
    les heures nocturnes configurées (par défaut 23h-7h).
    
    Retourne True si on est dans la plage horaire Silentium.
    """
    silentium_cfg = cfg.get("silentium", {})
    if not silentium_cfg.get("enabled", False):
        return False
    
    from datetime import datetime  # noqa: PLC0415
    
    current_hour = datetime.now().hour
    start_hour = int(silentium_cfg.get("start_hour", 23))
    end_hour = int(silentium_cfg.get("end_hour", 7))
    
    # Gestion des plages qui traversent minuit (ex: 23h-7h)
    if start_hour > end_hour:
        # Plage nocturne (ex: 23h à 7h)
        return current_hour >= start_hour or current_hour < end_hour
    else:
        # Plage diurne (ex: 14h à 16h)
        return start_hour <= current_hour < end_hour
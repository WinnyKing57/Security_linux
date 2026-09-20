"""Tests du démarrage automatique au logon (isolés au mode debug)."""
from __future__ import annotations

import security_linux.autostart as autostart
from security_linux.config import DEFAULT_CONFIG
from security_linux.runtime import debug_root


def _xdg_path():
    return debug_root() / "config" / "autostart" / autostart.AUTOSTART_FILENAME


def test_autostart_defaults_enabled():
    assert DEFAULT_CONFIG["general"]["autostart"] is True


def test_autostart_xdg_isolation_debug():
    path = _xdg_path()
    assert autostart.autostart_dir() == path.parent
    path.unlink(missing_ok=True)
    assert autostart.is_enabled() is False
    assert autostart.mechanism() == "none"


def test_autostart_xdg_toggle(tmp_path, monkeypatch):
    orig_dir = autostart.autostart_dir
    monkeypatch.setattr(autostart, "autostart_dir", lambda: tmp_path)
    path = tmp_path / autostart.AUTOSTART_FILENAME

    mechanism = autostart.set_enabled(True)
    assert mechanism == "xdg"
    assert path.exists()
    content = path.read_text("utf-8")
    assert "[Desktop Entry]" in content
    assert "X-KDE-autostart-after=panel" in content
    assert autostart.is_enabled() is True
    assert autostart.mechanism() == "xdg"

    mechanism = autostart.set_enabled(False)
    assert mechanism == "xdg"
    assert not path.exists()
    assert autostart.is_enabled() is False
    assert autostart.mechanism() == "none"
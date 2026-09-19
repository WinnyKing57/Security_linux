"""Tests de la commande CLI `bluetooth-test` (échantillonnage + synthèse).

Sans matériel : le moniteur est mocké, seuls le calcul de synthèse, l'écriture
CSV et le déroulement de la commande sont vérifiés.
"""
from __future__ import annotations

import csv

import pytest

from security_linux import cli, config


def _samples() -> list[dict]:
    return [
        {"ts": "10:00:01", "connected": True, "rssi": -45},
        {"ts": "10:00:06", "connected": True, "rssi": -50},
        {"ts": "10:00:11", "connected": True, "rssi": -48},
        {"ts": "10:00:16", "connected": False, "rssi": -78},
        {"ts": "10:00:21", "connected": True, "rssi": -52},
        {"ts": "10:00:26", "connected": False, "rssi": None},
    ]


def test_bt_summarize():
    summary = cli._bt_summarize(_samples())
    assert summary["total"] == 6
    assert summary["connected"] == 4
    assert summary["connected_pct"] == pytest.approx(66.66, abs=0.01)
    assert summary["rssi_count"] == 5
    assert summary["rssi_min"] == -78
    assert summary["rssi_avg"] == -54.6
    assert summary["rssi_max"] == -45
    # moyenne des "connectés" (-48.75) moins marge de 8 → -57 arrondi → -57
    assert summary["recommended_threshold"] == -57


def test_bt_summarize_never_connected():
    summary = cli._bt_summarize([{"ts": "t", "connected": False, "rssi": -80}] * 3)
    assert summary["connected"] == 0
    assert summary["connected_pct"] == 0.0
    assert summary["recommended_threshold"] is not None  # basé sur "présents" sinon tous


def test_bt_summarize_empty():
    summary = cli._bt_summarize([])
    assert summary["total"] == 0
    assert summary["recommended_threshold"] is None


def test_bt_summarize_clamps_threshold():
    strong = [{"ts": "t", "connected": True, "rssi": -25}] * 4
    weak = [{"ts": "t", "connected": True, "rssi": -95}] * 4
    assert cli._bt_summarize(strong)["recommended_threshold"] == -33
    assert cli._bt_summarize(weak)["recommended_threshold"] == -100


def test_bt_write_csv(tmp_path):
    out = tmp_path / "bt.csv"
    cli._bt_write_csv(str(out), _samples())
    with open(out, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["ts", "connected", "rssi"]
    assert len(rows) == 7
    assert rows[1] == ["10:00:01", "True", "-45"]
    assert rows[6] == ["10:00:26", "False", ""]


def test_cmd_bluetooth_test_no_device(capsys):
    old = config.DEFAULT_CONFIG["bluetooth"]["device_addr"]
    try:
        config.DEFAULT_CONFIG["bluetooth"]["device_addr"] = ""
        rc = cli.cmd_bluetooth_test(__import__("argparse").Namespace(duration=1, interval=1, out=None))
    finally:
        config.DEFAULT_CONFIG["bluetooth"]["device_addr"] = old
    assert rc == 1
    assert "Aucun appareil" in capsys.readouterr().out


def test_cmd_bluetooth_test_runs_and_writes_csv(tmp_path, monkeypatch, capsys):
    old = config.load_config()["bluetooth"]["device_addr"]
    cfg = config.load_config()
    cfg["bluetooth"]["device_addr"] = "aa:bb:cc:dd:ee:ff"
    config.save_config(cfg)
    try:
        monkeypatch.setattr(cli, "_bt_sample", lambda mon: _samples()[0] if not hasattr(mon, "_i") else _samples()[mon._i % 6])
        import types

        counter = {"i": 0}

        def fake_sample(mon):
            s = _samples()[counter["i"] % 6]
            counter["i"] += 1
            return s

        monkeypatch.setattr(cli, "_bt_sample", fake_sample)
        monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
        argv = ["--duration", "1", "--interval", "1", "--out", str(tmp_path / "bt.csv")]
        rc = cli.cmd_bluetooth_test(cli.build_parser().parse_args(["bluetooth-test", *argv]))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Synthèse" in out
        assert (tmp_path / "bt.csv").exists()
    finally:
        config.DEFAULT_CONFIG["bluetooth"]["device_addr"] = old
        cfg2 = config.load_config()
        cfg2["bluetooth"]["device_addr"] = old
        config.save_config(cfg2)
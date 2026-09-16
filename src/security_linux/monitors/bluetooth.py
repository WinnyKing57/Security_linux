"""Moniteur Bluetooth : présence d'un appareil (téléphone / tablette / montre).

Local via BlueZ (bluetoothctl). Aucun appel réseau extérieur.
"""
from __future__ import annotations

import subprocess

from security_linux.monitors.base import Monitor, MonitorResult


def _run(cmd: list[str], timeout: float = 10) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout
    except (subprocess.TimeoutExpired, OSError):
        return -1, ""


class BluetoothMonitor(Monitor):
    name = "bluetooth"

    def __init__(self, cfg: dict) -> None:
        monitored = dict(cfg)
        monitored.setdefault("enabled", True)
        super().__init__(monitored)
        self._addr = (cfg.get("device_addr") or "").strip().lower()
        self._min_rssi = int(cfg.get("min_rssi", -70))
        self._last_rssi: int | None = None
        self._last_connected: bool | None = None

    def supported(self) -> bool:
        return self._addr != ""

    def list_known_devices(self) -> list[dict]:
        rc, out = _run(["bluetoothctl", "devices"], timeout=8)
        devices = []
        if rc != 0:
            return devices
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "Device":
                devices.append({"address": parts[1].lower(), "name": " ".join(parts[2:])})
        return devices

    def _device_info(self) -> dict:
        rc, out = _run(["bluetoothctl", "info", self._addr], timeout=8)
        info: dict = {"connected": None, "rssi": None, "found": False}
        if rc != 0 or not out:
            return info
        for line in out.splitlines():
            line = line.strip()
            if line.lower().startswith("connected:"):
                info["connected"] = "yes" in line.lower()
                info["found"] = True
            elif line.upper().startswith("RSSI:"):
                try:
                    info["rssi"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        return info

    def _probe(self) -> dict:
        info = self._device_info()
        if info["rssi"] is None and not info["connected"]:
            _run(["bluetoothctl", "scan", "on"], timeout=1)
            _run(["bluetoothctl", "scan", "off"], timeout=2)
            info = self._device_info()
        self._last_rssi = info["rssi"]
        self._last_connected = info["connected"]
        return info

    def tick(self) -> MonitorResult:
        import time as _time

        if not self.enabled():
            return MonitorResult(self.name, "disabled", "moniteur coupé")
        self._addr = (self.cfg.get("device_addr") or "").strip().lower()
        self._min_rssi = int(self.cfg.get("min_rssi", -70))
        if not self.supported():
            return MonitorResult(
                self.name,
                "unconfigured",
                "aucun appareil Bluetooth choisi dans les réglages",
            )
        info = self._probe()
        connected = info["connected"] is True
        rssi = info["rssi"]
        present_now = connected or (rssi is not None and rssi >= self._min_rssi)
        # Petite mémoire : si connecté récemment, on ne déclare pas absent immédiatement
        self._start_absent_if(not present_now, _time.time())
        if present_now:
            detail = f"connecté (RSSI {rssi} dBm)" if connected else f"signal RSSI {rssi} dBm"
            return MonitorResult(self.name, "present", detail, extra={"rssi": rssi, "connected": connected})
        elapsed = int(self.absent_elapsed_seconds)
        return MonitorResult(
            self.name,
            "absent",
            f"appareil non joignable depuis {elapsed}s",
            absent_since=self.absent_since,
            extra={"rssi": rssi, "connected": connected},
        )
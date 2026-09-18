"""Moniteur Bluetooth : présence d'un appareil (téléphone / tablette / montre).

Local via BlueZ (bluetoothctl). Aucun appel réseau extérieur.
"""
from __future__ import annotations

import subprocess

from security_linux.i18n import _
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

    def _device_name(self) -> str:
        for dev in self.list_known_devices():
            if dev["address"] == self._addr:
                return dev["name"]
        return self._addr or _("(aucun)")

    def _connected_names(self) -> list[str]:
        names: list[str] = []
        for dev in self.list_known_devices():
            if dev["address"] == self._addr:
                continue
            rc, out = _run(["bluetoothctl", "info", dev["address"]], timeout=6)
            if rc == 0:
                for line in out.splitlines():
                    if "Connected:" in line and "yes" in line.lower():
                        names.append(dev["name"])
                        break
        return names

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
            return MonitorResult(self.name, "disabled", _("moniteur coupé"))
        self._addr = (self.cfg.get("device_addr") or "").strip().lower()
        self._min_rssi = int(self.cfg.get("min_rssi", -70))
        if not self.supported():
            return MonitorResult(
                self.name,
                "unconfigured",
                _("aucun appareil Bluetooth choisi dans les réglages"),
            )
        info = self._probe()
        connected = info["connected"] is True
        rssi = info["rssi"]
        present_now = connected or (rssi is not None and rssi >= self._min_rssi)
        # Petite mémoire : si connecté récemment, on ne déclare pas absent immédiatement
        self._start_absent_if(not present_now, _time.time())
        monitored = self._device_name()
        other_connected = ", ".join(self._connected_names()) or None
        if present_now:
            detail = (_("{appareil} : connecté (RSSI {rssi} dBm)").format(appareil=monitored, rssi=rssi)
                      if connected
                      else _("{appareil} : RSSI {rssi} dBm").format(appareil=monitored, rssi=rssi))
            return MonitorResult(self.name, "present", detail, extra={"rssi": rssi, "connected": connected, "monitored_name": monitored, "other_connected": other_connected})
        elapsed = int(self.absent_elapsed_seconds)
        extra = {"rssi": rssi, "connected": connected, "monitored_name": monitored, "other_connected": other_connected, "absent_elapsed_seconds": elapsed}
        suffix = _(" — autres connectés : {noms}").format(noms=other_connected) if other_connected else ""
        return MonitorResult(
            self.name,
            "absent",
            _("{appareil} non joignable depuis {secondes}s{suffix}").format(appareil=monitored, secondes=elapsed, suffix=suffix),
            absent_since=self.absent_since,
            extra=extra,
        )
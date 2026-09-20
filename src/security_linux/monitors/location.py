"""Moniteur de localisation : "suis-je à la maison ?".

Méthodes :
  * wifi (défaut, 100 % local) : le SSID Wi-Fi actif est comparé à la liste
    des SSID "maison". Aucun appel réseau extérieur.
  * gps (optionnel) : GeoClue si disponible (peut nécessiter des sources en
    ligne - activé à la demande uniquement). En cas d'échec, repli sur wifi.

La détection "hors ligne" (aucun réseau actif) est locale (table de routage/nmcli).
"""
from __future__ import annotations

import math
import subprocess

from security_linux.i18n import _
from security_linux.monitors.base import Monitor, MonitorResult


def _run(cmd: list[str], timeout: float = 8) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout
    except (subprocess.TimeoutExpired, OSError):
        return -1, ""


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def current_position() -> tuple[float, float] | None:
    """Position GPS actuelle via GeoClue2 : ``(latitude, longitude)``.

    Utilisée par le moniteur (méthode gps) et par la GUI pour poser le
    domicile sur la position courante. Retourne ``None`` si GeoClue2 ou le
    module dbus est indisponible / refusé (déclenche le repli Wi-Fi).
    """
    try:
        import dbus  # noqa: PLC0415
    except ImportError:
        return None
    client = None
    try:
        bus = dbus.SystemBus()
        obj = bus.get_object("org.freedesktop.GeoClue2", "/org/freedesktop/GeoClue2/Client")
        client = dbus.Interface(obj, "org.freedesktop.GeoClue2.Client")
        client.SetDesktopId("security-linux")
        client.Start()
        client.update_properties()
        props = client.Get("org.freedesktop.GeoClue2.Client", "Location", dbus_interface="org.freedesktop.DBus.Properties")
        loc = bus.get_object("org.freedesktop.GeoClue2", props)
        latt = loc.Get("org.freedesktop.GeoClue2.Location", "Latitude", dbus_interface="org.freedesktop.DBus.Properties")
        lngg = loc.Get("org.freedesktop.GeoClue2.Location", "Longitude", dbus_interface="org.freedesktop.DBus.Properties")
        return float(latt), float(lngg)
    except Exception:  # noqa: BLE001
        return None
    finally:
        if client is not None:
            try:
                client.Stop()
            except Exception:  # noqa: BLE001
                pass


class LocationMonitor(Monitor):
    name = "location"

    @staticmethod
    def _run(cmd: list[str], timeout: float = 8) -> tuple[int, str]:
        return _run(cmd, timeout)

    def current_wifi_ssid(self) -> str | None:
        rc, out = self._run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
        if rc != 0:
            return None
        for line in out.splitlines():
            if line.lower().startswith("oui:"):
                return line.split(":", 1)[1]
        return None

    def is_offline(self) -> bool:
        rc, out = self._run(["ip", "route", "show", "default"])
        if rc != 0:
            return True
        return not out.strip()

    def _geoclue_position(self) -> tuple[float, float] | None:
        return current_position()

    def tick(self) -> MonitorResult:
        cfg = self.cfg
        method = cfg.get("method", "wifi")
        home_ssids = [s.strip().lower() for s in cfg.get("home_ssids", []) if s.strip()]

        offline = self.is_offline()

        if method == "gps":
            pos = self._geoclue_position()
            if pos is not None:
                lat, lon = pos
                home_lat = float(cfg.get("home_lat", 0.0))
                home_lon = float(cfg.get("home_lon", 0.0))
                radius = float(cfg.get("radius_km", 1.0))
                if home_lat == 0.0 and home_lon == 0.0:
                    return self._unconfigured(_("GPS : coordonnées domicile non définies"), offline)
                dist = _haversine_km(lat, lon, home_lat, home_lon)
                at_home = dist <= radius
                return self._finish(
                    at_home,
                    offline,
                    _("GPS : dist {distance:.2f} km (rayon {rayon} km)").format(distance=dist, rayon=radius),
                )

        # repli wifi (défaut)
        ssid = self.current_wifi_ssid()
        if ssid is None:
            return self._finish(False, offline, _("aucun réseau Wi-Fi actif"))
        if not home_ssids:
            # Aucun SSID 'maison' défini : on ne peut pas juger — statut neutre.
            # Sinon "hors domicile" forcerait un réarmement immédiat (piège).
            return self._unconfigured(
                _("Wi-Fi : {ssid} — SSID 'maison' non défini dans les réglages").format(ssid=ssid), offline
            )
        at_home = ssid.strip().lower() in home_ssids
        detail = _("Wi-Fi : {ssid}").format(ssid=ssid) + (_(" (maison)") if at_home else _(" (hors domicile)"))
        return self._finish(at_home, offline, detail)

    def _unconfigured(self, detail: str, offline: bool) -> MonitorResult:
        """Localisation non configurée : neutre (ni maison, ni hors domicile).
        at_home à None pour ne pas déclencher de réarmement forcé (le moteur
        ignore aussi le statut hors-ligne quand la localisation est non configurée)."""
        suffix = _(" ; hors ligne") if offline else ""
        return MonitorResult(
            self.name,
            "unconfigured",
            detail + suffix,
            at_home=None,
            offline=offline,
        )

    def _finish(self, at_home: bool, offline: bool, detail: str) -> MonitorResult:
        status = "home" if at_home else "away"
        if at_home:
            detail += _(" ; connecté") if not offline else _(" ; hors ligne")
        else:
            detail += _(" ; hors ligne") if offline else _(" ; en ligne (hors domicile)")
        return MonitorResult(
            self.name,
            status,
            detail,
            at_home=at_home,
            offline=offline,
        )
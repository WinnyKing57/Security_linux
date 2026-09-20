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
import threading
import time

from security_linux.i18n import _
from security_linux.monitors.base import Monitor, MonitorResult

_GC_NAME = "org.freedesktop.GeoClue2"
_GC_MANAGER = "/org/freedesktop/GeoClue2/Manager"
_GC_AGENT_PATH = "/org/freedesktop/GeoClue2/Agent"

# Précision (m) au-delà de laquelle une position GeoClue est jugée trop
# grossière pour servir de "domicile" (fausses alarmes garanties sinon).
_ACCURACY_WARN_M = 2000.0


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


# ---------------------------------------------------------------------------
# Agent GeoClue auto-autorisant (précision maximale demandable).
#
# GeoClue ne fournit une position à précision utile (rrésolution fine) que si
# un *agent* d'autorisation est enregistré pour la session. Sans agent, il ne
# dévoile qu'une position "ville" (~26 km) à tout client. Cet agent est en
#registré dans un fil dédié (boucle GLib isolée) et autorise l'application.
# Il est pleinement efficace une fois l'identifiant ajouté à la liste blanche
# des agents (/etc/geoclue/geoclue.conf) via scripts/geoclue_whitelist.sh ;
# sans whitelist GeoClue le refuse et l'application se rabat sur la précision
# "ville" (le message GUI prévient alors l'utilisateur).
# ---------------------------------------------------------------------------
AGENT_ID = "security-linux"
_agent_thread_started = False
_agent_lock = threading.Lock()


def ensure_geoclue_agent() -> None:
    """Démarre (une seule fois) l'agent d'autorisation GeoClue de l'app."""
    global _agent_thread_started
    with _agent_lock:
        if _agent_thread_started:
            return
        _agent_thread_started = True
        threading.Thread(target=_run_geoclue_agent, daemon=True, name="geoclue-agent").start()


def _run_geoclue_agent() -> None:
    try:
        from gi.repository import GLib  # noqa: PLC0415
        import dbus  # noqa: PLC0415
        import dbus.exceptions  # noqa: PLC0415
        import dbus.service  # noqa: PLC0415
        from dbus.mainloop.glib import DBusGMainLoop  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return

    class AuthorizingAgent(dbus.service.Object):
        @dbus.service.method(
            dbus_interface="org.freedesktop.GeoClue2.Agent",
            in_signature="su",
            out_signature="bu",
        )
        def AuthorizeApp(self, desktop_id, req_accuracy_level):  # noqa: ARG002, N805
            # Autorisation immédiate, précision exacte (niveau 8)
            return (True, 8)

        @dbus.service.method(
            dbus_interface="org.freedesktop.DBus.Properties",
            in_signature="ss",
            out_signature="v",
        )
        def Get(self, interface_name, prop):  # noqa: N805
            if interface_name == "org.freedesktop.GeoClue2.Agent" and prop == "MaxAccuracyLevel":
                return dbus.UInt32(8)
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.UnknownProperty: no such property"
            )

    # Boucle isolée dans ce fil (contexte GLib dédié) : la GUI/démon ne sont
    # pas perturbés, et les connexions créées ici y sont rattachées.
    try:
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            DBusGMainLoop(set_as_default=True)
        except Exception:  # noqa: BLE001
            pass
        loop = GLib.MainLoop(ctx)
        bus = dbus.SystemBus()
        AuthorizingAgent(bus)
        try:
            manager = dbus.Interface(
                bus.get_object(_GC_NAME, _GC_MANAGER),
                "org.freedesktop.GeoClue2.Manager",
            )
            manager.AddAgent(AGENT_ID)
        except dbus.DBusException:
            # non whitelisté (refus de GeoClue) : toléré, on retombe sur la
            # précision "ville" ; retenter est inutile à chaque appel.
            return
        loop.run()
    except Exception:  # noqa: BLE001
        return


def current_position_with_accuracy() -> tuple[float, float, float | None] | None:
    """Position GPS actuelle via GeoClue2 : ``(latitude, longitude, précision_m)``.

    ``précision_m`` vaut ``None`` si GeoClue ne l'expose pas (ou ``0`` s'il
    connaît la position exacte). Retourne ``None`` si aucune position n'est
    disponible (service absent / refusé / aucune source).
    """
    ensure_geoclue_agent()
    try:
        import dbus  # noqa: PLC0415
    except ImportError:
        return None
    bus = dbus.SystemBus()
    client_path = None
    try:
        manager = dbus.Interface(
            bus.get_object(_GC_NAME, _GC_MANAGER),
            "org.freedesktop.GeoClue2.Manager",
        )
        client_path = manager.GetClient()
        client = bus.get_object(_GC_NAME, client_path)
        props = dbus.Interface(client, "org.freedesktop.DBus.Properties")
        props.Set("org.freedesktop.GeoClue2.Client", "DesktopId", AGENT_ID)
        props.Set("org.freedesktop.GeoClue2.Client", "RequestedAccuracyLevel", dbus.UInt32(8))
        client.Start(dbus_interface="org.freedesktop.GeoClue2.Client")

        deadline = time.monotonic() + 12.0
        loc_path = None
        while time.monotonic() < deadline:
            time.sleep(0.35)
            try:
                loc_path = props.Get("org.freedesktop.GeoClue2.Client", "Location")
            except dbus.DBusException:
                loc_path = None
            if loc_path is not None and str(loc_path) != "/":
                break
        if loc_path is None or str(loc_path) == "/":
            return None

        loc = bus.get_object(_GC_NAME, str(loc_path))
        lprops = dbus.Interface(loc, "org.freedesktop.DBus.Properties")
        lat = float(lprops.Get("org.freedesktop.GeoClue2.Location", "Latitude"))
        lon = float(lprops.Get("org.freedesktop.GeoClue2.Location", "Longitude"))
        accuracy: float | None = None
        try:
            accuracy = float(lprops.Get("org.freedesktop.GeoClue2.Location", "Accuracy"))
        except (dbus.DBusException, ValueError, TypeError):
            accuracy = None
        return (lat, lon, accuracy)
    except Exception:  # noqa: BLE001
        return None
    finally:
        if client_path:
            try:
                bus.get_object(_GC_NAME, client_path).Stop(dbus_interface="org.freedesktop.GeoClue2.Client")
            except Exception:  # noqa: BLE001
                pass


def current_position() -> tuple[float, float] | None:
    """Position GPS actuelle via GeoClue2 : ``(latitude, longitude)``.

    Utilisée par le moniteur (méthode gps) et par la GUI pour poser le
    domicile sur la position courante. Retourne ``None`` si GeoClue2 ou le
    module dbus est indisponible / refusé (déclenche le repli Wi-Fi).
    """
    pos = current_position_with_accuracy()
    if pos is None:
        return None
    return (pos[0], pos[1])


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

    def _geoclue_position(self) -> tuple[float, float, float | None] | None:
        return current_position_with_accuracy()

    def tick(self) -> MonitorResult:
        cfg = self.cfg
        method = cfg.get("method", "wifi")
        home_ssids = [s.strip().lower() for s in cfg.get("home_ssids", []) if s.strip()]

        offline = self.is_offline()

        if method == "gps":
            pos = self._geoclue_position()
            if pos is not None:
                lat, lon, accuracy = pos
                home_lat = float(cfg.get("home_lat", 0.0))
                home_lon = float(cfg.get("home_lon", 0.0))
                radius = float(cfg.get("radius_km", 1.0))
                if home_lat == 0.0 and home_lon == 0.0:
                    return self._unconfigured(_("GPS : coordonnées domicile non définies"), offline)
                radius_m = radius * 1000.0
                if accuracy is not None and accuracy > max(radius_m * 2.0, _ACCURACY_WARN_M):
                    # Position trop grossière pour un verdict maison/pas maison :
                    # une alarme erronée serait pire que pas de verdict (neutre).
                    return self._unconfigured(
                        _("GPS : précision insuffisante ({acc:.0f} m) pour le rayon de {rayon:.1f} km "
                          "— activez la pleine précision (root) ou saisissez la position manuellement").format(
                            acc=accuracy, rayon=radius
                        ),
                        offline,
                    )
                dist = _haversine_km(lat, lon, home_lat, home_lon)
                at_home = dist <= radius
                if accuracy is not None:
                    detail = _("GPS : dist {distance:.2f} km (rayon {rayon} km, précision {acc:.0f} m)").format(
                        distance=dist, rayon=radius, acc=accuracy
                    )
                else:
                    detail = _("GPS : dist {distance:.2f} km (rayon {rayon} km)").format(distance=dist, rayon=radius)
                return self._finish(at_home, offline, detail)

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
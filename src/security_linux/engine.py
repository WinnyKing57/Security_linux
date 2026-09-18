"""Moteur de décision : combine les moniteurs et déclenche le verrouillage.

Règle par défaut (AND) : on verrouille seulement si un visage n'est pas vu
ET QUE l'appareil Bluetooth est injoignable, pendant au moins
`min_absent_seconds`, puis un court délai de grâce (`lock_grace_seconds`)
pour laisser une chance de corriger un faux positif.

Armement effectif : verrouillage automatique actif si
  - armé manuellement (interrupteur), OU
  - hors domicile              (le système se réarme tout seul), OU
  - hors ligne ET secure_when_offline.
"""
from __future__ import annotations

import time

import security_linux.config as config
import security_linux.events as events
from security_linux import idle
from security_linux.i18n import _
from security_linux.monitors import MonitorResult


def effective_armed(manual_armed: bool, loc: MonitorResult | None, secure_when_offline: bool) -> dict:
    # Localisation non configurée (statut "unconfigured") : neutre — on ne
    # force jamais de réarmement sur une donnée inconnue (ex. SSID 'maison' vide).
    loc_conf = loc is not None and getattr(loc, "status", "") != "unconfigured"
    away = loc_conf and loc.at_home is not None and not loc.at_home
    offline = loc_conf and loc.offline is True
    force_away = away or (offline and secure_when_offline)
    return {
        "manual": bool(manual_armed),
        "away": away,
        "offline_secure": offline and secure_when_offline,
        "armed": bool(manual_armed) or force_away,
        "forced": force_away,
    }


class Engine:
    def __init__(self, cfg: dict, lock_fn, is_locked_fn) -> None:
        self.cfg = cfg
        self._lock_fn = lock_fn
        self._is_locked_fn = is_locked_fn
        self._pending_since: float | None = None
        self._last_lock_ts: float | None = None
        # États précédents des capteurs (détection de disparition soudaine).
        self._prev_status: dict[str, str] = {}

    def _detect_tamper(self, states: dict[str, MonitorResult], armed: bool) -> bool:
        """Un capteur disponible puis 'unavailable' pendant l'armement est
        traité comme un signal d'évasion possible, pas comme une simple
        exclusion du calcul de décision."""
        tamper = False
        for key in ("camera", "bluetooth"):
            mon = states.get(key)
            if mon is None:
                continue
            status = getattr(mon, "status", "")
            prev = self._prev_status.get(key)
            if (
                armed
                and prev not in (None, "unavailable", "disabled", "unconfigured")
                and status == "unavailable"
            ):
                tamper = True
                events.log_event(
                    "tamper",
                    _("capteur {key} indisponible pendant l'armement (évasion possible ?)").format(key=key),
                )
            self._prev_status[key] = status
        return tamper

    def _conditions_met(self, states: dict[str, MonitorResult]) -> dict:
        gen = self.cfg["general"]
        min_absent = float(gen.get("min_absent_seconds", 20))
        mode = gen.get("decision_mode", "AND").upper()
        reasons: list[str] = []

        cand = {}
        for key, mon in states.items():
            if key not in ("camera", "bluetooth"):
                continue
            if mon.status == "disabled":
                continue
            if mon.status == "unavailable":
                continue
            elapsed = self._absent_elapsed(mon)
            satisfied = mon.status == "absent" and elapsed >= min_absent
            cand[key] = satisfied
            if satisfied:
                reasons.append(_("{key} : absent depuis {secondes}s (>= {seuil}s)").format(key=key, secondes=int(elapsed), seuil=int(min_absent)))
            else:
                reasons.append(_("{key} : OK / pas assez absent").format(key=key))

        if mode == "OR":
            met = any(cand.values()) if cand else False
        else:
            met = all(cand.values()) if cand else False
        return {"met": met, "reasons": reasons, "counts": cand}

    @staticmethod
    def _absent_elapsed(mon: MonitorResult) -> float:
        """Durée d'absence en secondes, tous formats gérés.

        Le moniteur expose sa propre horloge interne (`absent_elapsed_seconds`
        sur l'objet Monitor), sinon la durée est relue dans `extra` (rempli par
        les moniteurs à chaque résultat absent), sinon calculée depuis
        `absent_since`. Ne jamais retomber silencieusement sur 0 : sinon la
        condition « absent depuis N s » ne peut jamais être satisfaite.
        """
        elapsed = getattr(mon, "absent_elapsed_seconds", None)
        if elapsed is not None:
            return float(elapsed)
        elapsed = float(mon.extra.get("absent_elapsed_seconds", 0.0))
        if elapsed > 0.0:
            return elapsed
        if mon.absent_since:
            try:
                from datetime import datetime, timezone

                parsed = datetime.fromisoformat(str(mon.absent_since).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
            except ValueError:
                pass
        return 0.0

    def tick(self, states: dict[str, MonitorResult], manual_armed: bool) -> dict:
        gen = self.cfg["general"]
        grace = float(gen.get("lock_grace_seconds", 10))
        repeat_min = float(gen.get("auto_lock_repeat_minutes", 3)) * 60.0
        idle_minutes = float(gen.get("idle_lock_minutes", 0))
        secure_offline = self.cfg["location"].get("secure_when_offline", True)

        loc = states.get("location")
        armed_state = effective_armed(manual_armed, loc, secure_offline)
        cond = self._conditions_met(states)
        silentium = config.is_silentium_active(self.cfg)
        now = time.time()

        decision = {"armed": armed_state, "conditions": cond, "action": None,
                    "silentium_active": silentium}
        decision["tamper"] = self._detect_tamper(states, armed_state["armed"])

        if not armed_state["armed"]:
            self._pending_since = None
            return decision

        # Mode Silentium : le verrouillage automatique est suspendu pendant
        # les heures nocturnes configurées (le verrouillage manuel reste possible).
        if silentium:
            self._pending_since = None
            decision["time_to_lock"] = 0.0
            return decision

        if cond["met"]:
            if self._pending_since is None:
                self._pending_since = now
            elapsed = now - self._pending_since
            if elapsed >= grace:
                if self._last_lock_ts is None or (now - self._last_lock_ts) > repeat_min:
                    ok = self._lock_fn()
                    self._last_lock_ts = now
                    self._pending_since = None
                    decision["action"] = "lock" if ok else "lock_failed"
                    events.log_event("lock", _("verrouillage automatique déclenché ({conditions})").format(conditions=", ".join(cond["reasons"])))
            decision["pending_since"] = self._pending_since
            decision["time_to_lock"] = max(0.0, grace - elapsed)
        else:
            self._pending_since = None
            decision["time_to_lock"] = 0.0

        # Filet de sécurité : verrouillage de repli sur inactivité prolongée,
        # indépendant des capteurs (utile si webcam + Bluetooth sont tous deux
        # indisponibles). 0 = désactivé.
        if idle_minutes > 0 and decision["action"] is None:
            idle_sec = idle.idle_seconds()
            if idle_sec is not None and idle_sec >= idle_minutes * 60:
                if self._last_lock_ts is None or (now - self._last_lock_ts) > repeat_min:
                    ok = self._lock_fn()
                    self._last_lock_ts = now
                    decision["action"] = "lock" if ok else "lock_failed"
                    events.log_event(
                        "lock",
                        _("verrouillage par inactivité ({inactivite}s >= {seuil} min)").format(
                            inactivite=int(idle_sec), seuil=idle_minutes
                        ),
                    )

        if self._is_locked_fn():
            # si l'utilisateur déverrouille alors que la menace persiste, re-verrouiller
            if self._last_lock_ts is not None and (now - self._last_lock_ts) > repeat_min:
                decision["action"] = "relock"
                self._lock_fn()
                self._last_lock_ts = now
        return decision
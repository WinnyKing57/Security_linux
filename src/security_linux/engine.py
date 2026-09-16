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

import security_linux.events as events
from security_linux.monitors import MonitorResult


def effective_armed(manual_armed: bool, loc: MonitorResult | None, secure_when_offline: bool) -> dict:
    away = loc is not None and loc.at_home is not None and not loc.at_home
    offline = loc is not None and loc.offline is True
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
            elapsed = 0.0
            if hasattr(mon, "absent_elapsed_seconds"):
                elapsed = mon.absent_elapsed_seconds
            else:
                elapsed = float(mon.extra.get("absent_elapsed_seconds", 0.0))
            satisfied = mon.status == "absent" and elapsed >= min_absent
            cand[key] = satisfied
            if satisfied:
                reasons.append(f"{key}: absent depuis {int(elapsed)}s (>= {int(min_absent)}s)")
            else:
                reasons.append(f"{key}: OK / pas assez absent")

        if mode == "OR":
            met = any(cand.values()) if cand else False
        else:
            met = all(cand.values()) if cand else False
        return {"met": met, "reasons": reasons, "counts": cand}

    def tick(self, states: dict[str, MonitorResult], manual_armed: bool) -> dict:
        gen = self.cfg["general"]
        grace = float(gen.get("lock_grace_seconds", 10))
        repeat_min = float(gen.get("auto_lock_repeat_minutes", 3)) * 60.0
        secure_offline = self.cfg["location"].get("secure_when_offline", True)

        loc = states.get("location")
        armed_state = effective_armed(manual_armed, loc, secure_offline)
        cond = self._conditions_met(states)
        now = time.time()

        decision = {"armed": armed_state, "conditions": cond, "action": None}

        if not armed_state["armed"]:
            self._pending_since = None
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
                    events.log_event("lock", f"verrouillage automatique déclenché ({', '.join(cond['reasons'])})")
            decision["pending_since"] = self._pending_since
            decision["time_to_lock"] = max(0.0, grace - elapsed)
        else:
            self._pending_since = None
            decision["time_to_lock"] = 0.0

        if self._is_locked_fn():
            # si l'utilisateur déverrouille alors que la menace persiste, re-verrouiller
            if self._last_lock_ts is not None and (now - self._last_lock_ts) > repeat_min:
                decision["action"] = "relock"
                self._lock_fn()
                self._last_lock_ts = now
        return decision
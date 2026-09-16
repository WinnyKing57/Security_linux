"""Base commune des moniteurs."""
from __future__ import annotations

from dataclasses import dataclass, field

_TS_FMT = "%Y-%m-%dT%H:%M:%S"


def _now_ts() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime(_TS_FMT)


@dataclass
class MonitorResult:
    name: str
    status: str = "disabled"  # present | absent | home | away | disabled | unavailable | unconfigured
    detail: str = ""
    absent_since: str | None = None
    extra: dict = field(default_factory=dict)
    at_home: bool | None = None
    offline: bool | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "absent_since": self.absent_since,
            "extra": self.extra,
            "at_home": self.at_home,
            "offline": self.offline,
            "ts": _now_ts(),
        }


class Monitor:
    name = "base"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self._absent_since: str | None = None
        self._last_absent_epoch: float | None = None

    def supported(self) -> bool:
        return True

    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", False))

    def poll_seconds(self) -> float:
        try:
            return max(1.0, float(self.cfg.get("poll_seconds", 10)))
        except (TypeError, ValueError):
            return 10.0

    def _start_absent_if(self, is_absent: bool, now_epoch: float) -> None:
        if is_absent:
            if self._last_absent_epoch is None:
                from datetime import datetime, timezone

                self._last_absent_epoch = now_epoch
                self._absent_since = datetime.fromtimestamp(now_epoch, tz=timezone.utc).strftime(_TS_FMT)
        else:
            self._last_absent_epoch = None
            self._absent_since = None

    @property
    def absent_since(self) -> str | None:
        return self._absent_since

    @property
    def absent_elapsed_seconds(self) -> float:
        import time

        if self._last_absent_epoch is None:
            return 0.0
        return time.time() - self._last_absent_epoch

    def tick(self) -> MonitorResult:
        raise NotImplementedError
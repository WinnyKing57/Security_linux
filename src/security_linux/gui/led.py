"""Voyant webcam : point flottant toujours au-dessus des fenêtres.

Allumé (rouge) pendant un court instant à chaque lecture d'image par
l'application, gris discret le reste du temps. Déplaçable à la souris ;
sa position est mémorisée dans la configuration.
"""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import security_linux.config as config  # noqa: E402
from security_linux.led import last_blink  # noqa: E402

_POLL_MS = 150


class CaptureLedOverlay(Gtk.Window):
    def __init__(self, app) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self.app = app
        led_cfg = app.cfg.get("led", {})
        self._size = max(12, min(48, int(led_cfg.get("size", 18))))
        self._lit_s = max(0.3, float(led_cfg.get("lit_seconds", 1.5)))
        self.set_default_size(self._size, self._size)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self._size, self._size)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("button-release-event", self._release)
        self.area.connect("motion-notify-event", self._motion)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.add(self.area)

        self._drag: tuple[int, int] | None = None
        self._lit = False
        self._src = GLib.timeout_add(_POLL_MS, self._tick)
        self.connect("destroy", self._cleanup)

    # ------------------------------------------------------- affichage
    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._load_position()
            self.show_all()
            self.area.queue_draw()
        else:
            self.hide()

    def reset_position(self) -> None:
        x, y = self._default_position()
        self.move(x, y)
        self._persist_position()
        self.area.queue_draw()

    def _default_position(self) -> tuple[int, int]:
        display = self.get_display()
        monitor = display.get_monitor_at_window(self.get_window()) if self.get_window() else None
        monitor = monitor or display.get_primary_monitor() or display.get_monitor(0)
        geo = monitor.get_geometry()
        return (geo.x + geo.width - self._size - 24, geo.y + 24)

    def _load_position(self) -> None:
        led = self.app.cfg.get("led", {})
        x, y = led.get("x"), led.get("y")
        if x is None or y is None:
            x, y = self._default_position()
        try:
            self.move(int(x), int(y))
        except TypeError:
            self.move(*self._default_position())

    def _persist_position(self) -> None:
        try:
            cfg = config.load_config()
            cfg.setdefault("led", {}).update({"x": int(self.get_position()[0]), "y": int(self.get_position()[1])})
            config.save_config(cfg)
        except (OSError, ValueError):
            pass

    def _tick(self) -> bool:
        if self.get_visible():
            lit = False
            ts = last_blink()
            if ts is not None:
                from datetime import datetime, timezone  # noqa: PLC0415

                lit = (datetime.now(timezone.utc) - ts).total_seconds() <= self._lit_s
            if lit != self._lit:
                self._lit = lit
                self.area.queue_draw()
        return True

    def _cleanup(self, *_args) -> None:
        if self._src is not None:
            GLib.source_remove(self._src)
            self._src = None

    # ------------------------------------------------------- dessin
    def _draw(self, _w, ctx) -> bool:
        r = (self._size - 4) / 2.0
        cx = cy = r + 2
        if self._lit:
            # halo rouge diffus (anneaux translucides) sans dépendance gradient
            for frac, alpha in ((1.45, 0.12), (1.25, 0.25), (1.08, 0.45)):
                ctx.arc(cx, cy, r * frac, 0, 2 * math.pi)
                ctx.set_source_rgba(0.95, 0.1, 0.1, alpha)
                ctx.fill()
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
            ctx.set_source_rgb(0.95, 0.05, 0.05)
            ctx.fill()
        else:
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
            ctx.set_source_rgb(0.42, 0.42, 0.42)
            ctx.fill()
        ctx.arc(cx, cy, r, 0, 2 * math.pi)
        ctx.set_line_width(1.5)
        if self._lit:
            ctx.set_source_rgb(1.0, 0.6, 0.6)
        else:
            ctx.set_source_rgb(0.55, 0.55, 0.55)
        ctx.stroke()
        return False

    # ------------------------------------------------------- déplacement
    def _press(self, _w, ev) -> bool:
        if ev.button == 1:
            _screen, px, py, _mods = self.get_display().get_pointer()
            self._drag = (px, py)
            gdk_win = self.area.get_window()
            if gdk_win is not None:
                cursor = Gdk.Cursor.new_for_display(self.get_display(), Gdk.CursorType.FLEUR)
                Gdk.pointer_grab(
                    gdk_win,
                    True,
                    Gdk.EventMask.BUTTON_PRESS_MASK
                    | Gdk.EventMask.BUTTON_RELEASE_MASK
                    | Gdk.EventMask.POINTER_MOTION_MASK,
                    None,
                    cursor,
                    ev.time,
                )
        return True

    def _motion(self, _w, _ev) -> bool:
        if self._drag is None:
            return True
        _screen, px, py, _mods = self.get_display().get_pointer()
        ox, oy = self._drag
        x, y = self.get_position()
        self.move(x + (px - ox), y + (py - oy))
        self._drag = (px, py)
        return True

    def _release(self, _w, ev) -> bool:
        if ev.button != 1 or self._drag is None:
            return True
        self._drag = None
        self._persist_position()
        gdk_win = self.area.get_window()
        if gdk_win is not None:
            Gdk.pointer_ungrab(ev.time)
            gdk_win.set_cursor(None)
        return True
"""Application graphique Security-Linux (GTK3, INTNtegrable KDE).

Fonctions :
  * interrupteur ARMÉ/DÉSARMÉ, désarmement protégé par code admin ;
  * statut en direct (webcam, bluetooth, localisation, Howdy) ;
  * réglages (seuils, méthodes, SSID maison, mode hors-ligne sécurisé) ;
  * boutons pratiques (verrouiller, enregistrer visage, journal, captures) ;
  * icône dans la barre système.
  * instance unique garantie par fichier lock
"""
from __future__ import annotations

import atexit
import fcntl
import os
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

import security_linux.config as config  # noqa: E402
import security_linux.events as events  # noqa: E402
import security_linux.howdy_ctrl as howdy_ctrl  # noqa: E402
import security_linux.runtime as runtime  # noqa: E402
from security_linux.i18n import _  # noqa: E402
from security_linux.version import APP_NAME, __version__  # noqa: E402

_TICK_MS = 1000
_LOCK_FILE = None


class AdminCodeDialog(Gtk.MessageDialog):
    def __init__(self, parent, title: str, message: str):
        super().__init__(
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_format=message,
        )
        self.set_title(title)
        box = self.get_message_area()
        self.entry = Gtk.Entry(visibility=False)
        self.entry.set_activates_default(True)
        box.pack_start(self.entry, True, True, 0)
        self.entry.show()
        self.set_default_response(Gtk.ResponseType.OK)

    def get_code(self) -> str:
        return self.entry.get_text() or ""


class SecurityLinuxApp:
    def __init__(self) -> None:
        self.cfg = config.load_config()
        self.last_state: dict = {}
        self.builder = None
        self.window = None
        self.armed_switch: Gtk.Switch | None = None
        self.labels: dict[str, Gtk.Label] = {}
        self.tray: Gtk.StatusIcon | None = None
        self.stale: bool = True
        self.daemon_row: Gtk.Box | None = None
        self._led_overlay = None

    # -------------------------------------------------------- construction
    def build_ui(self) -> Gtk.Window:
        win = Gtk.Window(title=_("{app} — Sécurité et verrouillage automatique").format(app=APP_NAME))
        win.set_border_width(12)
        win.set_default_size(520, 460)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # --- bannière mode développement
        self.labels["mode_banner"] = Gtk.Label(label="…", xalign=0)
        self.labels["mode_banner"].set_visible(False)
        vbox.pack_start(self.labels["mode_banner"], False, True, 0)

        # --- bannière démon à l'arrêt (capteurs figés)
        self.daemon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.labels["daemon_banner"] = Gtk.Label(label="", xalign=0)
        self.labels["daemon_banner"].set_line_wrap(True)
        b_daemon = Gtk.Button(label=_("Démarrer le démon"))
        b_daemon.connect("clicked", self._start_daemon)
        self.daemon_row.pack_start(self.labels["daemon_banner"], True, True, 0)
        self.daemon_row.pack_end(b_daemon, False, True, 0)
        self.daemon_row.set_visible(False)
        vbox.pack_start(self.daemon_row, False, True, 0)

        # --- bannière + interrupteur
        armed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.armed_switch = Gtk.Switch()
        self.armed_switch.set_active(bool(self.cfg["general"]["armed"]))
        self.armed_switch.connect("state-set", self.on_armed_toggle)
        lbl = Gtk.Label(label=_("Protection automatique"))
        lbl.set_markup("<b>{}</b>".format(_("Protection automatique")))
        armed_row.pack_start(lbl, False, True, 0)
        armed_row.pack_end(self.armed_switch, False, True, 0)

        self.labels["arm_eff"] = Gtk.Label(label="…")
        self.labels["arm_status"] = Gtk.Label(label="")

        # --- carte statut
        status_frame = Gtk.Frame(label=_("État du système"))
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        rows = [
            (_("Webcam (visage)"), "webcam"),
            (_("Bluetooth (appareil)"), "bluetooth"),
            (_("Localisation"), "location"),
            (_("Howdy (visage PAM)"), "howdy"),
            (_("Silentium"), "silentium"),
            (_("Dernier événement"), "last_event"),
        ]
        for i, (title, key) in enumerate(rows):
            t = Gtk.Label(label=title, xalign=0)
            v = Gtk.Label(label="…", xalign=0)
            v.set_line_wrap(True)
            grid.attach(t, 0, i, 1, 1)
            grid.attach(v, 1, i, 1, 1)
            self.labels[key] = v
        status_frame.add(grid)

        # --- boutons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        b_lock = Gtk.Button(label=_("Verrouiller maintenant"))
        b_lock.connect("clicked", lambda *_x: self.lock_now())
        b_settings = Gtk.Button(label=_("Réglages…"))
        b_settings.connect("clicked", lambda *_x: self.open_settings())
        b_howdy = Gtk.Button(label=_("Enregistrer mon visage"))
        b_howdy.connect("clicked", lambda *_x: self.enroll_face())
        b_events = Gtk.Button(label=_("Journal"))
        b_events.connect("clicked", lambda *_x: self.show_events())
        b_captures = Gtk.Button(label=_("Captures"))
        b_captures.connect("clicked", lambda *_x: self.show_captures())
        for b in (b_lock, b_settings, b_howdy, b_events, b_captures):
            btn_row.pack_start(b, False, True, 0)

        footer = Gtk.Label(label=_("{app} v{version} — analyse 100 % locale").format(app=APP_NAME, version=__version__), xalign=0)

        vbox.pack_start(armed_row, False, True, 0)
        vbox.pack_start(self.labels["arm_status"], False, True, 0)
        vbox.pack_start(status_frame, True, True, 0)
        vbox.pack_start(btn_row, False, True, 0)
        vbox.pack_start(footer, False, True, 0)
        win.add(vbox)
        win.show_all()
        win.connect("delete-event", self.on_delete_event)
        return win

    # --------------------------------------------------------- cycle de vie
    def run(self) -> None:
        if not _acquire_instance_lock():
            _msg_existing_instance()
            return
        self.window = self.build_ui()
        self.refresh_led()
        self._poll()
        self._maybe_setup_admin_code()
        self._build_tray()
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        try:
            Gtk.main()
        finally:
            _release_instance_lock()

    def _handle_signal(self, *_args):
        GLib.idle_add(self.quit_app)

    def on_delete_event(self, *_args):
        # se ferme en barre système et non quit
        self.tray.set_visible(True)
        self.window.hide()
        return True

    def quit_app(self, *_args):
        if self._led_overlay is not None:
            self._led_overlay.destroy()
            self._led_overlay = None
        if self.tray:
            self.tray.set_visible(False)
        Gtk.main_quit()

    def _build_tray(self) -> None:
        self.tray = Gtk.StatusIcon()
        self.tray.set_title(APP_NAME)
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            self._icon_path(), width=22, height=22, preserve_aspect_ratio=True
        )
        self.tray.set_from_pixbuf(pix)
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label=_("Ouvrir"))
        item.connect("activate", lambda *_x: self.window.present())
        menu.append(item)
        item = Gtk.MenuItem(label=_("Verrouiller"))
        item.connect("activate", lambda *_x: self.lock_now())
        menu.append(item)
        item = Gtk.MenuItem(label=_("Quitter"))
        item.connect("activate", lambda *_x: self.quit_app())
        menu.append(item)
        menu.show_all()
        self.tray.connect("popup-menu", lambda *_a, m=menu: m.popup(None, None, None, None, 0, Gtk.get_current_event_time()))
        self.tray.connect("activate", lambda *_a: self.window.present())
        self.tray.set_visible(False)

    @staticmethod
    def _icon_path() -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "..", "..", "..", "assets", "shield.png")

    # --------------------------------------------------- voyant webcam
    def refresh_led(self) -> None:
        """Affiche ou masque le voyant webcam selon la configuration."""
        self.cfg = config.load_config()
        enabled = bool(self.cfg.get("led", {}).get("enabled", False))
        if enabled and self._led_overlay is None:
            from security_linux.gui.led import CaptureLedOverlay  # noqa: PLC0415

            self._led_overlay = CaptureLedOverlay(self)
        if self._led_overlay is not None:
            self._led_overlay.set_enabled(enabled)

    def reset_led_position(self, *args) -> None:
        """Remet le voyant à sa position d'origine (haut à droite)."""
        cfg = config.load_config()
        cfg.setdefault("led", {}).update({"x": None, "y": None})
        config.save_config(cfg)
        if self._led_overlay is not None:
            self._led_overlay.reset_position()

    # -------------------------------------------------------------- état
    def _poll(self) -> bool:
        try:
            self.cfg = config.load_config()
        except Exception:  # noqa: BLE001
            pass
        state = events.read_state()
        self.stale = True
        if state:
            self.last_state = state
            self._render_state(state)
            self.stale = self._state_is_stale(state)
        else:
            self.last_state = {}
        self._render_daemon_banner()
        self._render_armed()
        GLib.timeout_add(_TICK_MS, self._poll)
        return False

    @staticmethod
    def _parse_ts(value) -> "object | None":
        from datetime import datetime, timezone

        if not value:
            return None
        try:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts

    def _state_is_stale(self, state: dict, max_age_s: float = 12.0) -> bool:
        from datetime import datetime, timezone

        ts = self._parse_ts(state.get("ts"))
        if ts is None:
            return True
        return (datetime.now(timezone.utc) - ts).total_seconds() > max_age_s

    def _render_daemon_banner(self) -> None:
        if self.daemon_row is None:
            return
        if runtime.is_debug():
            self.daemon_row.set_visible(False)
            return
        if self.stale:
            self.labels["daemon_banner"].set_markup(
                _('<span color="red" weight="bold">DÉMON À L\'ARRÊT — état figé. '
                  "Les capteurs et le relais ne sont pas actualisés.</span>")
            )
            self.daemon_row.set_visible(True)
        else:
            self.daemon_row.set_visible(False)

    def _daemon_running(self) -> bool:
        try:
            out = subprocess.run(
                ["pgrep", "-f", "security-linuxd|security_linux\\.daemon"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return out.returncode == 0 and bool(out.stdout.strip())
        except Exception:  # noqa: BLE001
            return False

    def _start_daemon(self, *_args) -> None:
        if self._daemon_running() or not self.stale:
            self._render_daemon_banner()
            return
        if sys.executable:
            cmd = [sys.executable, "-m", "security_linux.daemon"]
        else:
            cmd = ["security-linuxd"]
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._msg(_("Démon de sécurité démarré en arrière-plan."), error=False)
        except Exception as exc:  # noqa: BLE001
            self._msg(_("Impossible de démarrer le démon : {erreur}").format(erreur=exc), error=True)

    def _render_state(self, state: dict) -> None:
        def setl(key, text, color=None):
            lbl = self.labels.get(key)
            if lbl is None:
                return
            if color:
                lbl.set_markup(f"<span color=\"{color}\">{text}</span>")
            else:
                lbl.set_text(text)

        mon = state.get("monitors", {})
        c = mon.get("camera", {})
        b = mon.get("bluetooth", {})
        l = mon.get("location", {})
        h = state.get("howdy", {})

        def col(status):
            return {"present": "green", "home": "green", "ok": "green", "absent": "orange",
                    "away": "orange", "unavailable": "red", "unconfigured": "#b0a000", "unknown": "#b0a000"}.get(status, "black")

        setl("webcam", c.get("detail", c.get("status", "…")), col(c.get("status", "")))
        setl("bluetooth", b.get("detail", b.get("status", "…")), col(b.get("status", "")))
        setl("location", l.get("detail", l.get("status", "…")), col(l.get("status", "")))

        installed = _("installé") if h.get("installed") else _("non installé")
        mods = h.get("models_status", "unknown")
        mods_fr = {"ok": _("visages enregistrés"), "none": _("aucun visage"), "unknown": _("droits insuffisants"), "not_installed": "—"}.get(mods, mods)
        setl("howdy", "{install} · {modeles}".format(install=installed, modeles=mods_fr), col(h.get("models_status", "unknown")))

        last = state.get("last_event")
        if last:
            setl("last_event", f"[{last.get('ts', '')}] {last.get('message', '')}")
        else:
            setl("last_event", "—")

        silentium = state.get("silentium_active", False)
        setl("silentium", _("actif — auto-lock suspendu") if silentium else _("inactif"), "orange" if silentium else "black")

        banner = self.labels.get("mode_banner")
        is_debug = state.get("mode") == "debug" or runtime.is_debug()
        if banner:
            if is_debug:
                banner.set_markup(_('<span color="orange" weight="bold">MODE DÉVELOPPEMENT (DEBUG) — config isolée, verrouillage SIMULÉ</span>'))
                banner.set_visible(True)
            else:
                banner.set_visible(False)

    def _render_armed(self) -> None:
        armed = self.last_state.get("armed_state", {})
        # L'interrupteur reflète l'INTENTION persistée (config), pas l'état
        # publié par le démon : si le démon est à l'arrêt, state.json est figé
        # et ramènerait l'interrupteur en position OFF à chaque tick.
        manual = bool(self.cfg["general"]["armed"])
        if not self.stale:
            eff = bool(armed.get("armed", manual))
            forced = bool(armed.get("forced", False))
        else:
            eff = manual
            forced = False
        if self.armed_switch.get_active() != manual:
            self.armed_switch.handler_block_by_func(self.on_armed_toggle)
            self.armed_switch.set_active(manual)
            self.armed_switch.handler_unblock_by_func(self.on_armed_toggle)
        status_lbl = self.labels.get("arm_status")
        if eff or manual:
            why = _(" (réarmé : hors domicile / hors-ligne)") if (forced and not manual) else ""
            fixed = _(" (état figé)") if self.stale else ""
            txt = _("Protection ACTIVE") + fixed + why
            color = "green"
        else:
            txt = _("Protection désactivée (code admin)")
            color = "grey"
        status_lbl.set_markup(f"<span color=\"{color}\" weight=\"bold\">{txt}</span>")

    # --------------------------------------------------------- actions
    def on_armed_toggle(self, switch, state):
        new_state = bool(state)
        if new_state is False:
            # Recharge depuis le disque AVANT toute vérification : on ne fige pas
            # un état périmé (réglages, compteur anti-bruteforce) au moment où on
            # va y écrire les tentatives et l'armement.
            self.cfg = config.load_config()
            if not self._confirm_admin(_("Désactiver la protection"), _("Saisissez le code administrateur pour désactiver le système.")):
                # True : empêche le handler par défaut de GTK d'appliquer l'état
                # rejeté, sinon l'interrupteur reste visuellement DÉSARMÉ alors
                # que la protection n'a jamais été désactivée.
                switch.handler_block_by_func(self.on_armed_toggle)
                switch.set_active(True)
                switch.handler_unblock_by_func(self.on_armed_toggle)
                return True
            # `_confirm_admin` a pu réécrire la config sur disque (compteur de
            # tentatives) : on recharge pour ne pas en récrire une version périmée.
            self.cfg = config.load_config()
        self.cfg["general"]["armed"] = new_state
        config.save_config(self.cfg)
        events.write_command({"type": "arm", "value": new_state})
        return False

    def _confirm_admin(self, title, message) -> bool:
        if not config.has_admin_code(self.cfg):
            # Aucun code enregistré : on en crée un. S'il est posé, l'action est
            # considérée autorisée — pas de troisième boîte de dialogue superflue.
            if self._setup_admin_code_dialog(_("Créer un code administrateur"), _("Aucun code n'est défini. Créez-en un pour autoriser cette action.")):
                return True
            return False
        dlg = AdminCodeDialog(self.window, title, message)
        dlg.show_all()
        resp = dlg.run()
        code = dlg.get_code()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return False
        if config.verify_admin_code(self.cfg, code):
            return True
        self._msg(
            _("Code administrateur incorrect (ou trop de tentatives — patientez 10 min)."),
            error=True,
        )
        return False

    def _maybe_setup_admin_code(self):
        if not config.has_admin_code(self.cfg):
            self._setup_admin_code_dialog(_("Premier lancement"), _("Créez votre code administrateur (nécessaire pour désactiver la protection)."))

    def _setup_admin_code_dialog(self, title, message) -> bool:
        dlg = AdminCodeDialog(self.window, title, message)
        dlg.show_all()
        resp = dlg.run()
        first = dlg.get_code()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not first:
            return False
        dlg2 = AdminCodeDialog(self.window, _("Confirmation"), _("Confirmez le code administrateur"))
        dlg2.show_all()
        resp2 = dlg2.run()
        second = dlg2.get_code()
        dlg2.destroy()
        if resp2 == Gtk.ResponseType.OK and first == second and config.valid_admin_code(first):
            config.set_admin_code(self.cfg, first)
            config.save_config(self.cfg)
            events.log_event("admin", _("code administrateur défini"))
            return True
        if resp2 == Gtk.ResponseType.OK and first == second:
            self._msg(
                _("Le code doit contenir au moins {n} caractères.").format(n=config.MIN_ADMIN_CODE_LENGTH),
                error=True,
            )
        else:
            events.log_event("admin", _("code administrateur non défini (annulé ou non confirmé)"))
        return False

    def lock_now(self):
        from security_linux.lock import lock_screen

        events.write_state(self.last_state)
        lock_screen()

    def enroll_face(self):
        if not howdy_ctrl.is_installed():
            self._msg(_("Howdy n'est pas installé sur ce système."), error=True)
            return
        cfg = config.load_config()
        device = cfg["camera"].get("device") or ""
        cmd = howdy_ctrl.enroll_command(device)
        if not cmd:
            self._msg(_("Aucun terminal disponible pour l'enregistrement du visage."), error=True)
            return
        subprocess.Popen(cmd, start_new_session=True)

    def _launch_howdy_install(self):
        script = howdy_ctrl.install_script_path()
        if not script:
            self._msg(_("Script d'installation Howdy introuvable dans le dépôt."), error=True)
            return
        dlg = Gtk.MessageDialog(
            transient_for=self.window,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_format=_("Installer Howdy (déverrouillage facial)"),
        )
        dlg.format_secondary_text(
            _("L'installation compile Howdy depuis le code source inclus dans le dépôt.\n\n"
              "- mot de passe root demandé (fenêtre pkexec / console)\n"
              "- durée : plusieurs minutes (compilation dlib)\n"
              "- nécessite une connexion Internet pour les dépendances et modèles\n\n"
              "Voulez-vous lancer l'installation ?")
        )
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        cmd = howdy_ctrl.install_command(script)
        if not cmd:
            self._msg(_("pkexec est nécessaire pour l'installation (paquet policykit-1)."), error=True)
            return
        try:
            subprocess.Popen(cmd, start_new_session=True)
            self._msg(_("Installation lancée dans une fenêtre root. Vérifiez son avancement, puis\n"
                        "réouvrez ces réglages pour activer Howdy après enregistrement du visage "
                        "(bouton « Enregistrer mon visage »)."))
        except OSError as exc:
            self._msg(_("Impossible de lancer l'installation : {erreur}").format(erreur=exc), error=True)
        self._msg(_("Terminal d'enregistrement ouvert (sudo howdy add). Suivez les instructions."))

    def _msg(self, text, error=False):
        dlg = Gtk.MessageDialog(
            transient_for=self.window,
            message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dlg.run()
        dlg.destroy()

    def show_events(self):
        text = "\n".join(
            f"[{r.get('ts', '')}] {r.get('kind', '')}: {r.get('message', '')}" for r in events.read_events(limit=50)
        ) or _("Aucun événement.")
        win = Gtk.Window(title=_("Journal des événements"))
        win.set_default_size(560, 320)
        sw = Gtk.ScrolledWindow()
        buf = Gtk.TextBuffer()
        buf.set_text(text)
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        sw.add(tv)
        win.add(sw)
        win.show_all()

    def show_captures(self):
        """Affiche la visionneuse des captures avec option de suppression."""
        import security_linux.config as cfg_module  # noqa: PLC0415

        captures_dir = cfg_module.data_dir() / "captures"

        # Récupérer la liste des fichiers de capture
        capture_files = []
        if captures_dir.exists():
            capture_files = sorted(
                [f for f in captures_dir.glob("*.jpg")],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )

        win = Gtk.Window(title=_("Captures d'écran ({nombre} images)").format(nombre=len(capture_files)))
        win.set_default_size(800, 600)
        win.set_border_width(10)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # Barre d'outils avec boutons
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        btn_refresh = Gtk.Button(label=_("Actualiser"))
        btn_refresh.connect("clicked", lambda *_x: self._refresh_captures(win, grid, status_label))
        toolbar.pack_start(btn_refresh, False, True, 0)

        btn_delete_all = Gtk.Button(label=_("Supprimer toutes les captures"))
        btn_delete_all.get_style_context().add_class("destructive-action")
        btn_delete_all.connect("clicked", lambda *_x: self._delete_all_captures(win, grid, status_label))
        toolbar.pack_end(btn_delete_all, False, True, 0)

        vbox.pack_start(toolbar, False, True, 0)

        # Grille pour les miniatures
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_homogeneous(True)
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)

        # Ajouter les miniatures
        for capture_file in capture_files[:100]:  # Limiter à 100 images
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(capture_file), width=200, height=150, preserve_aspect_ratio=True
                )
                img = Gtk.Image.new_from_pixbuf(pixbuf)

                # Bouton cliquable pour ouvrir l'image en grand
                btn = Gtk.Button()
                btn.add(img)
                btn.set_tooltip_text(f"{capture_file.name}\n{capture_file.stat().st_mtime}")
                btn.connect("clicked", lambda *_x, f=capture_file: self._open_capture_fullscreen(f))

                grid.add(btn)
            except Exception:
                continue

        sw.add(grid)
        vbox.pack_start(sw, True, True, 0)

        # Label de statut
        status_label = Gtk.Label(label=_("{nombre} capture(s) trouvée(s)").format(nombre=len(capture_files)))
        vbox.pack_start(status_label, False, True, 0)

        win.add(vbox)
        win.show_all()

    def _refresh_captures(self, win, grid, status_label):
        """Rafraîchit la grille des captures."""
        win.destroy()
        self.show_captures()

    def _delete_all_captures(self, win, grid, status_label):
        """Supprime toutes les captures après confirmation."""
        import security_linux.config as cfg_module  # noqa: PLC0415

        captures_dir = cfg_module.data_dir() / "captures"

        confirm_dlg = Gtk.MessageDialog(
            transient_for=win,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            message_format=_("Supprimer toutes les captures ?"),
        )
        confirm_dlg.format_secondary_text(
            _("Cette action va supprimer définitivement les {nombre} captures stockées.\n\n"
              "Cette action est irréversible.").format(nombre=grid.get_children().__len__())
        )
        resp = confirm_dlg.run()
        confirm_dlg.destroy()

        if resp != Gtk.ResponseType.YES:
            return

        deleted_count = 0
        if captures_dir.exists():
            for capture_file in captures_dir.glob("*.jpg"):
                try:
                    capture_file.unlink()
                    deleted_count += 1
                except Exception:
                    pass

        info_dlg = Gtk.MessageDialog(
            transient_for=win,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            message_format=_("{nombre} capture(s) supprimée(s).").format(nombre=deleted_count),
        )
        info_dlg.run()
        info_dlg.destroy()

        win.destroy()
        self.show_captures()

    def _open_capture_fullscreen(self, capture_file):
        """Ouvre une capture en taille réelle dans une nouvelle fenêtre."""
        win = Gtk.Window(title=capture_file.name)
        win.set_default_size(1024, 768)
        win.set_border_width(10)

        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(capture_file))
            img = Gtk.Image.new_from_pixbuf(pixbuf)

            sw = Gtk.ScrolledWindow()
            sw.add(img)
            win.add(sw)
            win.show_all()
        except Exception as exc:
            error_dlg = Gtk.MessageDialog(
                transient_for=win,
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format=_("Erreur lors de l'ouverture de l'image : {erreur}").format(erreur=exc),
            )
            error_dlg.run()
            error_dlg.destroy()
            win.destroy()

    def open_settings(self):
        SecuritySettingsDialog(self).run()


class SecuritySettingsDialog:
    def __init__(self, app: SecurityLinuxApp):
        self.app = app
        self.cfg = config.load_config()
        self.dlg = Gtk.Dialog(
            title=_("Réglages"),
            transient_for=app.window,
            flags=0,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK),
        )
        box = self.dlg.get_content_area()
        nb = Gtk.Notebook()
        nb.append_page(self._general_tab(), Gtk.Label(label=_("Général")))
        nb.append_page(self._camera_tab(), Gtk.Label(label=_("Webcam")))
        nb.append_page(self._bluetooth_tab(), Gtk.Label(label=_("Bluetooth")))
        nb.append_page(self._location_tab(), Gtk.Label(label=_("Localisation")))
        nb.append_page(self._howdy_tab(), Gtk.Label(label=_("Howdy")))
        nb.append_page(self._features_tab(), Gtk.Label(label=_("Fonctions avancées")))
        box.pack_start(nb, True, True, 0)
        box.show_all()

    # onglets
    def _general_tab(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.mode = Gtk.ComboBoxText()
        self.mode.append("AND", _("Visage ET Bluetooth (recommandé)"))
        self.mode.append("OR", _("Dès qu'un seul détecteur dit 'absent'"))
        self.mode.set_active_id(self.cfg["general"]["decision_mode"])
        self.mode.set_hexpand(True)
        self.grace = self._spin(self.cfg["general"]["lock_grace_seconds"], 0, 120, 1)
        self.min_absent = self._spin(self.cfg["general"].get("min_absent_seconds", 20), 5, 600, 5)
        self.autorepeat = self._spin(self.cfg["general"]["auto_lock_repeat_minutes"], 1, 60, 1)
        self.idle_lock = self._spin(self.cfg["general"].get("idle_lock_minutes", 0), 0, 240, 1)
        for lbl, wgt in (
            (_("Mode de décision"), self.mode),
            (_("Absence minimale (s)"), self.min_absent),
            (_("Délai de grâce avant verrouillage (s)"), self.grace),
            (_("Re-verrouillage si menace persistante (min)"), self.autorepeat),
            (_("Verrouillage de repli par inactivité (min, 0 = désactivé)"), self.idle_lock),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=lbl, xalign=0), True, True, 0)
            row.pack_start(wgt, False, True, 0)
            v.pack_start(row, False, True, 0)
        return v

    def _camera_tab(self):
        from security_linux.monitors.camera import CameraMonitor  # noqa: PLC0415

        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        c = self.cfg["camera"]
        self.cam_enabled = Gtk.Switch(active=bool(c["enabled"]))

        self.cam_devices = CameraMonitor.list_devices()
        self.cam_device = Gtk.ComboBoxText()
        for dev in self.cam_devices:
            self.cam_device.append(dev, _("{dev} — {libelle}").format(dev=dev, libelle=CameraMonitor.device_label(dev)))
        current = c["device"]
        if current not in self.cam_devices and current:
            self.cam_device.append(current, _("{dev} — configuré mais non détecté").format(dev=current))
        if not self.cam_devices:
            self.cam_device.append("", _("— aucun périphérique détecté —"))
            self.cam_device.set_active_id("")
        else:
            self.cam_device.set_active_id(current if current in self.cam_devices else self.cam_devices[0])

        self.cam_poll = self._spin(c["poll_seconds"], 2, 60, 1)
        self.cam_confirm = self._spin(c["absent_confirmations"], 1, 10, 1)
        self.cam_capture = Gtk.Switch(active=bool(c["capture_on_lock"]))
        for lbl, wgt in (
            (_("Activer la détection webcam"), self.cam_enabled),
            (_("Périphérique (aperçu en direct)"), self.cam_device),
            (_("Analyse toutes les (s)"), self.cam_poll),
            (_("Photos de confirmation"), self.cam_confirm),
            (_("Capturer une image locale lors du verrouillage"), self.cam_capture),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=lbl, xalign=0), True, True, 0)
            row.pack_end(wgt, False, True, 0)
            v.pack_start(row, False, True, 0)

        # --- aperçu en direct (test droits / périphérique) ---
        frame = Gtk.Frame(label=_("Aperçu en direct"))
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.cam_preview = Gtk.Image()
        self.cam_preview.set_size_request(420, 236)
        self.cam_status = Gtk.Label(label=_("Lancement de l'aperçu…"), xalign=0)
        self.cam_status.set_line_wrap(True)
        inner.pack_start(self.cam_preview, True, True, 0)
        inner.pack_start(self.cam_status, False, True, 0)
        frame.add(inner)
        v.pack_start(frame, True, True, 0)

        self._preview_cap = None
        self._preview_dev = None
        self._preview_src = GLib.timeout_add(150, self._preview_tick)

        v.pack_start(Gtk.Label(label=_("Aucune donnée n'est envoyée sur internet."), xalign=0), False, True, 0)
        return v

    def _preview_tick(self):
        dev = self.cam_device.get_active_id() or ""
        if not dev:
            self.cam_status.set_markup(
                _('<span color="red">Aucun périphérique vidéo détecté.</span>')
            )
            return True
        if self._preview_dev != dev:
            self._release_preview()
            self._preview_dev = dev
        cap = self._preview_cap
        if cap is None:
            try:
                import cv2  # noqa: PLC0415

                cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
                if not cap.isOpened():
                    cap.release()
                    self.cam_status.set_markup(
                        _("Impossible d'ouvrir {dev} — "
                          "droits insuffisants (groupe « video ») ou périphérique occupé.").format(dev=dev)
                    )
                    self.cam_preview.clear()
                    return True
            except Exception as exc:  # noqa: BLE001
                self.cam_status.set_markup(_("Erreur caméra : {erreur}").format(erreur=exc))
                return True
            self._preview_cap = cap
        try:
            from security_linux.monitors.camera import CameraMonitor  # noqa: PLC0415

            import cv2  # noqa: PLC0415

            ok, frame = cap.read()
            if not ok or frame is None:
                self.cam_status.set_markup(
                    _("Aucune image de {dev} "
                      "(périphérique occupé par un autre programme ?)").format(dev=dev)
                )
                return True
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            import security_linux.led as led  # noqa: PLC0415

            led.blink()
            h, w = rgb.shape[:2]
            pb = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes(rgb.tobytes()),
                GdkPixbuf.Colorspace.RGB,
                False,
                8,
                w,
                h,
                w * 3,
            )
            pb = pb.scale_simple(420, int(420 * h / w), GdkPixbuf.InterpType.BILINEAR)
            self.cam_preview.set_from_pixbuf(pb)
            self.cam_status.set_markup(
                _("OK — {libelle}").format(libelle=CameraMonitor.device_label(dev))
            )
        except Exception as exc:  # noqa: BLE001
            self.cam_status.set_markup(_("Erreur aperçu : {erreur}").format(erreur=exc))
        return True

    def _release_preview(self):
        cap = self._preview_cap
        self._preview_cap = None
        self._preview_dev = None
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass

    def _stop_preview(self):
        if self._preview_src is not None:
            GLib.source_remove(self._preview_src)
            self._preview_src = None
        self._release_preview()

    def _bluetooth_tab(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        b = self.cfg["bluetooth"]
        self.bt_enabled = Gtk.Switch(active=bool(b["enabled"]))
        from security_linux.monitors.bluetooth import BluetoothMonitor

        mon = BluetoothMonitor({"device_addr": ""})
        self.bt_dev = Gtk.ComboBoxText()
        self.bt_dev.append("", _("— Choisir un appareil —"))
        for dev in mon.list_known_devices():
            self.bt_dev.append(dev["address"].lower(), f"{dev['name']}  ({dev['address']})")
        current = b["device_addr"].lower()
        self.bt_dev.set_active_id(current if current in {d["address"].lower() for d in mon.list_known_devices()} else "")
        self.bt_poll = self._spin(b["poll_seconds"], 2, 120, 1)
        self.bt_rssi = self._spin(b["min_rssi"], -100, -30, 5)
        for lbl, wgt in (
            (_("Activer la détection Bluetooth"), self.bt_enabled),
            (_("Appareil à surveiller (téléphone/tablette/montre)"), self.bt_dev),
            (_("Vérification toutes les (s)"), self.bt_poll),
            (_("Seuil de signal RSSI (dBm)"), self.bt_rssi),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=lbl, xalign=0), True, True, 0)
            row.pack_end(wgt, False, True, 0)
            v.pack_start(row, False, True, 0)
        v.pack_start(Gtk.Label(label=_("Détection locale via BlueZ."), xalign=0), False, True, 0)
        return v

    def _location_tab(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        l = self.cfg["location"]
        self.loc_method = Gtk.ComboBoxText()
        self.loc_method.append("wifi", _("Wi-Fi SSID (100 % local, recommandé)"))
        self.loc_method.append("gps", _("GPS (GeoClue, optionnel) — repli Wi-Fi si indisponible"))
        self.loc_method.set_active_id(l["method"])
        self.loc_home = Gtk.Entry()
        self.loc_home.set_text(", ".join(l["home_ssids"]))
        self.offline_secure = Gtk.Switch(active=bool(l["secure_when_offline"]))
        self.loc_lat = self._entry(l["home_lat"])
        self.loc_lon = self._entry(l["home_lon"])
        self.loc_rad = self._spin(l["radius_km"], 0.1, 100.0, 0.1)
        for lbl, wgt in (
            (_("Méthode de localisation"), self.loc_method),
            (_("SSID 'maison' (séparés par des virgules)"), self.loc_home),
            (_("Sécurisé quand hors ligne (réarme auto)"), self.offline_secure),
            (_("Latitude du domicile (GPS)"), self.loc_lat),
            (_("Longitude du domicile (GPS)"), self.loc_lon),
            (_("Rayon de détection (km)"), self.loc_rad),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=lbl, xalign=0), True, True, 0)
            row.pack_end(wgt, False, True, 0)
            v.pack_start(row, False, True, 0)
        v.pack_start(Gtk.Label(label=_("Hors domicile ou hors ligne ⇒ le système se réarme automatiquement."), xalign=0), False, True, 0)
        return v

    def _howdy_tab(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        h = self.cfg["howdy"]
        self.howdy_enabled = Gtk.Switch(active=bool(h["enabled"]))
        installed = _("installé") if howdy_ctrl.is_installed() else _("non installé")
        status = howdy_ctrl.models_status()
        state_label = Gtk.Label(label=_("Howdy : {install} · visages : {statut}").format(install=installed, statut=status))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(Gtk.Label(label=_("Activer le déverrouillage facial"), xalign=0), True, True, 0)
        row.pack_end(self.howdy_enabled, False, True, 0)
        v.pack_start(row, False, True, 0)
        v.pack_start(state_label, False, True, 0)

        if not howdy_ctrl.is_installed():
            btn_install = Gtk.Button(label=_("Installer Howdy (via terminal root)"))
            btn_install.connect("clicked", lambda *_x: self.app._launch_howdy_install())
            v.pack_start(btn_install, False, True, 0)
        else:
            btn_reinstall = Gtk.Button(label=_("Recompiler Howdy (optionnel)"))
            btn_reinstall.set_tooltip_text(_("Reconstruire et réinstaller Howdy depuis le code source (root)"))
            btn_reinstall.connect("clicked", lambda *_x: self.app._launch_howdy_install())
            v.pack_start(btn_reinstall, False, True, 0)

        v.pack_start(
            Gtk.Label(label=_("Un visage doit être enregistré avant d'activer Howdy.\n"
                              "Le déverrouillage v1 reste le mot de passe de session."),
                      xalign=0),
            False, True, 0,
        )
        return v

    def _features_tab(self):
        """Onglet des fonctions avancées : Silentium et Mode braquage."""
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # --- Mode Silentium ---
        silentium_frame = Gtk.Frame(label=_("Mode Silentium (heures nocturnes)"))
        silentium_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        silentium_box.set_margin_top(8)
        silentium_box.set_margin_bottom(8)
        silentium_box.set_margin_start(8)
        silentium_box.set_margin_end(8)

        s = self.cfg.get("silentium", {})
        self.silentium_enabled = Gtk.Switch(active=bool(s.get("enabled", False)))
        self.silentium_start = self._spin(s.get("start_hour", 23), 0, 23, 1)
        self.silentium_end = self._spin(s.get("end_hour", 7), 0, 23, 1)

        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row1.pack_start(Gtk.Label(label=_("Activer le mode Silentium"), xalign=0), True, True, 0)
        row1.pack_end(self.silentium_enabled, False, True, 0)
        silentium_box.pack_start(row1, False, True, 0)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row2.pack_start(Gtk.Label(label=_("Heure de début (23 = 23h)"), xalign=0), True, True, 0)
        row2.pack_end(self.silentium_start, False, True, 0)
        silentium_box.pack_start(row2, False, True, 0)

        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row3.pack_start(Gtk.Label(label=_("Heure de fin (7 = 7h)"), xalign=0), True, True, 0)
        row3.pack_end(self.silentium_end, False, True, 0)
        silentium_box.pack_start(row3, False, True, 0)

        silentium_box.pack_start(
            Gtk.Label(label=_("Pendant ces heures, le verrouillage automatique est désactivé.\n"
                             "Utile pour les nuits où vous travaillez tard."),
                      xalign=0),
            False, True, 0,
        )
        silentium_frame.add(silentium_box)
        v.pack_start(silentium_frame, False, True, 0)

        # --- Mode braquage ---
        braquage_frame = Gtk.Frame(label=_("Mode braquage (alarme)"))
        braquage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        braquage_box.set_margin_top(8)
        braquage_box.set_margin_bottom(8)
        braquage_box.set_margin_start(8)
        braquage_box.set_margin_end(8)

        b = self.cfg.get("braquage", {})
        self.braquage_enabled = Gtk.Switch(active=bool(b.get("enabled", False)))
        self.braquage_duration = self._spin(b.get("alarm_duration", 5), 1, 60, 1)

        brow1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        brow1.pack_start(Gtk.Label(label=_("Activer l'alarme sonore"), xalign=0), True, True, 0)
        brow1.pack_end(self.braquage_enabled, False, True, 0)
        braquage_box.pack_start(brow1, False, True, 0)

        brow2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        brow2.pack_start(Gtk.Label(label=_("Durée de l'alarme (secondes)"), xalign=0), True, True, 0)
        brow2.pack_end(self.braquage_duration, False, True, 0)
        braquage_box.pack_start(brow2, False, True, 0)

        self.braquage_tamper = Gtk.Switch(active=bool(b.get("on_tamper", True)))
        brow3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        brow3.pack_start(Gtk.Label(label=_("Alarme si un capteur armé disparaît"), xalign=0), True, True, 0)
        brow3.pack_end(self.braquage_tamper, False, True, 0)
        braquage_box.pack_start(brow3, False, True, 0)

        braquage_box.pack_start(
            Gtk.Label(label=_("Une alarme sonore retentit en cas d'intrusion détectée,\n"
                             "y compris si la webcam/BT devient indisponible pendant l'armement.\n"
                             "Attention : peut être bruyant !"),
                      xalign=0),
            False, True, 0,
        )
        braquage_frame.add(braquage_box)
        v.pack_start(braquage_frame, False, True, 0)

        # --- Notifications ---
        notif_frame = Gtk.Frame(label=_("Notifications bureau"))
        notif_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        notif_box.set_margin_top(8)
        notif_box.set_margin_bottom(8)
        notif_box.set_margin_start(8)
        notif_box.set_margin_end(8)

        n = self.cfg.get("notifications", {})
        self.notif_rearm = Gtk.Switch(active=bool(n.get("rearm", True)))

        nrow1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nrow1.pack_start(Gtk.Label(label=_("Notifier lors du réarmement auto"), xalign=0), True, True, 0)
        nrow1.pack_end(self.notif_rearm, False, True, 0)
        notif_box.pack_start(nrow1, False, True, 0)

        notif_box.pack_start(
            Gtk.Label(label=_("Reçoit une notification quand le système se réarme\n"
                             "(hors domicile ou hors ligne)."),
                      xalign=0),
            False, True, 0,
        )
        notif_frame.add(notif_box)
        v.pack_start(notif_frame, False, True, 0)

        # --- Voyant webcam ---
        led_frame = Gtk.Frame(label=_("Voyant webcam (confirmation de capture)"))
        led_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        led_box.set_margin_top(8)
        led_box.set_margin_bottom(8)
        led_box.set_margin_start(8)
        led_box.set_margin_end(8)

        l = self.cfg.get("led", {})
        self.led_enabled = Gtk.Switch(active=bool(l.get("enabled", False)))

        lrow1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lrow1.pack_start(Gtk.Label(label=_("Afficher le voyant au-dessus des fenêtres"), xalign=0), True, True, 0)
        lrow1.pack_end(self.led_enabled, False, True, 0)
        led_box.pack_start(lrow1, False, True, 0)

        led_box.pack_start(
            Gtk.Label(label=_("Le voyant s'allume à chaque lecture d'image par l'application.\n"
                              "Déplacez-le en le glissant à la souris ; sa position est mémorisée."),
                      xalign=0),
            False, True, 0,
        )

        led_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b_led_test = Gtk.Button(label=_("Tester"))
        b_led_test.connect("clicked", lambda *_x: self._led_test_blink())
        b_led_reset = Gtk.Button(label=_("Remettre la position d'origine"))
        b_led_reset.connect("clicked", self.app.reset_led_position)
        led_btns.pack_start(b_led_test, False, True, 0)
        led_btns.pack_start(b_led_reset, False, True, 0)
        led_box.pack_start(led_btns, False, True, 0)

        led_frame.add(led_box)
        v.pack_start(led_frame, False, True, 0)

        v.pack_start(Gtk.Label(label="", xalign=0), True, True, 0)  # Spacer
        return v

    @staticmethod
    def _led_test_blink() -> None:
        import security_linux.led as led  # noqa: PLC0415

        led.blink()

    def _spin(self, value, lo, hi, step):
        adj = Gtk.Adjustment(value=float(value), lower=float(lo), upper=float(hi), step_increment=float(step))
        return Gtk.SpinButton(adjustment=adj)

    def _entry(self, value):
        e = Gtk.Entry()
        e.set_text(str(value))
        return e

    def run(self):
        self.dlg.show_all()
        try:
            resp = self.dlg.run()
            if resp == Gtk.ResponseType.OK:
                self._apply()
        finally:
            self._stop_preview()
            self.dlg.destroy()

    def _apply(self):
        g = self.cfg["general"]
        g["decision_mode"] = self.mode.get_active_id() or "AND"
        g["lock_grace_seconds"] = int(self.grace.get_value())
        g["min_absent_seconds"] = int(self.min_absent.get_value())
        g["auto_lock_repeat_minutes"] = int(self.autorepeat.get_value())

        c = self.cfg["camera"]
        c["enabled"] = self.cam_enabled.get_active()
        c["device"] = self.cam_device.get_active_id() or "/dev/video0"
        c["poll_seconds"] = int(self.cam_poll.get_value())
        c["absent_confirmations"] = int(self.cam_confirm.get_value())
        c["capture_on_lock"] = self.cam_capture.get_active()

        b = self.cfg["bluetooth"]
        b["enabled"] = self.bt_enabled.get_active()
        b["device_addr"] = (self.bt_dev.get_active_id() or "").lower()
        b["poll_seconds"] = int(self.bt_poll.get_value())
        b["min_rssi"] = int(self.bt_rssi.get_value())

        l = self.cfg["location"]
        l["method"] = self.loc_method.get_active_id() or "wifi"
        l["home_ssids"] = [s.strip() for s in self.loc_home.get_text().split(",") if s.strip()]
        l["secure_when_offline"] = self.offline_secure.get_active()
        try:
            l["home_lat"] = float(self.loc_lat.get_text().strip() or "0")
            l["home_lon"] = float(self.loc_lon.get_text().strip() or "0")
        except ValueError:
            l["home_lat"] = l["home_lon"] = 0.0
        l["radius_km"] = float(self.loc_rad.get_value())

        h = self.cfg["howdy"]
        want = self.howdy_enabled.get_active()
        if want and not howdy_ctrl.is_installed():
            self.app._msg(_("Howdy n'est pas installé. Impossible d'activer le déverrouillage facial."), error=True)
            want = False
        if want and howdy_ctrl.models_status() != "ok":
            self.app._msg(_("Aucun visage enregistré. Enregistrez votre visage avant d'activer Howdy."), error=True)
            want = False
        h["enabled"] = want

        # Général
        self.cfg["general"]["idle_lock_minutes"] = int(self.idle_lock.get_value())

        # Fonctions avancées
        s = self.cfg.setdefault("silentium", {})
        s["enabled"] = self.silentium_enabled.get_active()
        s["start_hour"] = int(self.silentium_start.get_value())
        s["end_hour"] = int(self.silentium_end.get_value())

        bq = self.cfg.setdefault("braquage", {})
        bq["enabled"] = self.braquage_enabled.get_active()
        bq["alarm_duration"] = int(self.braquage_duration.get_value())
        bq["on_tamper"] = self.braquage_tamper.get_active()

        n = self.cfg.setdefault("notifications", {})
        n["rearm"] = self.notif_rearm.get_active()

        l = self.cfg.setdefault("led", {})
        l["enabled"] = self.led_enabled.get_active()

        config.save_config(self.cfg)
        self.app.refresh_led()
        # applique à chaud les réglages au démon
        values = {
            "camera.enabled": self.cfg["camera"]["enabled"],
            "camera.device": self.cfg["camera"]["device"],
            "bluetooth.device_addr": self.cfg["bluetooth"]["device_addr"],
            "bluetooth.enabled": self.cfg["bluetooth"]["enabled"],
            "location.method": self.cfg["location"]["method"],
            "location.home_ssids": self.cfg["location"]["home_ssids"],
            "location.secure_when_offline": self.cfg["location"]["secure_when_offline"],
        }
        events.write_command({"type": "set_many", "values": values})
        events.log_event("config", _("réglages mis à jour"))


def _get_lock_path() -> Path:
    """Retourne le chemin du fichier lock pour l'instance unique.

    On privilégie XDG_RUNTIME_DIR (per-user, tmpfs) et on tombe sur le
    répertoire de config de l'utilisateur si celui-ci n'est pas défini —
    jamais sur /tmp (dossier partagé, monde inscriptible).
    """
    if runtime.is_debug():
        return runtime.debug_root() / "gui.lock"
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or config.config_dir())
    return base / "security-linux-gui.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_instance_lock() -> bool:
    """Acquiert le verrou d'instance unique. Retourne False si déjà acquis."""
    global _LOCK_FILE
    lock_path = _get_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Une instance précédente peut être morte (crash) : son flock est libéré
        # mais le fichier reste. On supprime le fichier orphelin avant de verrouiller.
        try:
            stale_pid = int(lock_path.read_text("utf-8").strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            stale_pid = 0
        if stale_pid and not _pid_alive(stale_pid):
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        # Ouverture en "a+" : ne tronque PAS un fichier potentiellement
        # verrouillé par une autre instance. On ne tronque qu'après verrouillage.
        _LOCK_FILE = open(lock_path, "a+")
        fcntl.flock(_LOCK_FILE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE.seek(0)
        _LOCK_FILE.truncate()
        _LOCK_FILE.write(f"{os.getpid()}\n")
        _LOCK_FILE.flush()
        atexit.register(_release_instance_lock)
        return True
    except (IOError, OSError):
        if _LOCK_FILE:
            try:
                _LOCK_FILE.close()
            except OSError:
                pass
            _LOCK_FILE = None
        return False


def _release_instance_lock() -> None:
    """Relâche le verrou d'instance."""
    global _LOCK_FILE
    if _LOCK_FILE:
        try:
            fcntl.flock(_LOCK_FILE.fileno(), fcntl.LOCK_UN)
            _LOCK_FILE.close()
            _LOCK_FILE = None
            lock_path = _get_lock_path()
            if lock_path.exists():
                lock_path.unlink(missing_ok=True)
        except (IOError, OSError):
            pass


def _msg_existing_instance() -> None:
    """Affiche un message indiquant qu'une instance est déjà en cours."""
    print(_("{app} : une instance est déjà en cours d'exécution.").format(app=APP_NAME), file=sys.stderr)
    sys.exit(1)


def main():
    SecurityLinuxApp().run()
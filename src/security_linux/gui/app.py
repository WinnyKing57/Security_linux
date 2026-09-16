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

import fcntl
import os
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

    # -------------------------------------------------------- construction
    def build_ui(self) -> Gtk.Window:
        win = Gtk.Window(title=f"{APP_NAME} — Sécurité et verrouillage automatique")
        win.set_border_width(12)
        win.set_default_size(520, 460)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # --- bannière mode développement
        self.labels["mode_banner"] = Gtk.Label(label="…", xalign=0)
        self.labels["mode_banner"].set_visible(False)
        vbox.pack_start(self.labels["mode_banner"], False, True, 0)

        # --- bannière + interrupteur
        armed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.armed_switch = Gtk.Switch()
        self.armed_switch.set_active(bool(self.cfg["general"]["armed"]))
        self.armed_switch.connect("state-set", self.on_armed_toggle)
        lbl = Gtk.Label(label="Protection automatique")
        lbl.set_markup("<b>Protection automatique</b>")
        armed_row.pack_start(lbl, False, True, 0)
        armed_row.pack_end(self.armed_switch, False, True, 0)

        self.labels["arm_eff"] = Gtk.Label(label="…")
        self.labels["arm_status"] = Gtk.Label(label="")

        # --- carte statut
        status_frame = Gtk.Frame(label="État du système")
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        rows = [
            ("Webcam (visage)", "webcam"),
            ("Bluetooth (appareil)", "bluetooth"),
            ("Localisation", "location"),
            ("Howdy (visage PAM)", "howdy"),
            ("Dernier événement", "last_event"),
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
        b_lock = Gtk.Button(label="Verrouiller maintenant")
        b_lock.connect("clicked", lambda *_x: self.lock_now())
        b_settings = Gtk.Button(label="Réglages…")
        b_settings.connect("clicked", lambda *_x: self.open_settings())
        b_howdy = Gtk.Button(label="Enregistrer mon visage")
        b_howdy.connect("clicked", lambda *_x: self.enroll_face())
        b_events = Gtk.Button(label="Journal")
        b_events.connect("clicked", lambda *_x: self.show_events())
        for b in (b_lock, b_settings, b_howdy, b_events):
            btn_row.pack_start(b, False, True, 0)

        footer = Gtk.Label(label=f"{APP_NAME} v{__version__} — analyse 100 % locale", xalign=0)

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
        self._poll()
        self._maybe_setup_admin_code()
        self._build_tray()
        Gtk.main()
        _release_instance_lock()

    def on_delete_event(self, *_args):
        # se ferme en barre système et non quit
        self.tray.set_visible(True)
        self.window.hide()
        return True

    def quit_app(self, *_args):
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
        item = Gtk.MenuItem(label="Ouvrir")
        item.connect("activate", lambda *_x: self.window.present())
        menu.append(item)
        item = Gtk.MenuItem(label="Verrouiller")
        item.connect("activate", lambda *_x: self.lock_now())
        menu.append(item)
        item = Gtk.MenuItem(label="Quitter")
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

    # -------------------------------------------------------------- état
    def _poll(self) -> bool:
        state = events.read_state()
        if state:
            self.last_state = state
            self._render_state(state)
        self._render_armed()
        GLib.timeout_add(_TICK_MS, self._poll)
        return False

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

        installed = "installé" if h.get("installed") else "non installé"
        mods = h.get("models_status", "unknown")
        mods_fr = {"ok": "visages enregistrés", "none": "aucun visage", "unknown": "droits insuffisants", "not_installed": "—"}.get(mods, mods)
        setl("howdy", f"{installed} · {mods_fr}", col(h.get("models_status", "unknown")))

        last = state.get("last_event")
        if last:
            setl("last_event", f"[{last.get('ts', '')}] {last.get('message', '')}")
        else:
            setl("last_event", "—")

        banner = self.labels.get("mode_banner")
        is_debug = state.get("mode") == "debug" or runtime.is_debug()
        if banner:
            if is_debug:
                banner.set_markup('<span color="orange" weight="bold">MODE DÉVELOPPEMENT (DEBUG) — config isolée, verrouillage SIMULÉ</span>')
                banner.set_visible(True)
            else:
                banner.set_visible(False)

    def _render_armed(self) -> None:
        armed = self.last_state.get("armed_state", {})
        eff = armed.get("armed", self.cfg["general"]["armed"])
        manual = armed.get("manual", self.cfg["general"]["armed"])
        forced = armed.get("forced", False)
        if self.armed_switch.get_active() != manual:
            self.armed_switch.handler_block_by_func(self.on_armed_toggle)
            self.armed_switch.set_active(manual)
            self.armed_switch.handler_unblock_by_func(self.on_armed_toggle)
        status_lbl = self.labels.get("arm_status")
        if eff:
            why = " (réarmé : hors domicile / hors-ligne)" if forced and not manual else ""
            txt = f"Protection ACTIVE{why}"
            color = "green"
        else:
            txt = "Protection désactivée (code admin)"
            color = "grey"
        status_lbl.set_markup(f"<span color=\"{color}\" weight=\"bold\">{txt}</span>")

    # --------------------------------------------------------- actions
    def on_armed_toggle(self, switch, state):
        new_state = bool(state)
        if new_state is False:
            if not self._confirm_admin("Désactiver la protection", "Saisissez le code administrateur pour désactiver le système."):
                switch.handler_block_by_func(self.on_armed_toggle)
                switch.set_active(True)
                switch.handler_unblock_by_func(self.on_armed_toggle)
                return
        events.write_command({"type": "arm", "value": new_state})
        self.cfg["general"]["armed"] = new_state
        config.save_config(self.cfg)
        return False

    def _confirm_admin(self, title, message) -> bool:
        if not config.has_admin_code(self.cfg):
            self._setup_admin_code_dialog(title, message)
        dlg = AdminCodeDialog(self.window, title, message)
        dlg.show_all()
        resp = dlg.run()
        code = dlg.get_code()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return False
        return config.verify_admin_code(self.cfg, code)

    def _maybe_setup_admin_code(self):
        if not config.has_admin_code(self.cfg):
            self._setup_admin_code_dialog("Premier lancement", "Créez votre code administrateur (nécessaire pour désactiver la protection).")

    def _setup_admin_code_dialog(self, title, message):
        dlg = AdminCodeDialog(self.window, title, message)
        dlg.show_all()
        resp = dlg.run()
        first = dlg.get_code()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not first:
            return
        dlg2 = AdminCodeDialog(self.window, "Confirmation", "Confirmez le code administrateur")
        dlg2.show_all()
        resp2 = dlg2.run()
        second = dlg2.get_code()
        dlg2.destroy()
        if resp2 == Gtk.ResponseType.OK and first == second and len(first) >= 4:
            config.set_admin_code(self.cfg, first)
            config.save_config(self.cfg)
            events.log_event("admin", "code administrateur défini")

    def lock_now(self):
        from security_linux.lock import lock_screen

        events.write_state(self.last_state)
        lock_screen()

    def enroll_face(self):
        if not howdy_ctrl.is_installed():
            self._msg("Howdy n'est pas installé sur ce système.", error=True)
            return
        cfg = config.load_config()
        device = cfg["camera"].get("device") or ""
        cmd = howdy_ctrl.enroll_command(device)
        if not cmd:
            self._msg("Aucun terminal disponible pour l'enregistrement du visage.", error=True)
            return
        subprocess.Popen(cmd, start_new_session=True)

    def _launch_howdy_install(self):
        script = howdy_ctrl.install_script_path()
        if not script:
            self._msg("Script d'installation Howdy introuvable dans le dépôt.", error=True)
            return
        dlg = Gtk.MessageDialog(
            transient_for=self.window,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_format="Installer Howdy (déverrouillage facial)",
        )
        dlg.format_secondary_text(
            "L'installation compile Howdy depuis le code source inclus dans le dépôt.\n\n"
            "- mot de passe root demandé (fenêtre pkexec / console)\n"
            "- durée : plusieurs minutes (compilation dlib)\n"
            "- nécessite une connexion Internet pour les dépendances et modèles\n\n"
            "Voulez-vous lancer l'installation ?"
        )
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        cmd = howdy_ctrl.install_command(script)
        if not cmd:
            self._msg("pkexec est nécessaire pour l'installation (paquet policykit-1).", error=True)
            return
        try:
            subprocess.Popen(cmd, start_new_session=True)
            self._msg("Installation lancée dans une fenêtre root. Vérifiez son avancement, puis\n"
                      "réouvrez ces réglages pour activer Howdy après enregistrement du visage (bouton « Enregistrer mon visage »).")
        except OSError as exc:
            self._msg(f"Impossible de lancer l'installation : {exc}", error=True)
        self._msg("Terminal d'enregistrement ouvert (sudo howdy add). Suivez les instructions.")

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
        ) or "Aucun événement."
        win = Gtk.Window(title="Journal des événements")
        win.set_default_size(560, 320)
        sw = Gtk.ScrolledWindow()
        buf = Gtk.TextBuffer()
        buf.set_text(text)
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        sw.add(tv)
        win.add(sw)
        win.show_all()

    def open_settings(self):
        SecuritySettingsDialog(self).run()


class SecuritySettingsDialog:
    def __init__(self, app: SecurityLinuxApp):
        self.app = app
        self.cfg = config.load_config()
        self.dlg = Gtk.Dialog(
            title="Réglages",
            transient_for=app.window,
            flags=0,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK),
        )
        box = self.dlg.get_content_area()
        nb = Gtk.Notebook()
        nb.append_page(self._general_tab(), Gtk.Label(label="Général"))
        nb.append_page(self._camera_tab(), Gtk.Label(label="Webcam"))
        nb.append_page(self._bluetooth_tab(), Gtk.Label(label="Bluetooth"))
        nb.append_page(self._location_tab(), Gtk.Label(label="Localisation"))
        nb.append_page(self._howdy_tab(), Gtk.Label(label="Howdy"))
        box.pack_start(nb, True, True, 0)
        box.show_all()

    # onglets
    def _general_tab(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.mode = Gtk.ComboBoxText()
        self.mode.append("AND", "Visage ET Bluetooth (recommandé)")
        self.mode.append("OR", "Dès qu'un seul détecteur dit 'absent'")
        self.mode.set_active_id(self.cfg["general"]["decision_mode"])
        self.mode.set_hexpand(True)
        self.grace = self._spin(self.cfg["general"]["lock_grace_seconds"], 0, 120, 1)
        self.min_absent = self._spin(self.cfg["general"].get("min_absent_seconds", 20), 5, 600, 5)
        self.autorepeat = self._spin(self.cfg["general"]["auto_lock_repeat_minutes"], 1, 60, 1)
        for lbl, wgt in (
            ("Mode de décision", self.mode),
            ("Absence minimale (s)", self.min_absent),
            ("Délai de grâce avant verrouillage (s)", self.grace),
            ("Re-verrouillage si menace persistante (min)", self.autorepeat),
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
            self.cam_device.append(dev, f"{dev} — {CameraMonitor.device_label(dev)}")
        current = c["device"]
        if current not in self.cam_devices and current:
            self.cam_device.append(current, f"{current} — configuré mais non détecté")
        if not self.cam_devices:
            self.cam_device.append("", "— aucun périphérique détecté —")
            self.cam_device.set_active_id("")
        else:
            self.cam_device.set_active_id(current if current in self.cam_devices else self.cam_devices[0])

        self.cam_poll = self._spin(c["poll_seconds"], 2, 60, 1)
        self.cam_confirm = self._spin(c["absent_confirmations"], 1, 10, 1)
        self.cam_capture = Gtk.Switch(active=bool(c["capture_on_lock"]))
        for lbl, wgt in (
            ("Activer la détection webcam", self.cam_enabled),
            ("Périphérique (aperçu en direct)", self.cam_device),
            ("Analyse toutes les (s)", self.cam_poll),
            ("Photos de confirmation", self.cam_confirm),
            ("Capturer une image locale lors du verrouillage", self.cam_capture),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=lbl, xalign=0), True, True, 0)
            row.pack_end(wgt, False, True, 0)
            v.pack_start(row, False, True, 0)

        # --- aperçu en direct (test droits / périphérique) ---
        frame = Gtk.Frame(label="Aperçu en direct")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.cam_preview = Gtk.Image()
        self.cam_preview.set_size_request(420, 236)
        self.cam_status = Gtk.Label(label="Lancement de l'aperçu…", xalign=0)
        self.cam_status.set_line_wrap(True)
        inner.pack_start(self.cam_preview, True, True, 0)
        inner.pack_start(self.cam_status, False, True, 0)
        frame.add(inner)
        v.pack_start(frame, True, True, 0)

        self._preview_cap = None
        self._preview_dev = None
        self._preview_src = GLib.timeout_add(150, self._preview_tick)

        v.pack_start(Gtk.Label(label="Aucune donnée n'est envoyée sur internet.", xalign=0), False, True, 0)
        return v

    def _preview_tick(self):
        dev = self.cam_device.get_active_id() or ""
        if not dev:
            self.cam_status.set_markup(
                '<span color="red">Aucun périphérique vidéo détecté.</span>'
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
                        f'<span color="red">Impossible d\'ouvrir {dev} — '
                        "droits insuffisants (groupe « video ») ou périphérique occupé.</span>"
                    )
                    self.cam_preview.clear()
                    return True
            except Exception as exc:  # noqa: BLE001
                self.cam_status.set_markup(f'<span color="red">Erreur caméra : {exc}</span>')
                return True
            self._preview_cap = cap
        try:
            from security_linux.monitors.camera import CameraMonitor  # noqa: PLC0415

            import cv2  # noqa: PLC0415

            ok, frame = cap.read()
            if not ok or frame is None:
                self.cam_status.set_markup(
                    f'<span color="orange">Aucune image de {dev} '
                    "(périphérique occupé par un autre programme ?)</span>"
                )
                return True
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
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
                f'<span color="green">OK — {CameraMonitor.device_label(dev)}</span>'
            )
        except Exception as exc:  # noqa: BLE001
            self.cam_status.set_markup(f'<span color="red">Erreur aperçu : {exc}</span>')
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
        self.bt_dev.append("", "— Choisir un appareil —")
        for dev in mon.list_known_devices():
            self.bt_dev.append(dev["address"].lower(), f"{dev['name']}  ({dev['address']})")
        current = b["device_addr"].lower()
        self.bt_dev.set_active_id(current if current in {d["address"].lower() for d in mon.list_known_devices()} else "")
        self.bt_poll = self._spin(b["poll_seconds"], 2, 120, 1)
        self.bt_rssi = self._spin(b["min_rssi"], -100, -30, 5)
        for lbl, wgt in (
            ("Activer la détection Bluetooth", self.bt_enabled),
            ("Appareil à surveiller (téléphone/tablette/montre)", self.bt_dev),
            ("Vérification toutes les (s)", self.bt_poll),
            ("Seuil de signal RSSI (dBm)", self.bt_rssi),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=lbl, xalign=0), True, True, 0)
            row.pack_end(wgt, False, True, 0)
            v.pack_start(row, False, True, 0)
        v.pack_start(Gtk.Label(label="Détection locale via BlueZ.", xalign=0), False, True, 0)
        return v

    def _location_tab(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        l = self.cfg["location"]
        self.loc_method = Gtk.ComboBoxText()
        self.loc_method.append("wifi", "Wi-Fi SSID (100 % local, recommandé)")
        self.loc_method.append("gps", "GPS (GeoClue, optionnel) — repli Wi-Fi si indisponible")
        self.loc_method.set_active_id(l["method"])
        self.loc_home = Gtk.Entry()
        self.loc_home.set_text(", ".join(l["home_ssids"]))
        self.offline_secure = Gtk.Switch(active=bool(l["secure_when_offline"]))
        self.loc_lat = self._entry(l["home_lat"])
        self.loc_lon = self._entry(l["home_lon"])
        self.loc_rad = self._spin(l["radius_km"], 0.1, 100.0, 0.1)
        for lbl, wgt in (
            ("Méthode de localisation", self.loc_method),
            ("SSID 'maison' (séparés par des virgules)", self.loc_home),
            ("Sécurisé quand hors ligne (réarme auto)", self.offline_secure),
            ("Latitude du domicile (GPS)", self.loc_lat),
            ("Longitude du domicile (GPS)", self.loc_lon),
            ("Rayon de détection (km)", self.loc_rad),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=lbl, xalign=0), True, True, 0)
            row.pack_end(wgt, False, True, 0)
            v.pack_start(row, False, True, 0)
        v.pack_start(Gtk.Label(label="Hors domicile ou hors ligne ⇒ le système se réarme automatiquement.", xalign=0), False, True, 0)
        return v

    def _howdy_tab(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        h = self.cfg["howdy"]
        self.howdy_enabled = Gtk.Switch(active=bool(h["enabled"]))
        installed = "installé" if howdy_ctrl.is_installed() else "non installé"
        status = howdy_ctrl.models_status()
        state_label = Gtk.Label(label=f"Howdy : {installed} · visages: {status}")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(Gtk.Label(label="Activer le déverrouillage facial", xalign=0), True, True, 0)
        row.pack_end(self.howdy_enabled, False, True, 0)
        v.pack_start(row, False, True, 0)
        v.pack_start(state_label, False, True, 0)

        if not howdy_ctrl.is_installed():
            btn_install = Gtk.Button(label="Installer Howdy (via terminal root)")
            btn_install.connect("clicked", lambda *_x: self.app._launch_howdy_install())
            v.pack_start(btn_install, False, True, 0)
        else:
            btn_reinstall = Gtk.Button(label="Recompiler Howdy (optionnel)")
            btn_reinstall.set_tooltip_text("Reconstruire et réinstaller Howdy depuis le code source (root)")
            btn_reinstall.connect("clicked", lambda *_x: self.app._launch_howdy_install())
            v.pack_start(btn_reinstall, False, True, 0)

        v.pack_start(
            Gtk.Label(label="Un visage doit être enregistré avant d'activer Howdy.\n"
                           "Le déverrouillage v1 reste le mot de passe de session.",
                      xalign=0),
            False, True, 0,
        )
        return v

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
            self.app._msg("Howdy n'est pas installé. Impossible d'activer le déverrouillage facial.", error=True)
            want = False
        if want and howdy_ctrl.models_status() != "ok":
            self.app._msg("Aucun visage enregistré. Enregistrez votre visage avant d'activer Howdy.", error=True)
            want = False
        h["enabled"] = want

        config.save_config(self.cfg)
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
        events.log_event("config", "réglages mis à jour")


def _get_lock_path() -> Path:
    """Retourne le chemin du fichier lock pour l'instance unique."""
    if runtime.is_debug():
        return runtime.debug_root() / "gui.lock"
    base = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    return base / f"security-linux-gui.lock"


def _acquire_instance_lock() -> bool:
    """Acquiert le verrou d'instance unique. Retourne False si déjà acquis."""
    global _LOCK_FILE
    lock_path = _get_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _LOCK_FILE = open(lock_path, "w")
        fcntl.flock(_LOCK_FILE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE.write(str(os.getpid()))
        _LOCK_FILE.flush()
        return True
    except (IOError, OSError):
        if _LOCK_FILE:
            _LOCK_FILE.close()
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
    print(f"{APP_NAME}: une instance est déjà en cours d'exécution.", file=sys.stderr)
    sys.exit(1)


def main():
    SecurityLinuxApp().run()
"""Alarme sonore et notifications système.

Fonctions :
  * Alarme sonore (mode braquage) avec sirène
  * Notifications KDE/GNOME via notify-send ou QDBus
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from security_linux.i18n import _


def play_alarm(duration_seconds: int = 5) -> bool:
    """Joue une alarme sonore pendant duration_seconds secondes.
    
    Utilise paplay (PulseAudio) ou aplay (ALSA) avec un son généré.
    Retourne True si l'alarme a pu être lancée.
    """
    # Essayer d'abord PulseAudio (plus courant sur KDE/GNOME)
    try:
        # Générer un son de sirène avec ffplay ou utiliser un son système
        # Pour simplifier, on utilise le son d'alerte système
        import gi  # noqa: PLC0415
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: PLC0415
        
        Gst.init(None)
        pipeline = Gst.parse_launch(
            f"audiotestsrc freq=880 wave=square ! volume volume=0.5 ! autoaudiosink"
        )
        bus = pipeline.get_bus()
        pipeline.set_state(Gst.State.PLAYING)
        
        # Laisser jouer pendant duration_seconds
        import time  # noqa: PLC0415
        time.sleep(duration_seconds)
        pipeline.set_state(Gst.State.NULL)
        return True
    except Exception:
        pass
    
    # Fallback: essayer avec paplay (son système)
    try:
        subprocess.run(
            ["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
            timeout=duration_seconds,
            capture_output=True,
        )
        return True
    except Exception:
        pass
    
    # Fallback: beep terminal (si disponible)
    try:
        for _i in range(duration_seconds):
            print("\a", end="", flush=True)
            import time  # noqa: PLC0415
            time.sleep(1)
        return True
    except Exception:
        return False


def send_notification(title: str, message: str, urgency: str = "normal") -> bool:
    """Envoie une notification bureau (KDE/GNOME).
    
    Args:
        title: Titre de la notification
        message: Contenu du message
        urgency: "low", "normal" ou "critical"
    
    Retourne True si la notification a été envoyée.
    """
    # Méthode 1: notify-send (standard freedesktop)
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-i", "security-linux", title, message],
            timeout=5,
            capture_output=True,
            check=False,
        )
        return True
    except Exception:
        pass
    
    # Méthode 2: QDBus pour KDE
    try:
        import dbus  # noqa: PLC0415
        
        bus = dbus.SessionBus()
        obj = bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
        iface = dbus.Interface(obj, "org.freedesktop.Notifications")
        
        # Mapping urgency
        urgency_map = {"low": 0, "normal": 1, "critical": 2}
        hints = {"urgency": dbus.Byte(urgency_map.get(urgency, 1))}
        
        iface.Notify(
            "Security-Linux",  # app_name
            0,  # replaces_id
            "security-linux",  # app_icon
            title,  # summary
            message,  # body
            [],  # actions
            hints,  # hints
            5000,  # expire_timeout (ms)
        )
        return True
    except Exception:
        pass
    
    # Fallback: affichage terminal
    print(_("[NOTIFICATION] {titre} : {message}").format(titre=title, message=message))
    return False


def send_rearm_notification(reason: str) -> bool:
    """Notification spécifique pour le réarmement automatique.

    Args:
        reason: Raison du réarmement ("hors_domicile", "hors_ligne", etc.)
    """
    reasons = {
        "hors_domicile": (_("Protection réactivée"), _("Vous avez quitté votre domicile. Le système s'est réarmé automatiquement.")),
        "hors_ligne": (_("Protection réactivée"), _("Réseau perdu. Le système s'est réarmé automatiquement (mode sécurisé).")),
        "manuel": (_("Protection activée"), _("La protection a été activée manuellement.")),
    }

    title, message = reasons.get(reason, (
        _("Protection réactivée"),
        _("Le système s'est réarmé automatiquement ({raison}).").format(raison=reason),
    ))

    return send_notification(title, message, urgency="normal")


def send_intrusion_alert(intrusion_type: str) -> bool:
    """Alerte d'intrusion avec notification critique.

    Args:
        intrusion_type: Type d'intrusion ("visage_absent", "bluetooth_absent", "intrusion_detectee")
    """
    alerts = {
        "visage_absent": (_("⚠️ Visage non détecté"), _("Aucun visage n'a été détecté devant l'écran. Verrouillage en cours...")),
        "bluetooth_absent": (_("📱 Appareil Bluetooth absent"), _("Votre appareil Bluetooth n'est plus à portée. Verrouillage en cours...")),
        "intrusion_detectee": (_("🚨 ALERTE INTRUSION"), _("Un mouvement ou une présence suspecte a été détectée !")),
    }

    title, message = alerts.get(intrusion_type, (
        _("⚠️ Alerte sécurité"),
        _("Une anomalie de sécurité a été détectée."),
    ))

    return send_notification(title, message, urgency="critical")

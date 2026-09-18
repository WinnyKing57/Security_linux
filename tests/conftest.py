"""Isolation des tests : jamais de toucher la configuration de production.

Certaines fonctions du noyau (ex. verify_admin_code / save_config) écrivent
sur le chemin de configuration par défaut. Sans précautions, un simple
`pytest` remplace la config réelle de l'utilisateur. Ici, on force le mode
debug pour toute la session : config et données restent dans le répertoire
isolé ~/.cache/security-linux/debug.
"""
import os

os.environ.setdefault("SECURITY_LINUX_MODE", "debug")
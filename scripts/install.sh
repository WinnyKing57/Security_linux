#!/usr/bin/env bash
# Installation de Security-Linux pour l'utilisateur courant (Debian/KDE).
# Usage : ./scripts/install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

echo "==> Environnement Python"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv --system-site-packages --upgrade-deps "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --quiet -r "$REPO_DIR/requirements.txt"
"$VENV_DIR/bin/python" -m pip install --quiet -e "$REPO_DIR"

echo "==> Paquets système utiles (facultatif, demande root)"
if command -v sudo >/dev/null; then
  sudo apt-get install -y --no-install-recommends \
    python3-dbus python3-gi gir1.2-gtk-3.0 bluetooth bluez nmcli \
    fswebcam 2>/dev/null || true
fi

echo "==> Configuration par défaut"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/security-linux"
mkdir -p "$CONF_DIR"
if [ ! -f "$CONF_DIR/config.json" ]; then
  cp "$REPO_DIR/config/default.json" "$CONF_DIR/config.json"
  chmod 600 "$CONF_DIR/config.json"
  echo "    config initiale créée"
fi

echo "==> Démarrage automatique (KDE autostart)"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/security-linux.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Security-Linux
Name[fr]=Security-Linux
Comment=Verrouillage automatique et détection de présence
Exec=$VENV_DIR/bin/security-linuxd
Terminal=false
StartupNotify=true
X-KDE-autostart-after=panel
EOF

# Supprimer l'ancien autostart cassé
rm -f "$AUTOSTART_DIR/lock_proximity.sh.desktop"

echo "==> Raccourci applicatif"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/security-linux.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Security-Linux
Name[fr]=Security-Linux
Comment=Sécurité et verrouillage automatique
Comment[fr]=Sécurité et verrouillage automatique
Exec=$VENV_DIR/bin/security-linux
Icon=$REPO_DIR/assets/shield.png
Terminal=false
Categories=System;Security;
EOF

echo "==> Terminé."
echo "  Lancez l'application :  $VENV_DIR/bin/security-linux"
echo "  Démon :                 $VENV_DIR/bin/security-linuxd"
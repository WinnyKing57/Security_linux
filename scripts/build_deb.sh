#!/usr/bin/env bash
# Construction du paquet natif Debian/Ubuntu : dist/security-linux_<version>_all.deb
# Outils requis : fakeroot, dpkg-deb (aucun besoin d'être root pour construire).
# Usage : ./scripts/build_deb.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

command -v fakeroot >/dev/null || { echo "ERREUR : fakeroot absent (apt install fakeroot)." >&2; exit 1; }
command -v dpkg-deb >/dev/null || { echo "ERREUR : dpkg-deb absent (paquet dpkg)." >&2; exit 1; }

VERSION="$(
  python3 - <<'EOF' || true
import re
src = open("src/security_linux/version.py", encoding="utf-8").read()
print(re.search(r'__version__\s*=\s*"([^"]+)"', src).group(1))
EOF
)"
[ -n "${VERSION:-}" ] || { echo "ERREUR : version introuvable." >&2; exit 1; }

STAGING=dist/deb-root
PKG=security-linux
ARCH=all
DEB="dist/security-linux_${VERSION}_${ARCH}.deb"

echo "==> Build du paquet .deb (v${VERSION})"
rm -rf "$STAGING" dist
mkdir -p "$STAGING/DEBIAN"
mkdir -p "$STAGING/usr/lib/python3/dist-packages"
mkdir -p "$STAGING/usr/bin"
mkdir -p "$STAGING/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$STAGING/usr/share/applications"
mkdir -p "$STAGING/etc/xdg/autostart"
mkdir -p "$STAGING/usr/share/security-linux"
mkdir -p "$STAGING/usr/lib/systemd/user"

# --- contenu Python (+ catalogues .mo) ---
cp -a src/security_linux "$STAGING/usr/lib/python3/dist-packages/security_linux"
find "$STAGING/usr/lib/python3/dist-packages" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGING/usr/lib/python3/dist-packages" -name '*.pyc' -delete

# --- wrappers /usr/bin ---
install -m755 /dev/stdin "$STAGING/usr/bin/security-linux" <<'EOF'
#!/usr/bin/python3
import sys
from security_linux.cli import main
sys.exit(main())
EOF
install -m755 /dev/stdin "$STAGING/usr/bin/security-linuxd" <<'EOF'
#!/usr/bin/python3
import sys
from security_linux.daemon import main
sys.exit(main())
EOF

# --- icône + config par défaut ---
install -m644 assets/shield.png "$STAGING/usr/share/icons/hicolor/256x256/apps/security-linux.png"
install -m644 config/default.json "$STAGING/usr/share/security-linux/default.json"

# --- lanceurs + autostart ---
install -m644 /dev/stdin "$STAGING/usr/share/applications/security-linux.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Security-Linux
Name[fr]=Security-Linux
Comment=Sécurité et verrouillage automatique
Comment[fr]=Sécurité et verrouillage automatique
Exec=/usr/bin/security-linux
Icon=security-linux
Terminal=false
Categories=System;Security;
EOF

install -m644 /dev/stdin "$STAGING/etc/xdg/autostart/security-linuxd.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Security-Linux
Name[fr]=Security-Linux
Comment=Verrouillage automatique et détection de présence
Exec=/usr/bin/security-linuxd
Terminal=false
StartupNotify=true
X-KDE-autostart-after=panel
EOF

# --- service systemd utilisateur (OPTIONNEL : activé par systemctl --user) ---
install -m644 packaging/systemd/security-linuxd.service \
  "$STAGING/usr/lib/systemd/user/security-linuxd.service"

# --- contrôle Debian ---
install -m644 /dev/stdin "$STAGING/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: WinnyKing57 <winnyking57@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-3.0,
 python3-opencv, python3-dbus, bluez, bluetooth, network-manager, v4l-utils
Description: Verrouillage automatique d'écran et sécurité de bureau - 100% local
 Verrouillage automatique quand le visage et l'appareil Bluetooth disparaissent,
 avec localisation, mode braquage, notifications et intégration Howdy.
 Aucune donnée n'est envoyée hors de la machine.
EOF

install -m755 /dev/stdin "$STAGING/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
# Configuration initiale (jamais écrasée)
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/security-linux"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/security-linux"
if [ ! -f "$CONF_DIR/config.json" ]; then
  install -d -m 700 "$CONF_DIR"
  install -m 600 /usr/share/security-linux/default.json "$CONF_DIR/config.json"
fi
if [ -d "$DATA_DIR" ]; then
  chmod 700 "$DATA_DIR" 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
update-mime-database /usr/share/mime >/dev/null 2>&1 || true
exit 0
EOF

install -m755 /dev/stdin "$STAGING/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "purge" ]; then
  # Suppression complète MAIS jamais les données utilisateur sans confirmation :
  # on ne supprime que ce que le paquet a créé. La config utilisateur reste.
  :
fi
exit 0
EOF

echo "==> dpkg-deb --build"
fakeroot dpkg-deb --build "$STAGING" "$DEB" >/dev/null
rm -rf "$STAGING"

echo "=== Fichier généré : $DEB ==="
dpkg-deb --info "$DEB" | sed -n '1,12p'
echo "---"
dpkg-deb --contents "$DEB" | awk '{print $6}' | sed -n '1,25p'
echo "Installation :  sudo dpkg -i $DEB"
echo "Désinstallation : sudo dpkg -r security-linux   (purge : sudo dpkg -P security-linux)"
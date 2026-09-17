#!/usr/bin/env bash
# Installation de Security-Linux pour l'utilisateur courant.
# Auto-détection de la distribution (Debian/Ubuntu, Fedora, Arch, openSUSE).
# Usage : ./scripts/install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/security-linux"

# ------------------------------------------------------------ détection distro
detect_distro() {
  local id="" id_like=""
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    id="${ID:-}"
    id_like="${ID_LIKE:-}"
  fi
  case "$id" in
    arch|manjaro|endeavouros) echo "arch"; return;;
    fedora) echo "fedora"; return;;
    opensuse-leap|opensuse-tumbleweed|suse) echo "opensuse"; return;;
  esac
  case " $id_like " in
    *" arch "*) echo "arch"; return;;
    *" suse "*) echo "opensuse"; return;;
    *" fedora "*) echo "fedora"; return;;
  esac
  # Défaut : famille Debian/Ubuntu (debian, ubuntu, linuxmint, pop, ...)
  echo "debian"
}
DISTRO="$(detect_distro)"
echo "==> Distribution détectée : $DISTRO"

DEBIAN_PKGS=(python3-dbus python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-opencv bluetooth bluez network-manager v4l-utils)
FEDORA_PKGS=(python3-gobject python3-dbus python3-opencv gtk3 bluez bluez-tools NetworkManager v4l-utils)
ARCH_PKGS=(python-gobject python-dbus opencv gtk3 bluez bluez-utils networkmanager v4l-utils)
OPENSUSE_PKGS=(python3-gobject python3-dbus python3-opencv gtk3 bluez bluez-tools NetworkManager v4l-utils)

# ---------------------------------------------------- paquets système (facultatif)
install_system_packages() {
  if ! command -v sudo >/dev/null; then
    echo "    sudo indisponible : paquets système ignorés."
    echo "    Installez-les manuellement selon la documentation (README)."
    return 0
  fi
  echo "==> Paquets système ($DISTRO) — mot de passe éventuellement demandé"
  # Toute étape est facultative : on ne fait jamais échouer l'installation
  # de l'application à cause d'un mot de passe manquant ou d'un paquet absent.
  case "$DISTRO" in
    debian)
      sudo apt-get update -qq || true
      sudo apt-get install -y --no-install-recommends "${DEBIAN_PKGS[@]}" || true;;
    fedora)
      sudo dnf install -y "${FEDORA_PKGS[@]}" || true;;
    arch)
      sudo pacman -S --needed --noconfirm "${ARCH_PKGS[@]}" || true;;
    opensuse)
      sudo zypper --non-interactive install "${OPENSUSE_PKGS[@]}" || true;;
  esac
}

# ------------------------------------------------------------ environnement python
prepare_venv() {
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    if ! python3 -m venv --help >/dev/null 2>&1; then
      echo "ERREUR : python3-venv est nécessaire (paquet 'python3-venv' sur Debian/Ubuntu)." >&2
      exit 1
    fi
    python3 -m venv --system-site-packages --upgrade-deps "$VENV_DIR"
  fi
  local venv_pip="$VENV_DIR/bin/pip"
  if [ -x "$venv_pip" ]; then
    "$venv_pip" install --quiet -r "$REPO_DIR/requirements.txt"
    "$venv_pip" install --quiet -e "$REPO_DIR"
  else
    echo "ERREUR : pip absent de l'environnement virtuel." >&2
    echo "        Installez 'python3-pip' puis relancez le script." >&2
    exit 1
  fi
}

# ------------------------------------------------------------ configuration
create_config() {
  echo "==> Configuration par défaut"
  mkdir -p "$CONF_DIR"
  if [ ! -f "$CONF_DIR/config.json" ]; then
    cp "$REPO_DIR/config/default.json" "$CONF_DIR/config.json"
    chmod 600 "$CONF_DIR/config.json"
    echo "    config initiale créée ($CONF_DIR/config.json)"
  else
    echo "    config existante conservée"
  fi
}

# ------------------------------------------------------------ autostart + lanceur
install_autostart() {
  echo "==> Démarrage automatique à l'ouverture de session"
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
}

install_launcher() {
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
}

# ------------------------------------------------------------ exécution
install_system_packages
prepare_venv
create_config
install_autostart
install_launcher

echo "==> Terminé."
echo "  Lancez l'application :  $VENV_DIR/bin/security-linux"
echo "  Démon :                 $VENV_DIR/bin/security-linuxd"
echo "  (Howdy/visage PAM : optionnel, bouton dans Réglages → Howdy)"
#!/usr/bin/env bash
# Installation de Howdy (déverrouillage facial PAM) depuis le code du dépôt.
#
# À lancer en root (le GUI ouvre un terminal : konsole -e pkexec install_howdy.sh).
# Réinstallation possible à tout moment.
#
# Journal complet : /tmp/howdy-install.log  (tout est aussi affiché en direct)
# La fenêtre reste ouverte à la fin tant qu'on n'appuie pas sur Entrée.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG=/tmp/howdy-install.log
: > "$LOG"                # journal neuf à chaque essai
exec > >(tee -a "$LOG") 2>&1   # tout (stdout+stderr) va au terminal ET au journal
trap 'rc=$?; echo; read -n1 -p "Fermer la fenêtre… [Entrée]" _ 2>/dev/null || true; echo; exit "$rc"' EXIT

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "[!] $*"; echo "Détails : $LOG"; exit 1; }

echo "=== démarrage install_howdy.sh : user=$(id -un) pid=$$ ==="

if [ "$(id -u)" -ne 0 ]; then
  die "Veuillez exécuter ce script en root (pkexec ou sudo)."
fi

log "Installation de Howdy (build depuis le dépôt) — journal : $LOG"

# --- 1. dépendances -------------------------------------------------
log "apt: mise à jour (en root)…"
apt-get update -qq || true
log "apt: installation des dépendances de compilation (dlib/inih/evdev/opencv)…"
export DEBIAN_FRONTEND=noninteractive
# NB: python3-dlib et pam-auth-update ne sont PAS des paquets Debian trixie ;
#     dlib Python est déjà présent (20.0.1) et pam-auth-update provient de libpam-runtime.
apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build meson pkg-config \
  python3-dev python3-numpy python3-opencv libopencv-dev \
  libpam0g-dev libevdev-dev \
  libboost-dev libboost-python-dev \
  wget bzip2 ca-certificates \
  || true

log "python dlib : $(python3 -c 'import dlib; print("OK", dlib.__version__)' 2>/dev/null || echo 'ABSENT → compilation pip (long)…')"
if ! python3 -c "import dlib" >/dev/null 2>&1; then
  apt-get install -y python3-pip >/dev/null 2>&1 || true
  python3 -m pip install --break-system-packages dlib >/dev/null 2>&1 || die "Échec de dlib (pip)."
fi

# --- 2. compilation Howdy -------------------------------------------
BUILD="$REPO_DIR/.build-howdy"
rm -rf "$BUILD"
log "meson setup (prefix /usr, config /etc/howdy)…"
if ! meson setup \
      --prefix=/usr \
      -Dpython_path=/usr/bin/python3 \
      -Dinstall_pam_config=true \
      -Dconfig_dir=/etc/howdy \
      -Dwith_polkit=true \
      "$BUILD" "$REPO_DIR"; then
  die "meson setup a échoué (inih/libevdev introuvables ?) — relancer après apt install libinih-dev libevdev-dev."
fi

log "ninja (compilation)…"
ninja -C "$BUILD" || die "ninja a échoué."

log "installation dans /usr…"
ninja -C "$BUILD" install || die "installation a échoué."

# --- 3. PAM ----------------------------------------------------------
log "activation du module PAM…"
pam-auth-update --enable howdy 2>/dev/null || true
pam-auth-update --package 2>/dev/null || true
chmod 755 -R /lib/security/howdy 2>/dev/null || true
chmod -R u=rwX,go=rX /etc/howdy 2>/dev/null || true

# --- 4. modèles dlib -------------------------------------------------
MODEL_DIR="/usr/share/dlib-data"
if [ -f "$MODEL_DIR/dlib_face_recognition_resnet_model_v1.dat" ]; then
  log "modèles dlib déjà présents."
else
  log "téléchargement des modèles dlib…"
  install -d -m 755 "$MODEL_DIR"
  for m in dlib_face_recognition_resnet_model_v1.dat mmod_human_face_detector.dat shape_predictor_5_face_landmarks.dat; do
    if [ ! -f "$MODEL_DIR/$m" ]; then
      log "   → $m"
      if ! wget -q --timeout=90 -O "$MODEL_DIR/$m.part" \
        "https://github.com/davisking/dlib-models/raw/master/$m.bz2"; then
        log "   échec du téléchargement ($m) — howdy add s'en chargera éventuellement."
        continue
      fi
      bunzip2 -c "$MODEL_DIR/$m.part" > "$MODEL_DIR/$m" \
        && rm -f "$MODEL_DIR/$m.part"
    fi
  done
  chmod 755 "$MODEL_DIR"
fi

# --- 5. finalisation -------------------------------------------------
if ! command -v howdy >/dev/null 2>&1; then
  die "binaire ‘howdy’ introuvable après installation."
fi
log "Howdy installé : OK"
if [ -f /etc/howdy/config.ini ] && ! grep -q '^device_path' /etc/howdy/config.ini 2>/dev/null; then
  echo "device_path = /dev/video0" >> /etc/howdy/config.ini
  log "device_path ajouté dans /etc/howdy/config.ini"
fi
log "Terminé. Enregistrer un visage → ‘sudo howdy add’ (ou bouton de l'application)."
log "Journal : $LOG"
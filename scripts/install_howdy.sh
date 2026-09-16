#!/usr/bin/env bash
# Installation de Howdy (déverrouillage facial PAM) à partir du code du dépôt.
#
# Utilisation (doit être lancé en root, le GUI le fait via pkexec) :
#   sudo ./scripts/install_howdy.sh
#
# Étapes :
#   1. dépendances système (apt) : build tools, dlib, opencv, inih, python
#   2. compilation/installation de Howdy via meson
#   3. activation du module PAM (pam-auth-update)
#   4. téléchargement des modèles dlib (reconnaissance faciale) si absents
#
# Journal : ~/.cache/security-linux/howdy-install.log (pour utilisateur courant)
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$(mktemp --suffix=-howdy-install.log 2>/dev/null || echo /tmp/howdy-install.log)"
if [ -n "${SUDO_USER:-}" ] || [ -n "${PKEXEC_UID:-}" ]; then
  LOG="/home/$(getent passwd "${SUDO_USER:-${PKEXEC_UID:-root}}" | cut -d: -f6 2>/dev/null || echo nobody)/.cache/security-linux/howdy-install.log"
fi

log()  { echo "[+]\t$(date '+%H:%M:%S')  $*" | tee -a "$LOG"; }
err()  { echo "[!]\t$(date '+%H:%M:%S')  $*" | tee -a "$LOG"; }
die()  { err "$*"; echo "Échec — détails dans $LOG" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Veuillez exécuter ce script en root (sudo ou pkexec)." >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG")"

log "Installation de Howdy (build depuis le dépôt)"

# --- 1. dépendances -------------------------------------------------
log "Installation des dépendances de compilation (apt)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build meson pkg-config \
  python3-dev python3-numpy python3-opencv libopencv-dev \
  libdlib-dev python3-dlib libinih-dev \
  libboost-dev libboost-python-dev \
  wget bzip2 ca-certificates pam-auth-update \
  >/dev/null 2>&1 || true

if ! python3 -c "import dlib" >/dev/null 2>&1; then
  log "dlib Python absent après apt → compilation pip de dlib (peut être long)…"
  apt-get install -y pip >/dev/null 2>&1 || apt-get install -y python3-pip >/dev/null 2>&1 || true
  python3 -m pip install --break-system-packages dlib >/dev/null 2>&1 || die "Échec dlib (pip)."
fi
log "dlib Python : OK ($(python3 -c 'import dlib; print(dlib.__version__)' 2>/dev/null || echo version inconnue))"

# --- 2. compilation Howdy -------------------------------------------
log "Configuration meson de Howdy…"
BUILD="$REPO_DIR/.build-howdy"
rm -rf "$BUILD"
meson setup \
  --prefix=/usr \
  -Dpython_path=/usr/bin/python3 \
  -Dinstall_pam_config=true \
  -Dconfig_dir=/etc/howdy \
  -Dwith_polkit=true \
  "$BUILD" "$REPO_DIR" >>"$LOG" 2>&1 || die "meson setup a échoué"

log "Compilation (ninja)…"
ninja -C "$BUILD" >>"$LOG" 2>&1 || die "ninja a échoué"

log "Installation dans /usr…"
ninja -C "$BUILD" install >>"$LOG" 2>&1 || die "installation a échoué"

# --- 3. PAM ----------------------------------------------------------
log "Activation du module PAM (pam-auth-update)…"
pam-auth-update --enable howdy >>"$LOG" 2>&1 || true
pam-auth-update --package >>"$LOG" 2>&1 || true

chmod 755 -R /lib/security/howdy 2>/dev/null || true
chmod 755 -R /etc/howdy 2>/dev/null || true

# --- 4. modèles dlib ------------------------------------------------
MODEL_DIR="/usr/share/dlib-data"
if [ ! -f "$MODEL_DIR/dlib_face_recognition_resnet_model_v1.dat" ]; then
  log "Téléchargement des modèles dlib (reconnaissance faciale)…"
  install -d -m 755 "$MODEL_DIR"
  for m in dlib_face_recognition_resnet_model_v1.dat mmod_human_face_detector.dat shape_predictor_5_face_landmarks.dat; do
    if [ ! -f "$MODEL_DIR/$m" ]; then
      wget -q --timeout=60 -O "$MODEL_DIR/$m.part" \
        "https://github.com/davisking/dlib-models/raw/master/$m.bz2" \
        && bunzip2 -c "$MODEL_DIR/$m.part" > "$MODEL_DIR/$m" \
        && rm -f "$MODEL_DIR/$m.part" \
        || err "Échec du téléchargement du modèle $m (tentez à nouveau avec: howdy add)"
    fi
  done
  chmod 755 "$MODEL_DIR"
fi

# --- final -----------------------------------------------------------
command -v howdy >/dev/null 2>&1 && chmod +x "$(command -v howdy)" >/dev/null 2>&1
if command -v howdy >/dev/null 2>&1; then
  log "Howdy installé : OK"
  if ! grep -q '^device_path' /etc/howdy/config.ini 2>/dev/null; then
    echo "device_path = /dev/video0" >> /etc/howdy/config.ini
  fi
  log "Terminé. Pour enregistrer un visage : sudo howdy add"
  log "Journal complet : $LOG"
else
  die "Howdy introuvable après installation"
fi
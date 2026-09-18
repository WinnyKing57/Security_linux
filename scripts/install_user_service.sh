#!/usr/bin/env bash
# Installe le démon Security-Linux en service systemd --user (durci).
# Repli automatique : si systemd/logind graphique indisponible, l'autostart
# XDG reste utilisé (install.sh s'en occupe).
# Usage : ./scripts/install_user_service.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$REPO_DIR/.venv/bin"
SYSD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$SYSD_USER_DIR/security-linuxd.service"

# Binaire réel : venv (installation source) ou /usr/bin (paquet natif)
if [ -x "$BIN_DIR/security-linuxd" ]; then
  EXEC="$BIN_DIR/security-linuxd"
else
  EXEC="/usr/bin/security-linuxd"
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "==> systemd indisponible : autostart XDG utilisé."
  exit 1
fi
if [ ! -d /run/user/$(id -u) ] && [ ! -e "$XDG_RUNTIME_DIR" ]; then
  echo "==> Session graphique sans bus systemd utilisateur : autostart XDG utilisé."
  exit 1
fi

echo "==> Service systemd utilisateur (démon durci)"
mkdir -p "$SYSD_USER_DIR"
sed "s|ExecStart=/usr/bin/security-linuxd|ExecStart=${EXEC}|" \
  "$REPO_DIR/packaging/systemd/security-linuxd.service" > "$UNIT"
chmod 644 "$UNIT"

systemctl --user daemon-reload
if systemctl --user is-enabled security-linuxd.service >/dev/null 2>&1 \
   || systemctl --user enable security-linuxd.service >/dev/null 2>&1; then
  systemctl --user restart security-linuxd.service 2>/dev/null || true
  echo "    démarré : systemctl --user status security-linuxd"
else
  echo "    unité installée mais non activée (systemctl --user enable security-linuxd)"
fi
exit 0
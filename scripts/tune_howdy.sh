#!/bin/sh
# Réglage de la fiabilité de Howdy : met à jour /etc/howdy/config.ini
# (certainty = seuil, plus bas = plus strict ; use_cnn = détection CNN).
# Usage (root) : tune_howdy.sh [--certainty N] [--use-cnn | --no-use-cnn]
set -eu

INI="${HOWDY_CONFIG:-/etc/howdy/config.ini}"

CERT=""
USE_CNN=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --certainty)
      CERT="$2"
      shift 2
      ;;
    --use-cnn)
      USE_CNN=true
      shift
      ;;
    --no-use-cnn)
      USE_CNN=false
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [ ! -f "$INI" ]; then
  echo "Erreur : $INI introuvable (Howdy est-il installé ?)" >&2
  exit 1
fi

if [ -n "$CERT" ]; then
  cp -a "$INI" "$INI.bak.$(date +%s)"
fi

patch_key() {
  section="$1"
  key="$2"
  value="$3"
  if ! awk -v sec="$section" -v k="$key" -v v="$value" '
    BEGIN { cur = ""; done = 0 }
    /^[ \t]*\[[^]]+\]/ {
      cur = $0
      sub(/^[ \t]*\[/, "", cur)
      sub(/\][ \t]*$/, "", cur)
    }
    cur == sec && !done && $0 ~ "^[# \t]*" k "[ \t]*=" {
      print k " = " v
      done = 1
      next
    }
    { print }
    END { if (!done) print k " = " v }
  ' "$INI" > "$INI.tmp"; then
    echo "Erreur : patche de $key impossible" >&2
    exit 1
  fi
  mv "$INI.tmp" "$INI"
  chmod 644 "$INI"
}

if [ -n "$CERT" ]; then
  patch_key video certainty "$CERT"
  echo "=> certainty = $CERT"
fi
if [ -n "$USE_CNN" ]; then
  patch_key core use_cnn "$USE_CNN"
  echo "=> use_cnn = $USE_CNN"
fi

echo "config Howdy mise à jour :"
grep -nE '^[ \t]*(certainty|use_cnn)' "$INI" || true
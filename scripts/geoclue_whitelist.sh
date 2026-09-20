#!/bin/sh
# Active la pleine précision GeoClue pour Security Linux.
#
# GeoClue ne dévoile qu'une position "ville" (~26 km) quand aucune application
# n'est autorisée comme *agent* ; pour demander une position exacte (Exact,
# niveau 8) l'identifiant "security-linux" doit être ajouté à la liste blanche
# des agents dans /etc/geoclue/geoclue.conf ([agent] whitelist).
#
# Usage (root) : geoclue_whitelist.sh
# Idempotent : sans effet si l'identifiant est déjà présent.
set -eu

CONF="${GEOCLUE_CONF:-/etc/geoclue/geoclue.conf}"
ID="security-linux"

if [ ! -f "$CONF" ]; then
  echo "Erreur : $CONF introuvable (geoclue est-il installé ?)" >&2
  exit 1
fi

if grep -q "^[ \t]*whitelist=.*${ID}" "$CONF"; then
  echo "=> ${ID} déjà dans la liste blanche."
else
  cp -a "$CONF" "$CONF.bak.$(date +%s)"
  # "whitelist=security-linux;<liste existante>" dans la section [agent]
  if ! sed -i "s|^\\([ \t]*whitelist=\\)|\\1${ID};|" "$CONF"; then
    echo "Erreur : échec de la mise à jour de $CONF" >&2
    exit 1
  fi
  echo "=> ${ID} ajouté à la liste blanche ([agent] whitelist)."
fi

if systemctl restart geoclue.service 2>/dev/null; then
  echo "=> geoclue relancé."
else
  echo "ATTENTION : geoclue n'a pas pu être relancé (relancez-le si besoin)." >&2
fi

echo "Terminé. Recliquez maintenant sur « Utiliser ma position actuelle »."
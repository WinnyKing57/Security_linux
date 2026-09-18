#!/bin/bash
# Met à jour les catalogues de traduction (extraction + compilation).
# Nécessite xgettext, msgmerge et msgfmt (paquet gettext).
set -eu
cd "$(dirname "$0")/.."
DOT="security-linux"
SRC=$(find src/security_linux -name '*.py' | sort | tr '\n' ' ')
LOCALEDIR="src/security_linux/locales"
POT="${LOCALEDIR}/${DOT}.pot"

# Extraction
echo ">>> Extraction des chaînes..."
xgettext --from-code=UTF-8 --package-name=security-linux \
  --package-version=1.1.0-beta --add-comments= -o "$POT" $SRC

# Mise à jour de chaque catalogue .po (fusion avec msgmerge)
for PODIR in "${LOCALEDIR}"/*/; do
  LANG="${PODIR##*/}"
  PO="${PODIR}LC_MESSAGES/${DOT}.po"
  if [ -f "$PO" ]; then
    echo ">>> Fusion ${LANG}..."
    msgmerge --update --no-fuzzy-matching "$PO" "$POT"
  fi
done

# Compilation
for PODIR in "${LOCALEDIR}"/*/; do
  LANG="${PODIR##*/}"
  PO="${PODIR}LC_MESSAGES/${DOT}.po"
  MO="${PODIR}LC_MESSAGES/${DOT}.mo"
  if [ -f "$PO" ]; then
    msgfmt --check-format -o "$MO" "$PO"
    echo "    ${MO} ($(/usr/bin/stat --printf='%s' "$MO") octets)"
  fi
done

echo "Terminé."

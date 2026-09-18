#!/usr/bin/env bash
# Construction du paquet natif RPM : dist/security-linux-<version>-0.1.beta.*.rpm
# Nécessite rpmbuild (Fedora/openSUSE : "sudo dnf install rpm-build python3-devel").
# Usage : ./scripts/build_rpm.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if ! command -v rpmbuild >/dev/null; then
  echo "ERREUR : rpmbuild absent." >&2
  echo "        Sur Fedora : sudo dnf install rpm-build python3-devel" >&2
  echo "        Sur openSUSE : sudo zypper install rpm-build" >&2
  echo "        (le script est prévu pour la CI / une machine Fedora/openSUSE.)" >&2
  exit 1
fi

VERSION="$(
  python3 - <<'EOF' || true
import re
src = open("src/security_linux/version.py", encoding="utf-8").read()
v = re.search(r'__version__\s*=\s*"([^"]+)"', src).group(1)
# RPM : la version ne peut pas contenir de '-'
print(v.split("-", 1)[0])
EOF
)"
[ -n "${VERSION:-}" ] || { echo "ERREUR : version introuvable." >&2; exit 1; }

WORK=$(mktemp -d "/tmp/security-linux-rpm.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}

echo "==> Agrégation des sources (v${VERSION})"
rm -rf dist && mkdir -p dist
tar --exclude='./.venv' --exclude='./.git' --exclude='./dist' \
    --exclude='./.build-howdy' --exclude='./.pytest_cache' \
    --exclude='./src/security_linux.egg-info' \
    -C "$REPO_DIR" \
    -czf "$WORK/SOURCES/security-linux-${VERSION}.tar.gz" \
    --transform "s|^\./|security-linux-${VERSION}/|" \
    src assets config packaging/systemd packaging/security-linux.spec README.md feature.md pyproject.toml

echo "==> rpmbuild (peut être long)"
sed "s/^Version:.*/Version:        ${VERSION}/" packaging/security-linux.spec > "$WORK/SPECS/security-linux.spec"
rpmbuild -bb \
  --define "_topdir $WORK" \
  --define "_sourcedir $WORK/SOURCES" \
  "$WORK/SPECS/security-linux.spec" 2>&1 | tail -6

RPM="$(find "$WORK/RPMS" -name '*.rpm' | head -1)"
if [ -z "${RPM:-}" ]; then
  echo "ERREUR : aucun RPM produit." >&2
  exit 1
fi
cp "$RPM" dist/
echo "=== Fichier généré : dist/$(basename "$RPM") ==="
echo "Installation : sudo dnf install $(basename "$RPM")"
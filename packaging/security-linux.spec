Name:           security-linux
Version:        1.1.0
Release:        0.1.beta%{?dist}
Summary:        Verrouillage automatique d'écran et sécurité de bureau - 100% local
License:        MIT
URL:            https://github.com/WinnyKing57/Security_linux
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3-gobject, python3-dbus, gtk3, python3-opencv, bluez, bluez-tools, NetworkManager, v4l-utils
BuildRequires:  python3-devel (>= 3.10)

%description
Verrouillage automatique de l'écran quand le visage (webcam) ET l'appareil
Bluetooth disparaissent, avec localisation WiFi, mode braquage (alarme),
mode Silentium, notifications et intégration Howdy (déverrouillage facial PAM).
100 % local : aucune donnée n'est envoyée hors de la machine.

%prep
%setup -q -n security-linux-%{version}

%build
# Rien à compiler : paquet Python pur.

%install
rm -rf %{buildroot}
install -d %{buildroot}
cp -a src/security_linux %{buildroot}%{python3_sitelib}/security_linux
find %{buildroot}%{python3_sitelib} -name '__pycache__' -type d -prune -exec rm -rf {} +
find %{buildroot}%{python3_sitelib} -name '*.pyc' -delete

install -d %{buildroot}%{_bindir}
%{__install} -m 0755 /dev/stdin %{buildroot}%{_bindir}/security-linux <<'WRAP'
#!/usr/bin/python3
import sys
from security_linux.cli import main
sys.exit(main())
WRAP
%{__install} -m 0755 /dev/stdin %{buildroot}%{_bindir}/security-linuxd <<'WRAP'
#!/usr/bin/python3
import sys
from security_linux.daemon import main
sys.exit(main())
WRAP

install -d %{buildroot}%{_datadir}/icons/hicolor/256x256/apps
install -m 0644 assets/shield.png %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/security-linux.png

install -d %{buildroot}%{_datadir}/security-linux
install -m 0644 config/default.json %{buildroot}%{_datadir}/security-linux/default.json

install -d %{buildroot}%{_datadir}/applications %{buildroot}/etc/xdg/autostart
%{__install} -m 0644 /dev/stdin %{buildroot}%{_datadir}/applications/security-linux.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Security-Linux
Name[fr]=Security-Linux
Comment=Sécurité et verrouillage automatique
Comment[fr]=Sécurité et verrouillage automatique
Exec=security-linux
Icon=security-linux
Terminal=false
Categories=System;Security;
EOF
%{__install} -m 0644 /dev/stdin %{buildroot}/etc/xdg/autostart/security-linuxd.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Security-Linux
Name[fr]=Security-Linux
Comment=Verrouillage automatique et détection de présence
Exec=security-linuxd
Terminal=false
StartupNotify=true
EOF

install -d %{buildroot}%{_unitdir}
install -m 0644 packaging/systemd/security-linuxd.service %{buildroot}%{_unitdir}/security-linuxd.service

%post
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || true
fi
exit 0

%postun
if [ "$1" -ge 1 ]; then
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || true
  fi
fi
exit 0

%files
%{python3_sitelib}/security_linux
%{_bindir}/security-linux
%{_bindir}/security-linuxd
%{_datadir}/icons/hicolor/256x256/apps/security-linux.png
%{_datadir}/security-linux/default.json
%{_datadir}/applications/security-linux.desktop
/etc/xdg/autostart/security-linuxd.desktop
%{_unitdir}/security-linuxd.service
%doc README.md feature.md

%changelog
* Fri Sep 18 2026 WinnyKing57 <winnyking57@users.noreply.github.com> - 1.1.0-0.1.beta
- Paquet natif RPM (Fedora/openSUSE) pour la version 1.1.0-beta.
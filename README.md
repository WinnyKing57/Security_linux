# Security-Linux

Verrouillage automatique d'écran et sécurité de bureau — **100 % local**, aucune donnée envoyée sur Internet.

## Fonctionnalités

| Capteur | Comment ça marche | Fiable ? |
|---|---|---|
| **Webcam** | Détection de visage (OpenCV, Haar) locale. Aucun visage = absent. | Oui (si webcam disponible) |
| **Bluetooth** | Mesure la présence d'un téléphone, tablette ou montre via BlueZ local. | Oui (RSSI/local) |
| **Localisation** | Wi-Fi SSID « maison » ou GPS (optionnel). Hors domicile = réarmé. | 100 % local |
| **Howdy** | Reconnaissance faciale PAM (dormant en v1, activable manuellement). | 100 % local |
| **Code admin** | Désactivation de l'interrupteur = code requis (haché PBKDF2, jamais en clair). | Sécurisé UI |

### Règle de verrouillage

- Mode **AND** (défaut) : verrouille quand la webcam VOIT aucun visage **ET** l'appareil Bluetooth est injoignable, pendant un délai configurable (défaut 20 s).
- Mode **OR** : dès qu'un seul capteur dit « absent ».
- **Délai de grâce** (défaut 10 s) avant verrouillage pour éviter les faux positifs.
- **Réarmement automatique** : hors domicile ou hors ligne (mode « sécurisé hors-ligne » activable) → le système se réarme tout seul, même si l'interrupteur était coupé.

## Installation

```bash
git clone https://github.com/WinnyKing57/Security_linux.git
cd Security_linux
./scripts/install.sh
```

Le script **détecte automatiquement la distribution** (Debian/Ubuntu, Fedora, Arch/Manjaro, openSUSE) et installe les bons paquets système via `apt`/`dnf`/`pacman`/`zypper`. Il :

- crée un environnement Python virtuel (`.venv` dans le dossier du dépôt) ;
- installe les dépendances Python et le paquet local ;
- installe la configuration initiale (`~/.config/security-linux/config.json`, 0600) ;
- configure le démarrage automatique à la session (KDE/GNOME/XDG) ;
- supprime l'ancien fichier `lock_proximity.sh` cassé.

> Les paquets système (`python3-gi`, `python3-opencv`, `bluez`, `network-manager`, …) sont installés en option avec `sudo` ; s'ils manquent, l'application fonctionnera avec une fonctionnalité réduite (bluetooth/localisation indisponibles).

## Utilisation

```bash
# Lance l'application graphique (interrupteur + réglages)
security-linux

# Lance le démon (démarre aussi via autostart KDE)
security-linuxd

# Verrouille l'écran immédiatement
security-linux lock-now

# État courant (captures en arrière-plan)
security-linux status

# Définir le code admin
security-linux set-code
```

## États de l'application

L'état courant est publié par le démon dans `state.json` (champ `machine_state`) et affiché dans l'application.

| État | Signification |
|---|---|
| `setup` | Premier lancement : aucun code admin encore défini |
| `armed` | Protection active (interrupteur manuel) |
| `disarmed` | Protection désactivée (code admin, à la maison) |
| `armed_away` | Réarmé automatiquement car hors domicile |
| `armed_offline` | Réarmé automatiquement car hors ligne (mode « sécurisé hors-ligne ») |
| `debug` | Mode développement actif (voir ci-dessous) |

## Mode développement (débogage temporaire)

Le mode **debug** est pensé pour développer sans risque : il **n'écrit pas** dans le dossier de configuration réel et **ne verrouille jamais** l'écran pour de vrai.

```bash
# GUI + démon en mode debug (config/état isolés, verrouillage simulé)
security-linux --debug
security-linux --debug daemon --one-shot

# Scénarios injectés (aucun accès au matériel)
security-linux --debug daemon --one-shot \
    --simulate-camera=absent --simulate-bluetooth=absent --simulate-location=offline
```

- Config/état isolés dans `~/.cache/security-linux/debug/` — votre vraie configuration n'est jamais touchée.
- Verrouillage **simulé** (message sur stdout, aucune action réelle).
- Capteurs **injectables** : pas besoin de webcam ni du téléphone.
- Équivalents : variable d'environnement `SECURITY_LINUX_MODE=debug`, `SECURITY_LINUX_SIM_CAMERA`, `SECURITY_LINUX_SIM_BLUETOOTH`, `SECURITY_LINUX_SIM_LOCATION`.
- Un bandeau orange « MODE DÉVELOPPEMENT » est affiché dans la GUI tant que le mode est actif.

## Configuration

Les réglages sont dans `~/.config/security-linux/config.json` (permissions 0600).

### Appareil Bluetooth

Le premier lancement vous demandera de choisir l'appareil à surveiller parmi vos appareils appariés. Une fois choisi, le démon mesure la force du signal (RSSI) et considère l'appareil « absent » si le signal tombe en dessous du seuil (-70 dBm par défaut).

### Réseau Wi-Fi « maison »

Renseignez le ou les SSID de votre réseau domestique. Quand l'ordinateur est connecté à l'un de ces SSID → « à la maison » → comportement normal. Sinon → hors domicile → le système se réarme automatiquement.

### Mode « sécurisé hors-ligne »

Activé par défaut. Si l'ordinateur perd toute connexion réseau (WiFi + Ethernet), le système se considère comme « hors domicile » et s'arme automatiquement. Désactiver cette option via l'application ou le fichier de config.

## Sécurité

| Aspect | Détail |
|---|---|
| Détection visage | OpenCV Haar cascade, 100 % local, aucune donnée envoyée |
| Détection Bluetooth | BlueZ local (dbus/bluetoothctl), aucun réseau |
| Localisation | nmcli (SSID) — 100 % local, pas de geolocalisation externe |
| Code admin | PBKDF2-SHA256 (200 000 itérations), stocké dans `~/.config/security-linux/config.json` (0600) |
| Commandes | JSON atomiques dans `~/.local/share/security-linux/`, supprimés après lecture |
| Configuration | Fichier config.json en 0600 |
| Journal | `~/.local/share/security-linux/events.log` (0600), pas de données sensibles en clair |

### Limitations connues (v1)

1. Le code admin est vérifié au niveau de l'application (GUI), pas au niveau du système. Un utilisateur root peut contourner la protection.
2. La reconnaissance faciale n'est pas branchée au verrouillage d'écran en v1 (déverrouillage = mot de passe de session). Howdy est dormant — activable dans les réglages après enregistrement du visage.
3. Le réarmement automatique hors domicile repose sur le Wi-Fi SSID ; si vous êtes hors domicile mais connecté à un WiFi « maison » (VPN ex.), le système vous considère comme « à la maison ».

## Architecture

```
src/security_linux/
├── cli.py              # CLI (security-linux, security-linuxd)
├── daemon.py           # Démon de surveillance (boucle 1s)
├── engine.py           # Moteur de décision (AND/OR, grâce, réarmement)
├── lock.py             # Verrouillage d'écran (loginctl / qdbus)
├── config.py           # Config JSON + hachage code admin
├── events.py           # Journal + état partagé (state.json / command.json)
├── hashing.py          # PBKDF2-SHA256
├── howdy_ctrl.py       # Module dormant Howdy
├── runtime.py          # Modes production / debug (débogage temporaire)
├── monitors/
│   ├── base.py         # MonitorResult + Monitor
│   ├── camera.py       # OpenCV Haar cascade
│   ├── bluetooth.py    # BlueZ (bluetoothctl)
│   └── location.py     # nmcli (Wi-Fi) / GeoClue (GPS)
└── gui/
    └── app.py          # GTK3 (interrupteur, réglages, tray)
```

## Technologies

- Python 3.13 (système) + OpenCV 4.x (local)
- GTK3 via PyGObject (fourni par Debian)
- Bluetooth : bluetoothctl (BlueZ)
- Wi-Fi : nmcli (NetworkManager)
- Verrouillage : loginctl / qdbus (KDE Plasma)

## Compatibilité multi-appareils et vie privée

L'application est conçue pour fonctionner sur n'importe quelle machine Linux, sans aucune donnée personnelle codée en dur :

- **Aucune donnée privée** : pas de compte, pas de téléchargement, pas de MAC/SSID/nom d'appareil dans le code ou les documents ; toute l'identification (appareil Bluetooth, SSID maison, visage virtuel Howdy) est faite localement par l'utilisateur via l'application.
- **Webcam** : auto-détection `/dev/video*` si le device configuré n'existe pas.
- **Bluetooth** : l'utilisateur choisit son appareil parmi les appareils appariés lors du premier réglage (aucun appareil imposé).
- **Localisation** : fonctionne avec NetworkManager (`nmcli`) ; repli possible via `iwgetid`.
- **Verrouillage d'écran** : `loginctl` (standard), compatible KDE Plasma et GNOME ; repli `qdbus` (KDE) et `xdg-screensaver`.
- **Dépendances système** : installées automatiquement par `./scripts/install.sh` selon la distribution — Debian/Ubuntu (`apt`), Fedora (`dnf`), Arch/Manjaro (`pacman`), openSUSE (`zypper`) ; liste exacte dans le script.
- **Espaces de noms XDG** : configuration et données suivent les standards (`~/.config`, `~/.local/share`, `~/.cache`) avec des chemins en environnement respectés.

## Licence

MIT (code original de Howdy) + code ajouté sous MIT.
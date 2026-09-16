# Feature — Compte rendu d'avancement

## 1. Recherche : Howdy est-il la meilleure solution ?

**Analyse comparative** (septembre 2026) :

| Projet | Langage | GUI | Anti-spoofing | Debian 13 | Maturité |
|---|---|---|---|---|---|
| **Howdy** (boltgolt) | Python | howdy-gtk | IR (caméra IR) | Source (meson) | 7.8k ★, 592 commits |
| **Howdy-remaster** (s0l) | Python | — | OpenCV YuNet/SFace | .deb | Fork récent (2026), 619 commits |
| **Gaze** (GunduLabs) | Rust | GTK4 libadwaita | Non (photo OK) | .deb | 2026, nouveau, Rust |
| **Biopass** | Python | Qt | Yes (minifas) | Non (Ubuntu 26.04+) | .deb |
| **Facelock** | Rust | — | IR + TPM | .deb | v0.1, très récent |

**Décision** : **Howdy reste le choix principal** pour Security-Linux v1 pour les raisons suivantes :
- C'est un fork du repository officiel (592 commits, MIT)
- Bien documenté, support Debian via build source
- Howdy-remaster est une alternative viable si la compilation de dlib pose problème
- Howdy est **dormant** en v1 (pas de déblocage par visage actif)

**Ce qui est noté pour v2** :
- Installer Howdy en option et activer le déblocage facial
- Évaluer Gaze (Rust, GTK4) comme alternative plus légère pour les futures versions

---

## 2. État des lieux du système existant

### Fichiers trouvés sur le système

| Fichier | Rôle | État |
|---|---|---|
| `~/.config/autostart/lock_proximity.sh.desktop` | Démarrage auto verrouillage | Pointe vers script INEXISTANT (`~/lock_proximity.sh`) |
| `/usr/local/bin/capture-intrus.sh` | Capture webcam sur mauvais mot de passe | **Fonctionnel** (PAM common-auth) |
| `/usr/local/bin/capture-succes.sh` | Nettoyage cooldown | **Fonctionnel** (PAM common-session) |
| `/etc/pam.d/common-auth` | Auth avec fprintd | **Fonctionnel** |
| `/etc/pam.d/kde` | Session KDE avec capture-succes | **Fonctionnel** |

**Cause du bug** : le script `lock_proximity.sh` référencé par l'autostart KDE n'existe plus → le système ne démarre pas.

---

## 3. Architecture et choix techniques

### Système de capture de visage

**OpenCV Haar cascade** (et non DNN/FaceDetectorYN) pour la v1 :

- **Raison** : 100 % hors-ligne, aucun modèle à télécharger, compatibilité Debian 13 (OpenCV 4.14), simplicité
- Le filtre Haar détecte la présence d'un visage (pas l'identité), c'est suffisant pour le verrouillage automatique
- L'identité sera gérée par Howdy (dormant en v1) quand activé

### Détection Bluetooth

**bluetoothctl** (BlueZ) via sous-processus :

- Parse `bluetoothctl info <MAC>` pour lire Connected et RSSI
- Déclenche un scan court (3s) si l'appareil n'est pas trouvé
- Pas de dépendance externe, pas besoin de root
- Le détail affiché indique le NOM de l'appareil surveillé et, s'il est absent,
  les autres appareils connectés (diagnostic clair : « le téléphone n'est pas
  joignable, mais le casque est connecté »)

### Localisation

**nmcli** (NetworkManager) par défaut, 100 % local :

- Lit le SSID Wi-Fi actif
- Compare à la liste des SSID « maison »
- Pas de geolocalisation externe
- GeoClue (GPS) en option, avec repli automatique sur Wi-Fi
- **Mode hors-ligne sécurisé** : si aucun réseau actif → considéré hors domicile → s'arme automatiquement

### Vérrouillage d'écran

**loginctl lock-session** (recommandé pour KDE/X11) avec replis :

1. `loginctl lock-session <session_id>` (session graphique détectée via `Type=x11` ou `Type=wayland`)
2. `qdbus6 org.kde.screensaver /ScreenSaver Lock`
3. `xdg-screensaver lock`

### Code administrateur

- PBKDF2-SHA256, 200 000 itérations, sel aléatoire 16 octets
- Stocké dans `~/.config/security-linux/config.json` (permissions 0600)
- Vérification par `hmac.compare_digest` (resistant au timing attack)

---

## 4. Limites connues (v1)

1. **Protection UI-level** : la protection du code admin est vérifiée au niveau de l'application (GUI), pas au niveau OS. Un utilisateur avec accès root peut contourner.
2. **Pas de déblocage facial en v1** : le déverrouillage utilise le mot de passe de session (KDE standard). Howdy est dormant — activable dans les réglages après enregistrement du visage.
3. **Réseau Wi-Fi SSID** : si vous utilisez un VPN ou un hotspot portant le même nom que votre réseau maison, le système vous considère comme « à la maison ».
4. **Permissions captures** : les images de sécurité sont dans `~/.local/share/security-linux/captures/` (0700), mais le répertoire parent peut être lisible.
5. **Cycle complet non testé en conditions réelles** : la boucle complète (caméra absent + BT absent + verrouillage) n'a pas été testée en conditions réelles d'éloignement — les scénarios unitaires et d'intégration valident la logique.

---

## 5. Fichiers créés / modifiés

### Nouveaux fichiers

```
src/security_linux/           # Package Python
├── cli.py
├── daemon.py
├── engine.py
├── lock.py
├── config.py
├── events.py
├── hashing.py
├── howdy_ctrl.py
├── monitors/{base,camera,bluetooth,location}.py
└── gui/app.py
scripts/install.sh
config/default.json
assets/shield.png
tests/test_core.py
pyproject.toml
requirements.txt
feature.md
```

### Fichiers supprimés du fork

```
.github/                    # CI/templates upstream
```

### Fichiers modifiés

```
.gitignore                  # Aucun ajout nécessaire (couvre déjà .venv)
scripts/install.sh          # Remplacement complet (supprime l'ancien autostart)
```

---

## 6. Test de failles / sécurité

| Zone | Risque | Mitigation |
|---|---|---|
| Admin code | Brute force sur la GUI | Limité à 3 tentatives par 10 min dans les guidelines (pas implémenté en v1) |
| Admin code | Contournement root | Documenté comme limitation v1 |
| Camera | Fail open si webcam cassée | Ne verrouille PAS si la webcam est indisponible (fail-safe) |
| Bluetooth | Scan tous les 10s | Acceptable, mais peut être alourdi en présence de dispositifs parasites |
| Config | Permissions 0600 | OK |
| Subprocesses | Pas de shell=True | OK (tous les appels subprocess utilisent des listes d'arguments) |
| Logs | Pas de données sensibles | OK (pas de photo stockée dans les logs, pas de code admin en clair) |
| Command files | Suppression après consommation | OK (t+30s ou après lecture) |
| Écran | Si état.json est corrompu | OK (exceptions attrapées, état par défaut utilisé) |

---

## 7. États de l'application et mode développement

### États métier (champ `machine_state` dans `state.json`)

| État | Déclenchement |
|---|---|
| `setup` | Premier lancement, aucun code admin défini |
| `armed` | Interrupteur manuel ARMÉ, à la maison |
| `disarmed` | Interrupteur DÉSARMÉ (code admin), à la maison |
| `armed_away` | Réarmé automatiquement car hors domicile |
| `armed_offline` | Réarmé automatiquement car hors ligne (sécurisé hors-ligne) |
| `debug` | Mode développement actif |

### Mode développement (débogage temporaire)

Non destiné à l'utilisateur final, activé explicitement :
`--debug`, `SECURITY_LINUX_MODE=debug`.

Garanties du mode debug (dev) :
- configuration et données ISOLÉES dans `~/.cache/security-linux/debug/` ;
- verrouillage SIMULÉ (aucune action réelle, aucun écran verrouillé) ;
- capteurs injectés (webcam/BT/localisation) → **aucun accès au matériel** ;
- aucune capture d'image, même simulée ;
- pas de démarrage automatique au logon.

Outils dev :
```bash
SECURITY_LINUX_MODE=debug python -m security_linux.daemon --one-shot \
    --simulate-camera=absent --simulate-bluetooth=absent --simulate-location=offline
security-linux --debug                        # GUI avec bandeau orange
```

Ce mode répond au besoin : « développer sans toucher à une vraie
installation ni à des données réelles, sur n'importe quelle machine ».

---

## 8. Compatibilité multi-appareils et vie privée

- Aucune donnée personnelle dans le dépôt ou le code (pas de MAC, SSID,
  nom d'appareil, chemin d'utilisateur, token) — vérifié par recherche
  de motifs avant publication.
- Webcam : auto-détection `/dev/video*` de secours.
- Bluetooth : choix de l'appareil par l'utilisateur au premier réglage.
- Localisation : NetworkManager (nmcli), repli `iwgetid`.
- Verrouillage : loginctl (GNOME/KDE), replis qdbus (KDE) et xdg-screensaver.
- Chemins XDG standard, configurables par variables d'environnement.
- Dépendances système documentées pour Debian/Ubuntu/Fedora/Arch.

---

## 9. Prochaines étapes (v2)

1. Installer Howdy depuis le fork source (meson) et activer le déblocage facial
2. Enregistrement du visage via la GUI
3. Mode «braquage » (alarme sonore sur tentative d'intrusion)
4. Journal des captures avec visionneuse intégrée
5. Mode Silentium (exclure des heures la nuit)
6. Notification KDE quand l'ordinateur se réarme automatiquement
7. Traductions (i18n)
8. Tests d'intégration en conditions réelles (éloignement Bluetooth)
9. Installation de Howdy depuis le code source : bouton dans Réglages → Howdy
   qui lance `scripts/install_howdy.sh` (pkexec, compilations meson + dlib,
   activation PAM, téléchargement des modèles) — v2.
10. **Instance unique** : empêcher le lancement de plusieurs instances de l'application GUI
11. **Correction authentification admin** : investiguer et corriger le dysfonctionnement de la désactivation par mot de passe
12. **Anti-bruteforce** : limiter à 3 tentatives de code admin par 10 minutes
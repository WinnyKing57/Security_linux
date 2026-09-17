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
| Admin code | Brute force sur la GUI | Anti-bruteforce **implémenté** : 3 tentatives par 10 min (`config.py` `verify_admin_code`) |
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

### ✅ Tâches terminées :
10. **Instance unique** : empêcher le lancement de plusieurs instances de l'application GUI — **TERMINÉ** (lock file dans app.py)
11. **Correction authentification admin** : dysfonctionnement de la désactivation par mot de passe corrigé — **TERMINÉ** (méthode `_confirm_admin()`)
12. **Anti-bruteforce** : limitation à 3 tentatives de code admin par 10 minutes — **TERMINÉ** (config.py lignes 118-149)
13. **Bouton installation Howdy** : bouton dans Réglages → Howdy qui lance `scripts/install_howdy.sh` — **TERMINÉ** (lignes 636-644 app.py)
14. **Enregistrement visage via GUI** : bouton "Enregistrer mon visage" ouvrant terminal howdy — **TERMINÉ** (ligne 120, méthode `enroll_face()`)

### ✅ Tâches v0.2.0 terminées :
1. **Mode "braquage"** - alarme sonore sur tentative d'intrusion — **TERMINÉ** et **intégré** (v0.2.0 : `alerts.play_alarm()` branché dans `daemon.py::_trigger_braquage()`, en thread pour ne pas bloquer le démon)
2. **Visionneuse des captures** - bouton et interface pour visualiser/supprimer les images capturées — **TERMINÉ** (app.py show_captures())
3. **Mode Silentium** - option pour exclure des heures nocturnes du verrouillage auto — **TERMINÉ** et **intégré** (v0.2.0 : `is_silentium_active()` consulté dans `engine.py::tick()`, état publié et affiché dans la GUI)
4. **Notification KDE réarmement** - notification système quand l'ordinateur se réarme automatiquement — **TERMINÉ** et **intégré** (v0.2.0 : `send_rearm_notification()` appelé dans `daemon.py::_maybe_notify_rearm()`, interrupteur persisté)
6. **Correction instance unique (renforcée)** — **TERMINÉ** v0.2.0 : récupération des verrous orphelins (vérification PID vivant), `atexit` + signaux, plus de fallback `/tmp`, pas de troncature avant `flock`
7. **Correction désarmement admin (renforcée)** — **TERMINÉ** v0.2.0 : `on_armed_toggle` renvoie `True` sur échec (l'interrupteur ne reste plus visuellement DÉSARMÉ), config rechargée du disque avant vérification, flux sans code simplifié, `disarm`/`arm` CLI persistent la config
8. **Packaging (script d'installation multi-distro)** — **TERMINÉ** v0.2.0 : `scripts/install.sh` auto-détecte Debian/Ubuntu, Fedora, Arch/Manjaro et openSUSE (apt/dnf/pacman/zypper)

### ✅ Tâches v0.2.1 terminées (correctifs d'utilisation) :
1. **Interrupteur fiable (désarmement)** — l'interrupteur reflète la config persistée au lieu de l'état figé `state.json` → désarmer fonctionne même démon arrêté — **TERMINÉ** `_render_armed()` (app.py)
2. **Démon à l'arrêt détecté** — bannière rouge **« DÉMON À L'ARRÊT »** + bouton « Démarrer le démon » ; fraîcheur de `state.json` (ts > 12 s) — **TERMINÉ** app.py `_poll()`/`_state_is_stale()`
3. **Instance unique du démon** — verrou `flock` (+ récupération PID orphelins) empêchant deux démons en parallèle (d'où le double verrouillage d'écran observé) — **TERMINÉ** daemon.py
4. **Webcam libérée entre sondages** — plus de conflit avec l'aperçu « Réglages → Webcam » (« droits insuffisants / périphérique occupé ») — **TERMINÉ** camera.py `tick()` (`_close_cap()` en `finally`)
5. **Localisation non configurée = neutre** — SSID « maison » vide → statut « unconfigured » et AUCUN réarmement forcé (le désarmement reste effectif) — **TERMINÉ** location.py + engine.py `effective_armed()`
6. **Feedback code admin** — message clair en cas de code incorrect ou trop de tentatives — **TERMINÉ** `_confirm_admin()` (app.py)
7. **Journal enrichi** — démarrage (version, mode, webcam, BT, localisation), « réglages appliqués » avec valeurs, « moniteur X reconfiguré » au changement — **TERMINÉ** daemon.py

### ❌ Tâches restantes :
1. **Traductions (i18n)** - système complet de localisation (fichiers .po/.mo, gettext) — **À DÉFINIR** (non prioritaire)
2. **Tests Bluetooth conditions réelles** - validation en conditions réelles d'éloignement — **À FAIRE** (nécessite test physique)
3. **Paquets natifs .deb/.rpm (optionnel)** - désormais facilité par le script d'installation ; des paquets natifs via CI restent possibles — **À FAIRE** (optionnel)
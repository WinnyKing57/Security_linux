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

### ✅ Tâches v1.0.0-beta terminées :
1. **Traductions (i18n)** — gettext complet : `i18n.py`, domain `security-linux`, catalogues embarqués `fr`/`en` (.po/.mo), surcharge via `SECURITY_LINUX_LANG`, script `scripts/i18n_update.sh` — **TERMINÉ** (toutes les chaînes GUI/CLI/démon enveloppées `_()`)
2. **Bug verrouillage auto (elapsed)** — la condition « absent depuis N s » n'était jamais satisfaite en réel (`absent_elapsed_seconds` jamais fourni par les vrais moniteurs) → le verrouillage après 30 s sans visage ne se déclenchait jamais — **CORRIGÉ** (`monitors/camera.py`, `monitors/bluetooth.py` remplissent `extra`, `engine._absent_elapsed()` robuste + repli `absent_since`), tests de régression
3. **Bug GUI webcam** — `for _ in range(...)` écrasait la fonction gettext `_()` → « 'int' object is not callable » — **CORRIGÉ** (variable `_frame`), test de régression
4. **Voyant webcam (LED)** — petit point flottant toujours au-dessus des fenêtres, rouge à chaque lecture d'image, déplaçable à la souris, position mémorisée (`led.py`, `gui/led.py`, réglages « Fonctions avancées ») — **TERMINÉ**
5. **Isolation des tests** — `tests/conftest.py` force le mode debug : plus aucun `pytest` ne touche la config utilisateur de production — **TERMINÉ** (corrige l'écrasement de la config/admin-code par les tests)
6. **Désarmement admin** — rechargement de la config après vérification admin (ne récrit plus un compteur de tentatives périmé) — **CORRIGÉ** (`on_armed_toggle`)

### ✅ Tâches v1.1.0-beta terminées (durcissement sécurité + packaging) :
1. **Canal de commande authentifié (HMAC)** — `command.json` est signé HMAC-SHA256 avec une clé de session (0600) ; le démon **rejette** toute commande mal signée et journalise la tentative — **TERMINÉ** (`events.py` `_load_session_key`/`_sign`/`consume_command`)
2. **Capteur armé indisponible = teinte anti-évasion** — un moniteur `present` qui passe `unavailable` pendant l'armement déclenche un événement de sécurité, une notification critique et, si le mode braquage est actif + `on_tamper`, une alarme — **TERMINÉ** (`engine._detect_tamper`, `daemon._handle_tamper`, réglage GUI « Alarme si un capteur armé disparaît »)
3. **Fallback GNOME natif** — `org.gnome.ScreenSaver.Lock` via `gdbus` ajouté à la chaîne de verrouillage (loginctl → KDE qdbus → GNOME gdbus → xdg-screensaver) — **TERMINÉ** (`lock.py`)
4. **Code admin : longueur minimale 6** — validation centralisée `config.valid_admin_code()`, GUI et CLI alignés, `set_admin_code()` refuse tout code trop court — **TERMINÉ**
5. **Journal : rotation + chaîne d'intégrité** — rotation auto par taille (1 Mio, 5 archives) + chaque ligne porte le SHA-256 de la précédente (`chain`) ; `verify_event_log()` détecte toute édition/suppression — **TERMINÉ** (`events.py`)
6. **Démon en service `systemd --user` durci** — unité avec `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths` ciblés… (`packaging/systemd/security-linuxd.service`, `scripts/install_user_service.sh`, install.sh privilégiant systemd puis repli autostart) — **TERMINÉ**
7. **Verrouillage de repli par inactivité** — filet de sécurité indépendant des capteurs (seuil `general.idle_lock_minutes`, 0 = désactivé, mesure KDE `GetSessionIdleTime` / GNOME Mutter `GetIdletime`) — **TERMINÉ** (`idle.py`, `engine.py`, réglage GUI)
8. **Paquets natifs .deb/.rpm** — `scripts/build_deb.sh` (dpkg-deb+fakeroot, vérifié localement) ; `packaging/security-linux.spec` + `scripts/build_rpm.sh` (CI Fedora) ; workflow GitHub Actions `package.yml` (tag `v*` → .deb/.rpm attachés à la release) — **TERMINÉ**
9. **Vérification du visage (test de passage)** — enregistrement d'une photo de référence puis comparaison avec le visage devant la caméra (score de similarité 0..1, seuil configurable) — **TERMINÉ** (`faces.py`, GUI « Réglages → Webcam », CLI `face-save`/`face-check`)

### ✅ Tâches v1.1.0-beta terminées (passage sur le matériel réel) :
1. **Compte rendu d'intrusion consolidé** — à chaque déclenchement du mode braquage ou tampering : capture + état machine + snapshot config/moniteurs + dernière minute de journal dans un rapport horodaté unique `data_dir()/reports/intrusion_*.json` (répertoire 0700, fichier 0600, **aucun secret** admin_code/SSID) — **TERMINÉ** (`report.py`, `daemon._trigger_braquage`/`_handle_tamper`, GUI bouton « Rapports »)
2. **Argon2id pour le code admin** — Argon2id (argon2-cffi, paramètres OWASP 2023) par défaut quand le module est installé, repli PBKDF2-SHA256 200k en stdlib ; **migration automatique** d'un hash PBKDF2 existant à la première vérification réussie ; extra `pip install security-linux[security]` — **TERMINÉ** (`hashing.py`, `config.py`, champ `admin_code.alg`)
3. **Commandes CLI `bluetooth-test`** — échantillonnage RSSI/connectivité sur le matériel réel (durée/intervalle paramétrables, écriture CSV `--out`) et synthèse : %, RSSI min/moy/max, **seuil `min_rssi` conseillé** pour la surveillance réelle — **TERMINÉ** (`cli.py`)
4. **État du voyant webcam publié dans state.json** — le démon publie `led_active` / `led_last_ts` (dernier clignotement < 60 s), affiché par la GUI (ligne « Voyant webcam ») et `security-linux status` — **TERMINÉ** (`daemon._publish`)
5. **Corrections GUI d'utilisation** — message double trompeur lors de l'installation de Howdy supprimé ; interrupteur Howdy verrouillé « dormant en v1 » ; seuil de correspondance du visage réglable dans « Réglages → Webcam » (0.1–0.9, persisté) ; compteur exact de captures à la suppression ; crash au redémarrage sans icône de tray corrigé — **TERMINÉ** (`app.py`, `faces.py`, `config.py`)

### ✅ Corrections du 20/09 (vie privée & diagnostic) :
1. **Webcam coupée quand DÉSARMÉ** — le démon ne lit plus la webcam tant que le système n'est pas armé (pas d'ouverture de `/dev/video*`, voyant éteint) ; le Bluetooth et la localisation restent surveillés (réarmement automatique) — `daemon.py` `_poll_monitors()` + `effective_armed()`
2. **Interrupteur « Démarrer au démarrage de session »** — GUI (Réglages → Fonctions avancées) + CLI `security-linux autostart on|off` ; géré via le service `systemd --user` quand installé, sinon par l'entrée autostart XDG — nouveau module `autostart.py`
3. **Traceback complète dans events.log** — les erreurs « boucle principale » et « moniteur X » journalisent désormais la stack complète (diagnostic du crash « str object is not callable ») — `daemon.py`

### ❌ Tâches restantes (v1.1.0-beta et au-delà) :
1. **Tests Bluetooth conditions réelles** — validation en conditions réelles d'éloignement — **À FAIRE** (nécessite test physique ; commande `bluetooth-test` disponible pour relever les RSSI réels)
2. **2FA réel une fois Howdy activé** — exiger visage **ET** code/mot de passe pour désarmer (aujourd'hui Howdy est dormant ; à l'activation il remplacerait le PAM, pas un facteur additionnel) — **À FAIRE** (nécessite Howdy actif)
3. **Export/rotation chiffrée des journaux et captures** — consultation a posteriori en cas d'incident réel — **À FAIRE**
4. **Verrou physique / capteur de proximité complémentaire** (couvercle webcam, dépend du matériel) — **À ÉTUDIER**
5. **Voyant quand la GUI est fermée** — **RÉSOLU (vie privée)** : le démon ne sonde plus jamais la webcam quand le système est désarmé → LED physique éteinte ; en état armé, l'état `led_active` / `led_last_ts` est déjà publié dans `state.json`
6. **Crash « boucle principale : 'str' object is not callable »** (20/09, erreur répétée chaque cycle pendant l'armement) — **À DIAGNOSTIQUER** : la stack complète est désormais journalisée ; reproduire en ré-armant pour corriger la cause exacte
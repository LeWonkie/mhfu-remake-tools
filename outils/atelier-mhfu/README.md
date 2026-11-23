# Atelier MHFU (PSPRecomp)

Essais JSON, interface lue en mémoire et outils de rétro-ingénierie : [README_RETRO.md](README_RETRO.md).

Lancer : `MHFU REMAKE\Atelier MHFU (PSPRecomp).bat`. Données de travail : `MHFU REMAKE\atelier-psprecomp\` (hors Git).

## Reprise de la campagne générale (22/10/2026)

`atelier_pilot.py --plan --plan-out DOSSIER_NEUF` prépare sans lancer le jeu le rapprochement
du disque avec les identifiants admis par `quest start` : 603 missions cataloguées,
549 IDs natifs et **548 candidates au repérage technique**, en 28 lots de 20 (dernier : 8).
Trois missions vides et 52 missions sans admission native sont exclues ; leurs motifs et
les empreintes des sources restent dans le plan. Les objectifs spéciaux restent bruts.
`--all --limit N` limite le prochain essai aux N premières candidates ; `--resume DOSSIER`
reprend son propre plan. L'admission ne prouve ni le chargement ni une victoire.

Le retour au village relit l'état après `quest results` avant d'envoyer ○ pour éviter
d'ouvrir le marchand en arrivant à Pokke. Si un dialogue bloque encore le village,
le pilote peut le fermer par × après reconnaissance répétée de la scène 3 : six pressions
au maximum, puis disponibilité réellement relue (`scene == 0`). Essai réel 1 puis 1213,
campagne `20261022-024122` : deux retours au village prêt, captures examinées, sans recours
au secours par ×. Les sauvegardes originales et le seed sont restés inchangés.

Premier lot réel : 20 quêtes parcourues en 216,69 s, 100 captures présentes et examinées,
aucun blocage de retour. La lecture des états distingue 19 échecs (5) et une réussite
signalée (3, quête 1001) : celle-ci était mal étiquetée `forced_end` par l'ancien pilote.
Le pilote corrigé réserve `forced_end` à l'état 5 après réglage confirmé du chronomètre ;
`reported_clear` conserve l'état 3, et les autres fins ne valident pas le repérage.
Essai de confirmation 1001 puis 1213 : respectivement `reported_clear` et `forced_end`,
deux villages prêts, sauvegardes inchangées. Ce sont des essais avec aides de debug.
Voir `COMPTE_RENDU_CODEX_2026-10-22_campagne_lot1.md` à la racine pour les preuves et limites.

La revue `review_campaign.py` adapte maintenant les planches aux cinq captures du mode
rapide ou aux sept du mode détaillé, dont `fin2`. Elle ne déclare plus manquantes les
phases non demandées ; une capture effectivement demandée et absente reste signalée.
Les fichiers supplémentaires restent audités.

Suite Python revérifiée le 22/10 après ces correctifs : **254 tests réussis**. La sonde locale
`work/probe_step6_batch.py --batch N --seconds 600` permet un lot borné avec arrêt garanti,
contrôle des empreintes et dossier de preuves neuf. Voir [README_REPRISE.md](README_REPRISE.md).

## Objectifs depuis le disque (21/10/2026, reprise Codex)

`deps\venv\Scripts\python.exe outils\atelier-mhfu\derive_objectives.py --disc game-mhfu\disc.iso`
reconstruit atomiquement `quest_objectives_disc.json`, sans lancer le jeu ni modifier l'ISO.
Les 603 missions du catalogue ont leurs deux descripteurs (type, cible, quantité brute), leur titre
et leur provenance. Les livraisons donnent les IDs et quantités d'objets. Les types spéciaux
restent bruts : leur champ final ne doit pas être interprété comme un nombre de monstres.

Le contrôleur charge, dans cet ordre : objectifs validés en jeu, relevés des campagnes, puis
objectifs du disque. Le dernier registre ajoute 401 objectifs ordinaires de chasse/abattage ;
161 livraisons, 38 objectifs spéciaux et 3 quêtes vides sont exclus de la chasse automatique.
Les deux espèces d'une chasse double sont conservées ; aucun compteur unique n'est proposé
pour ces missions. Pour un objectif simple, le compteur n'est utilisé que si le relevé initial
en RAM correspond exactement au descripteur du fichier. Le résultat reste l'état de mission du jeu.

Ces objectifs portent `verified: false` : lecture du fichier vérifiée, réussite en jeu non établie.
Les zones de monstres et les circuits de patrouille ne sont pas encore déduits. Les 17 anciens
relevés de campagne concordent (espèces, quantités et drapeaux de compteur). Les anciennes
commandes de `derive_objectives.py` avec des dossiers de campagne restent disponibles.

## Capture représentative de l'étape 5

`deps\venv\Scripts\python.exe work\probe_capture_v2.py` (depuis la racine) réalise un essai
borné de 240 secondes sur 1020 et une copie du seed : Shock Trap, vraies utilisations de Tranq Bombs,
placements et vie de debug. Capture réussie deux fois le 21/10 ; second essai
`20261021-231236-9a1ce4` : mission 3, boss vivant à 1 PV, objectif restant zéro, retour à Pokke
et village prêt. Captures de réussite et de retour examinées. Les sauvegardes originales et le seed
sont inchangés (SHA-256). Cela valide une capture assistée, pas un combat sans triches.
Voir `COMPTE_RENDU_CODEX_2026-10-21_objectifs_et_capture.md` pour les preuves et limites.

## Apparence de l'équipement corrigée

L'ordre natif des pièces portées est arme, jambes, tête, torse, bras, taille. Le mauvais ordre
utilisé auparavant par `equip` et `loadout best` produisait une apparence mélangée malgré des noms
et statistiques plausibles. Le correctif du 21/10 est compilé et vérifié sur le personnage masculin
au village : cinq bons modèles White Fatalis chargés, banques de textures retrouvées en RAM,
capture examinée. Les modèles sont toujours mis en cache ; une commande envoyée après chargement
ne garantit pas leur rechargement immédiat. Détails : `COMPTE_RENDU_CODEX_2026-10-21_armures.md`.

## Ce que fait l'atelier
- **Préparer** : copie privée d'`EBOOT.ELF` et `disc.iso` (une fois) et un point de départ de sauvegardes (`seeds\`). Les originaux ne sont jamais écrits.
- **Essai** : chaque essai (`runs\<id>\`) reçoit sa propre copie des sauvegardes, un `settings.ini` privé, un environnement purgé (HLE strict, page Debug coupée), une échéance (1..1800 s), des captures automatiques (~10 s), un manifeste (`manifest.json` : scénario, variante, commit, SHA de l'exécutable, empreintes des données et du départ, état, motif d'arrêt) et un rapport `rapport.html`.
- **Commandes en direct** : boutons manette/marche/capture ; seules `pad`, `axis`, `shot`, `quit` (et les raccourcis `move`, `screenshot`) sont acceptées.
- **Signaler un bug** : capture demandée au jeu + manifeste, réglages, journaux filtrés, dans `bugs\<id>\` et un ZIP. L'état GE synchronisé n'est PAS inclus (non implémenté).
- **Figer les sauvegardes** : nouveau point de départ depuis le dernier essai terminé.
- **Comparer** deux essais, ou **A/B automatique** d'une variante de rendu ; une seule mesure ne prouve pas un gain.
- **Scénarios** : `scenarios\` (fournis) et `atelier-psprecomp\scenarios\` (les tiens) ; enregistrement depuis les actions réellement exécutées par le jeu (pas celles de la manette physique).
- **Compiler / Compiler + tests** : `cmake --build ... -j2` et 7 tests C++ du rendu.

## Quêtes directes et campagnes (MHFU)
- Le jeu compilé (`out\mhfu-probe`) reconnaît MHFU à l'empreinte de son exécutable. Les outils Debug de MHP3rd y sont coupés, et des commandes propres à MHFU les remplacent (voir `psprecomp-mhfu\docs\MHFU_TOOLS.md`) : `state`, `quest start ID`, `quest`, `quest time S`, `monsterhp N`, `hold health|onehit|timer on|off`.
- **Session manuelle** : depuis le village, « Partir en quête » (n° de quête, ex. 1001) part directement en quête, sans passer par le comptoir ni le portail.
- **Onglet Campagnes** (ou `python atelier_campaign.py 1001 1201 …`) : enchaîne automatiquement les quêtes. Pour chacune : départ, captures (arrivée, marche, pendant, fin, retour au village), état du chasseur et des monstres, fin forcée par le chronomètre, puis retour au village. Un plantage ou un blocage est noté, et le jeu est relancé pour la quête suivante. Rapport dans `atelier-psprecomp\campaigns\<date>\campagne.html` et `campagne.json` : temps de chargement, zone, fonctions interprétées (non précompilées). Les images ne sont pas jugées : `review_campaign.py` produit seulement des alertes heuristiques, qui demandent une revue humaine.

## Reproductibilité, métadonnées et revue (21/10/2026)
- **Options de campagne** : `atelier_campaign.py QUÊTES… [--seed ID] [--look S] [--variant NOM] [--rtc-us N]`. Les variantes (`atelier_core.VARIANTS`) sont appliquées après la purge des `MHP3RD_*` hérités : une variable posée dans le shell parent n'a aucun effet. Variantes de mesure : `space_trace` (`MHP3RD_TRACE_FRAME_SPACE=16`), `space_flow` (plus `MHP3RD_TRACE_GE_FLOW=1`), `vb128_trace`, `vb256_trace`.
- **`--rtc-us N`** (946684800000000..4102444800000000) compose `<variante>_rtc_<N>` avec `MHP3RD_DEVICE_TEST=1` et `MHP3RD_DEVICE_TEST_RTC_US=N`. Cela fixe l'époque de démarrage seulement : le temps émulé avance ensuite. `MHP3RD_DEVICE_TEST=1` ajoute aussi des appels à `debug::frame` (menu/interface `ui/layer.cpp`, boucle photo `hle_media.cpp`). Ceux-ci traitent les commandes en attente, la console et les effets maintenus, ce qui peut décaler le moment des commandes. Une époque fixe ne garantit donc pas une séquence identique. La confirmation réelle est la ligne `[device-test] RTC epoch N us` du journal (`rtc_epoch_confirmed`).
- **Manifeste d'essai** : variante, `rtc_us`, `rtc_epoch_confirmed`, seed et empreintes, `window_size`/`window_sizes` (captures nommées seulement ; les `frame_N.bmp` sont l'image interne avant mise à l'échelle), `quest_results`.
- **`campagne.json`** : écrit atomiquement au lancement, après chaque quête et à l'arrêt (`state` : running/completed/interrupted/failed). Par quête : zone effective lue après chargement (`stage`, `stage_observed_at`) et dimensions de la capture d'arrivée. Un rapport interrompu reste lisible.
- **Revue** : `review_campaign.py --campaign <dossier> --out <nouveau dossier>` (ou `--run`). Le script écrit seulement dans un dossier neuf hors des sources et refuse d'écraser. Il audite toutes les captures et produit HTML, JSON et Markdown :
  - `near_black` : luminance moyenne ≤ 8/255 ;
  - `dark` : au-dessus, jusqu'à 20/255 ;
  - `near_uniform` : écart-type RGB maximal ≤ 3/255 ;
  - `vertex_saturation` : messages `ran out of vertex space` ou `[framespace]` avec abandons, dans l'intervalle de journal qui précède la capture.

  Les captures nommées sont délimitées par `[script] … shot`, les automatiques par `[render] frame N … -> frame_N.bmp`. Les seuils se règlent avec `--black-mean`, `--dark-mean` et `--uniform-stddev`.
- **Faux positifs connus** : écrans de chargement et fondus (quasi noirs), scènes de nuit (`dark`). Un intervalle relie une saturation au voisinage de la capture, pas à l'image exacte. L'absence de saturation ne prouve pas un rendu correct.
- **`probe_rtc_11001.py`** : 11001 seule, quatre époques, deux passages, avec contrôle des empreintes des originaux, des seeds et des anciens manifestes avant et après.

## Zones et chasse automatiques (21/10/2026)
- **`atelier_zones.py`** lit dans le jeu (`peek`), avec des adresses relues en jeu avant usage :
  - `StageManager` (`0x09A4ADA0`) → objet zone → vtable (au début de l'objet) ;
  - `definitions()` (entrée `0x28`, petite fonction `lui`/`addiu` décodée) → table des sorties (`+0x30`, nombre à `+0x3D`, 0x34 octets chacune : zone de destination, forme, volume) ;
  - à défaut, `exits()`/`exit_count()` redéfinies (entrées `0x2C`/`0x30`) ;
  - chasseur : position `+0x200`, position suivante `+0x210`, zone `+0x29A` ;
  - monstres : `EnemyManager` `+0x1220`, vie `+0x2E4`.

  Pour changer de zone, il place le chasseur dans le volume d'une sortie (plusieurs points essayés), puis le test de sortie du jeu fait le reste. Seule la mémoire de l'essai est écrite.
  Les deux vecteurs de position sont envoyés dans un seul lot de commandes, afin de ne pas écrire la suite d'un déplacement après le chargement de la zone suivante. Une lecture de zone est rejetée si `GameSys`, `StageManager`, le chasseur ou l'objet de zone changent pendant la lecture. Une tentative est arrêtée si elle mène à une autre zone que celle attendue.
- **`zone_tour.py QUÊTES… --seed ID --out DOSSIER`** : un essai neuf par quête, avec la vie tenue. Le script capture chaque zone une fois, puis va vers une zone non visitée (plus court chemin connu). Une sortie qui échoue est notée (`failed_exits`) et contournée. Il écrit `tour.json`, et `tour_sheet.py DOSSIER` produit les planches et `tour-summary.json` (luminance, saturations).
- **Triches de test et meilleur équipement** (commandes du jeu, détail dans `psprecomp-mhfu\docs\MHFU_TOOLS.md`) : `hold health|stamina|sharpness|onehit|timer|items on|off`, `money N`, `pouch [ID N]`, `equip TYPE ID`, `loadout best` (Ukanlos Soul Hatchet, White Fatalis Z et un kit de sacoche ; au village seulement). Onglet Pilotage : cases « Triches », boutons « Meilleur équipement » et « Argent max ». En quête, l'écran Statut lit alors attaque 518 et défense 531 ; l'apparence du chasseur reste l'ancienne (modèles chargés avec le personnage). Les chasses (`hunt.py`) équipent `loadout best` avant le départ et tiennent vie, endurance, tranchant et `onehit` ; `--loadout none` et `--cheats …` (liste vide possible) gardent l'équipement de la sauvegarde ou retirent les triches. `hunt.json` enregistre `loadout`, `loadout_answer`, `cheats` et `cheats_held` : un essai avec triches ne vaut pas comme preuve de jeu normal.
- **`hunt.py QUÊTES… --seed ID`** : la vie et `onehit` sont tenus. Le chasseur est placé à côté d'un monstre de sa zone, puis Triangle/Rond sont pressés. Les monstres ailleurs sont rejoints par les sorties. Il écrit `hunt.json` dans l'essai.
  **Fin de quête** : l'état de mission `GameSys +0x2E` (octet testé par `GameSys::Game_clear_ck`, `0x0884F9A0`) est relu au début de chaque tour, sans être modifié. 0–2 : quête en cours ; 3 : objectif rempli (écrit par le vérificateur de mission `0x0886E970` avec l'événement de réussite) ; 5 : échec (`0x0886E924`) ; 4, 6, 7, 8 : autres fins, non interprétées. Dès un état 3 à 8, plus aucun déplacement ni attaque : captures `h<quête>-fin`, `-banniere`, `-recompense`, puis passage borné (150 s) des écrans de fin jusqu'au village (`-village`). États `quest_cleared` (3, `success: true`), `quest_failed` (5, `success: false`) ou `quest ended` (autre code, `success: null`) ; transitions dans `mission_states`.
  Validé le 21/10/2026 sur 11001 (essai `20261021-070113-c04319`) : passage 2 → 3 à 178 s, capture `h11001-fin` montrant « Objective cleared! », retour au village de Pokke. Échec par chronomètre confirmé dans `20261021-071206-c688c2` : état 2 avant `quest time 2`, puis 5, capture `h11001-fin` montrant « Time's up! », `success: false` et retour à Pokke observé. Aucun déplacement ni attaque dans cet essai après le déclenchement.
  Les morts comptées (`kills`) sont un suivi indicatif et prudent : une mort compte seulement si elle est vue sur deux lectures successives, dans la zone du chasseur, pour le même acteur. Ce compteur sous-estime (11 pour les 20 Bullfangos de l'essai validé) ; seul l'état de mission décide de la fin.
  **Progression de l’objectif du jeu** : pour les objectifs 11001 et 21001 enregistrés dans `quest_objectives.json`, lecture seule des deux descripteurs de mission à `Quest +0x728` et de leurs copies initiales à `+0x738` (entrées de 8 octets : drapeaux u32, espèce u16, quantité restante s16). Le pointeur Quest, GameSys et l’identifiant de quête sont vérifiés pendant la lecture. L’espèce, la quantité initiale et les drapeaux doivent correspondre au format validé ; les missions à plusieurs objectifs et les autres formats ne sont pas interprétés.
  `objective_progress` et `objective_history` conservent les quantités accomplies/restantes, l’objectif et le moment du relevé. La progression est affichée dans l’interface et les rapports, séparément des morts observées. Une nouvelle quantité accomplie remet à jour la garde contre l’absence de progrès, même si aucune mort n’a pu être suivie. Si une lecture échoue, le dernier relevé est conservé et marqué indisponible (`objective_progress_available: false`) ; l’interface le désigne comme « dernier relevé du jeu ». Une quantité restante nulle ne décide jamais de la victoire.
  Validation du compteur : `20261021-073523-77e8b5`, 11001, captures Quest Info à 0/20 puis 10/20, quantités restantes 20 puis 10 (5 morts seulement observées). Cet essai est arrêté volontairement à mi-parcours. Sur 21001, `20261021-073805-040374` : quantité 1 → 0, drapeaux `0x211` → `0x231`, bannière « Objective cleared! », état de mission 3 et retour à Pokke.
  Les sorties en échec sont exclues des prochains itinéraires. Une arrivée suivie d'un retour après cinq secondes est notée `transient` et contournée ; elle ne valide pas le passage. Un chargement pendant une sortie est relu au tour suivant au lieu d'arrêter l'essai.
  `--routes CHEMIN/tour.json` utilise les zones d'un tour antérieur comme indications de trajet pour rejoindre plus directement un monstre distant. Ce fichier n'est pas une preuve de passage : les sorties sont relues en jeu à chaque zone et les échecs priment sur ces indications. Sans route connue ni zone à explorer, le script s'arrête au lieu de boucler sur la première sortie.
  **Vérifier d'abord l'objectif dans le menu du jeu.** `quest_targets()` lit les fiches des gros monstres, pas forcément l'objectif de la mission : 11001 demande 20 Bullfangos, alors que cette liste indique l'espèce 68 avec un compteur qui reste à 1 après sa mort. `--species 5` limite strictement les attaques aux Bullfangos (identifiant vérifié dans `EmType` du décompilateur local et par les lectures en zone 71) ; `--patrol ZONES…` revisite explicitement des zones pour les petits monstres qui réapparaissent. L'arrêt reste borné par `--seconds` et par l'échéance de l'essai.
- **`probe_hunt_transition.py [--walk]`** : sonde bornée de 11001, même seed et RTC que la passation. Mesure 68 → 67 → 44 → 16 → 72 ; `--walk` étalonne le stick en zone 16 et franchit la dernière sortie en marchant. Chaque lancement crée un essai neuf avec `transition_probe.json`.
- **`probe_hunt_failure.py [11001|21001] [--seed ID]`** : nouvelle copie privée, RTC `1609459200123456`, contrôleur de chasse réel avec chronomètre ramené à deux secondes avant toute attaque. Écrit `failure_probe.json` (état avant et réponse du chronomètre) et les sorties habituelles de chasse. Ne modifie jamais l'octet de mission. Code de sortie 0 seulement si le contrôleur observe un échec et revient au village ; examiner les captures pour la preuve visuelle.
- **`probe_hunt_objective.py [11001|21001] [--partial]`** : observe les descripteurs de mission bruts dans `objective_probe.json`, avec captures du menu Quest Info. `--partial` demande l’arrêt après la capture intermédiaire (à la moitié de l’objectif), sans déclarer une victoire. Le menu garde sa sélection Quest Info après la première capture. Examiner les captures avant de valider un nouveau compteur ; aucune écriture dans les descripteurs.
- **Arrêter une chasse** : Stop annule aussi le démarrage et reste disponible entre deux sessions. Une annulation avant le village est classée `cancelled`, une fermeture du jeu `runtime_stopped`, un délai dépassé avec jeu encore actif `start_failed`. Un arrêt demandé conserve son motif dans le bandeau, y compris si le jeu quitte avec un code anormal ; le manifeste conserve ce code.
  L’annulation est aussi relue après les mises à jour de progression et après un placement, avant d’envoyer l’attaque suivante.
- **Limites** :
  - certaines sorties ne se déclenchent pas avec ces points (escalade, conditions particulières) ;
  - une destination ≥ `0x1000` est un indicateur, pas une zone ;
  - le côté d'attaque est essayé à tour de rôle, sans connaître l'orientation du chasseur ;
  - les codes 3 (réussite) et 5 (échec par chronomètre) sont validés en jeu ; les autres fins restent non interprétées. Les autres causes d'échec n'ont pas été provoquées.

## Pilote rapide, cache et benchmark (21/10/2026)
- **Onglet Campagnes** : utilise désormais `atelier_pilot.run_campaign`, avec le point de départ courant figé au lancement et le RTC `1609459200123456`. Choix de la vitesse demandée (1..8, ×8 par défaut), des captures essentielles ou détaillées et de la durée d'observation (1..120 secondes de jeu). « Tout revérifier » ignore les validations en cache pour ce lancement. Stop annule la campagne ; le rapport conserve les preuves des validations réutilisées. Les chasses de l'interface restent en temps réel. La reprise d'une campagne reste disponible par `atelier_pilot.py --resume DOSSIER`.
- **`atelier_pilot.py QUÊTES… --seed ID --rtc-us N`** : repérage piloté par l'état du jeu (village prêt + fin de chargement `[load] fast`, départ confirmé et renvoyé s'il est abandonné, zone cohérente et chronomètre en marche, état de mission, retour), avance rapide du moteur (`--speed 1..8`, ×8 par défaut ; ×4 à ×5,5 obtenus en quête), captures planifiées en images de jeu et confirmées (`--captures fast|detailed`). Issue `forced_end` = fin par le chronomètre signalée par le jeu, jamais un objectif rempli. `--all` (548 candidates non vides du disque admises dans les sources natives), `--limit N`, `--plan`, `--resume DOSSIER`, `--force`, `--no-cache`.
- **Cache** (`atelier_ledger.py`) : une validation `passed` n'est réutilisée qu'avec la même clé complète (exécutable, overlays, données, seed, RTC, réglages, pilote, scénario) et pointe vers sa preuve d'origine ; `atelier_ledger.py forget survey QUÊTES…` force une nouvelle vérification ; `atelier_ledger.py coverage` : couverture par quête et par famille (une famille ne valide aucune quête non exécutée).
- **Benchmark** : `bench_atelier.py --mode legacy|fast --repeat N` (temps réel et émulé par phase, CPU du pilote). Mesuré : 4 quêtes en 331 s (ancien pilote) contre 53 s (pilote rapide).
- **`hunt.py --speed 8`** : avance rapide vérifiée en jeu sur 21001, 1004, 1006 et 11101, avec état de réussite, compteurs du jeu et captures de fin/retour examinées. `--speed 1` reste le défaut. Les abattages sans sortie attendent les vagues suivantes après progression partielle, sous les mêmes limites de temps et d'annulation.
- **Objectifs déduits** : les livraisons de Paw Pass Ticket restent des livraisons, même avec un gros monstre listé (menu de 2002 vérifié). Elles sont exclues de `hunt.py`. Les campagnes ont fourni 17 objectifs déduits, désormais complétés par le registre du disque décrit en début de document ; la liste des monstres ne définit jamais l'objectif.
- **Blocages de départ/chargement** : captures `q<quête>-depart-bloque` et `q<quête>-chargement-bloque`. `start_timeout` distingue une commande acceptée sans transition d'un refus (`start_refused`). Le premier départ peut encore rester figé au village ; cause non établie.
- **`deliver.py QUÊTE --seed ID --speed 8 --seconds 180`** : vérifie une livraison ordinaire au camp avec objets fournis par `pouch` et placement de test devant la boîte rouge (lue dans la table du camp). Attend la détection de la boîte, envoie ○, lit le résultat et revient au village. Écrit `delivery.json` et les captures ; délais et annulation bornés. Le correctif natif de `pouch` est nécessaire : pendant une quête, la commande doit écrire la sacoche active de GameSys `+0x6AEA6`, pas celle de sauvegarde. Livraisons 10001 (5 Mountain Herbs) et 2002 (1 Paw Pass Ticket) réussies et revenues, captures examinées. Ces essais avec objets fournis ne prouvent pas une récolte et ne remplissent pas le cache global de gameplay. Une capture assistée de monstre a ensuite été reproduite sur 1020 avec retour au village (voir le début de ce document).
- Validations et limites actualisées : `COMPTE_RENDU_CODEX_2026-10-21_validation_pilote_rapide.md`.
- Détail et limites : `PASSATION_CODEX_PILOTE_RAPIDE_2026-10-21.md`.

## Vérifications faites le 20/10/2026
- 19 tests Python (`tests\`) : contrat du moteur (isolation, environnement, arrêt, échéance, bornes, validation, enregistrement) et rapports.
- Vrai jeu : scénario « Démarrage et menus » jusqu'au GAME MENU, capture et rapport ; session manuelle avec commandes en direct, deux captures, rapport de bug, arrêt propre (0,5 s) ; comparaison, enregistrement de scénario, figement des sauvegardes.
- Interface : test de fumée `--smoke-ui` (3 onglets, état « Atelier prêt »).
- Scénario « village » rejoué en vrai : 11 actions exécutées, sélection du personnage puis Hunter dans le village de Pokke (captures).
- « Compiler + tests » : compilation OK et 7 tests C++ du rendu réussis (32 s), après correction de l'appel à `cmd`.

## Limites connues
- Les scénarios ne vérifient pas le contenu des images : il faut regarder les captures.
- Les variantes de rendu changent un réglage chacune ; leurs effets sur MHFU ne sont pas encore étudiés.
- La page Debug de MHP3rd reste coupée : ses adresses ne valent pas pour MHFU.

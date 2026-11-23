# Atelier : interface mémoire et essais JSON

Depuis la racine du projet, avec le jeu compilé à jour :

```powershell
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_retro.py outils\atelier-mhfu\scenarios\retro-forge.json --seed 20261020-153034-ceda7b
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_retro.py outils\atelier-mhfu\scenarios\retro-shop.json --seed 20261020-153034-ceda7b
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_retro.py outils\atelier-mhfu\scenarios\retro-save.json --seed 20261020-153034-ceda7b
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_retro.py outils\atelier-mhfu\scenarios\retro-memory.json --seed 20261020-153034-ceda7b --watch-writes
```

Chaque essai produit `retro-result.json`, `retro-report.html`, `contact-sheet.jpg`, `transcript.txt`, `transcript.jsonl`, les instantanés et les dumps dans son dossier privé. Un succès ne dispense pas de regarder la planche. Les scénarios forge/boutique fournissent argent ou matériaux et déplacent le personnage : ce sont des essais assistés, pas des preuves de collecte ou de jeu normal.

Le moteur vérifie les SHA-256 de `game-mhfu/ms0` et du seed avant/après, refuse de démarrer en présence d'un Yakumo, verrouille ses propres lancements concurrents et arrête son processus à la fin. Les boutons, attentes et essais sont bornés. `Ctrl+C` annule l'essai. Le moteur ne tue jamais un autre Yakumo.

## Session vivante : explorer sans relancer

Pour chercher une mécanique, ne plus écrire de plan à l'aveugle : `atelier_live.py` démarre le jeu une seule fois (environ 7 s jusqu'au village) et exécute ensuite chaque étape en un appel, avec l'état de l'écran en réponse. Une étape échouée laisse le jeu ouvert : on corrige et on renvoie seulement cette étape.

```powershell
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_live.py start --start apres-intro
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_live.py do snapshot name=avant
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_live.py do talk type=6
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_live.py do '[{"op":"advance","until":"menu"},{"op":"select","text":"Yes"}]'
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_live.py look
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_live.py export retro-farm
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_live.py stop
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_retro.py outils\atelier-mhfu\scenarios\retro-farm.json
```

- `do` accepte `OP clé=valeur…` (valeurs lues en JSON si possible), un objet JSON ou une liste ; une liste s'arrête à la première étape en échec. Chaque réponse donne l'entrée de l'étape (verdict, durée, erreur, fichiers) et `look` : tâche, zone, scène, village prêt, dialogue, menu (entrées, curseur) et textes affichés. `--no-look` l'omet.
- Mêmes validations et mêmes garde-fous qu'un plan : empreintes des originaux avant/après, un seul Yakumo, verrou partagé avec `atelier_retro.py`. Une étape refusée par la validation (`rejected`) n'est pas envoyée au jeu. Un nom (instantané, dump) n'existe qu'après la réussite de son étape. Chaque échec produit une capture `failure-N`.
- `export NOM` écrit `scenarios/NOM.json` avec les seules étapes réussies (`--first/--last` pour une plage), le point de départ de la session (`start`) et les appuis identiques consécutifs regroupés (`repeat`). Le rejeu en lot est la validation ; la session vivante n'est qu'une exploration.
- La session s'arrête seule après `--idle` minutes sans requête (10 par défaut) ou au bout de `--minutes` (25 par défaut, 29 au plus). Le serveur se place dans un job Windows « kill on close » (`job_guard: true` dans `status`) : s'il meurt, le jeu qu'il a lancé meurt avec lui. `status` montre l'enregistrement de session, dans `atelier-psprecomp/live/`.

Mesuré le 27/10 (session `20261027-200626-12e745`) : démarrage jusqu'au village 7,5 s ; appel `do` complet côté client 0,34 s pour un instantané, 0,42 s pour l'inventaire de zone, environ 1 s pour un trajet maison ; une étape volontairement en échec a coûté 1,6 s, la session a continué. Le scénario exporté `live-smoke-house.json` a été rejoué en lot : `passed`, originaux inchangés (`20261027-200720-ee762b`).

## Points de départ nommés

`fixtures.json` associe un nom à un seed, à une copie privée arrêtée et à l'empreinte de cette copie. `--start apres-intro` (dans `atelier_live.py` et `atelier_retro.py`) refuse de démarrer si la copie a changé depuis son enregistrement. Un plan qui contient `start` (c'est le cas des exports) se lance sans `--seed`.

## Lecture et navigation

La commande native `ui` renvoie une ligne JSON. Le texte vient des `GlyphRun` UTF-16 de `SystemFont`, avec les coordonnées et la couche. Aucun OCR. Les dialogues de la forge (scène 11), du magasin (3) et de la chef (36) ont été suivis en jeu. `Next` est lu littéralement. `text_complete` vaut `true` si un marqueur ou un menu le prouve, et `null` quand cette information reste inconnue ; une page finale sans marqueur n'est pas déclarée terminée par simple temporisation.

Les menus de choix du village exposent l'index du curseur de `LbManager`, les entrées et un identifiant mémoire. Les confirmations dont le jeu entoure la sélection de crochets exposent aussi leur axe horizontal/vertical. Les identifiants sont locaux à l'état mémoire et au type de jeu, pas un catalogue universel de tous les écrans. Un écran non décodé conserve ses textes ; le pilote refuse une sélection par nom s'il ne peut pas vérifier les entrées et le curseur.

`RetroPilot` fournit `advance_dialogue_until("menu"|"closed")`, `select("Weaponcraft")`, `back_to_village()`, `find(8|16|32, valeur)`, `next(valeur)` et `changed()`. Le retour au village vérifie réellement la zone 139 ; il sait sortir de la maison (66) et refuse une route non établie. Les commandes utilisent une lecture de clôture pour collecter complètement les réponses de plusieurs lignes, avec mesure du temps d'aller-retour.

`ui on` active en supplément les observations des appels de dessin Cockpit. `ui off` restaure le chemin sans observateur. Le pilote ordinaire n'a pas besoin de ce mode pour les choix de village décodés.

## Étapes JSON

Un plan contient `name`, `seconds` (10..1200), `assisted`, `steps` (1..300) et éventuellement `start` (`seed`, `from_run`). Un plan sans assertion qui va au bout finit en `explored`, pas en `passed` : il n'a rien validé. Les références à des instantanés ou dumps doivent désigner une étape antérieure. Exemples :

```json
{
  "name": "exemple",
  "seconds": 120,
  "assisted": true,
  "steps": [
    {"op":"command", "command":"money 10000", "expect":"zenny"},
    {"op":"snapshot", "name":"avant"},
    {"op":"talk", "type":2},
    {"op":"advance", "until":"menu", "timeout":40},
    {"op":"select", "text":"Weaponcraft"},
    {"op":"capture", "name":"menu"},
    {"op":"back"},
    {"op":"snapshot", "name":"apres"},
    {"op":"diff", "before":"avant", "after":"apres"},
    {"op":"assert", "field":"money_delta", "equals":0}
  ]
}
```

Autres opérations :

- `button` : `button`, éventuellement `frames` (1..60) et `repeat` (1..60).
- `wait` : exactement une condition `ui`, `state`, `address`/`equals`/`width`, ou `seconds` (0..10). Les conditions d'interface acceptent `contains_text`.
- `assert` : `money_delta`, `equipment_box`, `item_box`, `pouch`, `farm_changed`, `kitchen_changed`. `key` compare un gain précis, par exemple `"5:3"` pour Buster Sword ou `"163"` pour Herb ; les variations négatives sont conservées.
- `assert_snapshot` : valeur exacte d'un champ d'un instantané, notamment après rechargement.
- `area`, `interact` : inventaire de la zone et de ses points d'interaction ; l'interaction attend la détection du point avant d'envoyer le bouton. Il faut ensuite attendre et vérifier la destination effective.
- `symbols` : nom de `scope` d'un fichier `symbol_addrs` local.
- `dump` : nom simple, taille et adresse numérique ou référence `{"snapshot":"avant","field":"money_address"}`. Le fichier reste dans l'essai, y compris si le chemin contient des espaces.
- `memory_diff` : deux dumps de même plage, scope de symboles ; produit un JSON annoté par structure et symbole proche. Le symbole proche n'est pas une preuve d'appartenance de la mémoire à ce symbole.
- `watch` : adresse d'un instantané et taille (1..4096), avec `--watch-writes` au lancement. `command: "watch off"` désactive la plage.
- `reload` : arrête le jeu, vérifie qu'une écriture a changé la copie privée, puis redémarre sur une nouvelle copie de celle-ci. `--from-run ID` réutilise également une copie d'un essai arrêté, en protégeant cette source par empreinte.
- `quest_fixture` : uniquement dans un plan `assisted`. Réutilise le repérage de quête avec chronomètre raccourci ; son résultat distingue `forced_end` et `reported_clear`. Il ne valide pas un objectif accompli normalement.

## Mémoire et symboles hors jeu

`retro_memory.py` charge les symboles en conservant leur scope d'overlay ; des fichiers de même nom dans des répertoires distincts restent séparés. Il propose `sym`, `addr`, `objects` et `diff`. `--out` placé avant la sous-commande écrit dans un fichier neuf. `ValueSearch` permet aussi une recherche puis des filtres successifs sur des dumps, avec comparaison à la lecture précédente.

```powershell
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\retro_memory.py sym references\mhp2g-decomp\config NpcManager
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\retro_memory.py addr references\mhp2g-decomp\config 0x09B1E708 --scope lobby_task
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\retro_memory.py --out work\objets-neuf.json objects CHEMIN_DUMP.bin 0x089B6100 --size 0x240
```

Les correspondances de table virtuelle sont des candidats. La taille de chaque objet doit être justifiée avant d'interpréter ses champs.

## Limites à conserver

Les noms de rôle PNJ ne sont renseignés que lorsqu'ils sont identifiés ; les autres restent inconnus. Les coffres complets, l'argent, les améliorations de ferme et les identifiants de compétences cuisine sont structurés. Les préfixes des singletons `Farm*`, `ShopFarm`, `Kitchen`, `ShopCat` sont explicitement opaques : ils ne constituent pas un décodage complet de ces objets.

Les traces d'appels couvrent les entrées visibles par le répartiteur et les appels entre unités ; les sauts internes à une unité recompilée ne constituent pas des points d'observation universels. Elles sont bornées par `MAX_CALLS` et absentes par défaut. Une session armée pour les écritures conserve le chemin de stockage instrumentable jusqu'à son arrêt, même après `watch off` ; les sessions ordinaires gardent le chemin rapide.

Il n'y a pas de restauration de RAM ni de sauvegarde silencieuse en mémoire : `reload` fait un vrai nouveau démarrage. Regrouper des actions courtes dans un plan évite le coût de démarrage entre elles ; toute réinitialisation contrôlée reste un rechargement vérifié des fichiers privés.

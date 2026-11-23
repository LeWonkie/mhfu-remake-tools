# Atelier MHFU — reprise au 22 octobre 2026

L'Atelier fonctionne : `atelier_core.py` est présent et le lanceur est
`Atelier MHFU (PSPRecomp).bat`, à la racine du projet. Les données locales se trouvent
dans `atelier-psprecomp/`. Voir [README.md](README.md) pour les commandes et leurs limites.

## État vérifié

- Suite Python : **254 tests réussis** le 22 octobre (24,989 s après les correctifs Codex).
- Objectifs lus sur le disque : 603 missions ; 548 admissibles au repérage technique,
  regroupées en 28 lots de 20 (dernier lot de 8). Trois missions vides et 52 missions sans
  admission native sont exclues. Ce plan ne constitue pas une validation en jeu.
- Le correctif de retour au village relit la tâche après `quest results` avant de confirmer.
  Une fermeture de dialogue par × est disponible, reconnue et bornée à six pressions ;
  le village reste disponible seulement avec `scene == 0`.
- Essai réel précédent : entraînement 1 puis quête 1213, campagne
  `atelier-psprecomp/campaigns/20261022-024122/` : deux fins par chronomètre et deux retours
  au village prêt ; captures examinées, sauvegardes originales et seed inchangés.
  Le secours par × n'avait pas été nécessaire dans cet essai.
- Chasse, livraison, récolte et capture ont des preuves représentatives avec aides de debug.
  La campagne générale et le gameplay de toutes les missions restent à vérifier.
- Premier lot : 20 quêtes parcourues en 216,69 s, 100 captures examinées, aucun blocage.
  La quête 1001 rapporte une réussite (3), les 19 autres un échec (5). Le pilote corrigé
  distingue `reported_clear` de `forced_end` et rejette les fins non interprétées pour
  la validation technique. Confirmation 1001 puis 1213 en 31,88 s : deux villages prêts.
- La revue distingue maintenant les cinq phases rapides des sept phases détaillées et
  affiche `fin2`. Les anciennes alertes sur les phases non demandées étaient artificielles.
- Claude a ensuite parcouru les lots 2 et 3 (40 quêtes, 200 captures examinées), sans blocage.
  Les succès de 1001..1003 et 1101..1103 sont expliqués par l'objectif des récoltes :
  « Survive until Time Over or Deliver the Paw Pass Ticket ». Le chronomètre remplit donc
  cette condition. Voir `COMPTE_RENDU_CLAUDE_2026-10-22_lot2.md`.
- Lot 4 parcouru par Codex : 20 quêtes, 236,16 s, 100 captures examinées, sauvegardes
  inchangées. Preuves : `work/step6-batch04-20261022-114611/`.

## Reprendre l'étape 6

Depuis la racine du projet, préparer un plan hors ligne dans un dossier neuf :

```powershell
.\deps\venv\Scripts\python.exe outils\atelier-mhfu\atelier_pilot.py --plan --plan-out work\nouveau-plan
```

La sonde locale `work/probe_step6_batch.py --batch 1 --seconds 600` exécute un lot du plan
avec une échéance globale, une copie du seed, le RTC fixe, l'avance rapide ×8 demandée,
cinq captures par quête et trois secondes de jeu observées. Elle force une nouvelle exécution,
arrête l'essai dans `finally`, compare les empreintes des sauvegardes et écrit un nouveau
dossier `work/step6-batchNN-<date>/`. Les lots suivants utilisent `--batch 2`, etc.
Le code de sortie vaut zéro seulement si tout le lot passe le contrôle technique et si
les sauvegardes originales sont inchangées, sans essai actif.
L'option `--profile` joint le relevé existant du benchmark (`profile.json`) : durées réelles,
CPU du pilote et estimations du temps émulé issues des lignes `[perf]`.

`work/summarize_step6.py --out DOSSIER_NEUF` récapitule les archives complètes de lots,
leurs preuves, captures enregistrées et phases de retour. Ce récapitulatif conserve les
anciens libellés et ne constitue pas une validation sous la clé de cache actuelle.

La CLI principale propose aussi `--all --limit N`, des IDs explicites et `--resume DOSSIER`.
Ne pas confondre `forced_end` (fin provoquée par le chronomètre) et victoire de l'objectif.
`reported_clear` signifie que le jeu a signalé l'état 3 pendant l'essai assisté ; aucune
validation de gameplay normal n'en est déduite. Les essais historiques conservent leurs
anciens libellés et clés de cache. Les changements du pilote empêchent leur réutilisation
automatique sous la nouvelle clé ; il n'y a pas de migration silencieuse des résultats.
Examiner les captures après les essais ; les heuristiques automatiques ne jugent pas le rendu.

## Ressources

- `COMPTE_RENDU_CLAUDE_2026-10-22_dialogue_village.md` : dernier correctif du pilote.
- `COMPTE_RENDU_CODEX_2026-10-22_campagne_lot1.md` : lot réel, classification corrigée et preuves.
- `PASSATION_CLAUDE_CODE_2026-10-22_ETAPE6.md` : préparation et ancien blocage désormais corrigé.
- `PASSATION_CLAUDE_CODE_2026-10-22.md` : objectifs du disque, capture et armures.
- `work/step6-plan-20261022/plan.md` : plan historique, sans résultat d'exécution.

Une seule instance de jeu, essais bornés et copies privées. Préserver les trois modifications
natives d'armures non commitées ; aucun commit, push ou changement de moteur automatique.

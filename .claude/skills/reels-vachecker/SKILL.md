---
name: reels-vachecker
description: Fabriquer, vérifier et publier les réels verticaux « moment » de vachecker.info (Instagram) — une phrase de débat, « Vrai ou faux ? », la carte de vérification tirée de la fiche publiée, la fiche du débat et l'outro. À utiliser pour produire un nouveau réel de fact-check, en préparer un lot, corriger un réel existant ou en publier un.
---

# Réels « moment » de vachecker.info

Tout se passe dans `videos/reels` (projet HyperFrames, CLI épinglée `hyperframes@0.8.152`).
Le script `scripts/build_reels.py` impose le format ; `scripts/outils_reels.py` prépare les
extraits, écrit les légendes et **vérifie ce que le format ne garantit pas**. Les réels déjà
faits (MOMENTS dans `build_reels.py`) servent de modèles.

## Le format (figé — ne pas le modifier sans demande de l'utilisateur)

Accroche (citation exacte + « Vrai ou faux ? », c'est aussi la miniature) → extrait recadré
sur l'orateur, sous-titres mot à mot → « Vrai ou faux ? » et décompte → carte de vérification
(verdict, affirmation, explication, source de la fiche) pendant que le débat continue, étouffé
→ « Et sur tout le débat ? » (nombre d'affirmations de la fiche) → fiche verticale sombre du
débat → « La fiche complète du débat » → outro « va✓ ». La mise en page évite déjà la barre du
haut, les boutons à droite et la légende d'Instagram : n'y touchez pas.

## Règles (non négociables)

1. **Les textes de l'IA tels quels.** Verdict, affirmation, explication et source viennent de
   `site/sessions/<id>.fiche.json` : ne jamais les reformuler, corriger, raccourcir, ni même
   changer une majuscule. La carte et la légende les reprennent automatiquement.
2. **L'accroche cite les mots exacts de l'orateur** (`quote` dans MOMENTS) : coupes en `[…]`,
   ajouts de contexte entre crochets (`[Jordan Bardella] a accepté…`). Jamais l'affirmation
   résumée de la fiche (« Le salaire des enseignants n'a pas été augmenté » ≠ ce qu'a dit
   Bompard : « Vous n'avez pas augmenté le salaire des enseignants »).
3. **La légende se génère** (`legende`), elle ne s'écrit pas à la main : même modèle pour tous,
   avec la mention « vérifiées par notre IA, sources à l'appui ».
4. **Choix du moment** : verdict tranché (vrai, faux, trompeur, partiellement vrai), confiance
   ≥ 85, affirmation absente de `EXAMPLES_SKIPPED` (`publish_session.py`), orateur **à l'image
   pendant sa phrase**, phrase de 2 à 10 s. Varier les bords politiques et les verdicts (pas que
   des « faux »). Signaler à l'utilisateur tout verdict qui peut prêter à discussion : c'est lui
   qui vérifie chaque verdict à la source avant de publier.
5. **Hors dépôt** : extraits des chaînes (`assets/clips`), transcriptions (`assets/words`),
   musique, rendus — déjà dans `.gitignore`, ne jamais les forcer. Les clés de `.env` ne
   s'affichent jamais.
6. **Publication** : jamais sans un « publie » explicite de l'utilisateur, réel par réel ; essai à
   blanc d'abord. L'API ne supprime ni n'épingle : c'est l'utilisateur qui le fait dans l'appli.
   **Ce qui est en ligne ne se republie pas** (même corrigé) sans qu'il le demande : il supprime
   l'ancienne version, puis `--republier`.

## Où sont les vidéos

- `videos/reels/renders/a-publier/` : réels rendus, pas encore en ligne.
- `videos/reels/renders/publiees/` : réels en ligne (le script de publication les y range).
- `social/publications.json` (versionné) : date et lien de chaque publication — c'est la référence.
- `python scripts/build_reels.py etat` : ce qui reste à publier, ce qui est en ligne.
- `videos/reels/renders/verif/` : planches de `verifier`.

## Étapes

Depuis `videos/reels` :

1. **L'affirmation** : dans la fiche du débat, noter `id`, `t` (secondes dans la vidéo du site),
   `qui`, `verdict`, `confiance`, `texte`. L'id du débat est celui de la vidéo YouTube du site.
2. **L'extrait** (s'il n'existe pas déjà dans `assets/clips`) :
   `python scripts/build_reels.py extrait <id YouTube> <t − 35> 60 <chaine>-<orateur>-<sujet>-<début>`
   (télécharge le passage, une image clé par seconde, son ramené à −16 LUFS comme les autres
   extraits, transcription Whisper en local). Un extrait trop faible ou trop fort :
   `python scripts/build_reels.py normaliser <extrait>`.
   Exception : `2V70R8-Om7c` (législatives 2024) porte un bandeau de campagne ; les extraits
   viennent de la version France 2 `dFbfWKwEAEA`, décalée de −660 s.
3. **Les bornes** `a` (début du premier mot) et `b` (fin du dernier) : lire
   `assets/words/<extrait>.words.json` autour de `t`. Les temps de Whisper sont souvent décalés
   de 0,1 à 0,4 s : c'est `verifier` qui tranche, au son. Un mot mal transcrit se corrige dans
   `FIXES`, un début de mot mal placé dans `RETIME`.
4. **Le cadrage** : regarder des images de l'extrait
   (`ffmpeg -ss <s> -i assets/clips/<x>.mp4 -frames:v 1 x.png`, puis les lire) et choisir
   `crop = (x, y, largeur, hauteur)` en pixels de la source 1920 × 1080, **rapport largeur /
   hauteur = 1,08**, visage centré, sans logo ni bandeau de chaîne. Si la réalisation change de
   plan pendant la phrase, ajouter `recrop = [(instant, fenêtre)]` (instants donnés par
   `verifier`).
5. **Les entrées** `CLIPS[...]` (src, words, t0, crop, recrop, who, where) et `MOMENTS[...]`
   (clip, debat, aff, a, b, quote, contexte, tags) dans `build_reels.py`.
6. **Légende et vérification** :
   `python scripts/build_reels.py legende <réel>` puis
   `python scripts/build_reels.py verifier <réel>` — tout doit être ✓ (un ⚠ se justifie).
   **Regarder** `renders/verif/<réel>.jpg` : l'orateur visible et centré pendant la phrase.
7. **Construire et contrôler** : `python scripts/build_reels.py <réel>`,
   `npx --yes hyperframes@0.8.152 lint` (0 erreur), snapshots aux moments clés
   (`npx --yes hyperframes@0.8.152 snapshot --at 0.4,<carte>,<fiche> --no-end --describe false`)
   et les regarder.
8. **Rendre** : `python scripts/build_reels.py rendre <réel>` (construit, lint, rend dans
   `renders/a-publier/vachecker-<réel>.mp4` et affiche le niveau sonore : ⚠ hors de −19…−13 LUFS),
   puis une planche à une image par seconde
   (`ffmpeg -i renders/a-publier/vachecker-<réel>.mp4 -vf "fps=1,scale=150:-1,tile=14x2" -frames:v 1 p.jpg`)
   à regarder en entier, et le niveau sonore (≈ −16 LUFS : `-af ebur128`).
9. **Livrer** : envoyer le rendu à l'utilisateur ; commit (script, légende, fiche capturée dans
   `assets/fiches`) puis `git push origin ameliorations` et `git push origin ameliorations:main`.
10. **Publier** (après « publie ») depuis la racine du dépôt :
    `python social/publier_instagram.py publier videos/reels/renders/a-publier/vachecker-<réel>.mp4 --legende social/legendes/<réel>.txt`
    (essai), puis la même commande avec `--confirme`. Le script range la vidéo dans `publiees/`
    et inscrit le lien dans `social/publications.json` : committer ce fichier. Renvoyer le lien.

## Pièges déjà rencontrés

- Fin du mot précédent au début de l'extrait (Attal, Geffray) → `verifier` / « début ».
- Mot suivant dans les sous-titres (« le carburant parce », Bardella) → `b` = fin réelle du
  dernier mot ; un mot qui commence à `b` n'est plus pris.
- Orateur hors champ sur un plan large (Geffray) → planche de `verifier`, recadrer ou `recrop`.
- Accroche qui cite le résumé de la fiche au lieu des mots dits → règle 2.
- Légende réécrite à la main (mention de l'IA supprimée, explication modifiée) → règle 3.
- `index.html` et `compositions/` ne contiennent qu'un réel à la fois : reconstruire le réel
  juste avant de le rendre.

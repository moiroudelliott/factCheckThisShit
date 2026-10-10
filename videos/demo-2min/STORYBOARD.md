---
format: 1920x1080
duration: 130s
message: "Pendant un débat politique, SOURCÉ vérifie les chiffres en quelques secondes, sources à l'appui, sans prendre parti"
arc: Le volume (195 affirmations) → la promesse → 4 moments vérifiés → la fiche → où la trouver
audience: grand public curieux de politique, journalistes, personnes qui découvrent SOURCÉ
mode: collaborative
---

# Démo SOURCÉ — 2 minutes · v4

## Décisions

- **Message** : pendant un débat, SOURCÉ vérifie les chiffres en quelques secondes, sources à l'appui, sans prendre parti.
- **Public et arc** : grand public ; le volume → la promesse → la preuve (4 moments réels, 4 politiques, 4 verdicts) → la fiche → l'adresse.
- **Format** : 1920×1080, 113 s, pas de voix off, son du débat sous les extraits, pas de musique (un souffle de son discret possible sur l'ouverture et la fin, à confirmer). Pas de sous-titres permanents : les cartes portent le propos.
- **Fil conducteur** : la **carte SOURCÉ** de l'extension (verre sombre, tag de verdict) qui entre à droite de l'image à chaque affirmation ; et une **rangée de pastilles de verdict** en bas à gauche qui s'allume d'un point à chaque moment (rouge, jaune, orange, vert) et que la fiche finale reprend.
- **Marque** : deux registres réels du produit. Ouverture, fiche et fin = le site (fond blanc #ffffff / surface #f6f5f1, encre #1c1c1c, rouge #a3172c, Newsreader italique pour la marque, Archivo pour le texte, IBM Plex Mono pour les étiquettes — site/style.css). Extraits = l'extension (carte rgba(22,24,29,.84) floutée, rayon 16, tags oklch : vrai 0.74 0.13 145, partiel 0.76 0.14 90, trompeur 0.78 0.14 75, faux 0.62 0.20 25 — extension/overlay.css, content.js).
- **Interdits** : pas de fausse interface (les cartes sont reconstruites depuis le CSS de l'extension, mot pour mot depuis les verdicts publiés) ; aucune coupe à l'intérieur d'une citation ; pas de lueurs néon ; pas de diaporama (chaque extrait garde sa carte qui vit) ; pas d'écran de fin figé.
- **Plan tenu** : la fiche (frame 08) se pose et ne bouge plus 3 s sur l'indice d'exactitude.
- **Vérité** : les 5 citations sont retranscrites au mot près sur l'extrait (Whisper, horodatage au mot) et chaque verdict est contrôlé sur une source fiable. Délai de vérification raccourci au montage, dit à l'écran en fin de vidéo.

## Images

Les extraits du débat des législatives viennent du direct de France 2 (mise en ligne dFbfWKwEAEA, piste audio française d'origine), et non de la version publiée par Ensemble pour la République (2V70R8-Om7c, utilisée par le site), qui porte un bandeau de campagne. Décalage : −660 s. Fichiers : assets/clips/leg-*.mp4 (1080p, France 2), lci-bompard-decret-1890.mp4 (1080p, LCI ; recadré ×1,17 par le haut pour sortir le bandeau défilant, sans rapport avec le moment). Crédits « Extraits : France 2, LCI » en fin de vidéo. Horodatages des cartes = ceux de la vidéo France 2.

## Changes from v1

Remarques de l'utilisateur, mot pour mot : « smic c'est bien, le défenseur des droits aussi, EPR non on comprend pas de quoi ça parle c'est pas assez grand public, et la france insoumise a voté contre c'était pas utile au débat en cours c'est moins intéressant et le dernier de bardella est bien, trouve d'autres exemples pour ces deux-là » ; « tu veux pas afficher un faux et un trompeur ? on a que des vrai ou partiellement vrai ».

- Frame 05 : Bardella / EPR (partiel) → **Bardella / taxes sur l'essence (FAUX)**.
- Frame 06 : Geffray / vote LFI (vrai) → **Attal / binationaux (TROMPEUR)**.
- Les deux derniers moments forment un duel Attal–Bardella. Minutage revu : 115 s.

## Changes from v2

Remarques de l'utilisateur, mot pour mot : « le faux est sévère 54% c'est pas non plus loin de 60/70% si tu trouves rien d'autre tant pis mais si tu trouves autre chose c'est mieux » ; « enlève le trompeur de Bompard, remplace le vrai de Bardella par un vrai de Bompard, c'est plus intéressant d'avoir des politiques différents à chaque fois, et tu peux enlever le vrai de Faure au début » ; « il faut un partiellement vrai, un vrai, un trompeur et un faux à chaque fois de politiciens différents ».

- Essence (Bardella) : faux → **partiellement vrai** (54 % : plus de la moitié, moins que 60 à 70 %).
- Nouveau faux : **Faure / Meloni** (« elle a régularisé 450 000 sans-papiers »).
- Nouveau vrai : **Bompard / décret de février 2024** (692 millions d'euros annulés pour l'enseignement scolaire).
- Retirés : Faure / Espagne, Bompard / Défenseur des droits, Bardella / électricité. Quatre moments : 113 s.

## Locked

Planche v3 validée par l'utilisateur (« parfait alors go ») : 8 plans, 4 moments (faux, partiellement vrai, trompeur, vrai ; Faure, Bardella, Attal, Bompard), mises en page, textes des cartes, placements, recadrage du plan 06, fiche et fin.

## Changes from v3

Remarques de l'utilisateur, mot pour mot : « la vidéo est super mais je ne suis toujours pas fan des extraits choisis en général surtout que ça reste beaucoup sur un seul débat lorsqu'on en a 4, 2 sont complètement absents », avec ses choix : faux = Bompard, salaire des enseignants ; trompeur = Bardella, TVA à 5,5 % ; partiellement vrai = Attal, protocole Bardella-Farage (ou les entrées d'immigrés) ; vrai = Geffray, lycée Mandela à 2 millions d'euros. Puis : « laisse les verdicts plus longtemps » et « ajoute une musique ».

- Quatre débats, quatre politiques, quatre verdicts.
- Attal : la phrase de la carte du site était celle du journaliste ; la carte vérifie désormais la phrase d'Attal (« l'intégralité des personnes migrantes au Royaume-Uni »). Les entrées d'immigrés (autre option) écartées : explication contradictoire avec une autre carte.
- Cartes 11 à 14 s après le verdict ; son du débat normalisé à -16 LUFS, murmure sous la lecture.
- Musique « Jungle Waves » (dimmysad, Pixabay) : drop au début du volet (10,1 s), très basse sous la parole, remontée sous les cartes, pleine sur la fiche. Fichier hors du dépôt (licence).

## Moments retenus (vérifiés)

| # | Qui | Citation exacte (extrait) | Verdict | Contrôle |
|---|---|---|---|---|
| 1 | Manuel Bompard (LFI) — LCI, 2 oct. 2026 | « Vous me dites que vous avez augmenté les professeurs. […] Vous n'avez pas augmenté le salaire des enseignants. » 31:22–31:36 | FAUX | DEPP, note n° 26-36 : salaire net moyen +6,4 % en 2024 (+4,3 % en euros constants) |
| 2 | Gabriel Attal (Renaissance) — Franc-jeu | « …qui allait au Royaume-Uni signer un accord […] que la France soit le réceptacle, reçoive l'intégralité des personnes migrantes au Royaume-Uni. » 2:59–3:17 | PARTIELLEMENT VRAI | Protocole Farage-Bardella du 4/09/2026, art. 5 : personnes interceptées dans les eaux britanniques après un départ de France ; conditionné à l'arrivée au pouvoir des deux partis |
| 3 | Jordan Bardella (RN) — France 2, 27 juin 2024 | « Dès l'été, j'entends baisser la TVA de 20 % à 5,5 % sur évidemment l'électricité, le gaz, le fioul, l'énergie et le carburant » 23:03–23:11 | TROMPEUR | Les Surligneurs (12/06/2024) : taux réduit permis pour l'électricité et le gaz, contraire à la directive TVA pour le fioul et les carburants |
| 4 | Édouard Geffray (gouvernement) — Franc-jeu | « Mais ce n'est pas encore évalué parce qu'il y a des dommages énormes. Vous voyez, par exemple, le lycée Mandela, c'est 2 millions d'euros. » 24:28–24:33 | VRAI | France 3 Régions (2/10/2026) : Région Pays de la Loire, au moins 2 millions d'euros |

Durées : 01 5 s · 02 5 s · 03 Bompard 26,2 s · 04 Attal 32,05 s · 05 Bardella 20,65 s · 06 Geffray 16,85 s · 07 fiche 16 s · 08 fin 8 s = 129,75 s. Les compositions (compositions/*.html) font foi ; le détail est dans scripts/build_scenes.py.

## Frame 01 — Le volume

- scene: Fond blanc ; « 195 » en très grand (Archivo), étiquette mono « Débat des législatives · France 2 · 27 juin 2024 », « affirmations vérifiées en un seul débat de 1 h 58 »
- duration: 5s
- poster: 3s
- transition_in: cut
- status: built
- src: compositions/01-volume.html
- voiceover: onscreen

Le chiffre compte de 0 à 195 en 1,2 s puis se fige ; dessous, en Archivo : « Qui a le temps de tout vérifier ? ». Pourquoi : pose le problème avec un chiffre réel tiré de nos données (195 affirmations vérifiables dans le débat Attal–Bardella–Faure). Contrainte : pas d'image de débat ici, la typographie seule.

## Frame 02 — La promesse

- scene: Marque « SOURCÉ » (Newsreader italique, É rouge) ; « Chaque affirmation vérifiée en direct, sources à l'appui, sur la vidéo. » ; trois étapes mono : transcription → recherche → verdict
- duration: 5s
- poster: 3.5s
- transition_in: wipe
- status: built
- src: compositions/02-promesse.html
- voiceover: onscreen

La marque entre la première, la phrase suit, les trois étapes s'allument de gauche à droite. Sortie : le fond blanc se retire vers la gauche et découvre le premier débat. Pourquoi : la promesse arrive au 2e plan, avant toute preuve.

## Frame 03 — Faure, Meloni

- scene: Extrait (France 2, 27 juin 2024) ; badge « Olivier Faure » ; carte : FAUX
- duration: 22s
- poster: 18s
- transition_in: wipe
- status: built
- src: compositions/03-faure-meloni.html
- voiceover: son du débat 1:00:17.6 → 1:00:39.6 (clip leg-faure-meloni-3608, 9.6 → 31.6 ; fondu avant la réponse de Bardella à 27.3)

« Et je l'assume d'autant plus que Mme Meloni, qui est votre amie en Italie, qu'est-ce qu'elle a fait ? Parce qu'elle avait dit la même chose que vous au départ […] Et puis qu'est-ce qu'elle a fait ? Elle a régularisé 450 000 sans-papiers. Pourquoi ? Parce qu'elle en avait besoin pour faire tourner l'économie italienne. » Carte au mot « sans-papiers » ; FAUX. Première pastille, rouge. Pourquoi : un chiffre repris partout, qui désigne en fait autre chose.

## Frame 04 — Bardella, l'essence

- scene: Extrait (France 2) ; badge « Jordan Bardella » ; carte : PARTIELLEMENT VRAI
- duration: 12.4s
- poster: 9s
- transition_in: cut
- status: built
- src: compositions/04-bardella-essence.html
- voiceover: son du débat 22:40.1 → 22:52.5 (clip leg-bardella-essence-1346, 14.1 → 26.5 ; fondu avant « un tiers de taxes » à 24.1)

« Quand on a par exemple 60 à 70 % de taxes sur le carburant lorsqu'on va mettre de l'essence dans sa voiture, c'est évidemment un moyen direct pour l'État de rendre du pouvoir d'achat aux Français. » Carte au mot « voiture » ; PARTIEL : 54 % en 2024, plus de la moitié mais moins que 60 à 70 %. Pastille jaune. Pourquoi : un ordre de grandeur juste, un chiffre gonflé.

## Frame 05 — Attal, les binationaux

- scene: Extrait (France 2) ; badge « Gabriel Attal » ; carte : TROMPEUR
- duration: 23.5s
- poster: 19s
- transition_in: cut
- status: built
- src: compositions/05-attal-binationaux.html
- voiceover: son du débat 2:47.6 → 3:11 (clip leg-attal-binationaux-160, 7.5 → 31.0)

Phrase entière d'Attal sur les « 3,5 millions de Français binationaux » qu'il dit présentés comme « plus corruptibles » ; Bardella proteste en direct (« Je n'ai jamais dit ça, M. Attal, vous mentez… ») au moment où la carte arrive ; TROMPEUR. Pastille orange. Pourquoi : SOURCÉ vérifie aussi ce qu'un débatteur prête à son adversaire.

## Frame 06 — Bompard, le décret

- scene: Extrait (LCI, 2 octobre 2026), recadré ×1,17 ancré en haut à 20 % ; badge « Manuel Bompard » ; carte : VRAI
- duration: 20.8s
- poster: 16s
- transition_in: cut
- status: built
- src: compositions/06-bompard-decret.html
- voiceover: son du débat 31:44.4 → 32:05.2 (clip lci-bompard-decret-1890, 14.4 → 35.2)

Il brandit le décret : « Regardez, ça, c'est un décret, 22 février 2024, décret signé par Gabriel Attal lui-même, sur les coupes budgétaires. Ici, c'est la ligne enseignement scolaire. Vous étiez porte-parole du gouvernement. 700 millions de coupes dans le budget de l'éducation nationale. » Carte au mot « nationale » ; VRAI (Légifrance : 692 millions). Thévenot répond sous la carte. Pastille verte : la rangée compte 4 points. Sortie : les pastilles glissent au centre et deviennent la barre de la fiche.

## Frame 07 — La fiche

- scene: Fond blanc ; titre « À la fin du débat, la fiche. » ; capture réelle du résumé d'une page (resume.html, débat des législatives) : indices d'exactitude par débatteur, frise par thème, chiffres contestés
- duration: 16s
- poster: 10s
- transition_in: wipe
- status: built
- src: compositions/07-fiche.html
- voiceover: onscreen

La capture entre par un lent zoom ; trois repères s'allument tour à tour (« qui a été le plus exact », « sur quels thèmes », « les chiffres contestés »). Plan tenu 3 s sur les indices (Attal 82 %, Bardella 66 %, Faure 83 %). Le « 195 » de l'ouverture réapparaît en haut à droite.

## Frame 08 — Où la trouver

- scene: Fond blanc ; « SOURCÉ » ; « source.codeminds.fr » ; « Débats en relecture · fiches · méthode » ; mention fine : « Montage : délai de vérification raccourci. Verdicts tels que publiés sur le site. Extraits : France 2, LCI. »
- duration: 8s
- poster: 5s
- transition_in: crossfade
- status: built
- src: compositions/08-fin.html
- voiceover: onscreen

L'adresse s'écrit, la ligne rouge se trace dessous ; fondu au blanc.

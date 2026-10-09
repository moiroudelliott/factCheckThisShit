---
workflow: general-video
flow: automation
storyboard: yes
message: "Pendant un débat politique, SOURCÉ vérifie les chiffres en quelques secondes, sources à l'appui, sans prendre parti"
destination: youtube
aspect: 1920x1080
language: fr
audience: grand public curieux de politique, journalistes, personnes qui découvrent SOURCÉ
length: 120s
---

## Intent

Une vidéo de démo courte (≈ 2 minutes) qui capture les moments les plus intéressants des
débats publiés sur le site, pour qu'on voie à quoi ressemble SOURCÉ sans regarder un débat
entier. Demande de l'utilisateur : « capturer les moments les plus intéressants et en faire
une vidéo de démo courte de 2 minutes qui permet de visualiser à quoi ça ressemble ».

## Customizations

- Son : le son du débat (on entend la phrase), les cartes de vérification apparaissent,
  des titres courts à l'écran font le lien. Pas de voix off.
- Les cartes reprennent fidèlement le style de l'extension (extension/overlay.css,
  extension/content.js : carte en verre sombre, tag de verdict, explication, source).

## Notes

- Exigence de l'utilisateur : « vérifie à chaque fois minutieusement qu'on ne se trompe pas,
  ce serait con d'afficher ceux où on se trompe ». Chaque moment retenu est vérifié :
  citation exacte retranscrite au mot près sur l'extrait, verdict contrôlé sur source fiable.
- Les verdicts montrés sont ceux publiés sur le site (corrigés au besoin sur le site avant).
- Délai de vérification raccourci au montage : le dire discrètement à l'écran.
- Aucune mention, nulle part, de corrections faites par Claude.
- Équilibre politique : plusieurs camps, plusieurs types de verdict.

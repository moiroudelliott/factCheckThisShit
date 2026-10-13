---
workflow: general-video
flow: automation
storyboard: no
message: "Un doute ? Va checker l'info : pendant un débat, chaque affirmation vérifiée en direct, sources à l'appui"
destination: instagram-reels, tiktok
aspect: 1080x1920
language: fr
audience: grand public sur Instagram et TikTok, curieux de politique, qui ne connaît pas encore vachecker.info
length: "présentation ≈ 27 s ; moment ≈ 20-25 s"
---

## Intent

Lancement des comptes Instagram et TikTok de vachecker.info. Deux réels verticaux :

1. **Présentation (épinglée)** — un teaser, sans aucune vérification : accroche « Ils l'ont
   dit à la télé… », quatre phrases de politiques (Bardella, Attal, Bompard, Zemmour),
   « Vrai ? Faux ? » sur le creux de la musique, puis au drop « Va checker l'info ! » qui se
   replie en vachecker.info.
2. **Moment** — un seul extrait : l'orateur recadré, sous-titres mot à mot, « Vrai ou faux ? »
   avec ~2 s pour deviner, puis la carte de vérification (verdict + source), fin « Fiche
   complète sur vachecker.info ». Premier moment : Éric Zemmour, « Tous les terroristes sont
   musulmans » (BFMTV, 23 septembre 2021), FAUX.

## Assets

- assets/clips/ — extraits de la démo (videos/demo-2min/assets/clips), hors dépôt (droits).
- assets/words/ — transcriptions mot à mot (Whisper) des extraits, pour les sous-titres.
- assets/fonts/ — Newsreader, Archivo, IBM Plex Mono (OFL), comme le site.

## Customizations

- Son : présentation sur « No Title » (Technophobe), fichier fourni par l'utilisateur, calée
  pour que le drop tombe sur la fin ; les extraits passent par-dessus. Moments : le son du
  débat seul (musique ajoutée dans l'application au moment de poster).
- Recadrage : une fenêtre par extrait, et une nouvelle à chaque changement de plan de la
  réalisation (`recrop`), pour garder le visage dans le cadre.
- Cartes : même style que la démo et l'extension (verre sombre, tag de verdict, source).
- Sous-titres incrustés, mot à mot, sous l'image (jamais par-dessus les bandeaux des chaînes).
- Zones de sécurité communes TikTok / Instagram : contenu utile entre y ≈ 230 et y ≈ 1480,
  rien d'important à droite sous y ≈ 900 (boutons).
- Signature : drapeau + « Projet français, 100 % souverain » (formulation choisie par
  l'utilisateur, comme sur le site).

## Notes

- Textes des cartes repris tels quels des fiches publiées ; chaque verdict posté est vérifié
  à la source avant publication (Zemmour : Europol TE-SAT 2021 — 57 attentats dans l'UE en
  2020, dont 10 jihadistes).
- Extraits courts, source citée (chaîne, date) : limiter le risque de réclamation de droits.
- Publication : uniquement après accord explicite de l'utilisateur, post par post.

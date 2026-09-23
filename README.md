# SOURCÉ

Fact-checking en temps réel pour les débats politiques regardés sur YouTube
ou sur les lecteurs des chaînes (france.tv, LCP, Public Sénat, Twitch…) :
transcription (Whisper), identification des locuteurs par empreinte vocale,
extraction de talking points et vérification sourcée (Mistral + recherche
web souveraine, fact-checks déjà publiés par les rédactions, séries
Eurostat, votes de l'Assemblée nationale), avec un verdict et sa source en
quelques dizaines de secondes — pendant que le débat est encore en cours.

Prototype étudiant mené par Elliott Moiroud, Université Savoie Mont Blanc.
Site du projet : [source.codeminds.fr](https://source.codeminds.fr) — voir
aussi [`site/`](site/).

Le produit, c'est **l'extension Chrome** dans [`extension/`](extension/) —
elle affiche les cartes de vérification directement par-dessus la vidéo.
Le dossier racine ([`backend.py`](backend.py)) est le serveur Python qui fait
tourner Whisper et parle à l'API Mistral ; l'extension s'y connecte en
local. Pour le détail technique du pipeline (structure des données, seuils,
évènements socket.io), voir [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Démarrage rapide

Suivre [`SETUP_GUIDE.md`](SETUP_GUIDE.md) pour l'installation complète (CUDA,
FFmpeg, dépendances). En résumé :

1. **SearxNG** (recherche web du fact-checker, auto-hébergée) : `cd searxng && docker compose up -d`
2. **Backend Python** : `python backend.py`. Attendre `Modèle prêt.`
3. **Extension** : `chrome://extensions` → activer le *mode développeur* → *Charger
   l'extension non empaquetée* → sélectionner le dossier [`extension/`](extension/)
4. Ouvrir une vidéo (YouTube ou le direct d'une chaîne), cliquer l'icône de l'extension, remplir (ou laisser
   la détection automatique remplir) l'émission/les intervenants, puis
   *Démarrer l'analyse*

Le backend écoute en local uniquement (`127.0.0.1:5000`) et n'accepte, côté
navigateur, que l'extension Chrome. Un jeton d'accès optionnel
(`BACKEND_TOKEN`) ajoute une barrière contre les autres extensions et
applications locales — voir [`.env.example`](.env.example).

Tests (aucun GPU, clé ni réseau nécessaires) : `python tests/test_backend_smoke.py`
et les autres fichiers de [`tests/`](tests/) — voir [ARCHITECTURE.md](ARCHITECTURE.md#12-tests).

## Structure

| Dossier / fichier | Rôle |
|---|---|
| `extension/` | Extension Chrome (MV3) — le produit réel |
| `backend.py` | Point d'entrée du serveur (monkey-patch eventlet + lancement) |
| `server/` | Logique du serveur : Whisper, diarisation, Mistral, sources, cache — voir [ARCHITECTURE.md](ARCHITECTURE.md) |
| `tests/` | Tests unitaires et test de fumée du backend complet (sans GPU ni réseau) |
| `searxng/` | Instance SearxNG auto-hébergée (recherche web sans dépendance à un moteur tiers) |
| `site/` | Page vitrine statique du projet |
| `enroll.py`, `harvest_voices.py`, `remove_voice.py` | Outils CLI pour peupler/retirer des voix dans `voices/` (banque d'empreintes vocales) |
| `vocabulaire.txt` | Mots que Whisper doit s'attendre à entendre (sigles, partis, termes souvent mal transcrits) — modifiable |
| `purge_cache.py` | Purge les verdicts douteux ou signalés du cache de fact-checks (`--dry-run` d'abord ; sauvegarde automatique) |
| `data/` | Données téléchargées et journaux locaux (gitignored) : open data de l'Assemblée nationale (~40 Mo, au premier démarrage), signalements de verdicts (`reports.jsonl`) |
| `voices/` | Empreintes vocales enregistrées (gitignored, générées localement) |
| `ARCHITECTURE.md` | Fonctionnement technique détaillé du pipeline |
| `SETUP_GUIDE.md` | Installation détaillée du backend (CUDA, FFmpeg, SearxNG…) |

## Historique

Le projet a d'abord existé comme prototype React (webcam + micro, sans
diarisation). Il a été entièrement remplacé par l'extension Chrome actuelle ;
le prototype a été retiré du dépôt. Le projet s'appelait initialement
« vérif.live », renommé SOURCÉ.

# SOURCÉ

Fact-checking en temps réel pour les débats politiques regardés sur YouTube :
transcription (Whisper), identification des locuteurs par empreinte vocale,
extraction de talking points et vérification sourcée (Mistral + recherche
web souveraine), avec un verdict et sa source en quelques dizaines de
secondes — pendant que le débat est encore en cours.

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
4. Ouvrir une vidéo YouTube, cliquer l'icône de l'extension, remplir (ou laisser
   la détection automatique remplir) l'émission/les intervenants, puis
   *Démarrer l'analyse*

Le backend écoute en local uniquement (`127.0.0.1:5000`). Un jeton d'accès
optionnel (`BACKEND_TOKEN`) peut être ajouté si plusieurs apps tournent dans
le même navigateur — voir [`.env.example`](.env.example).

## Structure

| Dossier / fichier | Rôle |
|---|---|
| `extension/` | Extension Chrome (MV3) — le produit réel |
| `backend.py` | Serveur Flask/Socket.IO : Whisper, diarisation, Mistral, cache |
| `searxng/` | Instance SearxNG auto-hébergée (recherche web sans dépendance à un moteur tiers) |
| `site/` | Page vitrine statique du projet |
| `enroll.py`, `harvest_voices.py` | Outils CLI pour peupler `voices/` (banque d'empreintes vocales) |
| `voices/` | Empreintes vocales enregistrées (gitignored, générées localement) |
| `ARCHITECTURE.md` | Fonctionnement technique détaillé du pipeline |
| `SETUP_GUIDE.md` | Installation détaillée du backend (CUDA, FFmpeg, SearxNG…) |

## Historique

Le projet a d'abord existé comme prototype React (webcam + micro, sans
diarisation). Il a été entièrement remplacé par l'extension Chrome actuelle ;
le prototype a été retiré du dépôt. Le projet s'appelait initialement
« vérif.live », renommé SOURCÉ.

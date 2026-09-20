# vérif.live

Fact-checking en temps réel pour les débats politiques regardés sur YouTube :
transcription (Whisper), extraction de talking points et vérification sourcée
(Mistral + recherche web), avec identification des locuteurs par empreinte
vocale.

Le produit, c'est **l'extension Chrome** dans [`extension/`](extension/) —
elle affiche les cartes de vérification directement par-dessus la vidéo.
Le dossier racine ([`backend.py`](backend.py)) est le serveur Python qui fait
tourner Whisper et parle à l'API Mistral ; l'extension s'y connecte en
local.

## Démarrage rapide

1. Backend Python : suis [`SETUP_GUIDE.md`](SETUP_GUIDE.md) (CUDA, FFmpeg,
   dépendances), puis `python backend.py`. Attends `Modèle prêt.`
2. Extension : `chrome://extensions` → activer le *mode développeur* → *Charger
   l'extension non empaquetée* → sélectionner le dossier [`extension/`](extension/).
3. Ouvre une vidéo YouTube, clique l'icône de l'extension, remplis (ou laisse
   la détection automatique remplir) l'émission/les intervenants, puis
   *Démarrer l'analyse*.

Le backend écoute en local uniquement (`127.0.0.1:5000`). Un jeton d'accès
optionnel (`BACKEND_TOKEN`) peut être ajouté si plusieurs apps tournent dans
le même navigateur — voir [`.env.example`](.env.example).

## Structure

| Dossier / fichier | Rôle |
|---|---|
| `extension/` | Extension Chrome (MV3) — le produit réel |
| `backend.py` | Serveur Flask/Socket.IO : Whisper, diarisation, Mistral, cache |
| `enroll.py`, `harvest_voices.py` | Outils CLI pour peupler `voices/` (banque d'empreintes vocales) |
| `voices/` | Empreintes vocales enregistrées (gitignored, générées localement) |
| `SETUP_GUIDE.md` | Installation détaillée du backend (CUDA, FFmpeg…) |

## Historique

Le projet a d'abord existé comme prototype React (webcam + micro, sans
diarisation). Il a été entièrement remplacé par l'extension Chrome actuelle ;
le prototype a été retiré du dépôt.

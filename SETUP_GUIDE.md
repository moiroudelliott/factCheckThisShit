# Guide d'installation — SOURCÉ

Ce guide installe le **backend** (Whisper + diarisation + Mistral). Le
produit lui-même est l'extension Chrome dans [`extension/`](extension/),
chargée à l'étape 5 — voir aussi [`README.md`](README.md).

## Prérequis
- Python 3.10+
- Google Chrome
- NVIDIA GPU (RTX 4070 Super recommandé)
- CUDA 12.x installé

---

## Étape 1 — Fix PyTorch CUDA (OBLIGATOIRE)

Par défaut, pip installe une version CPU-only de PyTorch. Il faut la remplacer:

```bash
pip uninstall torch torchaudio -y
pip install torch==2.5.1+cu121 torchaudio==2.5.1+cu121 --index-url https://download.pytorch.org/whl/cu121
```

Vérifier:
```bash
python -c "import torch; print('CUDA disponible:', torch.cuda.is_available())"
# Doit afficher: CUDA disponible: True
```

---

## Étape 2 — Installer FFmpeg

faster-whisper utilise FFmpeg pour décoder les chunks audio WebM.

1. Télécharger: https://ffmpeg.org/download.html → Windows builds → ffmpeg-release-full.7z
2. Extraire et copier le dossier dans `C:\ffmpeg\`
3. Ajouter `C:\ffmpeg\bin` au PATH système (Paramètres → Variables d'environnement)
4. Vérifier dans un nouveau terminal:

```bash
ffprobe -version
```

---

## Étape 3 — Dépendances Python

```bash
cd factCheckThisShit
pip install -r requirements.txt
cp .env.example .env
# éditer .env et renseigner MISTRAL_API_KEY (https://console.mistral.ai/)
```

---

## Étape 4 — Pré-télécharger le modèle Whisper (~1,6 Go)

Le modèle est téléchargé une seule fois et mis en cache:

```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cuda', compute_type='float16'); print('Modèle prêt')"
```

Cache: `%USERPROFILE%\.cache\huggingface\hub\`

---

## Étape 5 — Lancer SearxNG (recherche web du fact-checker)

Le fact-checker s'appuie sur une instance **SearxNG auto-hébergée** (Brave +
Mojeek uniquement — ni Google ni Bing, voir [`ARCHITECTURE.md`](ARCHITECTURE.md))
plutôt que d'interroger un moteur tiers directement. Nécessite Docker Desktop.

```bash
cd searxng
docker compose up -d
```

Vérifier: `curl http://127.0.0.1:8080/healthz` doit répondre `200`. Sans
instance joignable, le backend le signale au démarrage et le fact-check tourne
en dégradé (verdicts basés sur les connaissances du modèle, sans preuve web) —
ça ne bloque rien, mais dégrade la qualité des verdicts.

---

## Étape 6 — Charger l'extension Chrome

1. `chrome://extensions`
2. Activer *Mode développeur* (coin supérieur droit)
3. *Charger l'extension non empaquetée* → sélectionner le dossier `extension/`

---

## Étape 7 — Lancer l'application

**Terminal — Backend:**
```bash
python backend.py
```
Attendre: `Modèle prêt.` puis `Running on http://127.0.0.1:5000`

**Extension:**
1. Ouvrir une vidéo de débat sur YouTube (ou le direct d'une chaîne : france.tv, LCP, Public Sénat, Twitch…)
2. Cliquer l'icône SOURCÉ (point rouge) dans la barre d'extensions
3. Vérifier que l'émission/les intervenants sont bien détectés (ou les
   compléter à la main), puis *Démarrer l'analyse*
4. Autoriser le partage d'onglet si Chrome le demande

---

## Étape 8 — Vérification

1. Après quelques secondes de parole, un badge "qui parle" apparaît en haut
   à gauche de la vidéo
2. Après ~20-30s de propos substantiel, les premières cartes de vérification
   apparaissent en haut à droite de la vidéo
3. La puce SOURCÉ (en bas à droite de la vidéo, au-dessus des contrôles)
   affiche l'état de l'analyse ; son bouton *Récap* liste tous les points
   extraits et permet de les exporter en Markdown
4. *■* arrête l'analyse : les derniers verdicts arrivent pendant la
   « finalisation », le récap reste consultable et exportable, *✕* ferme
5. Vérifier que le GPU travaille pendant la transcription: `nvidia-smi`
   (utilisation GPU doit monter)

Tests (sans GPU, sans clé, sans réseau) : `python tests/test_backend_smoke.py`
et les autres fichiers de `tests/` — voir `ARCHITECTURE.md` §12.

---

## Dépannage

| Problème | Solution |
|---|---|
| `torch.cuda.is_available()` retourne False | Refaire l'étape 1 (PyTorch CUDA) |
| `ffprobe: command not found` | Ajouter FFmpeg au PATH (étape 2) |
| Popup affiche "backend éteint" | Le backend n'écoute que sur `127.0.0.1:5000` — vérifier qu'il tourne (`python backend.py`) et qu'aucun autre process n'occupe le port |
| Puce affiche "jeton invalide" | `BACKEND_TOKEN` est défini dans `.env` mais ne correspond pas au champ "Jeton d'accès" (section Avancé de la popup) — ou vice-versa |
| Pas de transcription mais pas d'erreur | Vérifier que l'onglet capturé joue bien du son (icône haut-parleur dans l'onglet Chrome) et que le son du lecteur vidéo n'est pas coupé (la puce l'indique) |
| Puce affiche "clé Mistral invalide" / "crédit Mistral épuisé" | Vérifier `MISTRAL_API_KEY` dans `.env` et le crédit sur console.mistral.ai, puis relancer le backend |
| Console backend : "repli sur small (CPU)" | Pas de GPU CUDA utilisable : refaire l'étape 1 — la transcription fonctionne mais lentement |
| Puce affiche "arrêtée : vidéo changée" | Normal : l'analyse est liée à une vidéo ; relancer depuis la popup sur la nouvelle |
| Latence de transcription élevée | Vérifier que CUDA est bien actif (`nvidia-smi` pendant l'analyse) |
| Console backend affiche "SearxNG injoignable" | Docker Desktop n'est pas lancé, ou `cd searxng && docker compose up -d` n'a pas été fait (étape 5) — le fact-check continue de fonctionner mais sans preuve web |
| Port 5000 déjà utilisé | Changer le port dans `backend.py` (`socketio.run(...)`) et dans `BACKEND_URL` (`extension/offscreen.js` et `extension/popup.js`) |

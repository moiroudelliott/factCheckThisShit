# Setup Guide — FactCheckThis MVP

## Prérequis
- Python 3.10+
- Node.js 18+
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

faster-whisper utilise FFmpeg pour décoder les fichiers audio WebM.

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
```

---

## Étape 4 — Pré-télécharger le modèle Whisper (~1.5 Go)

Le modèle est téléchargé une seule fois et mis en cache:

```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cuda', compute_type='float16'); print('Modèle prêt')"
```

Cache: `%USERPROFILE%\.cache\huggingface\hub\`

---

## Étape 5 — Dépendances Node

```bash
npm install
```

---

## Étape 6 — Lancer l'application

**Terminal 1 — Backend:**
```bash
python backend.py
```
Attendre: `Modèle prêt.` puis `Running on http://0.0.0.0:5000`

**Terminal 2 — Frontend:**
```bash
npm start
```
Ouvre automatiquement http://localhost:3000

---

## Étape 7 — Vérification

1. Ouvrir http://localhost:3000
2. Cliquer "Démarrer la transcription"
3. Autoriser la caméra et le micro dans le navigateur
4. La webcam s'affiche à gauche — parler normalement
5. Après ~5 secondes, les premiers mots apparaissent à droite
6. Vérifier que le GPU travaille: `nvidia-smi` (utilisation GPU doit monter)

---

## Setup OBS pour transcription de débats

Une fois le MVP fonctionnel avec webcam/micro réels:

### Installer VB-Cable (audio virtuel)
1. Télécharger: https://vb-audio.com/Cable/
2. Installer et redémarrer Windows

### Configurer OBS
1. OBS → Settings → Audio → "Desktop Audio" → CABLE Input (VB-Audio)
2. Ajouter la source vidéo (capture fenêtre, capture jeu, etc.)
3. Tools → "Start Virtual Camera"

### Dans le navigateur
Quand l'appli demande les permissions:
- **Caméra** → sélectionner "OBS Virtual Camera"
- **Micro** → sélectionner "CABLE Output (VB-Audio Virtual Cable)"

---

## Dépannage

| Problème | Solution |
|---|---|
| `torch.cuda.is_available()` retourne False | Refaire l'étape 1 (PyTorch CUDA) |
| `ffprobe: command not found` | Ajouter FFmpeg au PATH (étape 2) |
| "Accès refusé caméra/micro" | Autoriser dans chrome://settings/content/camera |
| Pas de transcription mais pas d'erreur | Parler plus fort ou réduire CHUNK_DURATION_MS à 3000 dans WhisperMVP.jsx |
| Latence > 10s | Vérifier que CUDA est bien actif (`nvidia-smi` pendant la transcription) |
| Port 5000 déjà utilisé | Changer le port dans backend.py et dans WhisperMVP.jsx (SOCKET_URL) |

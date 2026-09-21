"""Bootstrap : app Flask/SocketIO + chargement des modèles lourds (Whisper,
ECAPA-TDNN) + diagnostics de démarrage.

IMPORTANT : ce module fait des appels réseau et charge des modèles GPU au
moment de l'import — il ne doit être importé qu'APRÈS eventlet.monkey_patch()
(voir backend.py, le point d'entrée). C'est pour ça que ce monkey_patch ne
peut pas vivre ici : il doit précéder même l'import de `requests`."""

import os

import requests
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from faster_whisper import WhisperModel

from server.config import (
    SEARXNG_URL, MISTRAL_API_KEY, BACKEND_TOKEN, WHISPER_MODEL, DIARIZATION_DEVICE,
)

app = Flask(__name__)
CORS(app, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", max_http_buffer_size=50 * 1024 * 1024)

# ── Whisper (transcription) ────────────────────────────────────────────────
# Chargement au démarrage du serveur (pas au premier chunk).
print(f"Chargement du modèle Whisper {WHISPER_MODEL} (CUDA)…")
try:
    model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
except Exception as e:
    print(f"⚠️  Échec du chargement de {WHISPER_MODEL} ({type(e).__name__}: {e}) — repli sur medium")
    model = WhisperModel("medium", device="cuda", compute_type="float16")
print("Modèle prêt.")

import threading
model_lock = threading.Lock()

try:
    requests.get(f"{SEARXNG_URL}/healthz", timeout=2)
    print(f"SearxNG local détecté sur {SEARXNG_URL}.")
except Exception:
    print(f"⚠️  SearxNG injoignable sur {SEARXNG_URL} — fact-checking sans recherche web "
          f"(cf. searxng/docker-compose.yml : `docker compose up -d`).")

if not MISTRAL_API_KEY:
    print("⚠️  MISTRAL_API_KEY non définie — les talking points seront désactivés.")

if not BACKEND_TOKEN:
    print("⚠️  BACKEND_TOKEN non défini — le backend accepte toute connexion locale sans "
          "authentification (voir .env.example). Recommandé si d'autres apps tournent dans le même navigateur.")

# ── Diarisation : identification des locuteurs par empreinte vocale ───────
# ECAPA-TDNN (SpeechBrain, non gated) : un embedding par segment transcrit,
# clustering incrémental par session → labels stables "Intervenant A/B/C…".
#
# IMPORTANT: ECAPA tourne sur CPU. Sur GPU, le cuDNN de torch entre en conflit
# avec celui de CTranslate2 (faster-whisper) dans le même processus Windows
# ("Could not load symbol cudnnGetLibConfig") et crash le backend. Le modèle
# est minuscule, le CPU suffit largement (~100 ms/segment).
try:
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)  # torch.load de speechbrain, bruyant
    import torch
    from speechbrain.inference.speaker import EncoderClassifier
    print(f"Chargement du modèle de diarisation (ECAPA-TDNN, {DIARIZATION_DEVICE})…")
    speaker_encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": DIARIZATION_DEVICE},
    )
    DIARIZATION = True
    print("Diarisation prête.")
except Exception as e:
    print(f"⚠️  Diarisation indisponible ({type(e).__name__}: {e}) — points non attribués. (pip install speechbrain)")
    DIARIZATION = False
    speaker_encoder = None

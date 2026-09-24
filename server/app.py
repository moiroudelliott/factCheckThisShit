"""Bootstrap : app Flask/SocketIO + chargement des modèles lourds (Whisper,
ECAPA-TDNN) + diagnostics de démarrage.

IMPORTANT : ce module fait des appels réseau et charge des modèles GPU au
moment de l'import — il ne doit être importé qu'APRÈS eventlet.monkey_patch()
(voir backend.py, le point d'entrée). C'est pour ça que ce monkey_patch ne
peut pas vivre ici : il doit précéder même l'import de `requests`."""

import re

import requests
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from faster_whisper import WhisperModel

from server import network
from server.config import (
    SEARXNG_URL, MISTRAL_API_KEY, BACKEND_TOKEN, WHISPER_MODEL, DIARIZATION_DEVICE, ALLOWED_ORIGINS, FORCE_IPV4,
)

# Origines autorisées : l'extension Chrome (popup + document offscreen,
# origine chrome-extension://<id>) — plus « * ». Avec « * », n'importe quelle
# page ouverte dans le navigateur pouvait appeler /analyze_video ou ouvrir
# un socket et consommer la clé Mistral (le jeton reste désactivé par
# défaut). Les clients hors navigateur n'envoient pas d'Origin et ne sont
# pas concernés. ALLOWED_ORIGINS (.env) ajoute d'autres origines si besoin.
_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")


def origin_allowed(origin, environ=None) -> bool:
    return bool(origin) and (bool(_EXTENSION_ORIGIN.match(origin)) or origin in ALLOWED_ORIGINS)


app = Flask(__name__)
CORS(app, origins=[_EXTENSION_ORIGIN, *ALLOWED_ORIGINS])
socketio = SocketIO(app, cors_allowed_origins=origin_allowed, async_mode="eventlet", max_http_buffer_size=50 * 1024 * 1024)

# ── Whisper (transcription) ────────────────────────────────────────────────
# Chargement au démarrage du serveur (pas au premier chunk).
# Repli en cascade : le modèle demandé sur GPU, puis medium sur GPU (VRAM
# insuffisante), puis small sur CPU — sans GPU CUDA le backend plantait à
# l'import sur une trace brute au lieu de démarrer (lentement) avec un
# message clair.
_WHISPER_ATTEMPTS = [(WHISPER_MODEL, "cuda", "float16"), ("medium", "cuda", "float16"), ("small", "cpu", "int8")]
model = None
for _name, _device, _compute in dict.fromkeys(_WHISPER_ATTEMPTS):
    print(f"Chargement du modèle Whisper {_name} ({_device.upper()})…")
    try:
        model = WhisperModel(_name, device=_device, compute_type=_compute)
        break
    except Exception as e:
        print(f"⚠️  Échec du chargement de {_name} sur {_device} ({type(e).__name__}: {e})")
if model is None:
    raise SystemExit("✗ Aucun modèle Whisper n'a pu être chargé — voir SETUP_GUIDE.md (CUDA, FFmpeg).")
if _device == "cpu":
    print("⚠️  Pas de GPU CUDA utilisable : transcription sur CPU avec le modèle small — nettement plus "
          "lente et moins précise (voir SETUP_GUIDE.md, étape 1).")
print("Modèle prêt.")

import threading
model_lock = threading.Lock()

# Avant toute requête sortante (tâches de fond comprises) : repli IPv4 si
# l'IPv6 du réseau ne passe pas (voir server/network.py)
if network.configure(FORCE_IPV4) == "ipv4":
    print("⚠️  IPv6 inutilisable sur ce réseau : connexions sortantes forcées en IPv4 "
          "(sinon 8 à 40 s perdues à chaque appel Mistral). FORCE_IPV4=0 dans .env pour désactiver.")

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

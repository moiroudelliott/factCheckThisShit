"""Constantes et variables d'environnement — aucun effet de bord (pas de
print, pas de chargement de modèle) : les diagnostics de démarrage vivent
dans app.py, qui sait dans quel ordre les afficher."""

import os

from dotenv import load_dotenv
load_dotenv()

# ── Mistral ────────────────────────────────────────────────────────────────
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-medium-latest")  # medium: suit bien le prompt d'extraction; small le sur-applique
MISTRAL_MAX_RETRIES = 3     # tentatives supplémentaires sur 429 (rate limit)
MISTRAL_RETRY_BASE_S = 2.0  # backoff exponentiel: 2s, 4s, 8s (sauf Retry-After fourni par l'API)

# ── Recherche web ──────────────────────────────────────────────────────────
# Instance SearxNG auto-hébergée (docker-compose dans searxng/), pas d'appel
# direct à un moteur US : voir ARCHITECTURE.md. SEARXNG_URL doit rester un
# hôte local (127.0.0.1) — c'est le backend qui interroge SearxNG, jamais un
# tiers qui voit passer les claims.
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8080").rstrip("/")

# Domaines jamais retenus comme preuve (filtrés des résultats AVANT le prompt).
# Réseaux sociaux et plateformes vidéo : ce ne sont pas des sources, et la
# « preuve » y est souvent la déclaration même qu'on vérifie. Sites de
# désinformation notoires (cf. Décodex) : une liste courte, volontairement
# éditoriale — à ajuster ici. Correspondance par nom de domaine exact ou
# sous-domaine (fr.x.com est couvert par x.com).
EXCLUDED_SOURCE_DOMAINS = (
    # réseaux sociaux / plateformes
    "facebook.com", "fb.com", "x.com", "twitter.com", "instagram.com", "tiktok.com",
    "youtube.com", "youtu.be", "dailymotion.com", "reddit.com", "linkedin.com",
    "threads.net", "bsky.app", "t.me", "telegram.me", "pinterest.com", "pinterest.fr",
    # désinformation notoire
    "ripostelaique.com", "bvoltaire.fr", "fdesouche.com", "egaliteetreconciliation.fr",
    "francesoir.fr", "reseauinternational.net", "lesmoutonsenrages.fr", "wikistrike.com",
    "sputniknews.com", "rt.com",
)

# Rédactions de vérification françaises dont les fact-checks publiés sont
# indexés (server/known_factchecks.py) : (nom affiché, flux RSS). L'AFP
# Factuel refuse les requêtes automatiques (403) et n'est donc pas listée.
FACTCHECK_FEEDS = (
    ("Les Décodeurs (Le Monde)", "https://www.lemonde.fr/les-decodeurs/rss_full.xml"),
    ("CheckNews (Libération)", "https://www.liberation.fr/arc/outboundfeeds/rss-all/category/checknews/?outputType=xml"),
    ("Vrai ou fake (franceinfo)", "https://www.francetvinfo.fr/vrai-ou-fake.rss"),
    ("Fake off (20 Minutes)", "https://www.20minutes.fr/feeds/rss-fake-off.xml"),
    ("Les Surligneurs", "https://www.lessurligneurs.eu/feed/"),
)
FACTCHECK_FEEDS_REFRESH_S = 3600
FACTCHECK_INDEX_DB = os.environ.get("FACTCHECK_INDEX_DB") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "factchecks_index.db")
# Rubriques de fact-checking reconnues dans les résultats web (hôte + début
# du chemin) : annotées « FACT-CHECK PUBLIÉ », au-dessus de la presse
FACTCHECK_SECTIONS = (
    "factuel.afp.com", "lemonde.fr/les-decodeurs", "liberation.fr/checknews",
    "francetvinfo.fr/vrai-ou-fake", "20minutes.fr/fake-off", "lessurligneurs.eu",
    "tf1info.fr/politique/les-verificateurs", "tf1info.fr/societe/les-verificateurs",
)

# Votes à l'Assemblée nationale (server/votes.py) : open data officiel,
# téléchargé dans AN_DATA_DIR (ignoré par git) et rafraîchi chaque semaine.
# AN_VOTES=0 dans .env pour désactiver (~40 Mo au premier démarrage).
AN_VOTES_ENABLED = os.environ.get("AN_VOTES", "1") != "0"
AN_LEGISLATURES = (16, 17)
AN_REFRESH_S = 7 * 86400
AN_DATA_DIR = os.environ.get("AN_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "assemblee")

# Signalements de verdicts (bouton ⚑ de l'extension), une ligne JSON par signalement
REPORTS_FILE = os.environ.get("REPORTS_FILE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "reports.jsonl")

# ── Buffer de transcription → analyse Mistral ─────────────────────────────
FLUSH_INTERVAL = 22    # secondes max entre deux analyses Mistral
MIN_WORDS = 30         # ne pas appeler Mistral avec moins de 30 mots (trop peu pour un talking point)
MAX_BUFFER_WORDS = 55  # flush anticipé dès que le buffer est assez dense (échange rapide = analyse plus tôt)
CHECKWORTHY_MIN = 6      # note de vérifiabilité (0-10, par Mistral) min pour vérifier une affirmation — dessous : « trop vague »
MIN_WORDS_ON_PAUSE = 12  # pause de parole (chunk sans texte neuf) : analyser la fin de tirade plutôt que l'oublier
MIN_WORDS_ON_STOP = 8    # à l'arrêt : dernier buffer analysé s'il reste au moins ça
FINISH_TIMEOUT_S = 45    # arrêt propre : attente max des analyses / fact-checks encore en vol
CHUNK_OVERLAP_S = 1.5    # chevauchement entre deux chunks (= OVERLAP_MS de offscreen.js)
CHUNK_S = 10.0           # durée d'un chunk (= CHUNK_MS de offscreen.js) : date un segment (fin de chunk − CHUNK_S + début du segment)

# Jeton partagé optionnel : sans lui, quiconque atteint ce port (même onglet
# tiers ouvert dans le même navigateur) peut piloter le backend et consommer
# la clé Mistral. Si non défini, le serveur reste ouvert (comportement
# historique) mais le signale au démarrage.
BACKEND_TOKEN = os.environ.get("BACKEND_TOKEN", "").strip()

# Origines navigateur autorisées EN PLUS de l'extension Chrome (voir app.py),
# séparées par des virgules. Vide par défaut.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# ── Whisper ────────────────────────────────────────────────────────────────
# large-v3-turbo : nettement meilleur que medium sur les chiffres et noms
# propres (ce qu'on fact-checke), ~6 Go VRAM. Repli sur medium si le
# chargement échoue (voir app.py).
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")

# ── Diarisation (SpeechBrain ECAPA-TDNN) ──────────────────────────────────
# IMPORTANT: ECAPA tourne sur CPU. Sur GPU, le cuDNN de torch entre en
# conflit avec celui de CTranslate2 (faster-whisper) dans le même processus
# Windows ("Could not load symbol cudnnGetLibConfig") et crash le backend.
# Le modèle est minuscule, le CPU suffit largement (~100 ms/segment).
DIARIZATION_THRESHOLD = 0.34  # similarité cosinus min pour rattacher un segment à un locuteur connu
MIN_NEW_SPEAKER_SEC = 2.0     # un segment plus court ne peut PAS créer un nouveau locuteur
MAX_SPEAKERS = 12             # au-delà, toujours rattacher au plus proche
PROBE_MATCH_T = 0.28          # seuil (plus tolérant) des sondes temps réel "qui parle"
DIARIZATION_DEVICE = os.environ.get("DIARIZATION_DEVICE", "cpu")

# ── Banque d'empreintes vocales ────────────────────────────────────────────
# Il n'existe aucune API publique d'empreintes de personnalités (un embedding
# n'est comparable qu'au sein d'un même modèle + terrain miné RGPD). On
# construit donc la nôtre : empreintes ECAPA locales dans voices/, alimentées
# manuellement (enroll.py) ou automatiquement quand un locuteur a été
# identifié de façon fiable (vote LLM ou match acoustique).
VOICES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "voices")
VOICE_MATCH_THRESHOLD = 0.45   # cos min entre centroïde de session et empreinte en banque
VOICE_MATCH_MARGIN = 0.08      # écart min avec la 2e meilleure empreinte de TOUTE la banque (anti-confusion)
VOICE_MATCH_SOLO_BONUS = 0.10  # une seule voix en banque (pas de 2e pour la marge) : seuil relevé d'autant
VOICE_ENROLL_MIN_SEGMENTS = 8  # segments min pour auto-enrôler une voix
VOICE_ENROLL_MIN_VOTES = 3     # votes LLM concordants (et aucun vote contraire) avant d'enrôler : une empreinte en banque est définitive

# ── Cache persistant des fact-checks ──────────────────────────────────────
# Les politiques répètent les mêmes claims pendant des mois : un claim déjà
# vérifié (cette session ou une précédente) obtient son verdict
# instantanément, sans recherche web ni appel Mistral.
CACHE_DB = os.environ.get("FACTCHECK_CACHE_DB") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "factcheck_cache.db")
CACHE_TTL_DAYS = 30      # les chiffres politiques/économiques périment
CACHE_MIN_CONF = 60      # ne jamais mettre en cache un verdict peu sûr
CACHE_SIM_THRESHOLD = 0.75  # similarité (mots-clés) pour considérer deux claims identiques

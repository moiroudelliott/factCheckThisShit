import eventlet
# Rendre les I/O réseau coopératives AVANT tout autre import : sans ça, chaque
# appel HTTP (Mistral ~3 s, recherche web ~1 s) gèle TOUT le serveur — y compris
# la réception des chunks audio. Source majeure de latence cumulée.
eventlet.monkey_patch(socket=True, select=True)
import eventlet.semaphore

import os
import re
import json
import time
import sqlite3
import tempfile
import threading
import unicodedata

import requests
from dotenv import load_dotenv
load_dotenv()

# Recherche web pour fonder les verdicts (pip install ddgs)
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS  # ancien nom du package
    except ImportError:
        DDGS = None

from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from faster_whisper import WhisperModel

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-medium-latest")  # medium: suit bien le prompt d'extraction; small le sur-applique
FLUSH_INTERVAL = 22    # secondes max entre deux analyses Mistral
MIN_WORDS = 30         # ne pas appeler Mistral avec moins de 30 mots (trop peu pour un talking point)
MAX_BUFFER_WORDS = 55  # flush anticipé dès que le buffer est assez dense (échange rapide = analyse plus tôt)

app = Flask(__name__)
CORS(app, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", max_http_buffer_size=50 * 1024 * 1024)

# Chargement du modèle au démarrage du serveur (pas au premier chunk).
# large-v3-turbo : nettement meilleur que medium sur les chiffres et noms propres
# (ce qu'on fact-checke), ~6 Go VRAM. Repli sur medium si le chargement échoue.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
print(f"Chargement du modèle Whisper {WHISPER_MODEL} (CUDA)…")
try:
    model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
except Exception as e:
    print(f"⚠️  Échec du chargement de {WHISPER_MODEL} ({type(e).__name__}: {e}) — repli sur medium")
    model = WhisperModel("medium", device="cuda", compute_type="float16")
print("Modèle prêt.")

if DDGS is None:
    print("⚠️  Package 'ddgs' absent (pip install ddgs) — fact-checking sans recherche web.")

if not MISTRAL_API_KEY:
    print("⚠️  MISTRAL_API_KEY non définie — les talking points seront désactivés.")

model_lock = threading.Lock()

# ── Diarisation : identification des locuteurs par empreinte vocale ───────────
# ECAPA-TDNN (SpeechBrain, non gated) : un embedding par segment transcrit,
# clustering incrémental par session → labels stables "Intervenant A/B/C…".
#
# IMPORTANT: ECAPA tourne sur CPU. Sur GPU, le cuDNN de torch entre en conflit
# avec celui de CTranslate2 (faster-whisper) dans le même processus Windows
# ("Could not load symbol cudnnGetLibConfig") et crash le backend. Le modèle
# est minuscule, le CPU suffit largement (~100 ms/segment).

import numpy as np

DIARIZATION_THRESHOLD = 0.34  # similarité cosinus min pour rattacher un segment à un locuteur connu
MIN_NEW_SPEAKER_SEC = 2.0     # un segment plus court ne peut PAS créer un nouveau locuteur
MAX_SPEAKERS = 12             # au-delà, toujours rattacher au plus proche
PROBE_MATCH_T = 0.28          # seuil (plus tolérant) des sondes temps réel "qui parle"
DIARIZATION_DEVICE = os.environ.get("DIARIZATION_DEVICE", "cpu")

try:
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)  # torch.load de speechbrain, bruyant
    import torch
    from speechbrain.inference.speaker import EncoderClassifier
    from faster_whisper.audio import decode_audio
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


class SpeakerTracker:
    """Clustering incrémental des voix d'une session. Chaque locuteur est un
    centroïde d'embeddings ; un segment rejoint le locuteur le plus proche
    (cosinus ≥ seuil) ou en crée un nouveau."""

    def __init__(self, threshold: float = DIARIZATION_THRESHOLD):
        self.threshold = threshold
        self.sums = []      # somme des embeddings normalisés par locuteur
        self.counts = []
        self.last = ""      # dernier label (repli pour les segments trop courts)

    @staticmethod
    def label_for(idx: int) -> str:
        return f"Intervenant {chr(65 + idx)}" if idx < 26 else f"Intervenant {idx + 1}"

    def _best(self, emb):
        best, best_sim = -1, -1.0
        for i, (s, c) in enumerate(zip(self.sums, self.counts)):
            centroid = s / c
            centroid = centroid / (np.linalg.norm(centroid) + 1e-8)
            sim = float(np.dot(emb, centroid))
            if sim > best_sim:
                best, best_sim = i, sim
        return best, best_sim

    def match(self, emb):
        """Lecture seule : (label, similarité) du locuteur le plus proche,
        sans modifier les clusters. Utilisé par les sondes temps réel."""
        emb = emb / (np.linalg.norm(emb) + 1e-8)
        best, sim = self._best(emb)
        return (self.label_for(best), sim) if best >= 0 else ("", -1.0)

    def assign(self, emb, dur: float) -> str:
        emb = emb / (np.linalg.norm(emb) + 1e-8)
        best, best_sim = self._best(emb)
        if best >= 0 and best_sim >= self.threshold:
            # Même voix : rejoint le locuteur et affine son empreinte
            self.sums[best] += emb
            self.counts[best] += 1
            idx = best
        elif best >= 0 and (dur < MIN_NEW_SPEAKER_SEC or len(self.sums) >= MAX_SPEAKERS):
            # Segment court (interjection, brouhaha) : rattaché au plus proche
            # SANS polluer son centroïde — les interjections créaient des
            # locuteurs fantômes (11 labels pour 6 voix réelles)
            idx = best
        else:
            self.sums.append(emb.copy())
            self.counts.append(1)
            idx = len(self.sums) - 1
        self.last = self.label_for(idx)
        return self.last


def speaker_label(tracker, wav, seg) -> str:
    """Label du locuteur d'un segment Whisper (seg.start/end relatifs au chunk)."""
    if not DIARIZATION or tracker is None or wav is None:
        return ""
    piece = wav[int(seg.start * 16000):int(seg.end * 16000)]
    if len(piece) < 8000:  # < 0,5 s : embedding peu fiable → locuteur précédent
        return tracker.last
    try:
        with torch.no_grad():
            t = torch.from_numpy(piece).float().unsqueeze(0)
            emb = speaker_encoder.encode_batch(t).squeeze().cpu().numpy()
        return tracker.assign(emb, float(seg.end - seg.start))
    except Exception as e:
        print(f"[Diar error] {type(e).__name__}: {e}")
        return tracker.last


# ── Banque d'empreintes vocales ───────────────────────────────────────────────
# Il n'existe aucune API publique d'empreintes de personnalités (un embedding
# n'est comparable qu'au sein d'un même modèle + terrain miné RGPD). On
# construit donc la nôtre : empreintes ECAPA locales dans voices/, alimentées
# manuellement (enroll.py) ou automatiquement en fin de session quand un
# locuteur a été identifié de façon fiable par les votes LLM.

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
VOICE_MATCH_THRESHOLD = 0.45   # cos min entre centroïde de session et empreinte en banque
VOICE_MATCH_MARGIN = 0.08      # écart min avec la 2e meilleure empreinte (anti-confusion)
VOICE_ENROLL_MIN_SEGMENTS = 8  # segments min pour auto-enrôler une voix en fin de session

_voice_bank: dict = {}  # nom → embedding normalisé


def _voice_slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "voix"


def load_voice_bank(verbose: bool = True):
    _voice_bank.clear()
    idx_path = os.path.join(VOICES_DIR, "index.json")
    if not os.path.exists(idx_path):
        return
    try:
        with open(idx_path, encoding="utf-8") as f:
            index = json.load(f)
        for name, meta in index.items():
            p = os.path.join(VOICES_DIR, meta.get("file", ""))
            if os.path.exists(p):
                v = np.load(p)
                _voice_bank[name] = v / (np.linalg.norm(v) + 1e-8)
        if _voice_bank and verbose:
            print(f"[Voix] {len(_voice_bank)} empreinte(s) en banque: {', '.join(_voice_bank)}")
    except Exception as e:
        print(f"[Voix] erreur chargement banque: {type(e).__name__}: {e}")


def save_voice(name: str, emb, auto: bool = False):
    os.makedirs(VOICES_DIR, exist_ok=True)
    fn = _voice_slug(name) + ".npy"
    np.save(os.path.join(VOICES_DIR, fn), emb)
    idx_path = os.path.join(VOICES_DIR, "index.json")
    index = {}
    if os.path.exists(idx_path):
        try:
            with open(idx_path, encoding="utf-8") as f:
                index = json.load(f)
        except Exception:
            pass
    index[name] = {"file": fn, "auto": auto, "updated": time.time()}
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    _voice_bank[name] = emb / (np.linalg.norm(emb) + 1e-8)
    print(f"[Voix] empreinte {'auto-' if auto else ''}enregistrée: {name}")


load_voice_bank()


def build_transcript(entries: list) -> str:
    """Transcript annoté par locuteur, tours de parole consécutifs fusionnés."""
    if not any(label for label, _ in entries):
        return " ".join(t for _, t in entries)
    lines, cur_label, cur_texts = [], None, []
    for label, t in entries:
        label = label or cur_label or "Intervenant ?"
        if label != cur_label and cur_texts:
            lines.append(f"{cur_label}: {' '.join(cur_texts)}")
            cur_texts = []
        cur_label = label
        cur_texts.append(t)
    if cur_texts:
        lines.append(f"{cur_label}: {' '.join(cur_texts)}")
    return "\n".join(lines)

# ── Prompts Mistral ────────────────────────────────────────────────────────────

MISTRAL_PROMPT_TEMPLATE = """Tu es un analyste de débats politiques français. Extrais les talking points du passage suivant.
{context_block}Transcription (~25 secondes de débat, possiblement coupée en début/fin):

\"\"\"{text}\"\"\"
{history_block}

MISSION: un passage de débat contient presque toujours 1 à 3 talking points. Extrais-les.
Ne retourne [] QUE si le passage est réellement vide de contenu politique (politesses, gestion de parole, phrases incompréhensibles). Un tableau vide doit rester RARE.

Réponds UNIQUEMENT avec un tableau JSON valide, sans markdown:
[{{"type": "TYPE", "texte": "le point condensé en une phrase claire", "qui": "qui l'a dit, ou chaîne vide"}}]

Types:
- "affirmation" = fait VÉRIFIABLE: chiffre, date, événement, fait historique ou économique.
  Ex: "BYD est le leader chinois de l'automobile électrique"
  Ex: "Les socialistes français et allemands se sont fait la guerre en 1914"
  Ex: "L'Union européenne impose la fin du moteur thermique en 2035"
- "argument" = raisonnement cause-effet ou proposition concrète.
  Ex: "Les fermetures d'usines s'expliquent d'abord par le niveau des charges sociales"
- "subjectif" = opinion, jugement de valeur, promesse vague — non vérifiable.
  Ex: "Le modèle économique actuel abandonne les classes populaires"
- "remarque" = accusation ou commentaire politique visant l'adversaire.
  Ex: "L'adversaire est accusé d'admirer des dirigeants hostiles aux intérêts de la France"
  Ex: "L'adversaire est accusé de fantasmer une France qui n'a jamais existé"
- "question" = interpellation directe sur un sujet politique
- "accord" / "désaccord" = convergence ou réfutation explicite d'un propos adverse

RÈGLES:
1. {attribution_rule}
2. Ignore la pure gestion de plateau ("laissez-le parler", interruptions) — MAIS les attaques et accusations politiques entre débatteurs sont des points valides (type "remarque").
3. Ne répète pas un point déjà dans l'historique, même reformulé. Les points NOUVEAUX doivent toujours être extraits.
4. PRIORITÉ ABSOLUE aux faits vérifiables: si le passage contient un chiffre, une date ou un fait précis, il DOIT devenir un point de type "affirmation"."""


FACTCHECK_PROMPT_TEMPLATE = """Tu es un fact-checker expert sur les données françaises et européennes. Nous sommes le {today}.
{context_block}
Affirmation à vérifier: "{claim}"

{evidence_block}

Évalue la véracité en te basant PRIORITAIREMENT sur les résultats de recherche ci-dessus (leur fiabilité est annotée), complétés par tes connaissances.
Réponds UNIQUEMENT avec un objet JSON valide, sans markdown:
{{"verdict": "VERDICT", "confiance": 85, "explication": "une phrase courte et précise", "source": "nom de la source (ex: INSEE, Eurostat, Le Monde)", "url": "URL du résultat de recherche utilisé, ou chaîne vide"}}

Verdicts disponibles:
- "vrai": affirmation exacte et vérifiable
- "partiellement_vrai": vrai mais incomplet ou imprécis
- "trompeur": techniquement vrai mais donne une fausse impression
- "faux": factuellement incorrect
- "non_verifiable": ni les résultats de recherche ni tes connaissances ne permettent de trancher

RÈGLES DE RIGUEUR:
- Un verdict tranché ("vrai", "faux", "trompeur") exige AU MOINS deux sources indépendantes concordantes, OU une SOURCE OFFICIELLE (INSEE, Eurostat, Légifrance, parlement…). Sinon: "partiellement_vrai" ou "non_verifiable".
- Pour une affirmation CAUSALE ou sociologique ("X provoque Y", "X n'a pas d'effet sur Y"), les SOURCES ACADÉMIQUES (études évaluées par les pairs) pèsent plus lourd que la presse et que tes intuitions. Ne les utilise que si elles portent réellement sur le sujet de l'affirmation.
- "confiance" (0-100) = ta certitude dans le verdict: ~90+ = sources officielles concordantes; ~70 = bien sourcé; ~50 = plausible mais mal sourcé; en dessous de 40, utilise plutôt "non_verifiable".
- Quand les sources donnent un chiffre exact, cite-le dans "explication".
- "url" doit être COPIÉE depuis un des résultats de recherche fournis — jamais inventée. Si aucun résultat n'appuie ton verdict, url vide.
- Sois honnête : en cas de doute réel, réponds "non_verifiable" plutôt que de deviner."""


def _clean_description(desc: str) -> str:
    """Garde l'info utile (intervenants, sujet, date) d'une description YouTube,
    vire le bruit (liens, hashtags, appels à s'abonner, réseaux sociaux)."""
    noise = ("http://", "https://", "www.", "abonnez-vous", "abonne-toi", "#",
             "twitter", "instagram", "facebook", "tiktok", "twitch", "discord")
    lines = []
    for line in desc.splitlines():
        l = line.strip()
        if l and not any(n in l.lower() for n in noise):
            lines.append(l)
    return " ".join(lines)[:600]


VIDEO_ANALYSIS_PROMPT = """Voici les métadonnées d'une vidéo YouTube de débat ou plateau politique français.
Titre: {title}
Chaîne: {channel}
Date de publication: {publish_date}
Description: {description}

Liste les intervenants: les personnes qui PARLENT dans la vidéo (débatteurs, invités, journalistes ou animateurs identifiables). Pas les personnes seulement mentionnées comme sujet.
Réponds UNIQUEMENT avec un objet JSON, sans markdown:
{{"intervenants": ["Prénom Nom", "Prénom Nom"]}}
Si aucun intervenant identifiable: {{"intervenants": []}}"""


SPEAKER_MAP_PROMPT_TEMPLATE = """Tu identifies les locuteurs anonymes d'un débat télévisé français.
{emission_line}Intervenants connus: {guests_line}

Extraits transcrits (les labels sont stables sur toute la session):

{excerpts}

Associe chaque label à un nom réel UNIQUEMENT si les preuves sont décisives:
- le locuteur est interpellé par son nom juste avant de prendre la parole, ou on s'adresse à lui nommément ("vous, monsieur X…"),
- il se présente lui-même,
- ses propos correspondent sans aucune ambiguïté aux positions publiques d'un intervenant de la liste.

Réponds UNIQUEMENT avec un objet JSON, sans markdown:
{{"Intervenant A": "Prénom Nom ou null", "Intervenant B": "Prénom Nom ou null"}}

Règles: null au moindre doute — une mauvaise attribution est pire qu'une absence. Ne choisis un nom hors de la liste des intervenants que si une interpellation nominale explicite l'impose."""


def build_context_block(context: dict) -> str:
    parts = []
    if context.get("emission"):
        parts.append(f"Émission: {context['emission']}")
    if context.get("guests"):
        parts.append(f"Intervenants: {', '.join(context['guests'])}")
    if context.get("date"):
        # Ancre temporelle : "hier", "cette année", "le dernier budget"… se
        # comprennent par rapport à la date de la vidéo, pas celle du visionnage
        parts.append(f"Date de publication de la vidéo: {context['date']} — les propos datent de cette période")
    if context.get("description"):
        parts.append(
            "Description de la vidéo (contexte de fond UNIQUEMENT — "
            f"n'en extrais jamais de talking point): {context['description']}"
        )
    if not parts:
        return ""
    return "Contexte de l'émission:\n" + "\n".join(f"- {p}" for p in parts) + "\n\n"


def build_history_block(recent_points: list) -> str:
    if not recent_points:
        return ""
    lines = ["\nTalking points déjà identifiés — NE PAS RÉPÉTER, même sous une formulation légèrement différente:"]
    for p in recent_points[-25:]:
        lines.append(f"- [{p['type']}] {p['texte']}")
    return "\n".join(lines)


# ── Déduplication sémantique locale ───────────────────────────────────────────

_STOPWORDS = {
    'avec', 'aussi', 'alors', 'autre', 'autres', 'bien', 'mais', 'même',
    'nous', 'plus', 'pour', 'puis', 'quand', 'sans', 'sont', 'très',
    'tout', 'tous', 'toute', 'toutes', 'vers', 'vous', 'dans', 'ainsi',
    'avoir', 'être', 'faire', 'dire', 'cette', 'cela', 'comme', 'donc',
    'dont', 'elle', 'elles', 'entre', 'leur', 'leurs', 'celui', 'celle',
    'ceux', 'celles', 'depuis', 'quel', 'quelle', 'quels', 'quelles',
}

def _key_words(text: str) -> set:
    tokens = re.findall(r'\b(?:[a-zàâçéèêëîïôùûü]{4,}|\d{3,})\b', text.lower())
    return {t for t in tokens if t not in _STOPWORDS}

def _is_duplicate(new_text: str, existing: list, threshold: float = 0.45) -> bool:
    new_w = _key_words(new_text)
    if len(new_w) < 3:
        return False
    for p in existing:
        ex_w = _key_words(p['texte'])
        if len(ex_w) < 3:
            continue
        overlap = len(new_w & ex_w)
        if overlap / min(len(new_w), len(ex_w)) >= threshold:
            return True
    return False


# ── Cache persistant des fact-checks ──────────────────────────────────────────
# Les politiques répètent les mêmes claims pendant des mois : un claim déjà
# vérifié (dans cette session ou une précédente) obtient son verdict
# instantanément, sans recherche web ni appel Mistral.

CACHE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "factcheck_cache.db")
CACHE_TTL_DAYS = 30      # les chiffres politiques/économiques périment
CACHE_MIN_CONF = 60      # ne jamais mettre en cache un verdict peu sûr
CACHE_SIM_THRESHOLD = 0.75  # similarité (mots-clés) pour considérer deux claims identiques

_cache_conn = sqlite3.connect(CACHE_DB, check_same_thread=False)
_cache_conn.execute("""CREATE TABLE IF NOT EXISTS factchecks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confiance INTEGER,
    explication TEXT,
    source TEXT,
    url TEXT,
    created_at REAL NOT NULL
)""")
_cache_conn.commit()
_cache_lock = threading.Lock()
_cache_mem: list = []  # [(mots_clés, résultat)] — copie mémoire pour le matching flou


def _cache_load():
    cutoff = time.time() - CACHE_TTL_DAYS * 86400
    with _cache_lock:
        _cache_conn.execute("DELETE FROM factchecks WHERE created_at < ?", (cutoff,))
        _cache_conn.commit()
        rows = _cache_conn.execute(
            "SELECT claim, verdict, confiance, explication, source, url FROM factchecks").fetchall()
    for claim, verdict, conf, expl, src, url in rows:
        words = _key_words(claim)
        if len(words) >= 3:
            _cache_mem.append((words, {"verdict": verdict, "confiance": conf,
                                       "explication": expl or "", "source": src or "", "url": url or ""}))
    print(f"[Cache] {len(_cache_mem)} fact-check(s) en cache")


def cache_lookup(claim: str):
    words = _key_words(claim)
    if len(words) < 3:
        return None
    for w, row in _cache_mem:
        if len(words & w) / min(len(words), len(w)) >= CACHE_SIM_THRESHOLD:
            return row
    return None


def cache_store(claim: str, result: dict):
    conf = result.get("confiance")
    if result.get("verdict") in (None, "", "non_verifiable") or not isinstance(conf, int) or conf < CACHE_MIN_CONF:
        return
    words = _key_words(claim)
    if len(words) < 3:
        return
    with _cache_lock:
        _cache_conn.execute(
            "INSERT INTO factchecks (claim, verdict, confiance, explication, source, url, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (claim, result["verdict"], conf, result.get("explication", ""),
             result.get("source", ""), result.get("url", ""), time.time()))
        _cache_conn.commit()
    _cache_mem.append((words, {k: result.get(k) for k in ("verdict", "confiance", "explication", "source", "url")}))


_cache_load()


def _call_mistral_api(prompt: str) -> str:
    resp = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
        json={"model": MISTRAL_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


ATTRIBUTION_RULE_DIAR = (
    'La transcription est annotée par locuteur ("Intervenant A"… ou un nom réel une fois '
    'le locuteur identifié). Recopie EXACTEMENT cette annotation dans le champ "qui" — '
    "ne devine JAMAIS un nom toi-même. Les personnalités CITÉES dans le propos "
    "(Poutine, Orban…) peuvent apparaître dans le texte du point."
)
ATTRIBUTION_RULE_NODIAR = (
    'Impossible de savoir qui parle: laisse le champ "qui" vide et formule le point sans '
    'nom d\'intervenant (écris "l\'adversaire" ou formule sans sujet). Les personnalités '
    "CITÉES dans le propos (Poutine, Orban…) peuvent apparaître."
)


def call_mistral(text: str, context: dict = None, recent_points: list = None) -> list:
    prompt = MISTRAL_PROMPT_TEMPLATE.format(
        context_block=build_context_block(context or {}),
        text=text,
        history_block=build_history_block(recent_points or []),
        attribution_rule=ATTRIBUTION_RULE_DIAR if DIARIZATION else ATTRIBUTION_RULE_NODIAR,
    )
    content = _call_mistral_api(prompt)
    print(f"[Mistral talking points] {content[:200]}")
    try:
        start = content.find('[')
        if start != -1:
            result, _ = json.JSONDecoder().raw_decode(content, start)
            if isinstance(result, list):
                # type/texte doivent être des strings non vides, sinon le rendu côté extension casse
                points = [p for p in result
                          if isinstance(p, dict)
                          and isinstance(p.get("type"), str) and p["type"].strip()
                          and isinstance(p.get("texte"), str) and p["texte"].strip()]
                for p in points:
                    p["qui"] = str(p.get("qui") or "").strip()[:48]
                return points
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def web_search(query: str, max_results: int = 6) -> list:
    """Recherche web (DuckDuckGo, sans clé API). Retourne [] en cas d'échec."""
    if DDGS is None:
        return []
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, region="fr-fr", max_results=max_results))
    except Exception as e:
        print(f"[Search error] {type(e).__name__}: {e}")
        return []


# Hiérarchie de fiabilité des domaines — annotée dans le prompt pour que le
# verdict pèse une source officielle plus lourd qu'un blog
_TIER_OFFICIAL = (
    "insee.fr", "eurostat", "ec.europa.eu", "legifrance.gouv.fr", "vie-publique.fr",
    "senat.fr", "assemblee-nationale.fr", ".gouv.fr", "banque-france.fr",
    "oecd.org", "ocde.org", "ecb.europa.eu", "ined.fr", "ademe.fr", "conseil-constitutionnel.fr",
)
_TIER_PRESS = (
    "afp.com", "lemonde.fr", "liberation.fr", "francetvinfo.fr", "radiofrance.fr",
    "franceinfo.fr", "lefigaro.fr", "lesechos.fr", "publicsenat.fr", "lcp.fr",
    "reuters.com", "latribune.fr", "ouest-france.fr", "bfmtv.com", "20minutes.fr",
    "courrierinternational.com", "la-croix.com", "sudouest.fr",
)


def _scholar_query(claim: str) -> str:
    """Les moteurs académiques (Solr) marchent aux mots-clés, pas aux phrases:
    on garde les mots significatifs du claim, dans l'ordre."""
    tokens = re.findall(r"[a-zàâçéèêëîïôùûü]{4,}|\d{2,}", claim.lower())
    words = [t for t in tokens if t not in _STOPWORDS]
    return " ".join(words[:6])


def scholar_search(claim: str, max_results: int = 4) -> list:
    """Études académiques pour ancrer les claims sociologiques/causaux.
    HAL (archive ouverte française, fort en sciences sociales FR) + OpenAlex
    (index scientifique mondial). APIs publiques, gratuites, sans clé."""
    query = _scholar_query(claim)
    if len(query.split()) < 2:
        return []
    out = []
    try:
        r = requests.get(
            "https://api.archives-ouvertes.fr/search/",
            params={"q": query, "rows": 2, "fl": "title_s,abstract_s,uri_s,producedDateY_i"},
            timeout=6,
        )
        for doc in r.json().get("response", {}).get("docs", []):
            title = (doc.get("title_s") or [""])[0]
            abstract = (doc.get("abstract_s") or [""])[0]
            uri = doc.get("uri_s", "")
            year = doc.get("producedDateY_i", "")
            if title and uri:
                out.append({"title": f"{title} ({year}, HAL)", "body": abstract[:300], "href": uri})
    except Exception as e:
        print(f"[Scholar HAL] {type(e).__name__}: {e}")
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={"search": query, "per-page": 2},
            timeout=6,
        )
        for w in r.json().get("results", []):
            title = w.get("display_name") or ""
            year = w.get("publication_year", "")
            url = (w.get("primary_location") or {}).get("landing_page_url") or w.get("id", "")
            # OpenAlex stocke les résumés en index inversé — reconstruction
            abstract = ""
            inv = w.get("abstract_inverted_index")
            if inv:
                pos = {}
                for word, idxs in inv.items():
                    for i in idxs:
                        pos[i] = word
                abstract = " ".join(pos[i] for i in sorted(pos))[:300]
            if title and url:
                out.append({"title": f"{title} ({year}, OpenAlex)", "body": abstract, "href": url})
    except Exception as e:
        print(f"[Scholar OpenAlex] {type(e).__name__}: {e}")
    return out[:max_results]


def _source_tier(url: str) -> str:
    u = (url or "").lower()
    if any(d in u for d in _TIER_OFFICIAL):
        return "SOURCE OFFICIELLE"
    if any(d in u for d in _TIER_PRESS):
        return "PRESSE ÉTABLIE"
    return "FIABILITÉ INCONNUE"


def build_evidence_block(results: list, academic: list = None) -> str:
    if not results and not academic:
        return "Aucun résultat de recherche disponible — base-toi sur tes connaissances uniquement."
    lines = ["Résultats de recherche (fiabilité annotée):"]
    i = 0
    for r in (academic or []):
        i += 1
        lines.append(f"[{i}] [SOURCE ACADÉMIQUE] {(r.get('title') or '').strip()} — "
                     f"{(r.get('body') or '').strip()[:300]}\n    URL: {(r.get('href') or '').strip()}")
    for r in (results or []):
        i += 1
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()[:300]
        href = (r.get("href") or "").strip()
        lines.append(f"[{i}] [{_source_tier(href)}] {title} — {body}\n    URL: {href}")
    return "\n".join(lines)


def call_mistral_factcheck(claim: str, context: dict = None) -> dict:
    results = web_search(claim)
    academic = scholar_search(claim)
    print(f"[Search] {len(results)} web + {len(academic)} académique(s) pour «{claim[:50]}»")
    prompt = FACTCHECK_PROMPT_TEMPLATE.format(
        today=time.strftime("%d/%m/%Y"),
        context_block=build_context_block(context or {}),
        claim=claim,
        evidence_block=build_evidence_block(results, academic),
    )
    content = _call_mistral_api(prompt)
    print(f"[FactCheck résultat] {content[:150]}")
    try:
        start = content.find('{')
        if start != -1:
            data, _ = json.JSONDecoder().raw_decode(content, start)
            if isinstance(data, dict) and "verdict" in data:
                data.setdefault("source", "")
                # L'URL doit venir des résultats de recherche : jamais d'URL inventée,
                # et uniquement http(s) (une URL javascript: serait un vecteur XSS)
                url = data.get("url", "")
                valid_hrefs = {r.get("href") for r in results} | {r.get("href") for r in academic}
                if not (isinstance(url, str) and url.startswith(("http://", "https://")) and url in valid_hrefs):
                    data["url"] = ""
                conf = data.get("confiance")
                data["confiance"] = max(0, min(100, int(conf))) if isinstance(conf, (int, float)) else None
                return data
    except (json.JSONDecodeError, ValueError):
        pass
    return {"verdict": "non_verifiable", "confiance": None, "explication": "Impossible de vérifier.", "source": "", "url": ""}


def fact_check_affirmation(sid: str, claim_id: str, claim_text: str):
    print(f"[FactCheck] «{claim_text[:60]}»")
    # Claim déjà vérifié (cette session ou une précédente) → verdict instantané
    cached = cache_lookup(claim_text)
    if cached:
        print(f"[FactCheck] cache hit → {cached['verdict']} ({cached.get('confiance')}%)")
        socketio.emit("fact_check_result", {"id": claim_id, **cached}, to=sid)
        return
    context = session_contexts.get(sid, {})
    try:
        result = call_mistral_factcheck(claim_text, context=context)
        cache_store(claim_text, result)
        socketio.emit("fact_check_result", {"id": claim_id, **result}, to=sid)
    except Exception as e:
        print(f"[FactCheck error] {type(e).__name__}: {e}")
        # Toujours émettre un résultat, sinon la carte côté extension reste
        # bloquée en spinner et gèle toute la file d'affichage
        socketio.emit("fact_check_result", {
            "id": claim_id,
            "verdict": "non_verifiable",
            "explication": "Vérification indisponible.",
            "source": "",
            "url": "",
        }, to=sid)


def speaker_labels_list(tracker) -> list:
    if not tracker:
        return []
    return [SpeakerTracker.label_for(i) for i in range(len(tracker.sums))]


def apply_speaker_map(sid: str, transcript: str) -> str:
    """Substitue les labels confirmés par les vrais noms dans un transcript annoté."""
    for label, name in session_speaker_map.get(sid, {}).items():
        transcript = transcript.replace(f"{label}:", f"{name}:")
    return transcript


def match_clusters_to_bank(sid: str):
    """Attribution ACOUSTIQUE : compare les centroïdes de la session aux
    empreintes de la banque. Une correspondance nette (seuil + marge sur la
    2e meilleure) est définitive et prioritaire sur l'identification LLM."""
    if not DIARIZATION or not _voice_bank:
        return
    tracker = session_speakers.get(sid)
    if not tracker or not tracker.sums:
        return
    confirmed = session_speaker_map.setdefault(sid, {})
    locked = session_voice_locked.setdefault(sid, set())
    labels = speaker_labels_list(tracker)
    changed = False
    for i, label in enumerate(labels):
        if label in locked or tracker.counts[i] < 3:
            continue  # déjà identifié par la voix, ou pas assez de matière
        centroid = tracker.sums[i] / tracker.counts[i]
        centroid = centroid / (np.linalg.norm(centroid) + 1e-8)
        sims = sorted(((float(np.dot(centroid, ref)), name)
                       for name, ref in _voice_bank.items()), reverse=True)
        best, best_name = sims[0]
        second = sims[1][0] if len(sims) > 1 else -1.0
        if best >= VOICE_MATCH_THRESHOLD and (best - second) >= VOICE_MATCH_MARGIN:
            locked.add(label)
            if confirmed.get(label) != best_name:
                confirmed[label] = best_name
                changed = True
                print(f"[Voix] {label} = {best_name} (cos {best:.2f})")
    if changed:
        session_speaker_map[sid] = confirmed
        socketio.emit("speaker_map", {"map": confirmed}, to=sid)


def auto_enroll_voices(sid: str):
    """Fin de session : sauvegarde en banque les voix identifiées de façon
    fiable par les votes LLM (invité connu, cluster fourni, pas déjà en banque).
    La banque s'enrichit toute seule — au prochain débat, la reconnaissance
    est acoustique et immédiate."""
    if not DIARIZATION:
        return
    tracker = session_speakers.get(sid)
    confirmed = session_speaker_map.get(sid, {})
    locked = session_voice_locked.get(sid, set())
    guests = (session_contexts.get(sid, {}).get("guests")) or []
    if not tracker or not confirmed:
        return
    labels = speaker_labels_list(tracker)
    for i, label in enumerate(labels):
        name = confirmed.get(label)
        if (not name or label in locked or name in _voice_bank
                or name not in guests or tracker.counts[i] < VOICE_ENROLL_MIN_SEGMENTS):
            continue
        centroid = tracker.sums[i] / tracker.counts[i]
        save_voice(name, centroid, auto=True)


def identify_speakers(sid: str):
    """Tâche de fond : associe les labels anonymes aux vrais noms par VOTE
    MAJORITAIRE. Chaque appel Mistral = un vote par label ; un nom est confirmé
    à 2 votes concordants (et strictement devant les autres candidats). Un
    mapping confirmé reste corrigeable si les votes suivants le contredisent —
    une identification isolée ne peut plus verrouiller une erreur."""
    state = session_map_state.get(sid)
    try:
        tracker = session_speakers.get(sid)
        excerpts = session_excerpts.get(sid, [])
        confirmed = session_speaker_map.get(sid, {})
        labels = speaker_labels_list(tracker)
        unmapped = [l for l in labels if l not in confirmed]
        if not excerpts or not unmapped:
            return
        ctx = session_contexts.get(sid, {})
        guests = ctx.get("guests") or []
        prompt = SPEAKER_MAP_PROMPT_TEMPLATE.format(
            emission_line=f"Émission: {ctx['emission']}\n" if ctx.get("emission") else "",
            guests_line=", ".join(guests) if guests else "(liste non fournie)",
            excerpts="\n---\n".join(excerpts),
        )
        content = _call_mistral_api(prompt)
        print(f"[SpeakerMap] {content[:150]}")
        start = content.find('{')
        if start == -1:
            return
        data, _ = json.JSONDecoder().raw_decode(content, start)
        if not isinstance(data, dict):
            return

        # Enregistrer les votes (les "null" ne votent pas)
        votes = session_map_votes.setdefault(sid, {})
        for label, name in data.items():
            if (label in labels and isinstance(name, str) and name.strip()
                    and name.strip().lower() not in ("null", "none", "?")):
                n = name.strip()[:48]
                votes.setdefault(label, {})
                votes[label][n] = votes[label].get(n, 0) + 1

        # Confirmation / correction à la majorité (jamais sur un label déjà
        # identifié acoustiquement — la voix prime sur l'inférence LLM)
        locked = session_voice_locked.get(sid, set())
        changed = False
        for label, cand in votes.items():
            if label in locked:
                continue
            ranked = sorted(cand.items(), key=lambda kv: -kv[1])
            best_name, best_n = ranked[0]
            second_n = ranked[1][1] if len(ranked) > 1 else 0
            if best_n >= 2 and best_n > second_n and confirmed.get(label) != best_name:
                if label in confirmed:
                    print(f"[SpeakerMap] correction: {label}: {confirmed[label]} → {best_name}")
                confirmed[label] = best_name
                changed = True
        if changed:
            session_speaker_map[sid] = confirmed
            print(f"[SpeakerMap] confirmé: {confirmed}")
            # L'extension renomme rétroactivement tous les points déjà affichés
            socketio.emit("speaker_map", {"map": confirmed}, to=sid)
    except Exception as e:
        print(f"[SpeakerMap error] {type(e).__name__}: {e}")
    finally:
        if state is not None:
            state["inflight"] = False


def flush_to_mistral(sid: str, text: str, ts: float = None):
    print(f"[Mistral] Envoi de {len(text.split())} mots pour analyse…")
    lock = session_flush_locks.get(sid)
    if lock:
        lock.acquire()
    try:
        # Substituer les labels déjà identifiés par les vrais noms
        text = apply_speaker_map(sid, text)
        context = session_contexts.get(sid, {})
        all_points = session_points.get(sid, [])
        raw_points = call_mistral(text, context=context, recent_points=all_points)
        print(f"[Mistral] {len(raw_points)} talking point(s) reçus")

        # Toutes les 2 analyses : tenter d'identifier les locuteurs encore anonymes
        state = session_map_state.get(sid)
        if DIARIZATION and state is not None and not state["inflight"]:
            state["flushes"] += 1
            tracker = session_speakers.get(sid)
            mapped = session_speaker_map.get(sid, {})
            if state["flushes"] % 2 == 0 and tracker and len(tracker.sums) > len(mapped):
                state["inflight"] = True
                socketio.start_background_task(identify_speakers, sid)

        if not raw_points:
            return
        # Conserver le label d'origine (pour la correction rétroactive côté
        # extension), puis substituer par le nom confirmé si disponible
        smap = session_speaker_map.get(sid, {})
        for p in raw_points:
            p["qui_label"] = p.get("qui", "")
            if p.get("qui") in smap:
                p["qui"] = smap[p["qui"]]
        import uuid
        unique: list = []
        for p in raw_points:
            if _is_duplicate(p['texte'], all_points + unique):
                print(f"[Dedup] ignoré: {p['texte'][:70]}")
            else:
                # ts = horodatage (unix) approximatif du moment où le propos a été
                # tenu → permet le "sauter à ce moment de la vidéo" côté extension
                unique.append({"id": uuid.uuid4().hex[:8], "ts": ts, **p})
        if not unique:
            print("[Dedup] tous les points étaient des doublons — rien émis")
            return
        socketio.emit("talking_points", {"points": unique}, to=sid)
        # Garder TOUS les points de la session (pas de cap) pour déduplication globale
        session_points[sid] = all_points + unique
        # Fact-check uniquement les affirmations
        for p in unique:
            if p["type"] == "affirmation":
                socketio.start_background_task(fact_check_affirmation, sid, p["id"], p["texte"])
    except Exception as e:
        print(f"[Mistral error] {type(e).__name__}: {e}")
    finally:
        if lock:
            lock.release()


# ── Hallucination filter ───────────────────────────────────────────────────────

# Phrases hallucinées connues par Whisper sur du contenu YouTube/TV
HALLUCINATION_BLACKLIST = [
    "amara.org",
    "sous-titres réalisés",
    "sous-titrages réalisés",
    "sous-titrage st",
    "sous-titres par",
    "abonnez-vous",
    "merci d'avoir regardé",
    "transcription by",
    "subtitles by",
    "community captions",
    "♪",
    "music:",
]

def is_hallucination(text: str) -> bool:
    low = text.lower().strip()

    if any(pattern in low for pattern in HALLUCINATION_BLACKLIST):
        return True

    # Texte composé quasi-uniquement de tirets/ponctuation ("— — — — —", "...", etc.)
    meaningful = text.replace("—", "").replace("-", "").replace(".", "").replace(" ", "").replace("…", "").strip()
    if len(text.strip()) > 3 and len(meaningful) < len(text.strip()) * 0.2:
        return True

    return False


session_history: dict[str, list[str]] = {}
session_starts: dict[str, float] = {}
session_buffers: dict[str, dict] = {}
session_contexts: dict[str, dict] = {}   # { sid: {"emission": str, "guests": [str]} }
session_points: dict[str, list] = {}     # { sid: talking points récents pour le contexte }
session_flush_locks: dict[str, object] = {}  # { sid: Semaphore } évite la race condition sur session_points
session_speakers: dict[str, "SpeakerTracker"] = {}  # { sid: tracker de locuteurs (diarisation) }
session_excerpts: dict[str, list] = {}       # { sid: derniers transcripts annotés (preuves pour l'identification) }
session_speaker_map: dict[str, dict] = {}    # { sid: {"Intervenant A": "Éric Zemmour", …} — mappings confirmés }
session_map_votes: dict[str, dict] = {}      # { sid: {label: {nom: nb_votes}} — une identification = un vote }
session_map_state: dict[str, dict] = {}      # { sid: {"flushes": int, "inflight": bool} }
session_voice_locked: dict[str, set] = {}    # { sid: labels identifiés ACOUSTIQUEMENT — définitifs, les votes LLM ne peuvent pas les changer }


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/analyze_video", methods=["POST"])
def analyze_video():
    """Extrait la liste des intervenants depuis les métadonnées de la vidéo
    (appelé par la popup à l'ouverture, pour préremplir le champ)."""
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", ""))[:300]
    channel = str(data.get("channel", ""))[:100]
    description = _clean_description(str(data.get("description", "")))
    publish_date = str(data.get("publishDate", ""))[:20]
    if not MISTRAL_API_KEY or not (title or description):
        return {"guests": []}
    try:
        content = _call_mistral_api(VIDEO_ANALYSIS_PROMPT.format(
            title=title, channel=channel,
            publish_date=publish_date or "inconnue",
            description=description or "(vide)",
        ))
        print(f"[AnalyzeVideo] {content[:150]}")
        start = content.find('{')
        if start != -1:
            d, _ = json.JSONDecoder().raw_decode(content, start)
            guests = [str(g).strip() for g in (d.get("intervenants") or [])
                      if isinstance(g, str) and str(g).strip()][:8]
            return {"guests": guests}
    except Exception as e:
        print(f"[AnalyzeVideo error] {type(e).__name__}: {e}")
    return {"guests": []}


@socketio.on("connect")
def on_connect():
    sid = request.sid
    session_history[sid] = []
    session_buffers[sid] = {"entries": [], "last_flush": time.time(), "start_abs": None}
    session_contexts[sid] = {}
    session_points[sid] = []
    session_flush_locks[sid] = eventlet.semaphore.Semaphore(1)
    session_speakers[sid] = SpeakerTracker() if DIARIZATION else None
    session_excerpts[sid] = []
    session_speaker_map[sid] = {}
    session_map_votes[sid] = {}
    session_map_state[sid] = {"flushes": 0, "inflight": False}
    session_voice_locked[sid] = set()
    # Recharger la banque : des voix ont pu être ajoutées (harvest_voices.py,
    # enroll.py, auto-enrôlement) depuis le démarrage du serveur
    load_voice_bank(verbose=False)
    print(f"Client connecté: {sid} — {len(_voice_bank)} voix en banque")
    emit("ready", {"status": "connected"})


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    try:
        auto_enroll_voices(sid)
    except Exception as e:
        print(f"[Voix] auto-enroll: {type(e).__name__}: {e}")
    session_history.pop(sid, None)
    session_starts.pop(sid, None)
    session_buffers.pop(sid, None)
    session_contexts.pop(sid, None)
    session_points.pop(sid, None)
    session_flush_locks.pop(sid, None)
    session_speakers.pop(sid, None)
    session_excerpts.pop(sid, None)
    session_speaker_map.pop(sid, None)
    session_map_votes.pop(sid, None)
    session_map_state.pop(sid, None)
    session_voice_locked.pop(sid, None)
    print(f"Client déconnecté: {sid}")


@socketio.on("set_context")
def on_set_context(data):
    sid = request.sid
    guests_raw = data.get("guests", "")
    guests = [g.strip() for g in guests_raw.replace(",", "\n").split("\n") if g.strip()]
    session_contexts[sid] = {
        "emission": data.get("emission", "").strip(),
        "guests": guests,
        "date": str(data.get("date", "")).strip()[:20],
        "description": _clean_description(str(data.get("description", ""))),
    }
    print(f"[Context] emission={session_contexts[sid]['emission']!r} "
          f"guests={session_contexts[sid]['guests']} "
          f"date={session_contexts[sid]['date']!r} "
          f"description={len(session_contexts[sid]['description'])} chars")


@socketio.on("start_transcription")
def on_start():
    session_starts[request.sid] = time.time()
    emit("ready", {"status": "listening"})


@socketio.on("speaker_probe")
def handle_speaker_probe(data):
    """Sonde temps réel "qui parle" : ~2,5 s d'audio → empreinte vocale →
    locuteur le plus proche (~100 ms, sans transcription). Lecture seule :
    ne modifie jamais les clusters — seuls les segments Whisper apprennent."""
    if not DIARIZATION or not data:
        return
    sid = request.sid
    tracker = session_speakers.get(sid)
    if not tracker or not tracker.sums:
        return  # pas encore de clusters (premier chunk Whisper pas passé)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(bytes(data) if not isinstance(data, bytes) else data)
            tmp_path = tmp.name
        wav = decode_audio(tmp_path)
        if len(wav) < 16000:
            return  # moins d'une seconde utile
        if float(np.sqrt((wav ** 2).mean())) < 0.005:
            return  # silence — le badge s'effacera tout seul
        with torch.no_grad():
            t = torch.from_numpy(wav).float().unsqueeze(0)
            emb = speaker_encoder.encode_batch(t).squeeze().cpu().numpy()
        label, sim = tracker.match(emb)
        if not label or sim < PROBE_MATCH_T:
            return
        # Le "locuteur courant" sert aussi d'héritage aux segments trop courts
        tracker.last = label
        emit("speaker_live", {"speaker": label})
    except Exception as e:
        print(f"[Probe error] {type(e).__name__}: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@socketio.on("audio_chunk")
def handle_audio_chunk(data):
    if not data:
        return

    sid = request.sid
    tmp_path = None
    try:
        print(f"[Chunk] reçu ({len(data)} bytes)")
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(bytes(data) if not isinstance(data, bytes) else data)
            tmp_path = tmp.name

        chunk_abs_time = time.time()
        session_start = session_starts.get(sid, chunk_abs_time)
        chunk_offset = chunk_abs_time - session_start

        with model_lock:
            segments_gen, _ = model.transcribe(
                tmp_path,
                language="fr",
                beam_size=5,
                temperature=0,
                vad_filter=True,
                vad_parameters={"threshold": 0.3, "min_silence_duration_ms": 300},
                no_speech_threshold=0.45,
                compression_ratio_threshold=2.4,
            )
            segments = list(segments_gen)

        print(f"[Whisper] {len(segments)} segment(s) produit(s)")

        history = session_history.get(sid, [])
        buf = session_buffers.get(sid) or {"entries": [], "last_flush": time.time(), "start_abs": None}
        tracker = session_speakers.get(sid)
        emitted = []  # [(label_locuteur, texte)]

        # Waveform 16 kHz pour les empreintes vocales (même fichier que Whisper)
        wav = None
        if DIARIZATION and segments:
            try:
                wav = decode_audio(tmp_path)
            except Exception as e:
                print(f"[Diar] decode_audio: {type(e).__name__}: {e}")

        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            if is_hallucination(text):
                print(f"  → filtré (hallucination): {text[:50]}")
                continue
            if text in history:
                print(f"  → filtré (doublon): {text[:50]}")
                continue
            history.append(text)
            if len(history) > 5:
                history.pop(0)
            label = speaker_label(tracker, wav, seg)
            emitted.append((label, text))
            print(f"  → émis{f' [{label}]' if label else ''}: {text[:80]}")
            emit("transcript_segment", {
                "text": text,
                "speaker": label,
                "start": round(chunk_offset + seg.start, 3),
                "end": round(chunk_offset + seg.end, 3),
                "abs_time": chunk_abs_time,
            })

        session_history[sid] = history
        if emitted:
            match_clusters_to_bank(sid)

        if not MISTRAL_API_KEY:
            print("[Buffer] MISTRAL_API_KEY manquante — flush désactivé")
        elif emitted:
            if not buf["entries"]:
                # Début (approximatif) du texte accumulé — sert d'horodatage aux points
                buf["start_abs"] = chunk_abs_time
            buf["entries"].extend(emitted)
            elapsed_since_flush = time.time() - buf["last_flush"]
            word_count = sum(len(t.split()) for _, t in buf["entries"])
            print(f"[Buffer] {word_count} mots, {elapsed_since_flush:.0f}s depuis dernier flush")
            if word_count >= MIN_WORDS and (elapsed_since_flush >= FLUSH_INTERVAL or word_count >= MAX_BUFFER_WORDS):
                text_to_send = build_transcript(buf["entries"])
                # Conserver le transcript annoté (labels d'origine) comme preuve
                # pour l'identification des locuteurs — 6 derniers extraits
                ex = session_excerpts.get(sid)
                if ex is not None and DIARIZATION:
                    ex.append(text_to_send)
                    del ex[:-6]
                ts = buf.get("start_abs") or chunk_abs_time
                buf = {"entries": [], "last_flush": time.time(), "start_abs": None}
                socketio.start_background_task(flush_to_mistral, sid, text_to_send, ts)
            session_buffers[sid] = buf

    except Exception as e:
        emit("transcription_error", {"error": str(e)})
        print(f"[ERREUR] {type(e).__name__}: {e}")

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)

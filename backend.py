import os
import re
import json
import time
import tempfile
import threading
import eventlet
import eventlet.semaphore

import requests
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from faster_whisper import WhisperModel

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
FLUSH_INTERVAL = 12   # secondes entre deux analyses Mistral
MIN_WORDS = 8         # ne pas appeler Mistral si moins de 8 mots accumulés

app = Flask(__name__)
CORS(app, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", max_http_buffer_size=50 * 1024 * 1024)

# Chargement du modèle au démarrage du serveur (pas au premier chunk)
print("Chargement du modèle Whisper medium (CUDA)...")
model = WhisperModel("medium", device="cuda", compute_type="float16")
print("Modèle prêt.")

if not MISTRAL_API_KEY:
    print("⚠️  MISTRAL_API_KEY non définie — les talking points seront désactivés.")

model_lock = threading.Lock()

# ── Prompts Mistral ────────────────────────────────────────────────────────────

MISTRAL_PROMPT_TEMPLATE = """Tu es un analyste de débats politiques français.
{context_block}Voici une transcription de ~30 secondes d'un débat:

\"\"\"{text}\"\"\"
{history_block}

RÈGLES ABSOLUES:
1. N'attribue JAMAIS un propos à un intervenant nommé — la transcription ne permet pas de savoir qui parle. Formule chaque point sans sujet nommé.
2. N'inclus PAS de méta-descriptions du comportement sur le plateau (interruptions, présentations de soi, réactions). Extrais le CONTENU politique du propos, pas ce qui se passe sur le plateau.
3. Ne génère JAMAIS un point si un claim sémantiquement identique figure déjà dans l'historique — même formulé différemment. En cas de doute, omet le point.

Réponds UNIQUEMENT avec un tableau JSON valide, sans markdown:
[{{"type": "TYPE", "texte": "talking point condensé en une phrase claire, sans nom propre"}}]

Types — lis les exemples avant de choisir:

"affirmation" = fait contenant un chiffre, une date ou un fait historique vérifiable
  ✓ "Un Français sur deux dispose de moins de 10€ de marge mensuelle"
  ✓ "Les factures d'électricité ont été multipliées par 2 à 5 ces dernières années"
  ✗ "Les politiques des 40 dernières années ont échoué" → subjectif
  ✗ "Le déplacement est devenu un bien de luxe" → subjectif
  ✗ "La gauche refuse de voter les textes de droite" → subjectif

"subjectif" = opinion, sentiment, promesse vague, jugement de valeur — NON vérifiable
  ✓ "Le modèle économique actuel abandonne les classes populaires"
  ✓ "La confiance envers les élus est épuisée depuis des décennies"

"argument" = raisonnement cause-effet ou proposition politique concrète
  ✓ "Plafonner l'électricité à 50€/MWh réduirait mécaniquement les dépenses contraintes"

"remarque" = commentaire sur l'adversaire ou le contexte politique — PAS une description de comportement
  ✓ "La gauche vote systématiquement contre les textes qu'elle n'a pas rédigés"
  ✗ "Un intervenant se présente comme bénévole" → méta-description, à exclure
  ✗ "Réaction à une interruption sur le plateau" → méta-description, à exclure

"question" = interpellation directe sur un sujet politique
"accord" = convergence explicite avec l'adversaire sur un point
"désaccord" = réfutation directe d'un propos adverse

Retourne [] si aucun talking point politique clair."""


FACTCHECK_PROMPT_TEMPLATE = """Tu es un fact-checker expert sur les données françaises et européennes.
{context_block}
Affirmation à vérifier: "{claim}"

Évalue la véracité en te basant sur tes connaissances (INSEE, Eurostat, sources officielles françaises).
Réponds UNIQUEMENT avec un objet JSON valide, sans markdown:
{{"verdict": "VERDICT", "explication": "une phrase courte et précise", "source": "nom de la source principale (ex: INSEE, Eurostat, Sénat, OCDE, Le Monde)"}}

Verdicts disponibles:
- "vrai": affirmation exacte et vérifiable
- "partiellement_vrai": vrai mais incomplet ou imprécis
- "trompeur": techniquement vrai mais donne une fausse impression
- "faux": factuellement incorrect

Pour "source": cite la source la plus pertinente. Si tu ne peux pas trancher, retourne "partiellement_vrai" et source vide."""


def build_context_block(context: dict) -> str:
    parts = []
    if context.get("emission"):
        parts.append(f"Émission: {context['emission']}")
    if context.get("guests"):
        parts.append(f"Intervenants: {', '.join(context['guests'])}")
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

def _is_duplicate(new_text: str, existing: list, threshold: float = 0.55) -> bool:
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


def _call_mistral_api(prompt: str) -> str:
    resp = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
        json={"model": "mistral-small-latest", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def call_mistral(text: str, context: dict = None, recent_points: list = None) -> list:
    prompt = MISTRAL_PROMPT_TEMPLATE.format(
        context_block=build_context_block(context or {}),
        text=text,
        history_block=build_history_block(recent_points or []),
    )
    content = _call_mistral_api(prompt)
    print(f"[Mistral talking points] {content[:200]}")
    try:
        start = content.find('[')
        if start != -1:
            result, _ = json.JSONDecoder().raw_decode(content, start)
            if isinstance(result, list):
                # type/texte doivent être des strings non vides, sinon le rendu côté extension casse
                return [p for p in result
                        if isinstance(p, dict)
                        and isinstance(p.get("type"), str) and p["type"].strip()
                        and isinstance(p.get("texte"), str) and p["texte"].strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def call_mistral_factcheck(claim: str, context: dict = None) -> dict:
    prompt = FACTCHECK_PROMPT_TEMPLATE.format(
        context_block=build_context_block(context or {}),
        claim=claim,
    )
    content = _call_mistral_api(prompt)
    print(f"[FactCheck résultat] {content[:150]}")
    try:
        start = content.find('{')
        if start != -1:
            data, _ = json.JSONDecoder().raw_decode(content, start)
            if isinstance(data, dict) and "verdict" in data:
                data.setdefault("source", "")
                return data
    except (json.JSONDecodeError, ValueError):
        pass
    return {"verdict": "partiellement_vrai", "explication": "Impossible de vérifier.", "source": ""}


def fact_check_affirmation(sid: str, claim_id: str, claim_text: str):
    print(f"[FactCheck] «{claim_text[:60]}»")
    context = session_contexts.get(sid, {})
    try:
        result = call_mistral_factcheck(claim_text, context=context)
        socketio.emit("fact_check_result", {"id": claim_id, **result}, to=sid)
    except Exception as e:
        print(f"[FactCheck error] {type(e).__name__}: {e}")
        # Toujours émettre un résultat, sinon la carte côté extension reste
        # bloquée en spinner et gèle toute la file d'affichage
        socketio.emit("fact_check_result", {
            "id": claim_id,
            "verdict": "partiellement_vrai",
            "explication": "Vérification indisponible.",
            "source": "",
        }, to=sid)


def flush_to_mistral(sid: str, text: str):
    print(f"[Mistral] Envoi de {len(text.split())} mots pour analyse…")
    lock = session_flush_locks.get(sid)
    if lock:
        lock.acquire()
    try:
        context = session_contexts.get(sid, {})
        all_points = session_points.get(sid, [])
        raw_points = call_mistral(text, context=context, recent_points=all_points)
        print(f"[Mistral] {len(raw_points)} talking point(s) reçus")
        if not raw_points:
            return
        import uuid
        unique: list = []
        for p in raw_points:
            if _is_duplicate(p['texte'], all_points + unique):
                print(f"[Dedup] ignoré: {p['texte'][:70]}")
            else:
                unique.append({"id": uuid.uuid4().hex[:8], **p})
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


@socketio.on("connect")
def on_connect():
    sid = request.sid
    session_history[sid] = []
    session_buffers[sid] = {"text": "", "last_flush": time.time()}
    session_contexts[sid] = {}
    session_points[sid] = []
    session_flush_locks[sid] = eventlet.semaphore.Semaphore(1)
    print(f"Client connecté: {sid}")
    emit("ready", {"status": "connected"})


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    session_history.pop(sid, None)
    session_starts.pop(sid, None)
    session_buffers.pop(sid, None)
    session_contexts.pop(sid, None)
    session_points.pop(sid, None)
    session_flush_locks.pop(sid, None)
    print(f"Client déconnecté: {sid}")


@socketio.on("set_context")
def on_set_context(data):
    sid = request.sid
    guests_raw = data.get("guests", "")
    guests = [g.strip() for g in guests_raw.replace(",", "\n").split("\n") if g.strip()]
    session_contexts[sid] = {
        "emission": data.get("emission", "").strip(),
        "guests": guests,
    }
    print(f"[Context] {session_contexts[sid]}")


@socketio.on("start_transcription")
def on_start():
    session_starts[request.sid] = time.time()
    emit("ready", {"status": "listening"})


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
        buf = session_buffers.get(sid, {"text": "", "last_flush": time.time()})
        emitted_texts = []

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
            emitted_texts.append(text)
            print(f"  → émis: {text[:80]}")
            emit("transcript_segment", {
                "text": text,
                "start": round(chunk_offset + seg.start, 3),
                "end": round(chunk_offset + seg.end, 3),
                "abs_time": chunk_abs_time,
            })

        session_history[sid] = history

        if not MISTRAL_API_KEY:
            print("[Buffer] MISTRAL_API_KEY manquante — flush désactivé")
        elif emitted_texts:
            buf["text"] = (buf["text"] + " " + " ".join(emitted_texts)).strip()
            elapsed_since_flush = time.time() - buf["last_flush"]
            word_count = len(buf["text"].split())
            print(f"[Buffer] {word_count} mots, {elapsed_since_flush:.0f}s depuis dernier flush")
            if elapsed_since_flush >= FLUSH_INTERVAL and word_count >= MIN_WORDS:
                text_to_send = buf["text"]
                buf = {"text": "", "last_flush": time.time()}
                socketio.start_background_task(flush_to_mistral, sid, text_to_send)
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

"""Routes Flask et handlers Socket.IO — la couche d'orchestration qui relie
transcription (Whisper), diarisation (server.voices) et fact-checking
(server.factcheck) au client (extension Chrome)."""

import json
import os
import tempfile
import time
import uuid

import eventlet
import eventlet.tpool
from flask import request
from flask_socketio import emit, ConnectionRefusedError

from faster_whisper.audio import decode_audio

from server.app import app, socketio, model, model_lock, DIARIZATION
from server.config import (
    BACKEND_TOKEN, MISTRAL_API_KEY, FLUSH_INTERVAL, MIN_WORDS, MAX_BUFFER_WORDS, PROBE_MATCH_T,
    MIN_WORDS_ON_PAUSE, MIN_WORDS_ON_STOP, FINISH_TIMEOUT_S, CHUNK_OVERLAP_S,
)
from server import cache
from server.dedup import dupe_index_add, is_duplicate_indexed
from server.factcheck import call_mistral, call_mistral_api, fact_check_affirmation, VIDEO_ANALYSIS_PROMPT
from server.notify import describe_error, warn_client
from server.state import (
    session_history, session_starts, session_buffers, session_contexts, session_points,
    session_dupe_index, session_flush_locks, session_chunk_locks, session_speakers,
    session_excerpts, session_speaker_map, session_map_votes, session_map_state,
    session_voice_locked, session_bank_miss, session_pending, session_warned,
)
from server.text_utils import claim_signature, clean_description, build_transcript, is_hallucination, strip_overlap
from server.vocabulary import build_hotwords, is_hotword_echo, learn
from server.voices import (
    SpeakerTracker, speaker_label, probe_speaker, apply_speaker_map,
    match_clusters_to_bank, auto_enroll_voices, identify_speakers, load_voice_bank, _voice_bank,
)

cache.load()


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/analyze_video", methods=["POST"])
def analyze_video():
    """Extrait la liste des intervenants depuis les métadonnées de la vidéo
    (appelé par la popup à l'ouverture, pour préremplir le champ)."""
    if BACKEND_TOKEN and request.headers.get("X-Backend-Token") != BACKEND_TOKEN:
        return {"guests": []}, 401
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", ""))[:300]
    channel = str(data.get("channel", ""))[:100]
    description = clean_description(str(data.get("description", "")))
    publish_date = str(data.get("publishDate", ""))[:20]
    if not MISTRAL_API_KEY or not (title or description):
        return {"guests": []}
    try:
        content = call_mistral_api(VIDEO_ANALYSIS_PROMPT.format(
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
def on_connect(auth=None):
    if BACKEND_TOKEN and (not isinstance(auth, dict) or auth.get("token") != BACKEND_TOKEN):
        print(f"[Auth] connexion refusée (jeton manquant ou invalide): {request.sid}")
        raise ConnectionRefusedError("unauthorized")
    sid = request.sid
    session_history[sid] = []
    session_buffers[sid] = {"entries": [], "last_flush": time.time(), "start_abs": None}
    session_contexts[sid] = {}
    session_points[sid] = []
    session_dupe_index[sid] = {}
    session_flush_locks[sid] = eventlet.semaphore.Semaphore(1)
    session_chunk_locks[sid] = eventlet.semaphore.Semaphore(1)
    session_speakers[sid] = SpeakerTracker() if DIARIZATION else None
    session_excerpts[sid] = []
    session_speaker_map[sid] = {}
    session_map_votes[sid] = {}
    session_map_state[sid] = {"flushes": 0, "inflight": False}
    session_voice_locked[sid] = set()
    session_pending[sid] = 0
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
    session_dupe_index.pop(sid, None)
    session_flush_locks.pop(sid, None)
    session_chunk_locks.pop(sid, None)
    session_speakers.pop(sid, None)
    session_excerpts.pop(sid, None)
    session_speaker_map.pop(sid, None)
    session_map_votes.pop(sid, None)
    session_map_state.pop(sid, None)
    session_voice_locked.pop(sid, None)
    session_bank_miss.pop(sid, None)
    session_pending.pop(sid, None)
    session_warned.pop(sid, None)
    print(f"Client déconnecté: {sid}")


@socketio.on("set_context")
def on_set_context(data):
    sid = request.sid
    data = data if isinstance(data, dict) else {}
    guests_raw = data.get("guests", "")
    if isinstance(guests_raw, list):
        guests_raw = "\n".join(str(g) for g in guests_raw)
    guests = [g.strip()[:60] for g in str(guests_raw).replace(",", "\n").split("\n") if g.strip()][:12]
    session_contexts[sid] = {
        "emission": str(data.get("emission", "")).strip()[:200],
        "guests": guests,
        "date": str(data.get("date", "")).strip()[:20],
        "description": clean_description(str(data.get("description", ""))),
        "learned": [],  # noms propres appris pendant le débat (vocabulary.learn)
    }
    _refresh_hotwords(sid)
    print(f"[Context] emission={session_contexts[sid]['emission']!r} "
          f"guests={session_contexts[sid]['guests']} "
          f"date={session_contexts[sid]['date']!r} "
          f"description={len(session_contexts[sid]['description'])} chars")
    print(f"[Context] mots attendus par Whisper: {session_contexts[sid]['hotwords'][:160]}…")


def _refresh_hotwords(sid: str):
    """Mots attendus par Whisper pour cette session (voir server/vocabulary.py)."""
    ctx = session_contexts.get(sid)
    if ctx is not None:
        ctx["hotwords"] = build_hotwords(ctx.get("guests", []), ctx.get("emission", ""),
                                         ctx.get("description", ""), ctx.get("learned", []))


@socketio.on("start_transcription")
def on_start():
    session_starts[request.sid] = time.time()
    emit("ready", {"status": "listening"})
    if not MISTRAL_API_KEY:
        warn_client(request.sid, "MISTRAL_API_KEY absente — transcription seule, aucune analyse")


@socketio.on("stop_transcription")
def on_stop():
    """Arrêt demandé par l'extension (le dernier chunk audio est déjà parti) :
    analyser ce qui reste dans le buffer — sans ça, les ~20 dernières
    secondes du débat n'étaient jamais analysées — puis prévenir le client
    quand plus rien n'est en vol (session_done), pour qu'il ne coupe pas la
    connexion avant les derniers verdicts."""
    sid = request.sid
    lock = session_chunk_locks.get(sid)
    if lock:
        lock.acquire()  # attendre la fin du chunk éventuellement en cours de transcription
    try:
        buf = session_buffers.get(sid)
        if MISTRAL_API_KEY and buf and buf["entries"] \
                and sum(len(t.split()) for _, t in buf["entries"]) >= MIN_WORDS_ON_STOP:
            session_buffers[sid] = _flush_buffer(sid, buf, time.time())
    finally:
        if lock:
            lock.release()
    socketio.start_background_task(_finish_session, sid)


def _finish_session(sid: str):
    deadline = time.time() + FINISH_TIMEOUT_S
    while session_pending.get(sid, 0) > 0 and time.time() < deadline:
        eventlet.sleep(0.5)
    socketio.emit("session_done", {"complete": session_pending.get(sid, 0) == 0}, to=sid)


def _spawn_tracked(sid: str, fn, *args):
    """Tâche de fond comptée dans session_pending (voir _finish_session)."""
    session_pending[sid] = session_pending.get(sid, 0) + 1

    def run():
        try:
            fn(*args)
        finally:
            if sid in session_pending:
                session_pending[sid] -= 1
    socketio.start_background_task(run)


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
        label, sim = eventlet.tpool.execute(probe_speaker, tmp_path, tracker)
        if not label or sim < PROBE_MATCH_T:
            return
        # Ne PAS toucher tracker.last ici : la sonde entend le locuteur de
        # MAINTENANT, alors que les segments Whisper traités ensuite datent de
        # 10-15 s plus tôt — les segments courts en début de chunk héritaient
        # donc de la mauvaise personne (et depuis un autre thread).
        emit("speaker_live", {"speaker": label})
    except Exception as e:
        print(f"[Probe error] {type(e).__name__}: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _transcribe_and_diarize(path: str, sid: str, chunk_offset: float, chunk_abs_time: float) -> list:
    """Tout le travail bloquant d'un chunk (Whisper GPU, décodage, ECAPA CPU,
    filtrage) — exécuté en thread natif (tpool) pour ne pas geler la boucle
    eventlet pendant l'inférence (cf. commentaire sur eventlet.monkey_patch en
    tête de backend.py : celui-ci ne couvre que le réseau, pas le calcul).
    Aucun appel socket ici : on retourne les segments prêts à émettre,
    l'émission et les effets de bord socket.io restent sur le greenlet
    appelant. Appelé sous le verrou de session (session_chunk_locks) : sûr de
    muter tracker/history ici, un seul chunk de CETTE session est traité à la
    fois."""
    with model_lock:
        hotwords = session_contexts.get(sid, {}).get("hotwords") or None
        segments_gen, _ = model.transcribe(
            path,
            language="fr",
            beam_size=5,
            temperature=0,
            vad_filter=True,
            vad_parameters={"threshold": 0.3, "min_silence_duration_ms": 300},
            no_speech_threshold=0.45,
            compression_ratio_threshold=2.4,
            hotwords=hotwords,
        )
        segments = list(segments_gen)

    wav = None
    if DIARIZATION and segments:
        try:
            wav = decode_audio(path)
        except Exception as e:
            print(f"[Diar] decode_audio: {type(e).__name__}: {e}")

    tracker = session_speakers.get(sid)
    history = session_history.get(sid, [])
    out = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if is_hallucination(text) or is_hotword_echo(text, hotwords or ""):
            print(f"  → filtré (hallucination): {text[:50]}")
            continue
        if history and seg.start < CHUNK_OVERLAP_S + 0.5:
            # Début de chunk = zone de chevauchement avec le précédent
            trimmed = strip_overlap(history[-1], text)
            if trimmed != text:
                print(f"  → chevauchement retiré: {text[:len(text) - len(trimmed)][:50]}")
                text = trimmed
                if not text:
                    continue
        if text in history:
            print(f"  → filtré (doublon): {text[:50]}")
            continue
        history.append(text)
        if len(history) > 5:
            history.pop(0)
        label = speaker_label(tracker, wav, seg)
        print(f"  → émis{f' [{label}]' if label else ''}: {text[:80]}")
        out.append({
            "text": text,
            "speaker": label,
            "start": round(chunk_offset + seg.start, 3),
            "end": round(chunk_offset + seg.end, 3),
            "abs_time": chunk_abs_time,
        })
    session_history[sid] = history
    return out


def flush_to_mistral(sid: str, text: str, ts: float = None):
    print(f"[Mistral] Envoi de {len(text.split())} mots pour analyse…")
    lock = session_flush_locks.get(sid)
    if lock:
        lock.acquire()
    try:
        # Substituer les labels déjà identifiés par les vrais noms (contexte
        # utile à Mistral). Instantané de la correspondance utilisée : il sert
        # plus bas à retrouver le label d'origine de chaque point.
        smap_used = dict(session_speaker_map.get(sid, {}))
        text = apply_speaker_map(smap_used, text)
        context = session_contexts.get(sid, {})
        all_points = session_points.get(sid, [])
        raw_points = call_mistral(text, context=context, recent_points=all_points, sid=sid)
        print(f"[Mistral] {len(raw_points)} talking point(s) reçus")

        # Identification des locuteurs : toutes les 2 analyses tant qu'un label
        # est anonyme, puis toutes les 4 tant qu'un nom (non verrouillé par la
        # voix) peut encore être corrigé par les votes suivants — elle
        # s'arrêtait dès que tous les labels avaient un nom, même faux.
        state = session_map_state.get(sid)
        if DIARIZATION and state is not None and not state["inflight"]:
            state["flushes"] += 1
            tracker = session_speakers.get(sid)
            if tracker:
                mapped = session_speaker_map.get(sid, {})
                locked = session_voice_locked.get(sid, set())
                labels = [SpeakerTracker.label_for(i) for i in range(len(tracker.sums))]
                every = 2 if any(l not in mapped for l in labels) else 4
                if state["flushes"] % every == 0 and any(l not in locked for l in labels):
                    state["inflight"] = True
                    socketio.start_background_task(identify_speakers, sid)

        if not raw_points:
            return
        # Label d'origine de chaque point (pour la correction rétroactive côté
        # extension), puis nom confirmé ACTUEL. Mistral a vu le transcript avec
        # les noms substitués : son « qui » est souvent déjà un nom, qu'on
        # ramène à son label via l'instantané — sinon un point attribué à un
        # nom erroné ne pouvait plus jamais être corrigé.
        label_of = {}
        for label, name in smap_used.items():
            label_of.setdefault(name, label)
        smap = session_speaker_map.get(sid, {})
        for p in raw_points:
            qui = p.get("qui", "")
            p["qui_label"] = qui if qui in smap_used else label_of.get(qui, qui)
            p["qui"] = smap.get(p["qui_label"], qui)
        index = session_dupe_index.setdefault(sid, {})
        combined = list(all_points)
        unique: list = []
        for p in raw_points:
            if is_duplicate_indexed(p['texte'], combined, index):
                print(f"[Dedup] ignoré: {p['texte'][:70]}")
            else:
                # ts = horodatage (unix) approximatif du moment où le propos a été
                # tenu → permet le "sauter à ce moment de la vidéo" côté extension
                entry = {"id": uuid.uuid4().hex[:8], "ts": ts, **p}
                dupe_index_add(index, claim_signature(entry['texte']).words, len(combined))
                combined.append(entry)
                unique.append(entry)
        if not unique:
            print("[Dedup] tous les points étaient des doublons — rien émis")
            return
        socketio.emit("talking_points", {"points": unique}, to=sid)
        # Garder TOUS les points de la session (pas de cap) pour déduplication globale
        session_points[sid] = combined
        # Les noms propres des points (« Lecornu », « Fessenheim »…) deviennent
        # des mots attendus par Whisper pour la suite du débat
        ctx = session_contexts.get(sid)
        if ctx is not None:
            for p in unique:
                learn(ctx.setdefault("learned", []), p["texte"])
            _refresh_hotwords(sid)
        # Fact-check uniquement les affirmations
        for p in unique:
            if p["type"] == "affirmation":
                _spawn_tracked(sid, fact_check_affirmation, sid, p["id"], p["texte"])
    except Exception as e:
        print(f"[Mistral error] {type(e).__name__}: {e}")
        warn_client(sid, describe_error(e))
    finally:
        if lock:
            lock.release()


def _flush_buffer(sid: str, buf: dict, fallback_ts: float) -> dict:
    """Envoie le buffer à Mistral (tâche de fond) et renvoie un buffer vide."""
    text_to_send = build_transcript(buf["entries"])
    # Conserver le transcript annoté (labels d'origine) comme preuve pour
    # l'identification des locuteurs — 6 derniers extraits
    ex = session_excerpts.get(sid)
    if ex is not None and DIARIZATION:
        ex.append(text_to_send)
        del ex[:-6]
    ts = buf.get("start_abs") or fallback_ts
    _spawn_tracked(sid, flush_to_mistral, sid, text_to_send, ts)
    return {"entries": [], "last_flush": time.time(), "start_abs": None}


@socketio.on("audio_chunk")
def handle_audio_chunk(data):
    if not data:
        return

    sid = request.sid
    tmp_path = None
    lock = session_chunk_locks.get(sid)
    if lock:
        lock.acquire()
    try:
        print(f"[Chunk] reçu ({len(data)} bytes)")
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(bytes(data) if not isinstance(data, bytes) else data)
            tmp_path = tmp.name

        chunk_abs_time = time.time()
        session_start = session_starts.get(sid, chunk_abs_time)
        chunk_offset = chunk_abs_time - session_start

        results = eventlet.tpool.execute(_transcribe_and_diarize, tmp_path, sid, chunk_offset, chunk_abs_time)
        print(f"[Whisper] {len(results)} segment(s) émis")

        emitted = []  # [(label_locuteur, texte)]
        for r in results:
            emitted.append((r["speaker"], r["text"]))
            emit("transcript_segment", r)

        if emitted:
            match_clusters_to_bank(sid)
            # Retenté à chaque chunk (pas seulement à chaque nouvelle
            # confirmation LLM) : un nom confirmé avant VOICE_ENROLL_MIN_SEGMENTS
            # doit être réessayé au fur et à mesure que tracker.counts grandit.
            auto_enroll_voices(sid)

        if MISTRAL_API_KEY:  # sans clé : transcription seule (averti dans on_start)
            buf = session_buffers.get(sid) or {"entries": [], "last_flush": time.time(), "start_abs": None}
            if emitted:
                if not buf["entries"]:
                    # Début (approximatif) du texte accumulé — sert d'horodatage aux points
                    buf["start_abs"] = chunk_abs_time
                buf["entries"].extend(emitted)
            elapsed_since_flush = time.time() - buf["last_flush"]
            word_count = sum(len(t.split()) for _, t in buf["entries"])
            if buf["entries"]:
                print(f"[Buffer] {word_count} mots, {elapsed_since_flush:.0f}s depuis dernier flush")
            dense = word_count >= MIN_WORDS and (elapsed_since_flush >= FLUSH_INTERVAL or word_count >= MAX_BUFFER_WORDS)
            # Plus rien de neuf dans ce chunk (pause, fin de tirade, pub) : le
            # buffer n'était vidé qu'à l'arrivée de NOUVEAU texte — une fin
            # d'intervention restait en attente indéfiniment
            paused = not emitted and word_count >= MIN_WORDS_ON_PAUSE and elapsed_since_flush >= FLUSH_INTERVAL
            if dense or paused:
                buf = _flush_buffer(sid, buf, chunk_abs_time)
            session_buffers[sid] = buf

    except Exception as e:
        print(f"[ERREUR] {type(e).__name__}: {e}")
        warn_client(sid, f"erreur de transcription ({type(e).__name__})")

    finally:
        if lock:
            lock.release()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

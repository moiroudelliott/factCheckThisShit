"""Diarisation (clustering de voix par session) + banque d'empreintes
persistante + identification des locuteurs (vote LLM et/ou match acoustique).

Il n'existe aucune API publique d'empreintes de personnalités (un embedding
n'est comparable qu'au sein d'un même modèle + terrain miné RGPD). On
construit donc la nôtre : empreintes ECAPA locales dans voices/, alimentées
manuellement (enroll.py) ou automatiquement quand un locuteur a été
identifié de façon fiable."""

import json
import os

import numpy as np

from server.app import DIARIZATION, speaker_encoder, socketio
from server.config import (
    DIARIZATION_THRESHOLD, MIN_NEW_SPEAKER_SEC, MAX_SPEAKERS, PROBE_MATCH_T,
    VOICES_DIR, VOICE_MATCH_THRESHOLD, VOICE_MATCH_MARGIN, VOICE_MATCH_SOLO_BONUS,
    VOICE_ENROLL_MIN_SEGMENTS, VOICE_ENROLL_MIN_VOTES,
)
from server import voice_store
from server.factcheck import call_mistral_api
from server.names import canonical_name, matches_any
from server.state import (
    session_speakers, session_contexts, session_speaker_map, session_voice_locked,
    session_bank_miss, session_excerpts, session_map_votes, session_map_state,
)

if DIARIZATION:
    import torch
    from faster_whisper.audio import decode_audio


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
            self.last = self.label_for(best)
        elif dur < MIN_NEW_SPEAKER_SEC or len(self.sums) >= MAX_SPEAKERS:
            # Segment court (interjection, brouhaha) ou banque de locuteurs
            # pleine : jamais assez fiable pour créer un nouveau cluster (ça
            # fragmentait en locuteurs fantômes — 11 labels pour 6 voix
            # réelles). MAIS on ne force plus non plus le rattachement au
            # cluster "le moins pire" quand il ne ressemble en fait à rien
            # (best_sim < PROBE_MATCH_T) : la première réplique courte d'un
            # nouveau venu ne doit jamais être happée par un cluster
            # simplement parce qu'il est, de peu, le moins dissemblable des
            # existants (cas vécu : le "oui" de Jean-François Copé absorbé par le
            # cluster de Sébastien Chenu, qui n'a ensuite plus jamais son
            # propre cluster). Dans ce cas on hérite du dernier locuteur actif
            # (self.last inchangé) plutôt que de coller un nom au hasard.
            if best >= 0 and best_sim >= PROBE_MATCH_T:
                self.last = self.label_for(best)
        else:
            self.sums.append(emb.copy())
            self.counts.append(1)
            self.last = self.label_for(len(self.sums) - 1)
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


def probe_speaker(path: str, tracker) -> tuple:
    """Calcul bloquant (décodage + ECAPA) d'une sonde — exécuté en thread natif
    (tpool, voir routes.py) pour ne pas geler la boucle eventlet. Lecture
    seule sur le tracker : ne modifie jamais les clusters, donc pas besoin du
    verrou de session."""
    wav = decode_audio(path)
    if len(wav) < 16000:  # moins d'une seconde utile
        return "", -1.0
    if float(np.sqrt((wav ** 2).mean())) < 0.005:  # silence — le badge s'effacera tout seul
        return "", -1.0
    with torch.no_grad():
        t = torch.from_numpy(wav).float().unsqueeze(0)
        emb = speaker_encoder.encode_batch(t).squeeze().cpu().numpy()
    return tracker.match(emb)


# ── Banque d'empreintes vocales ───────────────────────────────────────────

_voice_bank: dict = {}  # nom → embedding normalisé


def load_voice_bank(verbose: bool = True):
    _voice_bank.clear()
    try:
        for name, meta in voice_store.load_index().items():
            p = os.path.join(VOICES_DIR, meta.get("file", ""))
            if meta.get("file") and os.path.exists(p):
                v = np.load(p)
                _voice_bank[name] = v / (np.linalg.norm(v) + 1e-8)
        if _voice_bank and verbose:
            print(f"[Voix] {len(_voice_bank)} empreinte(s) en banque: {', '.join(_voice_bank)}")
    except Exception as e:
        print(f"[Voix] erreur chargement banque: {type(e).__name__}: {e}")


def save_voice(name: str, emb, auto: bool = False):
    voice_store.save_embedding(name, emb, auto=auto)
    _voice_bank[name] = emb / (np.linalg.norm(emb) + 1e-8)
    print(f"[Voix] empreinte {'auto-' if auto else ''}enregistrée: {name}")


load_voice_bank()


def speaker_labels_list(tracker) -> list:
    if not tracker:
        return []
    return [SpeakerTracker.label_for(i) for i in range(len(tracker.sums))]


def apply_speaker_map(smap: dict, transcript: str) -> str:
    """Substitue les labels confirmés par les vrais noms dans un transcript annoté."""
    for label, name in smap.items():
        transcript = transcript.replace(f"{label}:", f"{name}:")
    return transcript


def _enrollable(sid: str, name: str, label: str) -> bool:
    """Ce nom POURRA-t-il être enrôlé pendant cette session ? (invité déclaré,
    pas déjà en banque, label pas identifié acoustiquement) — sinon
    l'extension n'affiche pas d'indicateur « capture d'empreinte » qui ne
    se terminerait jamais."""
    guests = session_contexts.get(sid, {}).get("guests") or []
    return (name not in _voice_bank and label not in session_voice_locked.get(sid, set())
            and matches_any(name, guests))


def _emit_speaker_map(sid: str, confirmed: dict):
    """Émission commune à match_clusters_to_bank et identify_speakers : inclut
    quels noms sont déjà en banque (enrolled) et lesquels peuvent l'être
    pendant cette session (enrollable), pour que l'extension sache si elle
    doit afficher une capture d'empreinte en cours ou juste le nom."""
    enrolled = sorted({n for n in confirmed.values() if n in _voice_bank})
    enrollable = sorted({n for label, n in confirmed.items() if _enrollable(sid, n, label)})
    socketio.emit("speaker_map", {"map": confirmed, "enrolled": enrolled, "enrollable": enrollable}, to=sid)


def match_clusters_to_bank(sid: str):
    """Attribution ACOUSTIQUE : compare les centroïdes de la session aux
    empreintes de la banque. Une correspondance nette est définitive et
    prioritaire sur l'identification LLM — elle doit donc être sûre :

    - la banque accumule des voix de TOUS les débats passés : seul un invité
      déclaré pour cette session peut être retenu (sinon un présentateur
      héritait du nom de quelqu'un d'un tout autre débat — cas vécu :
      « François Ruffin », absent de l'émission) ;
    - mais la comparaison se fait contre TOUTE la banque : l'invité doit
      être la meilleure correspondance de toute la banque, avec une marge
      sur la 2e. Restreindre aussi la comparaison aux invités supprimait la
      marge dès qu'un seul invité était en banque (2e meilleure = aucune) :
      n'importe quelle voix un peu proche était alors verrouillée à son nom.

    Un label comparé à la banque sans correspondance déclenche
    voice_not_in_bank : l'extension peut afficher « Locuteur non identifié »
    tout de suite (le vote LLM continue en parallèle)."""
    if not DIARIZATION:
        return
    tracker = session_speakers.get(sid)
    if not tracker or not tracker.sums:
        return
    guests = session_contexts.get(sid, {}).get("guests") or []
    candidates = {n for n in _voice_bank if not guests or matches_any(n, guests)}
    confirmed = session_speaker_map.setdefault(sid, {})
    locked = session_voice_locked.setdefault(sid, set())
    bank_missed = session_bank_miss.setdefault(sid, set())
    labels = speaker_labels_list(tracker)
    changed = False
    newly_missed = []
    for i, label in enumerate(labels):
        if label in locked or tracker.counts[i] < 3:
            continue  # déjà identifié par la voix, ou pas assez de matière
        matched = False
        if candidates:
            centroid = tracker.sums[i] / tracker.counts[i]
            centroid = centroid / (np.linalg.norm(centroid) + 1e-8)
            sims = sorted(((float(np.dot(centroid, ref)), name)
                           for name, ref in _voice_bank.items()), reverse=True)
            best, best_name = sims[0]
            if len(sims) > 1:
                clear = best - sims[1][0] >= VOICE_MATCH_MARGIN
            else:  # une seule voix en banque : pas de 2e pour mesurer la marge
                clear = best >= VOICE_MATCH_THRESHOLD + VOICE_MATCH_SOLO_BONUS
            if best_name in candidates and best >= VOICE_MATCH_THRESHOLD and clear:
                matched = True
                locked.add(label)
                if confirmed.get(label) != best_name:
                    confirmed[label] = best_name
                    changed = True
                    print(f"[Voix] {label} = {best_name} (cos {best:.2f})")
        if not matched and label not in bank_missed:
            bank_missed.add(label)
            newly_missed.append(label)
    if changed:
        session_speaker_map[sid] = confirmed
        _emit_speaker_map(sid, confirmed)
    if newly_missed:
        socketio.emit("voice_not_in_bank", {"labels": newly_missed}, to=sid)


def auto_enroll_voices(sid: str):
    """Sauvegarde en banque les voix identifiées de façon fiable par les votes
    LLM. Idempotente (name in _voice_bank bloque un doublon), donc sans
    risque à rappeler souvent : après chaque chunk transcrit, à chaque
    nouvelle confirmation dans identify_speakers, et à la déconnexion. Un nom
    confirmé avant d'avoir assez de segments est ainsi retenté au fur et à
    mesure que `tracker.counts` grandit.

    Une empreinte en banque est définitive (reconnaissance acoustique
    verrouillée aux débats suivants) : on exige donc plus que la simple
    confirmation d'affichage — VOICE_ENROLL_MIN_VOTES votes, tous pour ce
    même nom, et un invité déclaré. Émet voice_enrolled pour que l'extension
    retire son indicateur « capture de l'empreinte… »."""
    if not DIARIZATION:
        return
    tracker = session_speakers.get(sid)
    confirmed = session_speaker_map.get(sid, {})
    locked = session_voice_locked.get(sid, set())
    votes = session_map_votes.get(sid, {})
    guests = (session_contexts.get(sid, {}).get("guests")) or []
    if not tracker or not confirmed:
        return
    labels = speaker_labels_list(tracker)
    for i, label in enumerate(labels):
        name = confirmed.get(label)
        if (not name or label in locked or name in _voice_bank or not matches_any(name, guests)
                or tracker.counts[i] < VOICE_ENROLL_MIN_SEGMENTS):
            continue
        cand = votes.get(label, {})
        if cand.get(name, 0) < VOICE_ENROLL_MIN_VOTES or len(cand) > 1:
            continue  # vote pas encore assez sûr (ou contesté) pour une empreinte définitive
        centroid = tracker.sums[i] / tracker.counts[i]
        save_voice(name, centroid, auto=True)
        socketio.emit("voice_enrolled", {"name": name}, to=sid)


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


def identify_speakers(sid: str):
    """Tâche de fond : associe les labels anonymes aux vrais noms par VOTE
    MAJORITAIRE. Chaque appel Mistral = un vote par label ; un nom est confirmé
    à 2 votes concordants (et strictement devant les autres candidats). Un
    mapping confirmé reste corrigeable si les votes suivants le contredisent —
    c'est pourquoi l'identification continue tant qu'un label n'est pas
    verrouillé acoustiquement, même une fois tous les labels nommés.
    Les noms votés sont ramenés à leur forme de référence (clé de la banque
    ou invité déclaré) : « Bardella » et « Jordan Bardella » sont un seul
    candidat."""
    state = session_map_state.get(sid)
    try:
        tracker = session_speakers.get(sid)
        excerpts = session_excerpts.get(sid, [])
        confirmed = session_speaker_map.get(sid, {})
        locked = session_voice_locked.get(sid, set())
        labels = speaker_labels_list(tracker)
        if not excerpts or not [l for l in labels if l not in locked]:
            return
        ctx = session_contexts.get(sid, {})
        guests = ctx.get("guests") or []
        prompt = SPEAKER_MAP_PROMPT_TEMPLATE.format(
            emission_line=f"Émission: {ctx['emission']}\n" if ctx.get("emission") else "",
            guests_line=", ".join(guests) if guests else "(liste non fournie)",
            excerpts="\n---\n".join(excerpts),
        )
        content = call_mistral_api(prompt, sid=sid)
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
                n = canonical_name(name.strip()[:48], guests, _voice_bank)
                votes.setdefault(label, {})
                votes[label][n] = votes[label].get(n, 0) + 1

        # Confirmation / correction à la majorité (jamais sur un label déjà
        # identifié acoustiquement — la voix prime sur l'inférence LLM)
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
            _emit_speaker_map(sid, confirmed)
        # Empreinte vocale sauvegardée dès que possible, pas seulement à la
        # fin du débat — voir auto_enroll_voices (qui exige plus de votes).
        auto_enroll_voices(sid)
    except Exception as e:
        print(f"[SpeakerMap error] {type(e).__name__}: {e}")
    finally:
        if state is not None:
            state["inflight"] = False

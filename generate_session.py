"""Génère une session de relecture à partir d'un lien YouTube — sans regarder
la vidéo.

L'audio est téléchargé (yt-dlp), découpé comme le fait l'extension (chunks
de 10 s qui se chevauchent de 1,5 s, sondes « qui parle » de 2,5 s), et
envoyé aux VRAIS handlers du backend : transcription, voix, extraction,
fact-check, cache, identification des locuteurs — exactement le pipeline du
direct. Le fichier produit est celui qu'exporte le bouton ⏵ du récap.

Horloge : chaque morceau « arrive » à sa position dans la vidéo, comme en
direct, mais les temps d'attente sont sautés ; seuls les vrais temps de
calcul (Whisper, Mistral, recherche) s'écoulent. Chaque tâche de fond hérite
de l'heure de celle qui l'a lancée : une carte apparaît donc à la relecture
au même moment qu'en direct, avec le même délai de vérification — sans
passer 1 h devant une vidéo d'1 h. Les attentes dues aux limites de débit
de Mistral (plus fréquentes qu'en direct, tout allant plus vite) ne comptent
pas dans cette horloge.

Usage:
    python generate_session.py https://www.youtube.com/watch?v=XXXXXXXXXXX
    python generate_session.py URL --guests "Gabriel Attal, Marion Maréchal" --publish
    python generate_session.py URL --limit 120        # 2 premières minutes (essai)

Par défaut, les intervenants sont détectés comme dans la popup (titre et
description). --publish range directement la session sur le site
(publish_session.py). SearxNG doit tourner : sans recherche web, presque
aucun verdict n'aurait de source (--sans-recherche pour passer outre).
Le backend peut rester lancé : ce script charge sa propre copie des modèles.
"""

import eventlet

eventlet.monkey_patch(socket=True, select=True)

import argparse  # noqa: E402
import copy  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import wave  # noqa: E402

import greenlet  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

SR = 16000
CHUNK_S, OVERLAP_S, PROBE_S = 10.0, 1.5, 2.5   # = CHUNK_MS / OVERLAP_MS / PROBE_MS de offscreen.js
MAX_PENDING = 3          # analyses / fact-checks en vol avant d'envoyer la suite (débit Mistral)
SEARCH_GAP_S = 5.0       # écart mini entre deux vérifications (recherche web) : au-delà, Brave suspend SearxNG
DONE_TIMEOUT_S = 180     # attente max de la fin des dernières vérifications

# Ce que l'extension enregistre (content.js → TAPE_TYPES), sauf les messages
# de limite de débit : ici ce sont des artefacts du traitement accéléré
TAPE_EVENTS = {"talking_points", "fact_check_result", "speaker_map", "speaker_live", "transcript_segment",
               "voice_enrolled", "voice_not_in_bank", "session_reset", "server_warning", "session_done"}


# ── Horloge de la vidéo ─────────────────────────────────────────────────────

class VideoClock:
    """Heure virtuelle (unix) par greenlet : base fixée à l'arrivée d'un
    morceau, puis écoulement réel pendant le calcul. Une tâche lancée hérite
    de l'heure de sa lanceuse."""

    def __init__(self):
        self.origin = time.time()  # instant virtuel où la vidéo est à 0:00
        self._bases = {}

    def now(self) -> float:
        v0, r0 = self._bases.get(greenlet.getcurrent(), (self.origin, time.monotonic()))
        return v0 + (time.monotonic() - r0)

    def at(self, video_t: float):
        """Place le greenlet courant à la position video_t de la vidéo."""
        self._bases[greenlet.getcurrent()] = (self.origin + video_t, time.monotonic())

    def video_now(self) -> float:
        return self.now() - self.origin

    def paused(self, seconds: float):
        """Attente qui ne compte pas (limite de débit Mistral) : l'heure
        virtuelle reste figée pendant qu'elle s'écoule."""
        v_now = self.now()
        eventlet.sleep(seconds)
        self._bases[greenlet.getcurrent()] = (v_now, time.monotonic())

    def inherit(self, fn):
        v_spawn = self.now()

        def run(*a, **k):
            self._bases[greenlet.getcurrent()] = (v_spawn, time.monotonic())
            try:
                return fn(*a, **k)
            finally:
                self._bases.pop(greenlet.getcurrent(), None)
        return run


class _TimeShim:
    """Module time de routes.py : time.time() lit l'horloge de la vidéo."""

    def __init__(self, clock):
        self._clock = clock

    def time(self):
        return self._clock.now()

    def __getattr__(self, name):
        return getattr(time, name)


class _EventletShim:
    """Module eventlet de factcheck.py : les attentes de limite de débit ne
    comptent pas dans l'horloge."""

    def __init__(self, clock):
        self._clock = clock

    def sleep(self, seconds=0):
        if seconds and seconds > 0.2:
            self._clock.paused(seconds)
        else:
            eventlet.sleep(seconds)

    def __getattr__(self, name):
        return getattr(eventlet, name)


# ── Vidéo et audio ──────────────────────────────────────────────────────────

def download(url: str, workdir: str, force_ipv4: bool) -> tuple:
    import yt_dlp
    # YouTube exige de résoudre des défis JavaScript (paquet yt-dlp-ejs +
    # un moteur JS) : sans eux, téléchargement refusé (HTTP 403)
    opts = {"format": "bestaudio/best", "outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"),
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "js_runtimes": {"deno": {}, "node": {}, "bun": {}}}
    if force_ipv4:
        opts["source_address"] = "0.0.0.0"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    wav = os.path.join(workdir, "audio.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                    "-sample_fmt", "s16", wav], check=True)
    return info, wav


def wav_slice(reader: wave.Wave_read, start: float, end: float) -> bytes:
    reader.setpos(int(start * SR))
    frames = reader.readframes(max(0, int((end - start) * SR)))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(frames)
    return out.getvalue()


def schedule(duration: float) -> list:
    """(arrivée, type, début, fin) dans l'ordre d'arrivée — comme l'extension :
    un chunk de 10 s démarre toutes les 8,5 s, une sonde toutes les 2,5 s."""
    items = []
    k = 0
    while k * (CHUNK_S - OVERLAP_S) < duration:
        start = k * (CHUNK_S - OVERLAP_S)
        end = min(start + CHUNK_S, duration)
        items.append((end, "audio_chunk", start, end))
        k += 1
    t = PROBE_S
    while t <= duration:
        items.append((t, "speaker_probe", t - PROBE_S, t))
        t += PROBE_S
    return sorted(items, key=lambda i: (i[0], i[1] != "speaker_probe"))


# ── Bande enregistrée (format du bouton ⏵ de l'extension) ────────────────────

def to_message(event: str, data: dict, origin: float):
    """Message tel que l'offscreen le transmet à l'overlay (offscreen.js)."""
    data = data or {}
    if event in ("speaker_live", "transcript_segment"):
        return {"type": event, "speaker": data["speaker"]} if data.get("speaker") else None
    if event == "talking_points":
        points = []
        for raw in data.get("points") or []:
            # Position du propos dans la vidéo — même règle que addPoint
            # (content.js) : instant retrouvé par la citation, sinon début du
            # texte analysé moins 8 s
            said_at, ts = raw.get("said_at"), raw.get("ts")
            vt = (said_at - origin if isinstance(said_at, (int, float))
                  else ts - 8 - origin if isinstance(ts, (int, float)) else None)
            p = {k: v for k, v in raw.items() if k not in ("ts", "said_at")}
            p["vt"] = round(max(0.0, vt), 1) if vt is not None else None
            points.append(p)
        return {"type": event, "points": points}
    if event == "fact_check_result":
        return {"type": event, "id": data.get("id"), "verdict": data.get("verdict"),
                "explication": data.get("explication"), "source": data.get("source") or "",
                "url": data.get("url") or "", "confiance": data.get("confiance"),
                "indisponible": bool(data.get("indisponible"))}
    if event == "speaker_map":
        return {"type": event, "map": data.get("map") or {}, "enrolled": data.get("enrolled") or [],
                "enrollable": data.get("enrollable") or []}
    if event == "voice_enrolled":
        return {"type": event, "name": data["name"]} if data.get("name") else None
    if event == "voice_not_in_bank":
        return {"type": event, "labels": data["labels"]} if data.get("labels") else None
    if event == "server_warning":
        return {"type": event, "message": data["message"]} if data.get("message") else None
    if event == "session_done":
        return {"type": event, "complete": bool(data.get("complete"))}
    return {"type": event}


# ── Programme ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Génère une session de relecture à partir d'une vidéo YouTube.")
    ap.add_argument("url", help="Lien de la vidéo YouTube")
    ap.add_argument("--guests", default="", help="Intervenants, séparés par des virgules (défaut : détection auto)")
    ap.add_argument("--title", default="", help="Titre affiché sur le site (défaut : titre de la vidéo)")
    ap.add_argument("--limit", type=float, default=0, help="N'analyser que les N premières secondes (essai)")
    ap.add_argument("--out", default="", help="Fichier de sortie (défaut : source-session_<date>_<id>.json)")
    ap.add_argument("--publish", action="store_true", help="Publier directement sur le site (publish_session.py)")
    ap.add_argument("--sans-recherche", action="store_true", help="Continuer même si SearxNG est éteint")
    args = ap.parse_args()

    from server import network
    from server.config import FORCE_IPV4
    ipv4 = network.configure(FORCE_IPV4) == "ipv4"

    workdir = tempfile.mkdtemp(prefix="source_gen_")
    print("⬇ Téléchargement de l'audio…")
    info, wav_path = download(args.url, workdir, ipv4)
    vid, title = info.get("id"), info.get("title") or ""
    channel = info.get("channel") or info.get("uploader") or ""
    date = info.get("upload_date") or ""
    date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else ""
    reader = wave.open(wav_path, "rb")
    duration = reader.getnframes() / SR
    if args.limit:
        duration = min(duration, args.limit)
    print(f"  {title} — {channel} ({int(duration // 60)} min {int(duration % 60)} s analysées)")

    print("⚙ Chargement du pipeline (Whisper, voix, sources)…")
    import server.routes as routes
    from server import factcheck
    from server.app import socketio, app
    from server.config import BACKEND_TOKEN

    if not factcheck.search_available() and not args.sans_recherche:
        print(f"✗ {factcheck.SEARCH_DOWN_MESSAGE}.\n  Relance ensuite, ou ajoute --sans-recherche.")
        sys.exit(1)

    clock = VideoClock()
    routes.time = _TimeShim(clock)
    factcheck.eventlet = _EventletShim(clock)

    # Recherche web espacée : tout va plus vite qu'en direct, et une rafale
    # fait suspendre Brave par SearxNG (0 résultat pour la suite). L'attente
    # ne compte pas dans l'horloge : le minutage de la relecture reste celui
    # du direct.
    next_slot = [0.0]
    _factcheck = factcheck.call_mistral_factcheck

    def paced_factcheck(*a, **k):
        now = time.monotonic()
        start = max(now, next_slot[0])
        next_slot[0] = start + SEARCH_GAP_S
        if start > now:
            clock.paused(start - now)
        return _factcheck(*a, **k)
    factcheck.call_mistral_factcheck = paced_factcheck
    eio = socketio.server.eio
    _spawn = eio.start_background_task
    eio.start_background_task = lambda target, *a, **k: _spawn(clock.inherit(target), *a, **k)
    socketio.server.async_handlers = False  # un morceau est traité avant l'envoi du suivant

    tape, done = [], []

    def record(event, data):
        if event not in TAPE_EVENTS:
            return
        if event == "session_done":
            done.append(True)
        m = to_message(event, copy.deepcopy(data), clock.origin)
        if m:
            tape.append({"t": round(max(0.0, clock.video_now()), 1), "m": m})

    _sio_emit = socketio.emit

    def sio_emit(event, *a, **k):
        record(event, a[0] if a else None)
        return _sio_emit(event, *a, **k)
    socketio.emit = sio_emit
    _emit = routes.emit

    def handler_emit(event, *a, **k):
        record(event, a[0] if a else None)
        return _emit(event, *a, **k)
    routes.emit = handler_emit

    # Intervenants : comme la popup (Mistral lit le titre et la description)
    guests = [g.strip() for g in args.guests.split(",") if g.strip()]
    if not guests:
        r = app.test_client().post("/analyze_video", headers={"X-Backend-Token": BACKEND_TOKEN or ""},
                                   json={"title": title, "channel": channel,
                                         "description": info.get("description") or "", "publishDate": date})
        guests = (r.get_json() or {}).get("guests") or []
    print(f"👥 Intervenants : {', '.join(guests) or '(aucun détecté)'}")

    client = socketio.test_client(app, auth={"token": BACKEND_TOKEN or ""})
    clock.at(0)
    tape.append({"t": 0.0, "m": {"type": "connection_status", "status": "connected"}})
    client.emit("set_context", {"emission": f"{title} — {channel}" if channel else title,
                                "guests": "\n".join(guests), "date": date,
                                "description": info.get("description") or ""})
    client.emit("start_transcription")

    items = schedule(duration)
    total = sum(1 for i in items if i[1] == "audio_chunk")
    sent = 0
    chunk_free = 0.0       # position où le chunk précédent a fini d'être traité
    real_start = time.monotonic()
    for arrival, kind, start, end in items:
        # Débit Mistral : pas trop d'analyses en vol (une seule session dans ce
        # processus) — l'horloge de la vidéo ne bouge pas pendant cette attente
        while sum(routes.session_pending.values()) > MAX_PENDING:
            eventlet.sleep(0.2)
        if kind == "audio_chunk":
            # En direct, un chunk qui arrive pendant le traitement du
            # précédent attend son tour : même chose ici
            clock.at(max(arrival, chunk_free))
            client.emit("audio_chunk", wav_slice(reader, start, end))
            chunk_free = clock.video_now()
            sent += 1
            if sent % 10 == 0 or sent == total:
                print(f"  {sent}/{total} morceaux — {int(arrival // 60)}:{int(arrival % 60):02d} de vidéo "
                      f"en {int(time.monotonic() - real_start)} s")
        else:
            clock.at(arrival)  # les sondes tournent en parallèle des chunks, en direct
            client.emit("speaker_probe", wav_slice(reader, start, end))
        client.get_received()  # la file du client de test ne sert pas : on la vide

    # Arrêt, comme le bouton ■ : fin de la capture, derniers verdicts
    clock.at(max(duration, chunk_free))
    tape.append({"t": round(duration, 1), "m": {"action": "captureEnded", "reason": "user"}})
    tape.append({"t": round(duration, 1), "m": {"type": "finalizing"}})
    client.emit("stop_transcription")
    print("⏳ Dernières vérifications…")
    deadline = time.monotonic() + DONE_TIMEOUT_S
    while not done and time.monotonic() < deadline:
        eventlet.sleep(0.5)
        client.get_received()
    client.disconnect()
    reader.close()

    tape.sort(key=lambda e: e["t"])
    session = {
        "format": "source-session", "version": 1,
        "video": {"youtube": vid, "title": title},
        "exported": time.strftime("%Y-%m-%d"),
        "offset": 0, "started": 0,
        "events": tape,
    }
    out = args.out or os.path.join(ROOT, f"source-session_{time.strftime('%Y-%m-%d')}_{vid}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False)
    points = sum(len(e["m"].get("points", [])) for e in tape if e["m"].get("type") == "talking_points")
    verdicts = sum(1 for e in tape if e["m"].get("type") == "fact_check_result")
    print(f"✓ {out}\n  {points} points, {verdicts} verdicts, en {int(time.monotonic() - real_start)} s "
          f"pour {int(duration // 60)} min de vidéo")

    if args.publish:
        import publish_session
        publish_session.sync()
        entry = publish_session.publish(out, args.title)
        print(f"✓ Publiée : site/relecture.html?s={entry['id']} — redéployer le dossier site/")


if __name__ == "__main__":
    main()

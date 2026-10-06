"""Publie une session enregistrée sur la page « relecture » du site.

Pendant une analyse, l'extension garde chaque message du backend avec la
position vidéo où il est arrivé ; le bouton ⏵ du récap l'exporte
(source-session_AAAA-MM-JJ_<vidéo>.json). La page site/relecture.html
rejoue ce fichier par-dessus le lecteur YouTube intégré, avec la vraie
interface de l'extension : n'importe qui peut « tester » SOURCÉ sur un
débat, sans GPU ni backend.

Usage:
    python publish_session.py                            # synchronise seulement l'overlay du site
    python publish_session.py export.json                # publie la session (+ synchronise)
    python publish_session.py export.json --title "Débat des législatives — 25 juin 2024"
    python publish_session.py export.json --offset 12.5  # décale toutes les positions (s)

--offset sert à recaler une session enregistrée pendant un DIRECT sur sa
rediffusion YouTube, dont le minutage peut commencer ailleurs : si les
cartes arrivent 12 s trop tôt, --offset 12.

Le fichier publié est nettoyé (seuls les champs affichés sont gardés) et
rangé dans site/sessions/<id YouTube>.json, avec une entrée dans
site/sessions/index.json. La fiche du débat (server/summary.py) est
recalculée sur la session publiée — positions de la vidéo, décalage compris
— et rangée dans site/sessions/<id>.fiche.json pour la page fiche.html ; sa
rédaction (résumé, moments forts) vient de la session, et doit avoir été
écrite avant (en direct, ou python fiche_session.py). Synchronisation : site/overlay/ reçoit une copie
de extension/content.js et overlay.css, site/fonts/ les polices de
l'extension (tests/test_replay.py vérifie que les copies sont à jour).
Ensuite : redéployer le dossier site/.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys

from server import summary
from server.sources import is_inaudible as inaudible_explication

ROOT = os.path.dirname(os.path.abspath(__file__))
EXTENSION = os.path.join(ROOT, "extension")
SITE = os.path.join(ROOT, "site")
SESSIONS = os.path.join(SITE, "sessions")
INDEX = os.path.join(SESSIONS, "index.json")

# Fichiers de l'extension recopiés sur le site : (source, destination)
SYNCED = [
    (os.path.join(EXTENSION, "content.js"), os.path.join(SITE, "overlay", "content.js")),
    (os.path.join(EXTENSION, "overlay.css"), os.path.join(SITE, "overlay", "overlay.css")),
] + [
    (os.path.join(EXTENSION, "fonts", f), os.path.join(SITE, "fonts", f))
    for f in sorted(os.listdir(os.path.join(EXTENSION, "fonts"))) if f.endswith(".woff2")
]

# Fichiers chargés par les pages du site : un numéro de version (empreinte
# du contenu) dans leur adresse force le navigateur à recharger une version
# modifiée — sans lui, les visiteurs gardaient l'ancienne en cache (cas vécu :
# une correction du lecteur invisible après rechargement de la page)
RELECTURE_HTML = os.path.join(SITE, "relecture.html")
VERSIONED = ("style.css", "relecture.js", "fiche.js", "overlay/content.js", "overlay/overlay.css")


def site_pages() -> list:
    return sorted(os.path.join(SITE, f) for f in os.listdir(SITE) if f.endswith(".html"))

YOUTUBE_ID_RE = re.compile(r"^[\w-]{11}$")

# Champs gardés par type de message : tout le reste (horodatages unix du
# direct, champs internes) est retiré avant publication
_STR = (str,)
_POINT_FIELDS = {"id": _STR, "type": _STR, "texte": _STR, "qui": _STR, "qui_label": _STR,
                 "citation": _STR, "verifiable": (int, float), "vt": (int, float, type(None)), "theme": _STR}
_FIELDS = {
    "fact_check_result": {"id": _STR, "verdict": _STR, "confiance": (int, float, type(None)),
                          "explication": _STR, "source": _STR, "url": _STR, "indisponible": (bool,),
                          "inaudible": (bool,)},
    "speaker_map": {"map": (dict,), "enrolled": (list,), "enrollable": (list,)},
    "speaker_live": {"speaker": _STR},
    "transcript_segment": {"speaker": _STR},
    "voice_enrolled": {"name": _STR},
    "voice_not_in_bank": {"labels": (list,)},
    "session_reset": {},
    "connection_status": {"status": _STR},
    "server_warning": {"message": _STR},
    "mistral_rate_limited": {"attempt": (int, float), "max": (int, float), "wait": (int, float)},
    "finalizing": {},
    "session_done": {"complete": (bool,)},
    "debate_summary": {"fiche": (dict,), "final": (bool,)},
}
# Messages envoyés par le service worker (clé « action » et non « type »)
_ACTIONS = {"captureEnded": {"reason": _STR}}


def _keep(obj: dict, fields: dict) -> dict:
    return {k: obj[k] for k, types in fields.items() if k in obj and isinstance(obj[k], types)}


def clean_message(m: dict):
    """Message publiable (champs affichés seulement), ou None s'il est inconnu."""
    if not isinstance(m, dict):
        return None
    kind = m.get("type")
    if kind == "talking_points":
        points = [_keep(p, _POINT_FIELDS) for p in m.get("points") or [] if isinstance(p, dict)]
        points = [p for p in points if p.get("id") and p.get("texte")]
        return {"type": kind, "points": points} if points else None
    if kind in _FIELDS:
        return {"type": kind, **_keep(m, _FIELDS[kind])}
    action = m.get("action") if kind is None else None
    if action in _ACTIONS:
        return {"action": action, **_keep(m, _ACTIONS[action])}
    return None


def validate(data: dict, offset: float = 0.0) -> dict:
    """Session exportée par l'extension → session publiable. Lève ValueError
    si le fichier n'en est pas une."""
    if not isinstance(data, dict) or data.get("format") != "source-session" or data.get("version") != 1:
        raise ValueError("pas un export de session SOURCÉ (bouton ⏵ du récap)")
    video = data.get("video") or {}
    vid = video.get("youtube")
    if not isinstance(vid, str) or not YOUTUBE_ID_RE.match(vid):
        raise ValueError("la relecture ne fonctionne qu'avec une vidéo YouTube")
    shift = float(data.get("offset") or 0) + offset
    events = []
    for e in data.get("events") or []:
        t = e.get("t") if isinstance(e, dict) else None
        m = clean_message(e.get("m")) if isinstance(t, (int, float)) else None
        if m is None:
            continue
        if m.get("type") == "talking_points":
            for p in m["points"]:
                if isinstance(p.get("vt"), (int, float)):
                    p["vt"] = round(max(0.0, p["vt"] + shift), 1)
        events.append({"t": round(max(0.0, t + shift), 1), "m": m})
    events.sort(key=lambda e: e["t"])  # tri stable : l'ordre d'arrivée est gardé à position égale
    if not any(e["m"].get("type") == "talking_points" for e in events):
        raise ValueError("aucun point enregistré dans cette session")
    return {
        "format": "source-session",
        "version": 1,
        "video": {"youtube": vid, "title": str(video.get("title") or "")[:200]},
        "exported": str(data.get("exported") or "")[:10],
        # position où l'analyse a démarré : l'overlay apparaît là, comme en direct
        "started": round(max(0.0, float(data["started"]) + shift), 1)
        if isinstance(data.get("started"), (int, float)) else 0.0,
        "events": events,
    }


def is_inaudible(m: dict) -> bool:
    """Verdict « non vérifiable » dû à la transcription (même règle que
    l'overlay) : drapeau du backend, ou explication « Transcription douteuse »."""
    return m.get("verdict") == "non_verifiable" and (m.get("inaudible") is True
                                                     or inaudible_explication(m.get("explication") or ""))


def stats(session: dict) -> dict:
    points = [p for e in session["events"] if e["m"].get("type") == "talking_points" for p in e["m"]["points"]]
    results = [e["m"] for e in session["events"] if e["m"].get("type") == "fact_check_result"]
    # Propos mal transcrit : l'overlay retire la carte, il ne compte pas comme vérifié
    inaudible = {m["id"] for m in results if is_inaudible(m)}
    verdicts = {m["id"] for m in results if not m.get("indisponible")} - inaudible
    return {"points": len(points),
            "affirmations": sum(p.get("type") == "affirmation" and p.get("id") not in inaudible for p in points),
            "verdicts": len(verdicts),
            "duration": session["events"][-1]["t"] if session["events"] else 0}


def sync() -> list:
    """Recopie l'overlay et les polices de l'extension sur le site ; renvoie
    les fichiers modifiés."""
    changed = []
    for src, dst in SYNCED:
        with open(src, "rb") as f:
            new = f.read()
        old = None
        if os.path.exists(dst):
            with open(dst, "rb") as f:
                old = f.read()
        if new != old:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            changed.append(os.path.relpath(dst, ROOT))
    changed += [os.path.relpath(p, ROOT) for p in stamp_versions()]
    return changed


def asset_version(rel: str) -> str:
    with open(os.path.join(SITE, rel), "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:10]


def stamp_versions() -> list:
    """Inscrit dans chaque page du site la version des fichiers qu'elle
    charge (feuille de style, scripts) ; renvoie les pages modifiées."""
    changed = []
    for page in site_pages():
        with open(page, encoding="utf-8") as f:
            html = f.read()
        new = html
        for rel in VERSIONED:
            new = re.sub(rf'(["\']){re.escape(rel)}(\?v=\w+)?\1', rf"\g<1>{rel}?v={asset_version(rel)}\g<1>", new)
        if new != html:
            with open(page, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            changed.append(page)
    return changed


def load_index() -> dict:
    if not os.path.exists(INDEX):
        return {"sessions": []}
    with open(INDEX, encoding="utf-8") as f:
        return json.load(f)


def build_fiche(session: dict):
    """Fiche du débat recalculée sur la session publiée ; None si ses points
    n'ont pas de thème (session antérieure : python fiche_session.py)."""
    inputs = summary.inputs_from_tape(session)
    points = inputs["points"]
    if not points or sum(1 for p in points if p["theme"]) < 0.5 * len(points):
        return None
    return summary.build_fiche(**{**inputs, "titre": session["video"]["title"] or inputs["titre"]})


def publish(path: str, title: str = "", offset: float = 0.0) -> dict:
    with open(path, encoding="utf-8") as f:
        session = validate(json.load(f), offset)
    if title:
        session["video"]["title"] = title
    vid = session["video"]["youtube"]
    os.makedirs(SESSIONS, exist_ok=True)
    # Fiche : complète dans son propre fichier ; dans la relecture, la version
    # du récap (sans la liste des affirmations, que l'overlay a déjà)
    fiche = build_fiche(session)
    fiche_path = os.path.join(SESSIONS, f"{vid}.fiche.json")
    session["events"] = [e for e in session["events"] if e["m"].get("type") != "debate_summary"]
    if fiche:
        end = max((e["t"] for e in session["events"]), default=0.0)
        session["events"].append({"t": end, "m": {"type": "debate_summary", "fiche": summary.compact(fiche), "final": True}})
        with open(fiche_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(fiche, f, ensure_ascii=False, separators=(",", ":"))
    elif os.path.exists(fiche_path):
        os.remove(fiche_path)
    with open(os.path.join(SESSIONS, f"{vid}.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(session, f, ensure_ascii=False, separators=(",", ":"))
    index = load_index()
    entry = {"id": vid, "title": session["video"]["title"], "date": session["exported"], **stats(session),
             "fiche": bool(fiche)}
    if fiche and fiche.get("date"):
        entry["debat"] = fiche["date"]  # date du débat (≠ date de l'analyse)
    index["sessions"] = [s for s in index.get("sessions", []) if s.get("id") != vid] + [entry]
    with open(INDEX, "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return entry


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Publie une session enregistrée sur la page « relecture » du site.")
    ap.add_argument("session", nargs="?", help="Fichier exporté par l'extension (bouton ⏵ du récap)")
    ap.add_argument("--title", default="", help="Titre affiché sur le site (défaut : titre de la vidéo)")
    ap.add_argument("--offset", type=float, default=0.0, help="Secondes ajoutées à chaque position")
    args = ap.parse_args()

    for f in sync():
        print(f"↻ {f}")
    if not args.session:
        print("Overlay du site à jour.")
        return
    try:
        entry = publish(args.session, args.title, args.offset)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"✗ {e}")
        sys.exit(1)
    print(f"✓ site/sessions/{entry['id']}.json — {entry['title'] or entry['id']}")
    print(f"  {entry['points']} points, {entry['affirmations']} affirmations, {entry['verdicts']} verdicts, "
          f"{int(entry['duration'] // 60)} min")
    print(f"  Relecture : site/relecture.html?s={entry['id']} — redéployer le dossier site/")
    if entry["fiche"]:
        print(f"  Fiche : site/fiche.html?s={entry['id']}")
    else:
        print(f"  Pas de fiche (points sans thème) : python fiche_session.py {args.session}, puis republier")


if __name__ == "__main__":
    main()

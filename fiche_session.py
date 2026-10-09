"""Rédige la fiche de fin de débat d'une session enregistrée — pour une
session générée avant la fiche, ou pour la récrire après une revérification
(rejuger_session.py). En direct et avec generate_session.py, le backend la
rédige lui-même à la fin de l'analyse.

Usage:
    python fiche_session.py source-session_X.json --date 2024-06-27
    python fiche_session.py source-session_X.json --modele mistral-large-latest --out essai.json
    python fiche_session.py source-session_X.json --sans-redaction     # chiffres seuls, sans Mistral
    python fiche_session.py source-session_X.json --animateurs "Darius Rochebin"   # hors de la fiche

Points sans thème (sessions antérieures au champ « theme ») : classés
d'abord, en un ou deux appels, et le thème est inscrit dans la bande. La
fiche rejoint la bande (message debate_summary, comme en direct) ;
publish_session.py en tire ensuite site/sessions/<id>.fiche.json.
"""

import argparse
import json
import sys
import types


def _load_server():
    """Mistral seul : ni Whisper, ni socket.io."""
    app = types.ModuleType("server.app")
    app.DIARIZATION = True
    app.socketio = types.SimpleNamespace(emit=lambda *a, **k: None)
    sys.modules.setdefault("server.app", app)
    from server import summary
    return summary


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Rédige la fiche de fin de débat d'une session enregistrée.")
    ap.add_argument("session", help="Session enregistrée (source-session_*.json)")
    ap.add_argument("--date", default="", help="Date du débat AAAA-MM-JJ (défaut : celle de la fiche déjà enregistrée)")
    ap.add_argument("--titre", default="", help="Titre du débat (défaut : celui de la fiche enregistrée, sinon de la vidéo)")
    ap.add_argument("--modele", default="", help="Modèle Mistral de la rédaction (défaut : MISTRAL_FICHE_MODEL)")
    ap.add_argument("--out", default="", help="Fichier de sortie (défaut : la session elle-même)")
    ap.add_argument("--sans-redaction", action="store_true", help="Chiffres seuls, sans appel à Mistral pour la rédaction")
    ap.add_argument("--animateurs", default=None,
                    help="Présentateurs ou journalistes à laisser hors de la fiche, séparés par des virgules "
                         "(défaut : ceux de la fiche enregistrée ; \"\" pour aucun)")
    args = ap.parse_args()

    summary = _load_server()
    with open(args.session, encoding="utf-8") as f:
        session = json.load(f)
    inputs = summary.inputs_from_tape(session)
    if not inputs["points"]:
        raise SystemExit("✗ aucun point dans cette session")

    tagged = summary.ensure_themes(inputs["points"])
    if tagged:
        theme_of = {p["id"]: p["theme"] for p in inputs["points"]}
        for e in session["events"]:
            if e["m"].get("type") == "talking_points":
                for p in e["m"].get("points") or []:
                    if p.get("id") in theme_of:
                        p["theme"] = theme_of[p["id"]]
        print(f"🏷 {tagged} point(s) classés par thème")

    title = args.titre or inputs["titre"] or (session.get("video") or {}).get("title", "")
    if args.animateurs is not None:
        inputs["animateurs"] = [n.strip() for n in args.animateurs.split(",") if n.strip()]
    fiche = summary.build_fiche(**{**inputs, "titre": title, "date": args.date or inputs["date"], "redaction": None})
    if not args.sans_redaction:
        fiche["redaction"] = summary.write_redaction(fiche, inputs["points"], model=args.modele or None)

    events = [e for e in session["events"] if e["m"].get("type") != "debate_summary"]
    end = max((e["t"] for e in events), default=0.0)
    events.append({"t": end, "m": {"type": "debate_summary", "fiche": summary.compact(fiche), "final": True}})
    session["events"] = events
    out = args.out or args.session
    with open(out, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False)

    print(f"✓ {out}")
    if fiche["animateurs"]:
        print(f'  hors fiche (animateurs) : {", ".join(fiche["animateurs"])}')
    for d in fiche["debatteurs"]:
        idx = f'{d["exactitude"]} % ± {d["marge"]}' if d["suffisant"] else "trop peu de verdicts"
        print(f'  {d["nom"]:<22} {d["tranches"]:>3} tranchés · {idx} · {d["temps_parole"] // 60} min de parole')
    print("  thèmes :", ", ".join(f'{t["label"]} {t["duree"] // 60} min' for t in fiche["themes"] if t["duree"] >= 60))
    red = fiche["redaction"]
    if red:
        aff = {a["id"]: a for a in fiche["affirmations"]}
        print(f'\n  [{red["modele"]}] {red["resume"]}\n')
        for m in red["moments"]:
            a = aff[m["id"]]
            print(f'  ★ {a["verdict"]:<9} {m["qui"]} — « {a["texte"][:90]} »\n      {m["pourquoi"]}')
        for c in red["chiffres"]:
            print(f'  # {c["qui"]} : {c["annonce"]} → {c["selon_source"]}')
        for c in red["contradictions"]:
            a, b = aff[c["ids"][0]], aff[c["ids"][1]]
            print(f'  ⇄ {c["sujet"]} : {a["qui"]} ({a["verdict"]}) / {b["qui"]} ({b["verdict"]})')
        for p in red["propositions"]:
            print(f'  → {p["qui"]} : {p["intitule"]}')


if __name__ == "__main__":
    main()

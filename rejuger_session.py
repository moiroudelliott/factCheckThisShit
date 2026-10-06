"""Revérifie les affirmations d'une session déjà générée, sans refaire la
transcription ni l'extraction.

Usage:
    python rejuger_session.py source-session_X.json --depuis 3656
    python rejuger_session.py source-session_X.json --ids b641ea7b,8f7bda45
    python rejuger_session.py source-session_X.json --tout --guests "Gabriel Attal (Premier ministre), Jordan Bardella"

Pour chaque affirmation retenue : nouvelles recherches (web, articles,
académique, données officielles) et nouveau verdict, avec les règles
actuelles et la mémoire du débat (verdicts déjà rendus). Le nouveau verdict
remplace l'ancien dans la bande, à la même position : la carte apparaît au
même moment. Utile quand une génération a manqué de recherche web (quota
épuisé en cours de route), ou pour appliquer une règle corrigée sans
régénérer tout le débat (une nouvelle extraction donnerait d'autres
affirmations).

Le passage de transcription autour de chaque propos n'est pas enregistré
dans la bande : la revérification ne l'a pas, contrairement au direct.

La fiche de fin de débat enregistrée garde sa rédaction (résumé, moments
forts), écrite avec les anciens verdicts : la publication en recalcule les
chiffres et en écarte ce que les nouveaux verdicts démentent, mais mieux vaut
la rédiger de nouveau (python fiche_session.py <sortie>).
"""

import argparse
import collections
import json
import os
import sys
import types

import eventlet

eventlet.monkey_patch(socket=True, select=True)

import requests  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_server():
    """La vérification seule : ni Whisper, ni socket.io."""
    app = types.ModuleType("server.app")
    app.DIARIZATION = True
    app.socketio = types.SimpleNamespace(emit=lambda *a, **k: None)
    sys.modules["server.app"] = app
    from server import factcheck, indicators, known_factchecks, votes
    known_factchecks.load()
    indicators.load()
    votes.load()
    return factcheck


def search_ready() -> str:
    """Vide si la recherche web répond ; sinon, la raison."""
    from server.config import BRAVE_API_KEY
    if not BRAVE_API_KEY:
        return "pas de clé BRAVE_API_KEY : la recherche se limiterait à Wikipédia"
    r = requests.get("https://api.search.brave.com/res/v1/web/search", params={"q": "Insee", "count": 1},
                     headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}, timeout=10)
    return "" if r.status_code == 200 else f"API Brave : HTTP {r.status_code} (quota épuisé ou clé refusée)"


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Revérifie les affirmations d'une session enregistrée.")
    ap.add_argument("session", help="Session générée (source-session_*.json)")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--depuis", type=float, help="Les affirmations à partir de cette position (secondes)")
    which.add_argument("--ids", default="", help="Identifiants d'affirmations, séparés par des virgules")
    which.add_argument("--tout", action="store_true", help="Toutes les affirmations")
    ap.add_argument("--date", default="", help="Date du débat AAAA-MM-JJ (défaut : celle enregistrée, sinon aucune)")
    ap.add_argument("--guests", default="", help="Intervenants, « Prénom Nom (fonction) » séparés par des virgules")
    ap.add_argument("--out", default="", help="Fichier de sortie (défaut : <session>_rejuge.json)")
    ap.add_argument("--sans-recherche", action="store_true", help="Continuer même si la recherche web ne répond pas")
    args = ap.parse_args()

    reason = search_ready()
    if reason and not args.sans_recherche:
        raise SystemExit(f"✗ {reason}. Revérifier sans recherche web donnerait surtout des « non vérifiable ».")

    fc = _load_server()
    from server.points import parse_guests
    from server.sources import related_verdicts

    session = json.load(open(args.session, encoding="utf-8"))
    names, roles = parse_guests(args.guests)
    context = {"emission": (session.get("video") or {}).get("title", ""), "guests": names, "roles": roles,
               "date": args.date or session.get("date", "")}
    results = {e["m"]["id"]: e for e in session["events"] if e["m"].get("type") == "fact_check_result"}
    claims = [(e["t"], p) for e in session["events"] if e["m"].get("type") == "talking_points"
              for p in e["m"].get("points", []) if p.get("type") == "affirmation" and p.get("id") in results]
    wanted = set(filter(None, args.ids.split(",")))
    todo = {p["id"] for t, p in claims
            if args.tout or (args.depuis is not None and (p.get("vt") or t) >= args.depuis) or p["id"] in wanted}
    print(f"{len(todo)} affirmation(s) à revérifier sur {len(claims)}")

    before, after, done = collections.Counter(), collections.Counter(), []
    for n, (t, p) in enumerate(claims, 1):
        event = results[p["id"]]
        if p["id"] in todo:
            before[event["m"].get("verdict")] += 1
            res = fc.call_mistral_factcheck(p["texte"], context=context, citation=p.get("citation", ""),
                                            query=str(p.get("recherche") or ""), qui=p.get("qui", ""),
                                            periode=str(p.get("periode") or ""),
                                            previous=related_verdicts(p["texte"], done, qui=p.get("qui", "")))
            event["m"] = {"type": "fact_check_result", "id": p["id"], **res}
            after[res["verdict"]] += 1
            print(f"  {n}/{len(claims)} {res['verdict']:<18} « {p['texte'][:70]} »", flush=True)
        done.append({"claim": p["texte"], "qui": p.get("qui", ""), **event["m"]})

    out = args.out or args.session.replace(".json", "_rejuge.json")
    json.dump(session, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"✓ {out}")
    print("  avant :", dict(before))
    print("  après :", dict(after))
    if any(e["m"].get("type") == "debate_summary" for e in session["events"]):
        print(f"  fiche du débat rédigée avec les anciens verdicts : python fiche_session.py {out}")


if __name__ == "__main__":
    main()

"""Banc d'essai des verdicts : rejoue l'étape de vérification sur des preuves
figées, et compte les erreurs par rapport à des verdicts de référence.

Usage:
    python bench_verdicts.py collect                 # réunit et fige les preuves (une fois)
    python bench_verdicts.py run --label essai       # rejoue les verdicts et les note
    python bench_verdicts.py run --sans-articles     # … sans les passages d'articles
    python bench_verdicts.py run --sans-contre-verification
    python bench_verdicts.py run --modele mistral-large-latest

Pourquoi : régénérer un débat change aussi la transcription, l'extraction et
les résultats de recherche ; deux essais ne se comparaient qu'à l'œil. Ici,
les affirmations (bench/verdicts_gold.json, 103 affirmations de deux débats
de la démonstration, avec les verdicts jugés acceptables) et leurs preuves
(data/bench/evidence.json, hors dépôt : extraits d'articles de presse) sont
les mêmes d'un essai à l'autre : seul le jugement change.

Notes :
- « faux à tort » : verdict « faux » là où la référence ne l'accepte pas — le
  plus grave, puisqu'il accuse ;
- « parole validée » : « vrai » ou « partiel » là où la seule source est
  l'orateur (référence « non recoupé ») ;
- « dérobade » : « non vérifiable » là où une source permettait de trancher.
Les appels Mistral sont réels (clé MISTRAL_API_KEY) : ~100 verdicts par essai.
"""

import argparse
import json
import os
import sys
import time
import types

import eventlet

eventlet.monkey_patch(socket=True, select=True)

ROOT = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(ROOT, "bench", "verdicts_gold.json")
BENCH_DIR = os.path.join(ROOT, "data", "bench")
EVIDENCE = os.path.join(BENCH_DIR, "evidence.json")


def _load_server(model: str = "", recheck: bool = True):
    """Importe la vérification sans le serveur : ni Whisper, ni socket.io."""
    if model:
        os.environ["MISTRAL_FACTCHECK_MODEL"] = model
    os.environ["FACTCHECK_RECHECK_FALSE"] = "1" if recheck else "0"
    app = types.ModuleType("server.app")
    app.DIARIZATION = True
    app.socketio = types.SimpleNamespace(emit=lambda *a, **k: None)
    sys.modules["server.app"] = app
    from server import factcheck
    return factcheck


def collect(args):
    fc = _load_server()
    from server import indicators, known_factchecks, votes
    known_factchecks.load()
    indicators.load()
    votes.load()
    gold = json.load(open(GOLD, encoding="utf-8"))
    os.makedirs(BENCH_DIR, exist_ok=True)
    done = json.load(open(EVIDENCE, encoding="utf-8")) if os.path.exists(EVIDENCE) and not args.refaire else {}
    todo = [c for c in gold["claims"] if c["id"] not in done]
    print(f"{len(todo)} affirmation(s) à documenter ({len(done)} déjà figées)")
    pool = eventlet.GreenPool(4)

    def one(c):
        ctx = gold["sessions"][c["video"]]["context"]
        return c["id"], fc.gather_evidence(c["texte"], ctx, c["recherche"], c["periode"])

    for n, (cid, ev) in enumerate(pool.imap(one, todo), 1):
        done[cid] = ev
        if n % 10 == 0 or n == len(todo):
            json.dump(done, open(EVIDENCE, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  {n}/{len(todo)}")
    read = sum(bool(r.get("extrait")) for ev in done.values() for r in ev["results"])
    print(f"✓ {EVIDENCE} — {len(done)} affirmations, {read} passage(s) d'articles")


def score(rows: list) -> dict:
    s = {"total": len(rows), "ok": 0, "faux_a_tort": 0, "parole_validee": 0, "derobade": 0}
    for r in rows:
        v, accept = r["verdict"], r["accept"]
        s["ok"] += v in accept
        s["faux_a_tort"] += v == "faux" and "faux" not in accept
        s["parole_validee"] += v in ("vrai", "partiellement_vrai") and accept == ["non_recoupe"]
        s["derobade"] += v == "non_verifiable" and "non_verifiable" not in accept
    return s


def run(args):
    fc = _load_server(args.modele, recheck=not args.sans_contre_verification)
    from server.sources import related_verdicts
    gold = json.load(open(GOLD, encoding="utf-8"))
    evidence = json.load(open(EVIDENCE, encoding="utf-8"))
    claims = [c for c in gold["claims"] if not args.video or c["video"] == args.video]

    def one_video(video):
        ctx = gold["sessions"][video]["context"]
        done, rows = [], []
        for c in (c for c in claims if c["video"] == video):
            ev = json.loads(json.dumps(evidence[c["id"]]))
            if args.sans_articles:
                for r in ev["results"]:
                    r.pop("extrait", None)
            started = time.monotonic()
            res = fc.judge(c["texte"], ev, ctx, citation=c["citation"], qui=c["qui"], periode=c["periode"],
                           previous=related_verdicts(c["texte"], done, qui=c["qui"]))
            done.append({"claim": c["texte"], "qui": c["qui"], **res})
            rows.append({"id": c["id"], "texte": c["texte"], "verdict": res["verdict"], "accept": c["accept"],
                         "explication": res.get("explication", ""), "source": res.get("source", ""),
                         "note": c["note"], "s": round(time.monotonic() - started, 1)})
        return rows

    started = time.monotonic()
    rows = [r for video_rows in eventlet.GreenPool(2).imap(one_video, gold["sessions"]) for r in video_rows
            if r["id"] in {c["id"] for c in claims}]
    s = score(rows)
    label = args.label or time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BENCH_DIR, f"run-{label}.json")
    json.dump({"label": label, "options": vars(args), "score": s, "rows": rows},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n══ {label} ══ {time.monotonic() - started:.0f} s, {sum(r['s'] for r in rows) / len(rows):.1f} s par verdict")
    print(f"acceptables : {s['ok']}/{s['total']} · faux à tort : {s['faux_a_tort']} · parole validée : "
          f"{s['parole_validee']} · dérobades : {s['derobade']}")
    for r in rows:
        if r["verdict"] not in r["accept"]:
            print(f"  ✗ {r['verdict']:<18} (attendu {'/'.join(r['accept'])}) « {r['texte'][:80]} »")
            print(f"      {r['explication'][:160]}")
    print(f"→ {path}")


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Banc d'essai des verdicts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="Réunir et figer les preuves")
    c.add_argument("--refaire", action="store_true", help="Tout refaire, même les preuves déjà figées")
    r = sub.add_parser("run", help="Rejouer les verdicts et les noter")
    r.add_argument("--label", default="")
    r.add_argument("--video", default="", help="Un seul débat (identifiant YouTube)")
    r.add_argument("--modele", default="", help="Modèle de vérification (défaut : réglage du serveur)")
    r.add_argument("--sans-articles", action="store_true", help="Ignorer les passages d'articles")
    r.add_argument("--sans-contre-verification", action="store_true", help="Pas de second regard sur les « faux »")
    args = ap.parse_args()
    collect(args) if args.cmd == "collect" else run(args)


if __name__ == "__main__":
    main()

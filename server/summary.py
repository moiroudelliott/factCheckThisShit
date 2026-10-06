"""Fiche de fin de débat : ce que chacun a affirmé, ce qui était exact, sur
quels thèmes — à lire sans revoir la vidéo (site/fiche.html) ou en fin de
direct (récap de l'extension).

Deux parties, séparées à dessein :
- les CHIFFRES (verdicts par débatteur, indice d'exactitude et sa marge,
  temps de parole, frise des thèmes, tableau débatteur × thème, sources)
  sont calculés ici par le code, à partir des verdicts : reproductibles, et
  chacun peut relire les affirmations qui les composent. Le modèle ne note
  personne — une note donnée par un modèle ne se vérifie pas ;
- la RÉDACTION (résumé, moments forts, chiffres annoncés face aux sources,
  contradictions, propositions) est demandée à Mistral en un seul appel, une
  fois le débat fini. Il ne désigne que des affirmations par leur
  identifiant, et le code vérifie chaque choix : un moment fort est une
  erreur présélectionnée (faux ou trompeur, enjeu et confiance élevés) ou un
  chiffre exact, au plus deux erreurs et un chiffre exact par débatteur ; un
  chiffre « annoncé » doit figurer dans le propos et le chiffre « selon la
  source » dans l'explication du verdict ; une contradiction oppose deux
  débatteurs différents.

Entrées communes au direct (inputs_from_live) et aux sessions enregistrées
(inputs_from_tape : fiche_session.py, publish_session.py) :
  points    [{id, type, texte, citation, qui, theme, t, enjeu}] — qui : nom
            résolu ou "" ; t : position en secondes (vidéo, ou depuis le
            début de l'analyse en direct) ;
  verdicts  {id: {verdict, confiance, explication, source, url, inaudible}} ;
  speech    [(début, durée, nom)] — temps de parole, nom "" si inconnu ;
  duration  secondes analysées.
"""

import json
import math
import re
import time
from bisect import bisect_right
from collections import Counter

from server.config import FICHE_MIN_VERDICTS, MISTRAL_FICHE_MODEL, MISTRAL_FICHE_TIMEOUT_S, MISTRAL_MODEL
from server.sources import is_inaudible
from server.text_utils import figures, key_words
from server.themes import THEMES, normalize_theme, themes_prompt_list

TRANCHES = ("vrai", "partiellement_vrai", "trompeur", "faux")
VERDICTS = TRANCHES + ("non_recoupe", "non_verifiable")
# Indice d'exactitude : moyenne de ces poids sur les verdicts tranchés
POIDS = {"vrai": 1.0, "partiellement_vrai": 0.5, "trompeur": 0.0, "faux": 0.0}
ERREURS = ("faux", "trompeur")

RAW_LABEL_RE = re.compile(r"^Intervenant ([A-Z]|\d+)$")
SPEECH_GAP_MAX_S = 5.0   # bande : écart max entre deux relevés « qui parle » compté comme parole continue
THEME_MIN_VERDICTS = 3   # verdicts tranchés d'un débatteur sur un thème pour prétendre à son ★
CONTEXTE_AVANT_S, CONTEXTE_APRES_S = 90, 60  # propos voisins du même débatteur, donnés avec un moment candidat
FRISE_MIN_S = 90        # chapitre de la frise plus court : fondu dans le précédent (incise, point isolé)
MOMENT_ERREURS = 6       # erreurs présélectionnées par débatteur pour les moments forts
MOMENT_EXACTS = 3        # chiffres exacts présélectionnés par débatteur
MAX_MOMENTS = {"erreur": 2, "exact": 1}  # retenus par débatteur
MAX_CHIFFRES = 8
ARRONDI = 0.05           # écart relatif sous lequel un chiffre annoncé n'est qu'un arrondi de celui de la source
MAX_CONTRADICTIONS = 4
MAX_PROPOSITIONS = 6     # par débatteur
THEMES_BATCH = 150       # points classés par appel (rattrapage des sessions sans thème)


# ── Entrées ─────────────────────────────────────────────────────────────────

def resolve_name(qui: str, label: str, smap: dict) -> str:
    """Nom du débatteur : correspondance finale des voix (label → nom), sinon
    le nom porté par le point ; "" pour un locuteur jamais identifié."""
    name = smap.get(label or "") or smap.get(qui or "") or qui or ""
    return "" if not name or name == "?" or RAW_LABEL_RE.match(name) else name


def _point(p: dict, name: str, t) -> dict:
    return {"id": str(p["id"]), "type": str(p.get("type") or ""), "texte": str(p.get("texte") or ""),
            "citation": str(p.get("citation") or ""), "qui": name,
            "theme": normalize_theme(p["theme"]) if p.get("theme") else "",
            "t": round(max(0.0, float(t)), 1) if isinstance(t, (int, float)) else None,
            "enjeu": p.get("enjeu") if isinstance(p.get("enjeu"), (int, float)) else None}


def _verdict(m: dict) -> dict:
    return {k: m.get(k) for k in ("verdict", "confiance", "explication", "source", "url", "inaudible")}


def inputs_from_tape(session: dict) -> dict:
    """Session enregistrée (format source-session) → entrées de la fiche,
    plus la rédaction, le titre et la date d'une fiche déjà enregistrée."""
    events = [e for e in session.get("events") or [] if isinstance(e, dict) and isinstance(e.get("m"), dict)]
    smap, points, seen, verdicts, ticks, segs = {}, [], set(), {}, [], []
    duration, previous = 0.0, None
    for e in events:
        if e["m"].get("type") == "speaker_map":
            smap.update(e["m"].get("map") or {})
    for e in events:
        m, t = e["m"], e.get("t")
        if isinstance(t, (int, float)):
            duration = max(duration, t)
        kind = m.get("type")
        if kind == "talking_points":
            for p in m.get("points") or []:
                if isinstance(p, dict) and p.get("id") and p["id"] not in seen:
                    seen.add(p["id"])
                    points.append(_point(p, resolve_name(p.get("qui", ""), p.get("qui_label", ""), smap), p.get("vt")))
        elif kind == "fact_check_result" and m.get("id") and not m.get("indisponible"):
            verdicts[m["id"]] = _verdict(m)
        elif kind in ("speaker_live", "transcript_segment") and m.get("speaker") and isinstance(t, (int, float)):
            (ticks if kind == "speaker_live" else segs).append((t, m["speaker"]))
        elif kind == "debate_summary" and isinstance(m.get("fiche"), dict):
            previous = m["fiche"]
    ticks = ticks or segs
    speech = [(t0, min(t1 - t0, SPEECH_GAP_MAX_S), resolve_name(label, label, smap))
              for (t0, label), (t1, _) in zip(ticks, ticks[1:]) if t1 > t0]
    previous = previous or {}
    return {"points": points, "verdicts": verdicts, "speech": speech, "duration": duration,
            "redaction": previous.get("redaction"), "titre": previous.get("titre") or "",
            "date": previous.get("date") or ""}


def inputs_from_live(points: list, verdicts: list, speech: list, smap: dict, started_at: float,
                     ended_at: float) -> dict:
    """État d'une session du backend → entrées de la fiche. Positions en
    secondes depuis le début de l'analyse : instant retrouvé par la citation,
    sinon début du texte analysé moins 8 s (même règle que l'extension)."""
    pts = []
    for p in points:
        said = p.get("said_at")
        if not isinstance(said, (int, float)) and isinstance(p.get("ts"), (int, float)):
            said = p["ts"] - 8
        pts.append(_point(p, resolve_name(p.get("qui", ""), p.get("qui_label", ""), smap),
                          said - started_at if isinstance(said, (int, float)) else None))
    return {"points": pts,
            "verdicts": {v["id"]: _verdict(v) for v in verdicts if isinstance(v, dict) and v.get("id")},
            "speech": [(s, d, resolve_name(label, label, smap)) for s, d, label in speech],
            "duration": max(0.0, ended_at - started_at)}


# ── Chiffres (code) ─────────────────────────────────────────────────────────

def indice(counts: dict) -> tuple:
    """(indice d'exactitude 0-1, marge d'erreur à 95 %, verdicts tranchés).
    Marge calculée avec deux observations fictives (un 0 et un 1) : sans
    elles, 15 « vrai » sur 15 afficheraient « 100 % ± 0 »."""
    xs = [POIDS[v] for v in TRANCHES for _ in range(counts.get(v, 0))]
    n = len(xs)
    if not n:
        return None, None, 0
    smoothed = xs + [0.0, 1.0]
    mean_s = sum(smoothed) / len(smoothed)
    var = sum((x - mean_s) ** 2 for x in smoothed) / (len(smoothed) - 1)
    return sum(xs) / n, 1.96 * math.sqrt(var / n), n


def _counts(affs: list) -> dict:
    c = {v: 0 for v in VERDICTS}
    for a in affs:
        c[a["verdict"]] += 1
    return c


def build_frise(points: list, duration: float) -> list:
    """Chapitres du débat : thème des points dans l'ordre, lissé (le thème
    majoritaire de chaque fenêtre de 5 points), puis chapitres de moins de
    FRISE_MIN_S fondus dans le précédent. Couvre le débat de 0 à la fin."""
    pts = sorted((p for p in points if p["t"] is not None and p["type"] != "question"), key=lambda p: p["t"])
    if not pts:
        return []
    raw = [p["theme"] or "autre" for p in pts]
    smooth = []
    for i, theme in enumerate(raw):
        window = [w for w in raw[max(0, i - 2): i + 3] if w != "autre"]
        counts = Counter(window)
        top = max(counts.values()) if counts else 0
        smooth.append(theme if not counts or counts.get(theme) == top
                      else next(w for w in window if counts[w] == top))
    runs = []
    for p, theme in zip(pts, smooth):
        if runs and runs[-1]["theme"] == theme:
            runs[-1]["points"] += 1
        else:
            runs.append({"theme": theme, "debut": p["t"], "points": 1})
    runs[0]["debut"] = 0.0
    end = max(duration, pts[-1]["t"])

    def length(i):
        return (runs[i + 1]["debut"] if i + 1 < len(runs) else end) - runs[i]["debut"]

    while len(runs) > 1:
        i = min(range(len(runs)), key=length)
        if length(i) >= FRISE_MIN_S:
            break
        if i == 0:
            runs[1]["debut"] = 0.0
            runs[1]["points"] += runs[0]["points"]
        else:
            runs[i - 1]["points"] += runs[i]["points"]
        del runs[i]
        merged = [runs[0]]
        for r in runs[1:]:
            if r["theme"] == merged[-1]["theme"]:
                merged[-1]["points"] += r["points"]
            else:
                merged.append(r)
        runs = merged
    return [{"theme": r["theme"], "debut": round(r["debut"], 1), "fin": round(runs[i + 1]["debut"] if i + 1 < len(runs) else end, 1),
             "points": r["points"]} for i, r in enumerate(runs)]


def build_fiche(points: list, verdicts: dict, speech: list, duration: float, titre: str = "", date: str = "",
                redaction: dict = None, **_) -> dict:
    """Fiche complète. La rédaction éventuelle (déjà écrite) est revérifiée
    contre les verdicts actuels — une session revérifiée depuis ne garde
    pas un « moment fort » devenu vrai."""
    times = [p["t"] for p in points if p["t"] is not None]
    duration = max([duration or 0.0] + times)
    affs = []
    for p in sorted(points, key=lambda p: (p["t"] is None, p["t"] or 0.0)):
        v = verdicts.get(p["id"])
        if p["type"] != "affirmation" or not v or v.get("verdict") not in VERDICTS:
            continue
        if v["verdict"] == "non_verifiable" and (v.get("inaudible") is True or is_inaudible(v.get("explication") or "")):
            continue  # propos mal transcrit : l'overlay retire sa carte, il ne compte nulle part
        affs.append({"id": p["id"], "qui": p["qui"], "texte": p["texte"], "citation": p["citation"],
                     "theme": p["theme"] or "autre", "t": p["t"], "verdict": v["verdict"],
                     "confiance": v.get("confiance"), "explication": str(v.get("explication") or ""),
                     "source": str(v.get("source") or ""), "url": str(v.get("url") or "")})

    talk = Counter()
    for _start, dur, name in speech:
        talk[name] += dur
    names = []
    for a in affs:
        if a["qui"] and a["qui"] not in names:
            names.append(a["qui"])
    names.sort(key=lambda n: -talk[n])  # par temps de parole, jamais par score

    debatteurs, raw_index = [], {}
    for name in names:
        counts = _counts([a for a in affs if a["qui"] == name])
        mean, marge, n = indice(counts)
        raw_index[name] = (mean, marge)
        enough = n >= FICHE_MIN_VERDICTS
        debatteurs.append({"nom": name, "temps_parole": round(talk[name]),
                           "affirmations": sum(counts.values()), "verdicts": counts, "tranches": n,
                           "suffisant": enough,
                           "exactitude": round(mean * 100) if enough else None,
                           "marge": round(marge * 100) if enough else None})
    comparaisons = []
    shown = [d["nom"] for d in debatteurs if d["suffisant"]]
    for i, a in enumerate(shown):
        for b in shown[i + 1:]:
            (ma, ea), (mb, eb) = raw_index[a], raw_index[b]
            # écart au-delà de la marge combinée (marges à 95 % → erreurs types)
            significatif = abs(ma - mb) > 1.96 * math.hypot(ea / 1.96, eb / 1.96)
            comparaisons.append({"a": a, "b": b, "ecart": round(abs(ma - mb) * 100), "significatif": significatif})

    frise = build_frise(points, duration)
    starts = [s["debut"] for s in frise]
    theme_time, theme_talk = Counter(), {}
    for s in frise:
        theme_time[s["theme"]] += s["fin"] - s["debut"]
    for start, dur, name in speech:
        if name and frise:
            theme = frise[max(0, bisect_right(starts, start + dur / 2) - 1)]["theme"]
            theme_talk.setdefault(theme, Counter())[name] += dur
    themes = []
    for tid, (label, _hint) in THEMES.items():
        pts = [p for p in points if (p["theme"] or "autre") == tid]
        mine_all = [a for a in affs if a["theme"] == tid]
        if not pts and not theme_time[tid]:
            continue
        par = {}
        for name in names:
            counts = _counts([a for a in mine_all if a["qui"] == name])
            par[name] = {"verdicts": counts, "tranches": sum(counts[v] for v in TRANCHES),
                         "temps_parole": round(theme_talk.get(tid, Counter())[name])}
        # ★ du thème : le meilleur indice d'exactitude (même calcul que par
        # débatteur), entre au moins deux débatteurs qui ont THEME_MIN_VERDICTS
        # verdicts tranchés ; à égalité, celui qui en a avancé le plus. Compter
        # les « vrai » seuls donnait l'étoile à 7 vrais sur 9 plutôt qu'à 6
        # sur 6 (remarque d'un lecteur : n'avoir dit que des choses vraies doit
        # l'emporter)
        scored = []
        for name in names:
            mean, _marge, n = indice(par[name]["verdicts"])
            par[name]["exactitude"] = round(mean * 100) if mean is not None else None
            if n >= THEME_MIN_VERDICTS:
                scored.append((mean, n, name))
        scored.sort(reverse=True)
        plus = scored[0][2] if len(scored) >= 2 and scored[0][:2] != scored[1][:2] else None
        themes.append({"id": tid, "label": label, "duree": round(theme_time[tid]), "points": len(pts),
                       "affirmations": len(mine_all), "par_debatteur": par, "plus_exact": plus})
    themes.sort(key=lambda t: (t["id"] == "autre", -t["duree"], -t["points"]))

    src = Counter(a["source"].strip() for a in affs if a["verdict"] in TRANCHES and a["url"] and a["source"].strip())
    fiche = {
        "version": 1, "titre": titre, "date": date, "duree": round(duration), "genere": time.strftime("%Y-%m-%d"),
        "min_verdicts": FICHE_MIN_VERDICTS,
        "totaux": {"points": len(points), "affirmations": len(affs), "verdicts": _counts(affs)},
        "debatteurs": debatteurs, "comparaisons": comparaisons,
        "non_identifies": sum(1 for a in affs if not a["qui"]),
        "themes": themes, "frise": frise,
        "sources": [{"nom": k, "n": v} for k, v in src.most_common()],
        "affirmations": affs, "redaction": None,
    }
    if redaction:
        fiche["redaction"] = validate_redaction(redaction, affs, points)
    return fiche


def compact(fiche: dict) -> dict:
    """La fiche sans la liste des affirmations — ce qui voyage vers
    l'extension, qui a déjà les points (et rejoint la bande enregistrée)."""
    return {k: v for k, v in fiche.items() if k != "affirmations"}


# ── Rédaction (Mistral) ─────────────────────────────────────────────────────

FICHE_PROMPT = """Tu rédiges la fiche de synthèse d'un débat politique français à partir des vérifications faites pendant le débat. Les chiffres de la fiche (verdicts par débatteur, temps de parole) sont calculés par ailleurs : tu ne notes personne, et tu ne dis jamais qui a « gagné » ni qui a été le plus exact.
Débat : {titre}{date}
Débatteurs : {noms}
Thèmes, du plus long au plus court : {themes}

MOMENTS CANDIDATS (identifiant, débatteur, verdict, position, propos — explication du verdict ; puis ses mots exacts et ce qu'il disait juste avant et après) :
{candidats}

AFFIRMATIONS VÉRIFIÉES :
{affirmations}

AUTRES PROPOS DES DÉBATTEURS (identifiant, débatteur, propos) :
{autres}

Réponds UNIQUEMENT avec un objet JSON, sans markdown :
{{"resume": "…", "moments": [{{"id": "…", "pourquoi": "…"}}], "chiffres": [{{"id": "…", "annonce": "…", "selon_source": "…"}}], "contradictions": [{{"ids": ["…", "…"], "sujet": "…"}}], "propositions": [{{"id": "…", "intitule": "…"}}]}}

- "resume" : 3 ou 4 phrases neutres : de quoi le débat a parlé, sur quoi les débatteurs se sont opposés, quels faits ont été disputés. Aucun jugement sur les personnes, aucun adjectif de valeur, rien qui ne soit dans les listes.
- "moments" : choisis UNIQUEMENT parmi les MOMENTS CANDIDATS, pour chaque débatteur au plus 2 erreurs (faux ou trompeur) et 1 chiffre exact (vrai) : ceux qui comptent le plus pour le public — le cœur du débat (budget, bilan, statistique nationale, accusation contre l'adversaire), pas un détail. Le chiffre exact est un chiffre que le débatteur avance à l'appui de SON argument (pas la description du programme adverse). "pourquoi" = une phrase (30 mots au plus) qui situe le moment : à quoi le débatteur se servait de ce chiffre ou de ce fait (l'argument qu'il défendait, d'après ses mots exacts et le contexte), et ce que le verdict y change. Ex : « Bardella s'en sert pour dénoncer la hausse du coût de la vie : la hausse annoncée est bien celle décidée par la CRE. » L'explication du verdict est affichée juste à côté : n'en recopie pas les chiffres. Pas de formule toute faite (« invalide l'argument », « fausse le débat », « modifie la perception »).
- "chiffres" : affirmations faux, trompeur ou partiellement_vrai dont le chiffre annoncé diffère de celui des sources. "annonce" = le chiffre tel que dit, recopié du propos avec son unité (« +47 % ») ; "selon_source" = le chiffre recopié de l'explication, avec sa source et son année si elles y sont (« +38 % entre 2017 et 2024, selon l'Insee »). Au plus 8, les plus parlants ; pas un simple arrondi (30 pour 30,5).
- "contradictions" : deux affirmations de débatteurs DIFFÉRENTS sur le même fait, dont l'une affirme ce que l'autre nie (« des postes ont été créés » / « des postes ont été supprimés »), et que les verdicts départagent : l'une jugée vrai ou partiellement_vrai, l'autre faux ou trompeur. Deux erreurs dans le même sens ne sont pas une contradiction. Au plus 4. "sujet" = le fait disputé, en quelques mots.
- "propositions" : les mesures concrètes qu'un débatteur propose LUI-MÊME de prendre, pour son camp (pas un constat, pas une mesure déjà prise, pas le programme de l'adversaire qu'il décrit ou critique, pas ce que fait quelqu'un d'autre), prises dans les affirmations ou les autres propos, seulement quand le débatteur la présente comme la sienne (« je propose », « nous ferons », « il faut »). "intitule" = la mesure en moins de 12 mots, à l'infinitif (« Baisser la TVA sur l'énergie à 5,5 % »). Au plus 6 par débatteur.
- N'utilise que les identifiants fournis entre crochets."""

THEMES_PROMPT = """Classe chaque propos d'un débat politique français dans UN thème, parmi ces identifiants :
{themes}

Propos (identifiant entre crochets) :
{lignes}

Réponds UNIQUEMENT avec un objet JSON {{"identifiant du propos": "identifiant du thème", …}}, sans markdown."""


def _call(prompt: str, **kwargs) -> str:
    from server.factcheck import call_mistral_api  # import tardif : sans modèle ni socket pour les calculs seuls
    return call_mistral_api(prompt, **kwargs)


def _json(content: str) -> dict:
    start = str(content or "").find("{")
    if start == -1:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(content, start)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _items(value) -> list:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def fmt_t(t) -> str:
    if t is None:
        return "?"
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


# « Le programme du NFP propose un SMIC à 1 600 € » : exact, mais la
# description d'un programme n'est pas un chiffre marquant (cas vécu : retenu
# comme « chiffre exact » de l'adversaire qui le décrivait)
_PROGRAMME_RE = re.compile(r"\b(?:propos\w*|programme\w*|prom\w*)\b", re.IGNORECASE)


_OWN_MEASURE_RE = re.compile(r"\b(?:je|moi|nous|notre|nos|on|il faut)\b|\bj['’]", re.IGNORECASE)


def moment_candidates(affs: list, points: list, names: list) -> list:
    """Par débatteur : ses erreurs les plus importantes (enjeu, puis
    confiance) et ses chiffres exacts les plus importants."""
    enjeu = {p["id"]: p["enjeu"] for p in points if p["enjeu"] is not None}

    def key(a):
        return enjeu.get(a["id"], 7), a.get("confiance") or 0

    out = []
    for name in names:
        mine = [a for a in affs if a["qui"] == name]
        out += sorted((a for a in mine if a["verdict"] in ERREURS), key=key, reverse=True)[:MOMENT_ERREURS]
        out += sorted((a for a in mine if a["verdict"] == "vrai" and figures(a["texte"])
                       and not _PROGRAMME_RE.search(a["texte"])), key=key, reverse=True)[:MOMENT_EXACTS]
    return out


def _rounding(said: frozenset, found: frozenset) -> bool:
    """Le chiffre annoncé n'est qu'un arrondi de celui de la source (moins de
    ARRONDI d'écart : « 350 000 » pour 347 000, « 30 » pour 30,5) — rien à
    montrer comme une erreur de chiffre."""
    gaps = []
    for x in said:
        for y in found:
            try:
                fx, fy = float(x), float(y)
            except ValueError:
                continue
            if max(abs(fx), abs(fy)) > 0:
                gaps.append(abs(fx - fy) / max(abs(fx), abs(fy)))
    return bool(gaps) and min(gaps) < ARRONDI


def _candidate_line(a: dict, points: list) -> str:
    """Un moment candidat, avec ses mots exacts et les propos voisins du même
    débatteur : sans eux, le « pourquoi » ne pouvait que paraphraser le
    verdict (« invalide l'argument sur… »), sans dire à quoi servait le
    chiffre dans le débat."""
    line = _line(a)
    if a.get("citation"):
        line += f'\n    mots exacts : « {a["citation"]} »'
    if a["t"] is not None:
        near = [p["texte"] for p in points if p["qui"] == a["qui"] and p["id"] != a["id"] and p["t"] is not None
                and p["type"] != "question" and -CONTEXTE_AVANT_S <= p["t"] - a["t"] <= CONTEXTE_APRES_S]
        if near:
            line += "\n    contexte : " + " / ".join(f"« {t} »" for t in near[-3:])
    return line


def _line(a: dict, explain: bool = True) -> str:
    line = f'[{a["id"]}] {a["qui"] or "?"} · {a["verdict"]} · {fmt_t(a["t"])} · « {a["texte"]} »'
    return line + (f' — {a["explication"]}' if explain and a.get("explication") else "")


def validate_redaction(data: dict, affs: list, points: list, candidates: set = None) -> dict:
    """Garde de la rédaction : seulement des identifiants connus, et chaque
    choix vérifié (voir l'en-tête du module)."""
    data = data if isinstance(data, dict) else {}
    by_id = {a["id"]: a for a in affs}
    pts = {p["id"]: p for p in points}
    out = {"modele": _clean(data.get("modele"), 60), "resume": _clean(data.get("resume"), 900),
           "moments": [], "chiffres": [], "contradictions": [], "propositions": []}

    caps = Counter()
    for m in _items(data.get("moments")):
        a = by_id.get(str(m.get("id")))
        if not a or not a["qui"] or (candidates is not None and a["id"] not in candidates):
            continue
        kind = "erreur" if a["verdict"] in ERREURS else "exact" if a["verdict"] == "vrai" else None
        # Rédaction relue après une revérification : une « erreur » devenue
        # vraie (ou l'inverse) perd son moment — le « pourquoi » ne vaut plus
        if kind == "exact" and _PROGRAMME_RE.search(a["texte"]):
            continue
        if not kind or m.get("genre") not in (None, kind) or caps[(a["qui"], kind)] >= MAX_MOMENTS[kind] \
                or any(x["id"] == a["id"] for x in out["moments"]):
            continue
        caps[(a["qui"], kind)] += 1
        out["moments"].append({"id": a["id"], "qui": a["qui"], "genre": kind, "pourquoi": _clean(m.get("pourquoi"), 300)})
    out["moments"].sort(key=lambda m: by_id[m["id"]]["t"] or 0)

    for c in _items(data.get("chiffres")):
        a = by_id.get(str(c.get("id")))
        if not a or a["verdict"] not in ("faux", "trompeur", "partiellement_vrai") \
                or any(x["id"] == a["id"] for x in out["chiffres"]) or len(out["chiffres"]) >= MAX_CHIFFRES:
            continue
        annonce, source = _clean(c.get("annonce"), 120), _clean(c.get("selon_source"), 200)
        said, found = figures(annonce), figures(source)
        # Le chiffre annoncé est dans le propos, celui de la source dans
        # l'explication du verdict : rien d'inventé ni de déformé
        if not said or not found or said == found or not said <= figures(f'{a["texte"]} {a["citation"]}') \
                or not found <= figures(a["explication"]) or _rounding(said, found):
            continue
        out["chiffres"].append({"id": a["id"], "qui": a["qui"], "annonce": annonce, "selon_source": source})

    for c in _items(data.get("contradictions")):
        ids = c.get("ids")
        if not isinstance(ids, list) or len(ids) != 2 or len(out["contradictions"]) >= MAX_CONTRADICTIONS:
            continue
        a, b = by_id.get(str(ids[0])), by_id.get(str(ids[1]))
        if not a or not b or not a["qui"] or not b["qui"] or a["qui"] == b["qui"]:
            continue
        # Les verdicts départagent : l'un juste (ou presque), l'autre faux
        # ou trompeur — sinon ce n'est pas une contradiction que la fiche
        # puisse trancher (deux erreurs dans le même sens, deux « partiel »)
        right = {"vrai", "partiellement_vrai"}
        if not ((a["verdict"] in right and b["verdict"] in ERREURS) or (b["verdict"] in right and a["verdict"] in ERREURS)):
            continue
        # Même fait : au moins un mot-clé commun (cas vécu : une hausse de
        # l'électricité « contredite » par une taxe sur le gaz)
        if not key_words(a["texte"]) & key_words(b["texte"]):
            continue
        out["contradictions"].append({"ids": [a["id"], b["id"]], "sujet": _clean(c.get("sujet"), 120)})

    per = Counter()
    for c in _items(data.get("propositions")):
        p = pts.get(str(c.get("id")))
        intitule = _clean(c.get("intitule"), 120)
        if not p or not p["qui"] or not intitule or per[p["qui"]] >= MAX_PROPOSITIONS \
                or any(x["id"] == p["id"] for x in out["propositions"]):
            continue
        # Une affirmation jugée fausse n'est pas une proposition de son auteur :
        # le plus souvent, la description erronée du programme adverse (cas
        # vécu : « le programme du RN propose une CSG progressive », dit par
        # Gabriel Attal, devenu sa propre proposition)
        if by_id.get(p["id"], {}).get("verdict") == "faux":
            continue
        # La mesure est la sienne : ses mots exacts la portent à la première
        # personne (« j'entends baisser la TVA », « on propose, nous… ») ou
        # par « il faut ». Écartés : « vous avez proposé la CSG progressive »
        # (le programme adverse), « il a retiré 10 milliards » (un constat)
        if not _OWN_MEASURE_RE.search(p["citation"]):
            continue
        per[p["qui"]] += 1
        out["propositions"].append({"id": p["id"], "qui": p["qui"], "intitule": intitule, "t": p["t"]})
    return out


def write_redaction(fiche: dict, points: list, call=None, model: str = None) -> dict:
    """Un appel Mistral pour toute la rédaction ; None s'il n'y a rien à
    rédiger. Les erreurs réseau remontent à l'appelant."""
    affs = fiche["affirmations"]
    names = [d["nom"] for d in fiche["debatteurs"]]
    if not affs or not names:
        return None
    model = model or MISTRAL_FICHE_MODEL
    candidates = moment_candidates(affs, points, names)
    aff_ids = {a["id"] for a in affs}
    others = [p for p in points if p["qui"] and p["id"] not in aff_ids
              and p["type"] in ("argument", "subjectif", "secondaire", "affirmation")][:250]
    themes = [f'{t["label"]} ({t["duree"] // 60} min)' for t in fiche["themes"] if t["duree"] >= 60]
    prompt = FICHE_PROMPT.format(
        titre=fiche.get("titre") or "(sans titre)",
        date=f' — {fiche["date"]}' if fiche.get("date") else "",
        noms=", ".join(names), themes=", ".join(themes) or "(non classés)",
        candidats="\n".join(_candidate_line(a, points) for a in candidates) or "(aucun)",
        affirmations="\n".join(_line(a) for a in affs if a["qui"]),
        autres="\n".join(f'[{p["id"]}] {p["qui"]} · « {p["texte"]} »' for p in others) or "(aucun)",
    )
    content = (call or _call)(prompt, model=model, timeout=MISTRAL_FICHE_TIMEOUT_S)
    data = _json(content)
    data["modele"] = model
    return validate_redaction(data, affs, points, candidates={a["id"] for a in candidates})


def ensure_themes(points: list, call=None, model: str = None) -> int:
    """Classe dans un thème les points qui n'en ont pas (sessions
    enregistrées avant le champ « theme »). Renvoie le nombre classé."""
    todo = [p for p in points if not p.get("theme")]
    for i in range(0, len(todo), THEMES_BATCH):
        chunk = todo[i:i + THEMES_BATCH]
        prompt = THEMES_PROMPT.format(themes=themes_prompt_list(),
                                      lignes="\n".join(f'[{p["id"]}] {p["texte"]}' for p in chunk))
        data = _json((call or _call)(prompt, model=model or MISTRAL_MODEL, timeout=MISTRAL_FICHE_TIMEOUT_S))
        for p in chunk:
            p["theme"] = normalize_theme(data.get(p["id"]))
    return len(todo)

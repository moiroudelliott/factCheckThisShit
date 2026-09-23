"""Votes à l'Assemblée nationale (open data officiel, data.assemblee-nationale.fr)
pour vérifier les affirmations du type « X a voté contre … » ou « le RN a
voté pour … ».

Sans ça, un vote se vérifiait par la presse — ou pas du tout. L'open data
donne pour chaque scrutin public le vote de chaque député et la position
de chaque groupe : quand une affirmation parle d'un vote et nomme un député
ou un groupe, les scrutins correspondants sont ajoutés aux preuves.

Couverture : 16e et 17e législatures (depuis juin 2022), députés en
fonction (leurs votes des deux législatures), groupes de la 17e. Les
eurodéputés (ex. Jordan Bardella) et les sénateurs ne sont pas couverts.

Données : ~40 Mo téléchargés au premier démarrage dans AN_DATA_DIR
(ignoré par git), rafraîchis chaque semaine ; l'index compact (un octet
par député et par scrutin) est gardé sur disque pour les démarrages
suivants. parse_* / search sont pures (testables sans réseau)."""

import json
import os
import pickle
import re
import time
import zipfile

import requests

from server.config import AN_DATA_DIR, AN_LEGISLATURES, AN_REFRESH_S, AN_VOTES_ENABLED
from server.names import norm_name
from server.text_utils import key_words

SCRUTINS_URL = "https://data.assemblee-nationale.fr/static/openData/repository/{leg}/loi/scrutins/Scrutins.json.zip"
DEPUTES_URL = ("https://data.assemblee-nationale.fr/static/openData/repository/17/amo/"
               "deputes_actifs_mandats_actifs_organes/AMO10_deputes_actifs_mandats_actifs_organes.json.zip")
SCRUTIN_PAGE = "https://www.assemblee-nationale.fr/dyn/{leg}/scrutins/{numero}"
INDEX_VERSION = 3

# Une affirmation parle-t-elle d'un vote ?
VOTE_RE = re.compile(r"\bvot(?:é|ée|és|ées|e|er|ait|aient|ent|ant|ons|ez)\b|\babstenu|\bcensur", re.IGNORECASE)
# Vocabulaire du vote, sans valeur pour retrouver le SUJET du scrutin
_VOTE_WORDS = {"voté", "votée", "votés", "votées", "vote", "votes", "voter", "votait", "votaient", "votent",
               "votant", "contre", "abstenu", "abstenue", "abstenus", "abstention", "assemblée", "nationale",
               "député", "députée", "députés", "groupe", "groupes", "parti", "texte", "hémicycle"}
# Mots d'une affirmation qui ne figurent pas tels quels dans les titres de scrutins
_SYNONYMS = {"budget": {"financ"}, "budgets": {"financ"}, "smic": {"salaire", "minimum"},
             "euthanasie": {"mourir"}, "censurer": {"censure"}, "censuré": {"censure"}}
_MONTHS = {m: i for i, m in enumerate(["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                                       "septembre", "octobre", "novembre", "décembre"], start=1)}


def _stem(word: str) -> str:
    """Racine grossière pour comparer affirmation et titre : pluriel et
    finales courantes retirés (« retraites » = « retraite », « abroger » =
    « abrogation », « finances » = « financement »)."""
    w = word.lower()
    for suffix in ("ations", "ation", "ements", "ement", "ances", "ance", "er", "es", "e", "s", "x"):
        if len(w) - len(suffix) >= 5 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


# Position codée sur un octet par député et par scrutin
_CODES = {"pours": 1, "contres": 2, "abstentions": 3, "nonVotants": 4}
_LABELS = {0: "n'a pas pris part au vote", 1: "a voté pour", 2: "a voté contre", 3: "s'est abstenu(e)",
           4: "non-votant(e)"}

# Groupes de la 17e législature (libelleAbrev) → expressions courantes
GROUP_ALIASES = {
    "RN": r"rassemblement national|\brn\b|lepénistes?",
    "LFI-NFP": r"france insoumise|\blfi\b|insoumis",
    "SOC": r"socialistes?|\bps\b",
    "ECOS": r"écologistes?|\beelv\b|les verts",
    "DR": r"droite républicaine|les républicains|\blr\b",
    "EPR": r"ensemble pour la république|\brenaissance\b|macronistes?|majorité présidentielle",
    "DEM": r"\bmodem\b|les démocrates",
    "HOR": r"\bhorizons\b",
    "GDR": r"communistes?|\bpcf\b|\bgdr\b",
    "LIOT": r"\bliot\b",
    "UDDPLR": r"\budr\b|union des droites|ciottistes?",
}

_state = {"ready": False, "deputies": [], "groups": {}, "scrutins": []}


# ── Parsing (pur) ──────────────────────────────────────────────────────────

def parse_deputies(zip_path: str):
    """AMO10 → (députés [{ref, name, norm, nom_norm, group}], groupes {organeRef: {abrev, libelle}})."""
    deputies, groups = [], {}
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if "/acteur/" in n:
                a = json.loads(z.read(n))["acteur"]
                ident = a["etatCivil"]["ident"]
                uid = a["uid"]["#text"] if isinstance(a["uid"], dict) else a["uid"]
                name = f"{ident['prenom']} {ident['nom']}"
                mandats = (a.get("mandats") or {}).get("mandat") or []
                current_group = next((m["organes"]["organeRef"] for m in (mandats if isinstance(mandats, list) else [mandats])
                                      if m.get("typeOrgane") == "GP" and not m.get("dateFin")), None)
                deputies.append({"ref": uid, "name": name, "norm": norm_name(name),
                                 "nom_norm": norm_name(ident["nom"]), "group": current_group})
            elif "/organe/" in n:
                o = json.loads(z.read(n))["organe"]
                if o.get("codeType") == "GP":
                    groups[o["uid"]] = {"abrev": o.get("libelleAbrev", ""), "libelle": o.get("libelle", "")}
    return deputies, groups


def _votants(block) -> list:
    if not block:
        return []
    v = block.get("votant") if isinstance(block, dict) else None
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse_scrutin(s: dict, leg: int, dep_index: dict, n_deputies: int, members: dict = None) -> dict:
    """Scrutin brut (JSON de l'open data) → enregistrement compact. members
    (optionnel) accumule {organeRef: {député: nb de votes}} pour déduire les
    groupes des législatures passées (voir build_index)."""
    titre = str(s.get("titre") or (s.get("objet") or {}).get("libelle") or "")
    synth = (s.get("syntheseVote") or {}).get("decompte") or {}
    positions = bytearray(n_deputies)
    groups = {}
    grp_list = (((s.get("ventilationVotes") or {}).get("organe") or {}).get("groupes") or {}).get("groupe") or []
    for g in grp_list if isinstance(grp_list, list) else [grp_list]:
        vote = g.get("vote") or {}
        dv = vote.get("decompteVoix") or {}
        groups[g.get("organeRef")] = (vote.get("positionMajoritaire", ""), dv.get("pour", "0"),
                                      dv.get("contre", "0"), dv.get("abstentions", "0"))
        for key, code in _CODES.items():
            for v in _votants((vote.get("decompteNominatif") or {}).get(key)):
                i = dep_index.get(v.get("acteurRef"))
                if i is not None:
                    positions[i] = code
                    if members is not None:
                        counts = members.setdefault(g.get("organeRef"), {})
                        counts[i] = counts.get(i, 0) + 1
    words = {_stem(w) for w in key_words(titre)} | set(re.findall(r"\b(?:19|20)\d\d\b", titre))
    return {"leg": leg, "numero": str(s.get("numero", "")), "date": str(s.get("dateScrutin", "")),
            "titre": titre, "sort": (s.get("sort") or {}).get("code", ""),
            "pour": synth.get("pour", "0"), "contre": synth.get("contre", "0"), "abst": synth.get("abstentions", "0"),
            "final": bool(re.match(r"l['’]ensemble|la motion de censure", titre)),
            "words": frozenset(words), "positions": bytes(positions), "groups": groups}


def infer_past_groups(members: dict, deputies: list, groups: dict) -> dict:
    """Les groupes d'une législature passée ont d'autres identifiants que ceux
    d'aujourd'hui (l'open data des députés en fonction ne les décrit pas) :
    un ancien groupe prend le nom du groupe ACTUEL de la majorité (≥ 60 %)
    de ses membres encore députés (RN → RN, LR → DR, Renaissance → EPR…)."""
    inferred = {}
    for oref, counts in members.items():
        if oref in groups:
            continue
        tally = {}
        for i in counts:
            g = deputies[i].get("group")
            if g in groups:
                tally[g] = tally.get(g, 0) + 1
        if not tally:
            continue
        best, n = max(tally.items(), key=lambda kv: kv[1])
        if n >= 5 and n >= 0.6 * sum(tally.values()):
            inferred[oref] = {"abrev": groups[best]["abrev"], "libelle": f"{groups[best]['libelle']} (législature passée)"}
    return inferred


def build_index(deputies_zip: str, scrutins_zips: dict) -> dict:
    deputies, groups = parse_deputies(deputies_zip)
    dep_index = {d["ref"]: i for i, d in enumerate(deputies)}
    scrutins, members = [], {}
    for leg, path in scrutins_zips.items():
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith(".json"):
                    scrutins.append(parse_scrutin(json.loads(z.read(n))["scrutin"], leg, dep_index,
                                                  len(deputies), members))
    groups.update(infer_past_groups(members, deputies, groups))
    return {"version": INDEX_VERSION, "deputies": deputies, "groups": groups, "scrutins": scrutins}


# ── Recherche (pure) ───────────────────────────────────────────────────────

def _group_patterns(groups: dict) -> dict:
    out = {}
    for oref, g in groups.items():
        alias = GROUP_ALIASES.get(g["abrev"].upper())
        if alias:
            out[oref] = re.compile(alias, re.IGNORECASE)
    return out


def search(claim: str, state: dict = None, limit: int = 2) -> list:
    """Preuves [{title, body, href}] : scrutins dont le titre porte sur le
    sujet de l'affirmation, avec le vote des députés / groupes qu'elle nomme."""
    state = _state if state is None else state
    if not state.get("ready") or not VOTE_RE.search(claim or ""):
        return []
    nclaim = f" {norm_name(claim)} "
    people = [i for i, d in enumerate(state["deputies"]) if f" {d['norm']} " in nclaim]
    if not people:  # nom de famille seul, s'il ne désigne qu'un député
        by_nom = {}
        for i, d in enumerate(state["deputies"]):
            by_nom.setdefault(d["nom_norm"], []).append(i)
        people = [ids[0] for nom, ids in by_nom.items()
                  if len(ids) == 1 and len(nom) >= 4 and f" {nom} " in nclaim]
    patterns = state.get("_patterns") or _group_patterns(state["groups"])
    state["_patterns"] = patterns
    grp = [oref for oref, rx in patterns.items() if rx.search(claim)]
    if not people and not grp:
        return []

    name_words = {w for i in people for w in state["deputies"][i]["norm"].split()}
    words = [w for w in key_words(claim) if w not in _VOTE_WORDS and norm_name(w) not in name_words]
    topic = {_stem(w) for w in words}
    for w in words:
        topic |= _SYNONYMS.get(w, set())
    # La date aide à départager (« la loi immigration de décembre 2023 »)
    years = set(re.findall(r"\b(?:19|20)\d\d\b", claim))
    months = {n for m, n in _MONTHS.items() if re.search(rf"\b{m}\b", claim, re.IGNORECASE)}
    topic |= years  # « budget 2025 » ↔ « loi de finances pour 2025 »
    scored = []
    for s in state["scrutins"]:
        overlap = len(topic & s["words"])
        if not overlap:
            continue
        # « a voté contre la loi immigration » vise presque toujours le vote sur
        # l'ensemble du texte (ou une motion de censure) : bonus pour ceux-là
        score = (overlap + (s["date"][:4] in years) + (bool(months) and int(s["date"][5:7] or 0) in months)
                 + s["final"])
        if score >= 2:
            scored.append((score, s["final"], s["date"], s))
    scored.sort(key=lambda x: x[:3], reverse=True)

    out = []
    for *_, s in scored[:limit]:
        facts = [f"{state['deputies'][i]['name']} {_LABELS[s['positions'][i]]}" for i in people]
        for oref in grp:
            if oref in s["groups"]:
                pos, p, c, a = s["groups"][oref]
                facts.append(f"groupe {state['groups'][oref]['abrev']} : majoritairement {pos} "
                             f"({p} pour, {c} contre, {a} abstentions)")
        if not facts:
            continue
        date = "/".join(reversed(s["date"].split("-")))
        out.append({
            "title": f"Assemblée nationale — scrutin n°{s['numero']} du {date} : {s['titre'][:220]}",
            "body": f"{' ; '.join(facts)}. Résultat : {s['sort']} ({s['pour']} pour, {s['contre']} contre, "
                    f"{s['abst']} abstentions).",
            "href": SCRUTIN_PAGE.format(leg=s["leg"], numero=s["numero"]),
        })
    return out


# ── Téléchargement + chargement ────────────────────────────────────────────

def _download(url: str, path: str) -> bool:
    """Télécharge si absent ou plus vieux que AN_REFRESH_S. Vrai si le fichier a changé."""
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < AN_REFRESH_S:
        return False
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(r.content)
    os.replace(tmp, path)
    return True


def load(tpool_execute=None):
    """Télécharge (si besoin) puis charge l'index. Le parsing (~10 000
    fichiers) est confié à tpool_execute (thread natif) : jamais sur la
    boucle eventlet."""
    os.makedirs(AN_DATA_DIR, exist_ok=True)
    dep_zip = os.path.join(AN_DATA_DIR, "deputes.zip")
    scr_zips = {leg: os.path.join(AN_DATA_DIR, f"scrutins{leg}.zip") for leg in AN_LEGISLATURES}
    changed = _download(DEPUTES_URL, dep_zip)
    for leg, path in scr_zips.items():
        changed |= _download(SCRUTINS_URL.format(leg=leg), path)
    index_path = os.path.join(AN_DATA_DIR, "index.pkl")
    index = None
    if not changed and os.path.exists(index_path):
        try:
            with open(index_path, "rb") as f:
                index = pickle.load(f)
            if index.get("version") != INDEX_VERSION:
                index = None
        except Exception:
            index = None
    if index is None:
        run = tpool_execute or (lambda fn, *a: fn(*a))
        index = run(build_index, dep_zip, scr_zips)
        with open(index_path, "wb") as f:
            pickle.dump(index, f)
    _state.update(index, ready=True, _patterns=None)
    print(f"[Votes AN] {len(index['scrutins'])} scrutin(s), {len(index['deputies'])} député(s) en fonction")


def start(socketio, tpool_execute):
    if not AN_VOTES_ENABLED:
        return

    def loop():
        while True:
            try:
                load(tpool_execute)
            except Exception as e:
                print(f"[Votes AN] indisponible : {type(e).__name__}: {e}")
            socketio.sleep(AN_REFRESH_S)

    socketio.start_background_task(loop)

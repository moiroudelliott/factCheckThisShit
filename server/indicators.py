"""Données officielles structurées : séries Eurostat (API publique, sans
clé) pour les chiffres qui reviennent le plus dans les débats.

Quand une affirmation parle de chômage, de dette, d'inflation…, la série
officielle France + UE-27 est ajoutée aux preuves du fact-check : le modèle
compare le chiffre avancé à la valeur exacte, année par année, au lieu de
se fier à un article de presse ou à sa mémoire.

match / format_series / decode_jsonstat sont pures (testables sans réseau).
Les séries sont téléchargées en tâche de fond (start → refresh : au
démarrage puis toutes les CACHE_S) et gardées sur disque
(EUROSTAT_CACHE_FILE) : evidence() ne lit que ce cache et ne fait jamais
attendre un fact-check. Cas vécu : appelée pendant la vérification,
l'API ajoutait 16 à 33 s à chaque affirmation sur le chômage ou la dette."""

import json
import os
import re
import threading
import time

import requests

from server.config import EUROSTAT_CACHE_FILE

API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
DATABROWSER = "https://ec.europa.eu/eurostat/databrowser/view/{}/default/table?lang=fr"
SINCE = "2015"
CACHE_S = 24 * 3600
MAX_PER_CLAIM = 3

# key, libellé, jeu de données, filtres, unité affichée, diviseur, déclencheurs (regex sur l'affirmation)
INDICATORS = [
    dict(key="chomage_jeunes", label="Taux de chômage des 15-24 ans (BIT)", dataset="une_rt_a",
         filters={"age": "Y15-24", "unit": "PC_ACT", "sex": "T"}, unit="%",
         triggers=r"ch[ôo]m\w*.*\b(jeunes?|15[- ]24|moins de 25)|\b(jeunes?|15[- ]24).*ch[ôo]m"),
    dict(key="chomage", label="Taux de chômage des 15-74 ans (BIT)", dataset="une_rt_a",
         filters={"age": "Y15-74", "unit": "PC_ACT", "sex": "T"}, unit="%", triggers=r"\bch[ôo]m(age|eurs?)\b"),
    dict(key="inflation", label="Inflation (IPCH, moyenne annuelle)", dataset="prc_hicp_aind",
         filters={"unit": "RCH_A_AVG", "coicop": "CP00"}, unit="%",
         triggers=r"\binflation\b|hausse des prix|\bprix\b.{0,40}\b(augment|hausse|flamb|explos)"),
    dict(key="dette_pib", label="Dette publique (au sens de Maastricht), % du PIB", dataset="gov_10dd_edpt1",
         filters={"na_item": "GD", "sector": "S13", "unit": "PC_GDP"}, unit="% du PIB", triggers=r"\bdettes?\b"),
    dict(key="dette_eur", label="Dette publique (au sens de Maastricht), en milliards d'euros", dataset="gov_10dd_edpt1",
         filters={"na_item": "GD", "sector": "S13", "unit": "MIO_EUR"}, unit="Md€", divisor=1000,
         triggers=r"\bdettes?\b.*\b(milliards?|md|mille)\b|\b(milliards?|mille)\b.*\bdettes?\b"),
    dict(key="deficit", label="Déficit (−) ou excédent (+) public, % du PIB", dataset="gov_10dd_edpt1",
         filters={"na_item": "B9", "sector": "S13", "unit": "PC_GDP"}, unit="% du PIB",
         triggers=r"\bd[ée]ficits?\b(?!\s+commercia)"),
    dict(key="depense", label="Dépense publique totale, % du PIB", dataset="gov_10a_main",
         filters={"na_item": "TE", "sector": "S13", "unit": "PC_GDP"}, unit="% du PIB",
         triggers=r"d[ée]penses? publiques?|d[ée]pense de l'[ée]tat"),
    dict(key="prelevements", label="Prélèvements obligatoires (impôts et cotisations), % du PIB", dataset="gov_10a_taxag",
         filters={"na_item": "D2_D5_D91_D61_M_D995", "sector": "S13_S212", "unit": "PC_GDP"}, unit="% du PIB",
         triggers=r"pr[ée]l[èe]vements? obligatoires|pression fiscale|taux d'imposition|\bimp[ôo]ts?\b.{0,40}\b(pib|record|champion)"),
    dict(key="emploi_seniors", label="Taux d'emploi des 55-64 ans", dataset="lfsi_emp_a",
         filters={"age": "Y55-64", "unit": "PC_POP", "indic_em": "EMP_LFS", "sex": "T"}, unit="%",
         triggers=r"\bseniors?\b|55[- ]64|plus de 55 ans"),
    dict(key="pib_hab", label="PIB par habitant en standard de pouvoir d'achat (UE-27 = 100)", dataset="tec00114",
         filters={"indic_ppp": "VI_PPS_EU27_2020_HAB", "ppp_cat18": "GDP"}, unit="indice, UE-27 = 100",
         triggers=r"niveau de vie|pib par habitant|richesse par habitant"),
    dict(key="croissance", label="Croissance du PIB en volume", dataset="tec00115",
         filters={"na_item": "B1GQ", "unit": "CLV_PCH_PRE"}, unit="%",
         triggers=r"\bcroissance\b|\br[ée]cession\b|\bpib\b(?! par habitant)"),
    dict(key="pauvrete", label="Taux de pauvreté (seuil à 60 % du revenu médian)", dataset="ilc_li02",
         filters={"rskpovth": "B_60", "statinfo": "MED_EI", "age": "TOTAL", "sex": "T", "unit": "PC"}, unit="%",
         triggers=r"\bpauvret[ée]\b|\bpauvres\b|seuil de pauvret"),
    dict(key="immigration", label="Immigration (entrées sur le territoire, toutes nationalités)", dataset="migr_imm1ctz",
         filters={"citizen": "TOTAL", "age": "TOTAL", "sex": "T", "agedef": "COMPLET", "unit": "NR"}, unit="personnes",
         triggers=r"\bimmigr(ation|[ée]s?)\b|\bmigrants?\b"),
    dict(key="asile", label="Premières demandes d'asile", dataset="migr_asyappctza",
         filters={"applicant": "FRST", "citizen": "TOTAL", "sex": "T", "age": "TOTAL", "unit": "PER"}, unit="demandes",
         triggers=r"\basile\b"),
    dict(key="smic", label="Salaire minimum BRUT mensuel (le SMIC net vaut environ 79 % du brut)", dataset="earn_mw_cur",
         filters={"currency": "EUR"}, unit="€ brut/mois", eu=False, triggers=r"\bsmic\b|salaire minimum"),
    dict(key="ges", label="Émissions de gaz à effet de serre (hors UTCATF), millions de tonnes éq. CO2", dataset="env_air_gge",
         filters={"airpol": "GHG", "src_crf": "TOTX4_MEMO", "unit": "MIO_T"}, unit="Mt éq. CO2",
         triggers=r"[ée]missions?\b|gaz à effet de serre|\bco2\b|\bcarbone\b"),
]
for _ind in INDICATORS:
    _ind["_re"] = re.compile(_ind["triggers"], re.IGNORECASE)

_cache: dict = {}  # key → (fetched_at, {geo: {période: valeur}})
_lock = threading.Lock()


def match(claim: str) -> list:
    """Indicateurs dont parle l'affirmation (MAX_PER_CLAIM au plus). Le
    chômage des jeunes, plus précis, exclut le chômage général."""
    found = [ind for ind in INDICATORS if ind["_re"].search(claim or "")]
    keys = {i["key"] for i in found}
    if "chomage_jeunes" in keys:
        found = [i for i in found if i["key"] != "chomage"]
    return found[:MAX_PER_CLAIM]


def decode_jsonstat(d: dict) -> dict:
    """Réponse JSON-stat d'Eurostat → {geo: {période: valeur}} (toutes les
    autres dimensions sont fixées par les filtres, donc de taille 1)."""
    ids, sizes = d["id"], d["size"]
    strides, acc = [], 1
    for size in reversed(sizes):
        strides.append(acc)
        acc *= size
    strides.reverse()
    geo_idx = d["dimension"]["geo"]["category"]["index"]
    time_idx = d["dimension"]["time"]["category"]["index"]
    out = {}
    for geo, gi in geo_idx.items():
        for period, ti in time_idx.items():
            flat = sum((gi if dim == "geo" else ti if dim == "time" else 0) * stride
                       for dim, stride in zip(ids, strides))
            value = d.get("value", {}).get(str(flat))
            if value is not None:
                out.setdefault(geo, {})[period] = value
    return out


def _fmt(value, ind) -> str:
    v = value / ind.get("divisor", 1)
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    return f"{v:.1f}".replace(".", ",") if v != int(v) else f"{int(v)}"


def format_series(ind: dict, data: dict) -> str:
    """« France : 2019 8,4 · 2020 8,0 · … — UE-27 : 2024 5,9 (%) »."""
    fr = data.get("FR", {})
    if not fr:
        return ""
    periods = sorted(fr)[-11:]
    text = "France : " + " · ".join(f"{p} {_fmt(fr[p], ind)}" for p in periods)
    eu = data.get("EU27_2020", {})
    if eu and ind.get("eu", True):
        last = sorted(eu)[-2:]
        text += " — UE-27 : " + " · ".join(f"{p} {_fmt(eu[p], ind)}" for p in last)
    return f"{text} ({ind['unit']})"


def _fetch(ind: dict) -> dict:
    params = [("geo", "FR"), ("geo", "EU27_2020"), ("sinceTimePeriod", SINCE), ("lang", "fr"),
              *ind["filters"].items()]
    r = requests.get(API + ind["dataset"], params=params, timeout=(4, 20))
    r.raise_for_status()
    return decode_jsonstat(r.json())


def load(path: str = EUROSTAT_CACHE_FILE):
    """Séries déjà téléchargées (disque) : disponibles dès le démarrage."""
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    with _lock:
        for key, (fetched_at, data) in saved.items():
            _cache.setdefault(key, (fetched_at, data))


def _save(path: str = EUROSTAT_CACHE_FILE):
    with _lock:
        snapshot = dict(_cache)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)
    os.replace(tmp, path)


def refresh(force: bool = False, path: str = EUROSTAT_CACHE_FILE) -> int:
    """Télécharge les séries absentes ou périmées ; retourne le nombre mis à jour."""
    updated = 0
    for ind in INDICATORS:
        with _lock:
            hit = _cache.get(ind["key"])
        if hit and not force and time.time() - hit[0] < CACHE_S:
            continue
        try:
            data = _fetch(ind)
        except Exception as e:
            print(f"[Eurostat] {ind['key']}: {type(e).__name__}: {e}")
            continue
        with _lock:
            _cache[ind["key"]] = (time.time(), data)
        updated += 1
    if updated:
        try:
            _save(path)
        except OSError as e:
            print(f"[Eurostat] sauvegarde impossible : {e}")
        print(f"[Eurostat] {updated} série(s) mise(s) à jour, {len(_cache)}/{len(INDICATORS)} disponibles")
    return updated


def start(socketio):
    """Cache disque, puis téléchargement en tâche de fond (et chaque jour)."""
    load()

    def loop():
        while True:
            try:
                refresh()
            except Exception as e:
                print(f"[Eurostat] {type(e).__name__}: {e}")
            socketio.sleep(CACHE_S)

    socketio.start_background_task(loop)


def evidence(claim: str) -> list:
    """Preuves [{title, body, href}] pour les indicateurs dont parle
    l'affirmation — cache uniquement, instantané. Une série pas encore
    téléchargée est simplement absente (le fact-check ne l'attend pas)."""
    out = []
    for ind in match(claim):
        with _lock:
            hit = _cache.get(ind["key"])
        body = format_series(ind, hit[1]) if hit else ""
        if body:
            out.append({"title": f"Eurostat — {ind['label']}", "body": body,
                        "href": DATABROWSER.format(ind["dataset"])})
    return out

"""Cache persistant des fact-checks (SQLite + copie mémoire pour le matching
flou) — voir server/config.py pour les seuils.

Deux garde-fous contre un verdict resservi à tort :
- la correspondance passe par claims_match (text_utils) : un nombre, une
  négation ou un mot de sens différent suffit à refuser le verdict en cache
  (« n'a jamais gelé » ne récupère plus le VRAI de « a gelé ») ;
- chaque verdict est rangé sous l'ANNÉE de la vidéo vérifiée : un replay de
  2024 et un direct de 2026 ne partagent pas leurs verdicts, les chiffres
  ayant changé entre-temps.
Seuls les verdicts sourcés (URL issue de la recherche) sont mis en cache, et
jamais ceux d'une affirmation datée par rapport au jour même (« ce soir »,
« actuellement »). Nettoyage des verdicts déjà en base : purge_cache.py."""

import sqlite3
import threading
import time

from server.config import CACHE_DB, CACHE_TTL_DAYS, CACHE_MIN_CONF, CACHE_SIM_THRESHOLD
from server.text_utils import claim_signature, claims_match, has_relative_time

_cache_conn = sqlite3.connect(CACHE_DB, check_same_thread=False)
_cache_conn.execute("""CREATE TABLE IF NOT EXISTS factchecks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confiance INTEGER,
    explication TEXT,
    source TEXT,
    url TEXT,
    created_at REAL NOT NULL
)""")
# Migration : année de la vidéo vérifiée (NULL pour les lignes antérieures —
# on retombe alors sur l'année de création, cf. _row_year)
_columns = {row[1] for row in _cache_conn.execute("PRAGMA table_info(factchecks)")}
if "video_year" not in _columns:
    _cache_conn.execute("ALTER TABLE factchecks ADD COLUMN video_year INTEGER")
# Migration : verdict signalé par un utilisateur (bouton ⚑ de l'extension) —
# plus jamais resservi, supprimé par purge_cache.py
if "reported_at" not in _columns:
    _cache_conn.execute("ALTER TABLE factchecks ADD COLUMN reported_at REAL")
_cache_conn.commit()
_cache_lock = threading.Lock()
_cache_mem: list = []  # [(signature, année, created_at, résultat)] — copie mémoire pour le matching flou


def _row_year(video_year, created_at: float) -> int:
    return int(video_year) if video_year else time.localtime(created_at).tm_year


def load():
    cutoff = time.time() - CACHE_TTL_DAYS * 86400
    with _cache_lock:
        _cache_conn.execute("DELETE FROM factchecks WHERE created_at < ?", (cutoff,))
        _cache_conn.commit()
        rows = _cache_conn.execute(
            "SELECT claim, verdict, confiance, explication, source, url, created_at, video_year "
            "FROM factchecks WHERE reported_at IS NULL").fetchall()
    skipped = 0
    for claim, verdict, conf, expl, src, url, created_at, video_year in rows:
        # Verdicts non sourcés (antérieurs à la règle « pas d'URL, pas de
        # cache ») : conservés en base, mais jamais resservis
        if not url:
            skipped += 1
            continue
        sig = claim_signature(claim)
        if len(sig.words) >= 3:
            _cache_mem.append((sig, _row_year(video_year, created_at), created_at,
                               {"verdict": verdict, "confiance": conf, "explication": expl or "",
                                "source": src or "", "url": url or ""}))
    print(f"[Cache] {len(_cache_mem)} fact-check(s) en cache"
          + (f" ({skipped} non sourcé(s) ignoré(s))" if skipped else ""))


def mark_reported(claim: str) -> int:
    """Verdict signalé par un utilisateur : les entrées de cache qui
    correspondent à cette affirmation ne sont plus resservies (marquées en
    base, retirées de la mémoire). Retourne le nombre d'entrées touchées."""
    sig = claim_signature(claim)
    now = time.time()
    with _cache_lock:
        hits = [e for e in _cache_mem if claims_match(sig, e[0], CACHE_SIM_THRESHOLD)]
        for e in hits:
            _cache_mem.remove(e)
        rows = _cache_conn.execute("SELECT id, claim FROM factchecks WHERE reported_at IS NULL").fetchall()
        ids = [i for i, c in rows if c == claim or claims_match(sig, claim_signature(c), CACHE_SIM_THRESHOLD)]
        _cache_conn.executemany("UPDATE factchecks SET reported_at = ? WHERE id = ?", [(now, i) for i in ids])
        _cache_conn.commit()
    return len(ids)


def lookup(claim: str, year: int):
    sig = claim_signature(claim)
    if len(sig.words) < 3:
        return None
    cutoff = time.time() - CACHE_TTL_DAYS * 86400
    for s, y, created_at, row in _cache_mem:
        if y == year and created_at >= cutoff and claims_match(sig, s, CACHE_SIM_THRESHOLD):
            return row
    return None


def store(claim: str, result: dict, year: int):
    conf = result.get("confiance")
    if (result.get("verdict") in (None, "", "non_verifiable") or not result.get("url")
            or not isinstance(conf, int) or conf < CACHE_MIN_CONF
            or has_relative_time(claim)):  # « ce soir », « actuellement »… : vrai un jour, pas le suivant
        return
    sig = claim_signature(claim)
    if len(sig.words) < 3:
        return
    now = time.time()
    with _cache_lock:
        _cache_conn.execute(
            "INSERT INTO factchecks (claim, verdict, confiance, explication, source, url, created_at, video_year)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (claim, result["verdict"], conf, result.get("explication", ""),
             result.get("source", ""), result.get("url", ""), now, year))
        _cache_conn.commit()
    _cache_mem.append((sig, year, now, {k: result.get(k) for k in ("verdict", "confiance", "explication", "source", "url")}))

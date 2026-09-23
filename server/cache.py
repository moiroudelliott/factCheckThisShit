"""Cache persistant des fact-checks (SQLite + copie mémoire pour le matching
flou) — voir server/config.py pour les seuils.

Deux garde-fous contre un verdict resservi à tort :
- la correspondance passe par claims_match (text_utils) : un nombre, une
  négation ou un mot de sens différent suffit à refuser le verdict en cache
  (« n'a jamais gelé » ne récupère plus le VRAI de « a gelé ») ;
- chaque verdict est rangé sous l'ANNÉE de la vidéo vérifiée : un replay de
  2024 et un direct de 2026 ne partagent pas leurs verdicts, les chiffres
  ayant changé entre-temps.
Seuls les verdicts sourcés (URL issue de la recherche) sont mis en cache."""

import sqlite3
import threading
import time

from server.config import CACHE_DB, CACHE_TTL_DAYS, CACHE_MIN_CONF, CACHE_SIM_THRESHOLD
from server.text_utils import claim_signature, claims_match

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
if "video_year" not in {row[1] for row in _cache_conn.execute("PRAGMA table_info(factchecks)")}:
    _cache_conn.execute("ALTER TABLE factchecks ADD COLUMN video_year INTEGER")
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
            "FROM factchecks").fetchall()
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
            or not isinstance(conf, int) or conf < CACHE_MIN_CONF):
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

"""Cache persistant des fact-checks (SQLite + copie mémoire pour le matching
flou par mots-clés) — voir server/config.py pour les seuils."""

import sqlite3
import threading
import time

from server.config import CACHE_DB, CACHE_TTL_DAYS, CACHE_MIN_CONF, CACHE_SIM_THRESHOLD
from server.text_utils import key_words

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
_cache_conn.commit()
_cache_lock = threading.Lock()
_cache_mem: list = []  # [(mots_clés, résultat)] — copie mémoire pour le matching flou


def load():
    cutoff = time.time() - CACHE_TTL_DAYS * 86400
    with _cache_lock:
        _cache_conn.execute("DELETE FROM factchecks WHERE created_at < ?", (cutoff,))
        _cache_conn.commit()
        rows = _cache_conn.execute(
            "SELECT claim, verdict, confiance, explication, source, url FROM factchecks").fetchall()
    for claim, verdict, conf, expl, src, url in rows:
        words = key_words(claim)
        if len(words) >= 3:
            _cache_mem.append((words, {"verdict": verdict, "confiance": conf,
                                       "explication": expl or "", "source": src or "", "url": url or ""}))
    print(f"[Cache] {len(_cache_mem)} fact-check(s) en cache")


def lookup(claim: str):
    words = key_words(claim)
    if len(words) < 3:
        return None
    for w, row in _cache_mem:
        if len(words & w) / min(len(words), len(w)) >= CACHE_SIM_THRESHOLD:
            return row
    return None


def store(claim: str, result: dict):
    conf = result.get("confiance")
    if result.get("verdict") in (None, "", "non_verifiable") or not isinstance(conf, int) or conf < CACHE_MIN_CONF:
        return
    words = key_words(claim)
    if len(words) < 3:
        return
    with _cache_lock:
        _cache_conn.execute(
            "INSERT INTO factchecks (claim, verdict, confiance, explication, source, url, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (claim, result["verdict"], conf, result.get("explication", ""),
             result.get("source", ""), result.get("url", ""), time.time()))
        _cache_conn.commit()
    _cache_mem.append((words, {k: result.get(k) for k in ("verdict", "confiance", "explication", "source", "url")}))

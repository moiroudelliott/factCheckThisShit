"""Index local des fact-checks déjà publiés par les rédactions de
vérification françaises (flux RSS : Les Décodeurs, CheckNews, franceinfo
Vrai ou fake, 20 Minutes Fake off, Les Surligneurs).

Les politiques recyclent les mêmes affirmations pendant des mois : quand une
rédaction a déjà vérifié le propos, son article est la meilleure preuve
possible. Chaque flux ne montre que ses derniers articles : l'index
(SQLite, FACTCHECK_INDEX_DB) les accumule à chaque rafraîchissement
(FACTCHECK_FEEDS_REFRESH_S) et s'enrichit donc avec le temps.

parse_feed / search sont pures (testables sans réseau) ; start() lance le
rafraîchissement périodique en tâche de fond."""

import html
import re
import sqlite3
import threading
import time
from email.utils import parsedate_to_datetime

import requests

from server.config import FACTCHECK_FEEDS, FACTCHECK_FEEDS_REFRESH_S, FACTCHECK_INDEX_DB
from server.text_utils import claim_signature, key_words

_UA = "Mozilla/5.0 (compatible; SOURCE-factcheck/1.0; +https://source.codeminds.fr)"
_lock = threading.Lock()
_items: list = []  # [(mots-clés, nombres, item)] — copie mémoire pour la recherche
_conn = None


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_feed(xml_text: str, outlet: str) -> list:
    """Articles d'un flux RSS 2.0 : [{url, outlet, title, summary, published}]."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        url = (it.findtext("link") or "").strip()
        title = _strip_html(it.findtext("title") or "")
        if not url.startswith(("http://", "https://")) or not title:
            continue
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            published = None
        out.append({"url": url, "outlet": outlet, "title": title[:300],
                    "summary": _strip_html(it.findtext("description") or "")[:500], "published": published})
    return out


def _entry(item: dict):
    sig = claim_signature(f"{item['title']} {item['summary']}")
    return (sig.words, sig.numbers, item)


def search(claim: str, items: list = None, limit: int = 2) -> list:
    """Fact-checks publiés qui portent sur la même affirmation : au moins 3
    mots-clés communs couvrant 40 % de ceux de l'affirmation (les nombres
    communs comptent en plus). Les plus pertinents, puis les plus récents."""
    items = _items if items is None else items
    sig = claim_signature(claim)
    words = key_words(claim)
    if len(words) < 3:
        return []
    scored = []
    for iw, inums, item in items:
        overlap = len(words & iw)
        if overlap < 3 or overlap / len(words) < 0.4:
            continue
        scored.append((overlap + len(sig.numbers & inums), item.get("published") or 0, item))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [item for *_, item in scored[:limit]]


# ── Stockage + rafraîchissement ────────────────────────────────────────────

def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(FACTCHECK_INDEX_DB, check_same_thread=False)
        _conn.execute("""CREATE TABLE IF NOT EXISTS known_factchecks (
            url TEXT PRIMARY KEY, outlet TEXT, title TEXT, summary TEXT,
            published REAL, fetched_at REAL NOT NULL)""")
        _conn.commit()
    return _conn


def load():
    with _lock:
        rows = _db().execute("SELECT url, outlet, title, summary, published FROM known_factchecks").fetchall()
        _items[:] = [_entry({"url": u, "outlet": o, "title": t, "summary": s or "", "published": p})
                     for u, o, t, s, p in rows]
    print(f"[Fact-checks publiés] {len(_items)} article(s) en index")


def refresh() -> int:
    """Relit tous les flux ; retourne le nombre d'articles nouveaux."""
    new = 0
    for outlet, url in FACTCHECK_FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
            r.raise_for_status()
            items = parse_feed(r.text, outlet)
        except Exception as e:
            print(f"[Fact-checks publiés] {outlet}: {type(e).__name__}: {e}")
            continue
        with _lock:
            known = {it["url"] for *_, it in _items}
            for item in items:
                if item["url"] in known:
                    continue
                _db().execute("INSERT OR IGNORE INTO known_factchecks VALUES (?,?,?,?,?,?)",
                              (item["url"], item["outlet"], item["title"], item["summary"],
                               item["published"], time.time()))
                _items.append(_entry(item))
                new += 1
            _db().commit()
    if new:
        print(f"[Fact-checks publiés] +{new} article(s), {len(_items)} en index")
    return new


def start(socketio):
    """Chargement de l'index puis rafraîchissement périodique en tâche de fond."""
    load()

    def loop():
        while True:
            try:
                refresh()
            except Exception as e:
                print(f"[Fact-checks publiés] {type(e).__name__}: {e}")
            socketio.sleep(FACTCHECK_FEEDS_REFRESH_S)

    socketio.start_background_task(loop)

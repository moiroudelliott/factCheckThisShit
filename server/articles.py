"""Lecture des articles trouvés par la recherche web.

Le verdict se jouait sur ~300 caractères d'extrait par résultat : trop peu
pour voir qu'un chiffre porte sur une autre année, un autre périmètre ou un
autre bâtiment (cas vécu : « 160 internes à l'intérieur » face à « aucun
élève dans le bâtiment »). Les premiers articles sont donc ouverts, et seules
les phrases qui parlent de l'affirmation (ses chiffres, ses mots-clés, son
année) sont gardées — quelques centaines de caractères par article.

extract_text et relevant_passage sont pures (testables seules) ; fetch_passage
fait l'appel réseau, borné par un délai court : un article qui tarde est
simplement ignoré, le verdict garde l'extrait du moteur de recherche."""

import html as html_lib
import re

import requests

from server.config import ARTICLE_FETCH_TIMEOUT_S, ARTICLE_PASSAGE_CHARS
from server.text_utils import figures, key_words

_DROP_RE = re.compile(r"<(script|style|noscript|svg|header|footer|nav|aside|form|figure)\b.*?</\1>", re.S | re.I)
_BLOCK_RE = re.compile(r"<(p|li|h[1-4]|td|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_RE = re.compile(r"(?<=[.!?…»])\s+(?=[«A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ0-9])")
_YEAR_RE = re.compile(r"\b(?:19|20)\d\d\b")
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SOURCE-fact-check/1.0; +https://source.codeminds.fr)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def extract_text(page: str) -> str:
    """Texte des paragraphes d'une page HTML (titres, paragraphes, listes,
    cellules), sans menus, scripts ni pieds de page."""
    page = _DROP_RE.sub(" ", page or "")
    blocks = []
    for _, inner in _BLOCK_RE.findall(page):
        text = re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", inner))).strip()
        if len(text) >= 40:
            blocks.append(text)
    return "\n".join(blocks)


def relevant_passage(text: str, claim: str, query: str = "", periode: str = "",
                     max_chars: int = ARTICLE_PASSAGE_CHARS) -> str:
    """Les phrases de l'article qui parlent de l'affirmation : mêmes chiffres
    (le plus fort), mêmes mots-clés, même année. Gardées dans l'ordre de
    l'article, jusqu'à max_chars ; vide si rien ne s'en approche."""
    words = key_words(f"{claim} {query}")
    nums = figures(claim)
    years = set(_YEAR_RE.findall(periode or ""))
    scored = []
    for i, sentence in enumerate(s for block in text.split("\n") for s in _SENTENCE_RE.split(block)):
        sentence = sentence.strip()
        if not 30 <= len(sentence) <= 600:
            continue
        common = len(words & key_words(sentence))
        score = common + 3 * len(nums & figures(sentence)) + (1 if years & set(_YEAR_RE.findall(sentence)) else 0)
        if common >= 2 or (nums & figures(sentence)):
            scored.append((score, i, sentence))
    kept, size = [], 0
    for score, i, sentence in sorted(scored, key=lambda x: (-x[0], x[1])):
        if size + len(sentence) > max_chars:
            continue
        kept.append((i, sentence))
        size += len(sentence) + 3
    return " … ".join(s for _, s in sorted(kept))


def fetch_passage(url: str, claim: str, query: str = "", periode: str = "") -> str:
    """Ouvre l'article et en tire le passage utile ; vide en cas d'échec
    (délai dépassé, PDF, page vide, accès refusé)."""
    if not url.startswith(("http://", "https://")) or url.lower().split("?")[0].endswith(".pdf"):
        return ""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=ARTICLE_FETCH_TIMEOUT_S)
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "html"):
            return ""
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        return relevant_passage(extract_text(r.text[:600_000]), claim, query, periode)
    except Exception as e:
        print(f"[Article] {type(e).__name__} : {url[:80]}")
        return ""

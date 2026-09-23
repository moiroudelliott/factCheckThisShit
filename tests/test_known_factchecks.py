"""Index des fact-checks déjà publiés (server/known_factchecks.py) — sans réseau.

Lancer : python tests/test_known_factchecks.py   (ou python -m pytest tests)"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FACTCHECK_INDEX_DB"] = os.path.join(tempfile.mkdtemp(prefix="fct_idx_"), "index.db")

from server import known_factchecks as kf  # noqa: E402
from server.sources import finalize_result, source_tier  # noqa: E402

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>L’Allemagne est-elle vraiment championne des arrêts maladie en Europe, loin devant la France ?</title>
<link>https://www.liberation.fr/checknews/allemagne-arrets-maladie/</link>
<description>&lt;p&gt;Le chiffre avancé mélange deux indicateurs.&lt;/p&gt;</description>
<pubDate>Wed, 23 Sep 2026 08:00:00 +0200</pubDate></item>
<item><title>Le taux de chômage des jeunes a-t-il été divisé par deux depuis 2017 ?</title>
<link>https://www.liberation.fr/checknews/chomage-jeunes/</link>
<description>Non : il est passé de 22,3 % à 18,5 %.</description>
<pubDate>Mon, 21 Sep 2026 10:00:00 +0200</pubDate></item>
<item><title>Sans lien</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


def _index():
    return [kf._entry(it) for it in kf.parse_feed(RSS, "CheckNews (Libération)")]


def test_parse_feed():
    items = kf.parse_feed(RSS, "CheckNews (Libération)")
    assert len(items) == 2                                   # lien javascript: écarté
    assert items[0]["summary"] == "Le chiffre avancé mélange deux indicateurs."  # HTML retiré
    assert items[0]["published"] and items[0]["outlet"] == "CheckNews (Libération)"
    assert kf.parse_feed("pas du xml", "x") == []


def test_search_finds_the_same_claim_only():
    idx = _index()
    hit = kf.search("Le taux de chômage des jeunes a été divisé par deux depuis 2017", idx)
    assert [h["url"] for h in hit] == ["https://www.liberation.fr/checknews/chomage-jeunes/"]
    assert kf.search("L'Allemagne est championne d'Europe des arrêts maladie", idx)
    assert kf.search("La dette publique atteint 3000 milliards d'euros", idx) == []


def test_published_factcheck_outranks_and_names_the_outlet():
    assert source_tier("https://www.liberation.fr/checknews/chomage-jeunes/") == "FACT-CHECK PUBLIÉ"
    assert source_tier("https://www.lemonde.fr/les-decodeurs/article/2026/x.html") == "FACT-CHECK PUBLIÉ"
    assert source_tier("https://www.lemonde.fr/politique/article/x.html") == "PRESSE ÉTABLIE"
    known = kf.parse_feed(RSS, "CheckNews (Libération)")
    r = finalize_result({"verdict": "faux", "confiance": 90, "source": "Libé",
                         "url": "https://www.liberation.fr/checknews/chomage-jeunes/"}, [], [], [], known)
    assert r["url"].endswith("chomage-jeunes/") and r["source"] == "CheckNews (Libération)" and r["confiance"] == 90


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

"""Règles sur les sources et les verdicts (server/sources.py) — cas réels
relevés dans factcheck_cache.db.

Lancer : python tests/test_sources.py   (ou python -m pytest tests)"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.config import EXCLUDED_SOURCES, LOW_RELIABILITY_DOMAINS, PARTISAN_DOMAINS  # noqa: E402
from server.sources import (  # noqa: E402
    finalize_result, is_excluded, normalize_verdict, source_label, source_tier, video_year,
)


def test_tier_uses_hostname_not_substring():
    assert source_tier("https://www.insee.fr/fr/statistiques/1") == "SOURCE OFFICIELLE"
    assert source_tier("https://travail-emploi.gouv.fr/x") == "SOURCE OFFICIELLE"
    assert source_tier("https://blog.example.com/?ref=insee.fr") == "FIABILITÉ INCONNUE"
    assert source_tier("https://notafp.com/x") == "FIABILITÉ INCONNUE"
    assert source_tier("https://www.lemonde.fr/x") == "PRESSE ÉTABLIE"


def test_excluded_domains():
    assert is_excluded("https://www.facebook.com/ZemmourTV/posts/1")
    assert is_excluded("https://fr.x.com/someone")
    assert is_excluded("https://francais.rt.com/x")        # sanctions UE
    assert is_excluded("https://www.legorafi.fr/2026/x")   # satire
    assert not is_excluded("https://www.lemonde.fr/x")
    assert not is_excluded("https://max.com/x")  # pas un sous-domaine de x.com
    # un site militant n'est plus exclu pour sa ligne : il est annoté
    assert not is_excluded("https://ripostelaique.com/article")


def test_partisan_and_low_reliability_tiers():
    assert source_tier("https://ripostelaique.com/article") == "FIABILITÉ FAIBLE"
    assert source_tier("https://lundi.am/article") == "FIABILITÉ FAIBLE"
    assert source_tier("https://rassemblementnational.fr/programme") == "SOURCE PARTISANE"
    assert source_tier("https://lafranceinsoumise.fr/x") == "SOURCE PARTISANE"
    assert source_tier("https://www.pcf.fr/x") == "SOURCE PARTISANE"
    assert not set(LOW_RELIABILITY_DOMAINS) & set(PARTISAN_DOMAINS)


def test_low_reliability_proof_never_settles_alone():
    militant = [{"href": "https://www.fdesouche.com/x", "title": "t"}]
    r = finalize_result({"verdict": "vrai", "confiance": 90, "source": "Fdesouche",
                         "url": "https://www.fdesouche.com/x"}, militant, [], [])
    assert r["url"] == "https://www.fdesouche.com/x" and r["confiance"] == 50
    # un site de parti n'est pas plafonné : il prouve ce que le parti propose
    party = [{"href": "https://parti-socialiste.fr/programme", "title": "t"}]
    r = finalize_result({"verdict": "vrai", "confiance": 85, "source": "PS",
                         "url": "https://parti-socialiste.fr/programme"}, party, [], [])
    assert r["confiance"] == 85


def test_published_policy_matches_the_code():
    """La section « Sources » du site liste exactement les domaines du code."""
    with open(os.path.join(ROOT, "site", "index.html"), encoding="utf-8") as f:
        html = f.read()
    published = {name: set(re.findall(r"<li>([^<]+)</li>", body))
                 for name, body in re.findall(r'<ul class="domains" data-list="(\w+)">(.*?)</ul>', html, re.S)}
    expected = {name: set(domains) for name, domains in EXCLUDED_SOURCES.items()}
    expected.update(faible=set(LOW_RELIABILITY_DOMAINS), partisan=set(PARTISAN_DOMAINS))
    assert published == expected


def test_verdict_normalisation():
    assert normalize_verdict("Vrai") == "vrai"
    assert normalize_verdict("partiellement vrai") == "partiellement_vrai"
    assert normalize_verdict("Non vérifiable") == "non_verifiable"
    assert normalize_verdict("FAUX") == "faux"
    assert normalize_verdict("n'importe quoi") == "non_verifiable"
    assert normalize_verdict(None) == "non_verifiable"


def test_source_label_matches_linked_site():
    # #52 : autorité citée ≠ site lié → on affiche le site
    assert source_label("Ministère de l’Économie et des Finances",
                        "https://www.abcsr-performance.com/marches-publics") == "abcsr-performance.com"
    # #30 : seule la partie qui correspond au domaine est gardée
    assert source_label("Ministère de l’Économie et des Finances (via Gestia Solidaire)",
                        "https://gestia-solidaire.com/plf-2026") == "Gestia Solidaire"
    assert source_label("INSEE, Le Monde", "https://www.lemonde.fr/x") == "Le Monde"
    assert source_label("BFMTV (citant Eurostat/Insee)", "https://www.bfmtv.com/x") == "BFMTV"
    assert source_label("Wikipédia", "https://fr.wikipedia.org/wiki/x") == "Wikipédia"
    assert source_label("SOURCE ACADÉMIQUE", "https://doi.org/10.4000/ahrf.442", "academic") \
        == "étude académique (doi.org)"
    assert source_label("connaissances historiques", "") == "non sourcé"


def test_finalize_result_caps_unsourced_confidence():
    web = [{"href": "https://www.lemonde.fr/x", "title": "t"}]
    # #147 : verdict tranché à 95 % sans aucune URL → plafonné, marqué non sourcé
    r = finalize_result({"verdict": "faux", "confiance": 95, "explication": "e",
                         "source": "connaissances historiques", "url": ""}, web, [], [])
    assert r["confiance"] == 50 and r["source"] == "non sourcé" and r["url"] == ""
    # URL inventée (absente des résultats) → rejetée
    r = finalize_result({"verdict": "vrai", "confiance": 90, "source": "Le Monde",
                         "url": "https://www.lemonde.fr/autre"}, web, [], [])
    assert r["url"] == "" and r["confiance"] == 50
    # URL javascript: → rejetée
    r = finalize_result({"verdict": "vrai", "confiance": 90, "url": "javascript:alert(1)"}, web, [], [])
    assert r["url"] == ""
    # cas nominal
    r = finalize_result({"verdict": "Vrai", "confiance": 88.6, "source": "Le Monde",
                         "url": "https://www.lemonde.fr/x"}, web, [], [])
    assert r == {"verdict": "vrai", "explication": "", "url": "https://www.lemonde.fr/x",
                 "source": "Le Monde", "confiance": 88}


def test_video_year():
    assert video_year({"date": "2024-06-27"}) == 2024
    assert video_year({"date": ""}) >= 2025
    assert video_year({}) >= 2025


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

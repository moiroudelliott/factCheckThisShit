"""Règles sur les sources et les verdicts (server/sources.py) — cas réels
relevés dans factcheck_cache.db.

Lancer : python tests/test_sources.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    assert is_excluded("https://ripostelaique.com/article")
    assert not is_excluded("https://www.lemonde.fr/x")
    assert not is_excluded("https://max.com/x")  # pas un sous-domaine de x.com


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

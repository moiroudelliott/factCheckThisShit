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
    academic_relevant, finalize_result, parse_brave_results, is_excluded, normalize_verdict, related_verdicts,
    self_sourced, source_label, source_tier, video_year, years_in,
)


def test_tier_uses_hostname_not_substring():
    assert source_tier("https://www.insee.fr/fr/statistiques/1") == "SOURCE OFFICIELLE"
    assert source_tier("https://travail-emploi.gouv.fr/x") == "SOURCE OFFICIELLE"
    assert source_tier("https://blog.example.com/?ref=insee.fr") == "FIABILITÉ INCONNUE"
    assert source_tier("https://notafp.com/x") == "FIABILITÉ INCONNUE"
    assert source_tier("https://www.lemonde.fr/x") == "PRESSE ÉTABLIE"
    assert source_tier("https://fr.wikipedia.org/wiki/Gabriel_Attal") == "ENCYCLOPÉDIE"


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
    """La page « Sources » du site liste exactement les domaines du code."""
    with open(os.path.join(ROOT, "site", "sources.html"), encoding="utf-8") as f:
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


def test_academic_results_must_be_about_the_claim():
    claim = "Gabriel Attal a interdit le port de l'abaya et du qamis dans les établissements scolaires"
    on_topic = {"title": "L'interdiction de l'abaya dans les établissements scolaires (2024, HAL)",
                "body": "Analyse de la note de service interdisant le port de l'abaya et du qamis à l'école."}
    off_topic = {"title": "Gabriel Attal (2024, OpenAlex)", "body": "Notice de catalogue."}
    assert academic_relevant(claim, on_topic)
    assert not academic_relevant(claim, off_topic)


def test_brave_api_results():
    data = {"web": {"results": [
        {"title": "Immigration : les entrées <strong>en baisse</strong>", "url": "https://www.lemonde.fr/a",
         "description": "En 2024, les <strong>entrées</strong> ont baissé &amp; …", "extra_snippets": ["Selon l'Insee…"]},
        {"title": "Post", "url": "https://x.com/someone/status/1", "description": "réseau social : exclu"},
        {"title": "Sans lien", "url": "javascript:alert(1)", "description": "x"},
        {"title": "Insee", "url": "https://www.insee.fr/b", "description": "Chiffres"},
    ]}}
    r = parse_brave_results(data, 5)
    assert [x["href"] for x in r] == ["https://www.lemonde.fr/a", "https://www.insee.fr/b"]
    assert r[0]["title"] == "Immigration : les entrées en baisse"
    assert r[0]["body"] == "En 2024, les entrées ont baissé & … … Selon l'Insee…"
    assert parse_brave_results({}, 5) == [] and len(parse_brave_results(data, 1)) == 1


def test_false_needs_a_contradicting_fact_from_a_linked_source():
    """Cas vécu : FAUX 95 % expliqué par « rien ne prouve que… »."""
    web = [{"href": "https://www.insee.fr/x", "title": "t"}]
    base = {"verdict": "faux", "confiance": 95, "explication": "e", "url": "https://www.insee.fr/x"}
    assert finalize_result({**base, "contredit_par": "Insee : 375 000 entrées en 2022"}, web, [], [])["verdict"] == "faux"
    assert finalize_result({**base, "contredit_par": ""}, web, [], [])["verdict"] == "non_verifiable"
    assert finalize_result({**base, "url": "", "contredit_par": "de mémoire"}, web, [], [])["verdict"] == "non_verifiable"


def test_true_verdict_with_a_contradicted_element_is_partial():
    """Cas vécu : VRAI 95 % alors que l'explication citait une baisse en 2020."""
    data = {"verdict": "vrai", "confiance": 95, "explication": "Baisse en 2024, mais aussi en 2020.",
            "inexact": "une première depuis 15 ou 20 ans", "url": ""}
    assert finalize_result(data, [], [], [])["verdict"] == "partiellement_vrai"
    assert finalize_result({**data, "inexact": ""}, [], [], [])["verdict"] == "vrai"


def test_unrecouped_verdict():
    assert normalize_verdict("non_recoupe") == "non_recoupe"
    assert normalize_verdict("Non recoupé") == "non_recoupe"


def test_speaker_quoting_himself_is_not_a_proof():
    """Cas vécus (Geffray, France 2) : VRAI 95 % parce qu'un article rapporte
    ce que le ministre a lui-même annoncé."""
    qui = "Édouard Geffray"
    assert self_sourced("Édouard Geffray a effectivement annoncé que 24 établissements ont été saccagés.", qui)
    assert self_sourced("Édouard Geffray a bien déclaré que 78 personnels ont été blessés.", qui)
    assert self_sourced("Selon le ministre Édouard Geffray, 170 lycéens ont été blessés.", qui)
    assert self_sourced("Édouard Geffray a confirmé que 170 élèves ont été blessés.", qui)
    # preuve indépendante, autre personne, affirmation sur une déclaration
    assert not self_sourced("Le rectorat confirme que 24 établissements ont été saccagés.", qui)
    assert not self_sourced("Gérald Darmanin a bien déclaré sur RTL vouloir…", "Manuel Bompard")
    assert not self_sourced("Gérald Darmanin a bien déclaré…", "Gérald Darmanin",
                            claim="Gérald Darmanin a déclaré sur RTL vouloir engager la responsabilité des parents")
    assert not self_sourced("Édouard Geffray a annoncé…", "")
    web = [{"href": "https://www.lefigaro.fr/x", "title": "t"}]
    data = {"verdict": "vrai", "confiance": 95, "url": "https://www.lefigaro.fr/x",
            "explication": "Édouard Geffray a effectivement déclaré que 78 personnels ont été blessés."}
    assert finalize_result(data, web, [], [], qui=qui)["verdict"] == "non_recoupe"
    assert finalize_result(data, web, [], [], qui="Manuel Bompard")["verdict"] == "vrai"
    # « non recoupé » sans article lié : aucune source ne traite du sujet
    nr = {"verdict": "non_recoupe", "confiance": 60, "explication": "Chiffre du ministère, non recoupé.", "url": ""}
    assert finalize_result(nr, [], [], [])["verdict"] == "non_verifiable"
    assert finalize_result({**nr, "url": "https://www.lefigaro.fr/x"}, web, [], [])["verdict"] == "non_recoupe"
    # « non recoupé » qui ne dit pas de qui vient la déclaration : un « non vérifiable » déguisé
    vague = {**nr, "url": "https://www.lefigaro.fr/x",
             "explication": "Aucune source indépendante ne confirme ni ne contredit le lien causal."}
    assert finalize_result(vague, web, [], [], qui="Manuel Bompard")["verdict"] == "non_verifiable"
    named = {**vague, "explication": "Chiffre avancé par Édouard Geffray, sans confirmation indépendante."}
    assert finalize_result(named, web, [], [], qui="Édouard Geffray")["verdict"] == "non_recoupe"


def test_false_needs_a_source_on_the_same_period():
    """Cas vécus : « +1,2 milliard » (budget 2027) démenti par le budget 2026 ;
    une visite « lundi dernier » démentie par une page de 2025."""
    web = [{"href": "https://www.senat.fr/x", "title": "t"}]
    base = {"verdict": "faux", "confiance": 90, "explication": "e", "url": "https://www.senat.fr/x"}
    r = finalize_result({**base, "contredit_par": "PLF 2026 : hausse de 166 millions d'euros"}, web, [], [],
                        periode="2027")
    assert r["verdict"] == "non_verifiable" and "2026" in r["explication"]
    r = finalize_result({**base, "contredit_par": "Le Monde : budget 2027 en hausse de 1,2 milliard"}, web, [], [],
                        periode="2027")
    assert r["verdict"] == "faux"
    # sans période connue, ou sans année dans le fait contraire : rien ne change
    assert finalize_result({**base, "contredit_par": "PLF 2026 : +166 M€"}, web, [], [])["verdict"] == "faux"
    assert finalize_result({**base, "contredit_par": "aucun élève dans le bâtiment"}, web, [], [],
                           periode="2026")["verdict"] == "faux"
    assert finalize_result({**base, "contredit_par": "30,4 élèves en 2011"}, web, [], [],
                           periode="2011")["verdict"] == "faux"
    # prévision lointaine : l'année est l'affirmation, une autre année la contredit
    # (cas vécu : « majoritaires en 2045 » face à « après 2050 », classé non vérifiable)
    r = finalize_result({**base, "contredit_par": "OID : majorité extra-européenne possible après 2050"}, web, [], [],
                        periode="2045")
    assert r["verdict"] == "faux"


def test_misheard_claims_are_flagged():
    """Cas vécu : une carte « Transcription incomplète : l'affirmation ne précise
    pas quels travaux… » affichée sur la vidéo — rien n'a été vérifié."""
    nv = {"verdict": "non_verifiable", "confiance": 40, "url": ""}
    for expl in ("Transcription incomplète : l'affirmation ne précise pas quels travaux sont concernés.",
                 "Transcription douteuse : « vies sérielles » au lieu de « violences sérielles ».",
                 "« Transcription douteuse » : chiffre invraisemblable."):
        assert finalize_result({**nv, "explication": expl}, [], [], []).get("inaudible") is True, expl
    assert "inaudible" not in finalize_result({**nv, "explication": "Aucune source ne traite du sujet."}, [], [], [])
    # un vrai verdict qui parle de transcription n'est pas concerné
    web = [{"href": "https://www.lemonde.fr/x", "title": "t"}]
    assert "inaudible" not in finalize_result({**nv, "verdict": "faux", "contredit_par": "Le Monde : 12 %",
                                               "url": "https://www.lemonde.fr/x",
                                               "explication": "Transcription douteuse : x"}, web, [], [])


def test_absence_of_source_is_not_a_contradiction():
    """Cas vécus (Mistral Large, Attal / Maréchal) : FAUX 85-90 % sur « aucune
    projection ne confirme », « aucun accord n'est mentionné »."""
    web = [{"href": "https://www.lemonde.fr/x", "title": "t"}]
    base = {"verdict": "faux", "confiance": 90, "url": "https://www.lemonde.fr/x", "explication": "e"}
    for absent in ("Aucune projection de l'INED ou de l'INSEE ne prévoit une majorité en 2045",
                   "Aucun accord officiel n'est mentionné dans les sources fiables",
                   "Les sources ne confirment pas que tous les budgets l'ont été"):
        assert finalize_result({**base, "contredit_par": absent}, web, [], [])["verdict"] == "non_verifiable", absent
        assert finalize_result({**base, "contredit_par": "x", "explication": absent}, web, [], [])["verdict"] \
            == "non_verifiable", absent
    # un vrai fait contraire qui contient « aucun » reste un « faux »
    for fact in ("Région Pays de la Loire : aucun élève ne se trouvait dans le bâtiment",
                 "Aucun élève ne se trouvait dans le bâtiment selon la région"):
        assert finalize_result({**base, "contredit_par": fact}, web, [], [])["verdict"] == "faux", fact


def test_years_in():
    assert years_in("depuis 2017-2026") == set(range(2017, 2027))
    assert years_in("rentrée 2025-2026, budget 2027") == {2025, 2026, 2027}
    assert years_in("10 000 postes") == set() and years_in("") == set()


def test_related_verdicts_share_the_subject():
    done = [{"claim": "Le budget 2027 de l'Éducation nationale est en hausse de 1,2 milliard", "verdict": "vrai"},
            {"claim": "170 lycéens ont été blessés", "verdict": "vrai"}]
    got = related_verdicts("Le budget de l'Éducation nationale prévoit 650 millions de coupes", done)
    assert [v["verdict"] for v in got] == ["vrai"] and "budget" in got[0]["claim"]
    assert related_verdicts("Édouard Geffray était à Créteil lundi", done) == []
    # le fil de l'orateur : ses derniers verdicts, même sans mot commun
    done = [{"claim": "Le Figaro Magazine publie un dossier de l'OID cette semaine", "qui": "Marion Maréchal",
             "verdict": "vrai"},
            {"claim": "170 lycéens ont été blessés", "qui": "Édouard Geffray", "verdict": "non_recoupe"}]
    got = related_verdicts("Les naissances non européennes seront majoritaires en 2045", done, qui="Marion Maréchal")
    assert [v["claim"][:9] for v in got] == ["Le Figaro"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

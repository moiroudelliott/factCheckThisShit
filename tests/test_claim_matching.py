"""Non-régression de la comparaison d'affirmations (dédup + cache).

Lancer : python tests/test_claim_matching.py   (ou python -m pytest tests)

Chaque paire « différente » ci-dessous était auparavant fusionnée : la dédup
jetait la réplique de l'adversaire, le cache resservait le verdict d'une
affirmation pour son contraire."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Base de cache jetable : ne jamais toucher factcheck_cache.db pendant les tests
os.environ["FACTCHECK_CACHE_DB"] = os.path.join(tempfile.mkdtemp(prefix="fct_test_"), "cache.db")

from server import cache  # noqa: E402
from server.dedup import dupe_index_add, is_duplicate_indexed  # noqa: E402
from server.text_utils import claim_signature  # noqa: E402

DIFFERENT = [
    ("Marine Le Pen a voté contre la réforme des retraites",
     "Jordan Bardella a voté pour la réforme des retraites"),
    ("Le budget de la défense atteint 50 milliards d'euros en 2025",
     "Le budget de l'éducation atteint 80 milliards d'euros en 2025"),
    ("Le chômage a baissé de 2 points depuis 2017",
     "Le chômage a augmenté depuis 2017 selon l'INSEE"),
    ("La France compte 5 millions de chômeurs",
     "La France compte 3 millions de pauvres"),
    ("Le gouvernement a gelé et surgelé un certain nombre de crédits de l'État en cours d'année.",
     "Le gouvernement n'a jamais gelé de crédits de l'État en cours d'année."),
    ("La majorité de la dépense publique en France est constituée de dépenses sociales.",
     "La majorité de la dépense publique en France n'est pas constituée de dépenses sociales."),
    ("Le PLF 2026 prévoit 43 milliards d'euros de prélèvements supplémentaires.",
     "Le PLF 2026 prévoit 12 milliards d'euros de prélèvements supplémentaires."),
    ("Le gouvernement a doublé les franchises médicales dans le cadre de ses mesures budgétaires.",
     "Le gouvernement a triplé les franchises médicales dans le cadre de ses mesures budgétaires."),
]

SAME = [
    ("La dette publique atteint 3 000 milliards d'euros",
     "La dette de la France dépasse les 3000 milliards d'euros"),
    ("Le gouvernement a gelé et surgelé un certain nombre de crédits de l'État en cours d'année.",
     "Le gouvernement a gelé et surgelé des crédits de l'État en cours d'année"),
]


def _dup(a, b):
    index = {}
    dupe_index_add(index, claim_signature(a).words, 0)
    return is_duplicate_indexed(b, [{"texte": a}], index)


def test_different_claims_are_not_duplicates():
    for a, b in DIFFERENT:
        assert not _dup(a, b), (a, b)
        assert not _dup(b, a), (b, a)


def test_reformulations_are_duplicates():
    for a, b in SAME:
        assert _dup(a, b), (a, b)


def test_numbers_are_normalised():
    assert claim_signature("3 000 milliards").numbers == claim_signature("3000 milliards").numbers
    assert claim_signature("5,50 %").numbers == claim_signature("5,5 %").numbers
    assert claim_signature("en 2023").numbers != claim_signature("en 2024").numbers


def test_cache_never_serves_the_opposite_claim():
    sourced = {"verdict": "vrai", "confiance": 90, "explication": "x", "source": "Le Monde",
               "url": "https://www.lemonde.fr/x"}
    for a, b in DIFFERENT:
        cache.store(a, sourced, 2026)
        assert cache.lookup(b, 2026) is None, (a, b)
    # même affirmation, même année → servie ; autre année → non
    a = DIFFERENT[4][0]
    assert cache.lookup(a, 2026) is not None
    assert cache.lookup(a, 2024) is None


def test_cache_refuses_unsourced_verdicts():
    unsourced = {"verdict": "faux", "confiance": 95, "explication": "x", "source": "connaissances", "url": ""}
    claim = "La crise financière a eu lieu sous la présidence de Nicolas Sarkozy"
    cache.store(claim, unsourced, 2026)
    assert cache.lookup(claim, 2026) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

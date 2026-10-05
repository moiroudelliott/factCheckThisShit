"""Non-régression de la comparaison d'affirmations (dédup + cache).

Lancer : python tests/test_claim_matching.py   (ou python -m pytest tests)

Chaque paire « différente » ci-dessous était auparavant fusionnée : la dédup
jetait la réplique de l'adversaire, le cache resservait le verdict d'une
affirmation pour son contraire."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Base de cache jetable : ne jamais toucher factcheck_cache.db pendant les tests
os.environ["FACTCHECK_CACHE_DB"] = os.path.join(tempfile.mkdtemp(prefix="fct_test_"), "cache.db")

from server import cache  # noqa: E402
from server.dedup import dupe_index_add, is_duplicate_indexed, repeats_figures_indexed  # noqa: E402
from server.text_utils import claim_signature, figures, same_figures  # noqa: E402

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


def test_figures_in_words_and_digits():
    assert figures("Un dixième des cours ne peuvent pas être suivis") == {"10"}
    assert figures("Une heure de cours sur dix ne peut pas être suivie") == {"10"}
    assert figures("Un établissement scolaire du second degré public sur deux manque d'un enseignant") == {"50"}
    assert figures("Le budget 2027 prévoit 1 588 suppressions de postes") == {"1588"}
    assert figures("En 2024, la dette a augmenté") == frozenset()


def test_same_figures_on_the_same_subject_are_one_claim():
    """Cas vécus (Bompard / Geffray) : la même chose vérifiée deux à cinq fois."""
    assert same_figures("78 personnels de l'éducation nationale ont été blessés lors de violences ciblant leur statut",
                        "78 personnels de l'éducation nationale ont été blessés depuis le début des événements")
    assert same_figures("Un dixième des cours ne peuvent pas être suivis faute de remplaçants",
                        "Une heure de cours sur dix en France ne peut pas être suivie")
    assert same_figures("7,5 % des heures non assurées sont liées aux absences de professeurs",
                        "7,5 % des heures de cours ne sont pas remplacées en raison de l'absence de professeur")
    assert same_figures("24 établissements scolaires ont été saccagés",
                        "Au moins 24 établissements scolaires ont été incendiés ou saccagés")
    # autre sujet, autre sens, chiffre en plus : pas la même affirmation
    assert not same_figures("10 % des enfants sont victimes de violences", "10 % des heures de cours ne sont pas assurées")
    assert not same_figures("Le budget est en hausse de 1,2 milliard", "Le budget est en baisse de 1,2 milliard")
    assert not same_figures("10 % des heures ne sont pas remplacées, soit 20 millions d'heures",
                            "Un dixième des heures de cours ne sont pas remplacées")
    assert not same_figures("Le chômage a baissé en 2024", "Le chômage a baissé en 2024")  # sans chiffre : autre règle


def test_repeated_figures_are_skipped_only_for_claims():
    points, index = [], {}
    first = {"type": "affirmation", "texte": "78 personnels de l'éducation nationale ont été blessés"}
    points.append(first)
    dupe_index_add(index, claim_signature(first["texte"]).words, 0)
    assert repeats_figures_indexed("78 personnels ont été blessés depuis le début des événements", points, index)
    points[0] = {**first, "type": "remarque"}
    assert not repeats_figures_indexed("78 personnels ont été blessés depuis le début des événements", points, index)


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


def test_cache_ignores_unrecouped_and_old_rules():
    claim = "78 personnels de l'Éducation nationale ont été blessés lors des blocages"
    cache.store(claim, {"verdict": "non_recoupe", "confiance": 90, "explication": "x", "source": "Le Figaro",
                        "url": "https://www.lefigaro.fr/x"}, 2026)
    assert cache.lookup(claim, 2026) is None
    # ligne rendue sous les anciennes règles : jamais rechargée
    old = "Le budget de l'Éducation nationale est en hausse de 1,2 milliard d'euros"
    cache._cache_conn.execute(
        "INSERT INTO factchecks (claim, verdict, confiance, explication, source, url, created_at, video_year)"
        " VALUES (?,?,?,?,?,?,?,?)", (old, "faux", 90, "x", "Sénat", "https://www.senat.fr/x", time.time(), 2026))
    cache._cache_conn.commit()
    before = len(cache._cache_mem)
    cache._cache_mem.clear()
    cache.load()
    assert cache.lookup(old, 2026) is None
    assert len(cache._cache_mem) == before


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

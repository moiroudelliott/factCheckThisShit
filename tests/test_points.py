"""Validation des talking points renvoyés par Mistral (server/points.py).

Lancer : python tests/test_points.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.points import apply_checkworthiness, verifiable_score  # noqa: E402


def test_vague_affirmations_are_not_checked():
    # cas réels du cache : VRAI 90 % sur des généralités
    p = apply_checkworthiness({"type": "affirmation", "texte": "Il existe des fractures en France.", "verifiable": 2})
    assert p["type"] == "vague"
    p = apply_checkworthiness({"type": "affirmation", "texte": "Le chômage est à 7,4 % en 2024", "verifiable": 9})
    assert p["type"] == "affirmation"


def test_missing_or_bad_score_never_blocks_a_check():
    assert verifiable_score({}) == 10
    assert verifiable_score({"verifiable": "huit"}) == 10
    assert verifiable_score({"verifiable": "7"}) == 7
    assert verifiable_score({"verifiable": 42}) == 10
    p = apply_checkworthiness({"type": "affirmation", "texte": "x"})
    assert p["type"] == "affirmation"


def test_other_types_untouched():
    p = apply_checkworthiness({"type": "subjectif", "texte": "x", "verifiable": 0})
    assert p["type"] == "subjectif"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

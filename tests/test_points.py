"""Validation des talking points renvoyés par Mistral (server/points.py).

Lancer : python tests/test_points.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.points import (  # noqa: E402
    apply_checkworthiness, citation_time, speaker_named_in_citation, validate_citation, verifiable_score,
)


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


TRANSCRIPT = ("Intervenant A: Le chômage a baissé de deux points depuis 2017, et ça c'est un fait.\n"
              "Jordan Bardella: Non, l’immigration coûte 40 milliards par an !")
ENTRIES = [("Intervenant A", "Le chômage a baissé de deux points depuis 2017, et ça c'est un fait.", 1000.0),
           ("Intervenant B", "Non, l’immigration coûte 40 milliards par an !", 1012.5)]


def test_citation_must_be_in_the_transcript():
    ok = validate_citation("« le chômage a baissé de deux points depuis 2017 »", TRANSCRIPT)
    assert ok == "le chômage a baissé de deux points depuis 2017"
    # apostrophes et ponctuation normalisées
    assert validate_citation("l'immigration coûte 40 milliards par an", TRANSCRIPT)
    # reformulation ou invention : rejetée
    assert validate_citation("le chômage a baissé de 2 points depuis 2017", TRANSCRIPT) == ""
    assert validate_citation("l'immigration coûte 60 milliards", TRANSCRIPT) == ""
    assert validate_citation("oui", TRANSCRIPT) == ""


def test_citation_dates_the_statement():
    assert citation_time("l'immigration coûte 40 milliards par an", ENTRIES) == 1012.5
    assert citation_time("le chômage a baissé de deux points", ENTRIES) == 1000.0
    assert citation_time("absent de la transcription", ENTRIES) is None


def test_speaker_named_in_own_quote_is_not_the_speaker():
    """Cas vécus (débat Attal / Maréchal) : interpellation et présentateur."""
    assert speaker_named_in_citation("Marion Maréchal-Le Pen", "La réponse est non, Marion Maréchal, c'est un sujet central.")
    assert speaker_named_in_citation("Gabriel Attal", "Gabriel Attal fait référence à ce protocole d'accord signé")
    # l'adversaire nommé, ou personne : attribution gardée
    assert not speaker_named_in_citation("Gabriel Attal", "Mais contrairement à vous, Marion Maréchal, je ne considère pas")
    assert not speaker_named_in_citation("Marion Maréchal-Le Pen", "Monsieur Attal sous son ministère, c'est 1,9 million")
    assert not speaker_named_in_citation("Intervenant A", "Intervenant A a dit")
    assert not speaker_named_in_citation("Gabriel Attal", "")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

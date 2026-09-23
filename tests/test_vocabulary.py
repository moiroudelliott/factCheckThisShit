"""Mots attendus par Whisper (server/vocabulary.py).

Lancer : python tests/test_vocabulary.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.vocabulary import MAX_HOTWORDS_CHARS, build_hotwords, is_hotword_echo, learn, proper_nouns  # noqa: E402

TITLE = "Législatives 2024 : le replay du débat entre Gabriel Attal, Jordan Bardella et Manuel Bompard — LCI"
DESC = ("Ce mardi soir, Gabriel Attal pour le camp présidentiel, Jordan Bardella pour le Rassemblement "
        "national, Manuel Bompard du Nouveau Front populaire ont débattu sur TF1 et LCI. Suivez notre direct.")
LEXICON = ["INSEE", "Cour des comptes", "Rassemblement national", "maires ruraux", "abaya"]


def test_proper_nouns_from_a_real_title():
    found = proper_nouns(f"{TITLE}. {DESC}")
    for name in ("Gabriel Attal", "Jordan Bardella", "Manuel Bompard", "LCI", "TF1", "Nouveau Front"):
        assert name in found, name
    for noise in ("Législatives", "Suivez", "Ce", "Manuel Bompard du Nouveau Front"):
        assert noise not in found, noise


def test_priority_and_budget():
    guests = ["Gabriel Attal", "Jordan Bardella", "Manuel Bompard"]
    hw = build_hotwords(guests, TITLE, DESC, learn([], "Le budget Lecornu selon la Cour des comptes"), LEXICON)
    terms = hw.split(", ")
    assert terms[:3] == guests                       # intervenants en tête
    assert "Lecornu" in terms                        # appris pendant le débat
    assert "Cour" not in terms and "Rassemblement" not in terms  # morceaux de termes du lexique
    assert "maires ruraux" in terms
    long_lexicon = [f"Terme{i} très long pour remplir" for i in range(100)]
    assert len(build_hotwords(guests, lexicon=long_lexicon)) <= MAX_HOTWORDS_CHARS


def test_learned_terms_are_capped_and_recent_first():
    learned = []
    for i in range(30):
        learn(learned, f"Monsieur Nom{i} a parlé")
    assert len(learned) == 20 and learned[-1] == "Monsieur Nom29"


def test_hotword_echo_is_detected():
    hw = "Gabriel Attal, Jordan Bardella, Manuel Bompard, INSEE"
    assert is_hotword_echo("Gabriel Attal, Jordan Bardella, Manuel Bompard.", hw)
    assert not is_hotword_echo("Gabriel Attal a raison sur ce point.", hw)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

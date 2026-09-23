"""Nettoyage du texte transcrit (server/text_utils.py).

Lancer : python tests/test_transcript.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.text_utils import build_transcript, strip_overlap  # noqa: E402


def test_strip_overlap_removes_repeated_boundary_words():
    assert strip_overlap("le chômage a baissé de", "a baissé de 2 % en 2023") == "2 % en 2023"
    assert strip_overlap("Nous avons créé des emplois.", "des emplois. Et vous le savez") == "Et vous le savez"
    assert strip_overlap("une phrase complète ici", "une phrase complète ici") == ""


def test_strip_overlap_needs_two_words():
    assert strip_overlap("rien à voir", "avec la suite du débat") == "avec la suite du débat"
    assert strip_overlap("il parle de", "de tout autre chose") == "de tout autre chose"


def test_build_transcript_merges_turns():
    entries = [("Intervenant A", "Bonjour."), ("Intervenant A", "Je commence."), ("Intervenant B", "Non.")]
    assert build_transcript(entries) == "Intervenant A: Bonjour. Je commence.\nIntervenant B: Non."


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

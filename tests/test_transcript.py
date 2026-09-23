"""Nettoyage du texte transcrit (server/text_utils.py).

Lancer : python tests/test_transcript.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.text_utils import build_transcript, has_relative_time, strip_overlap  # noqa: E402


def test_strip_overlap_removes_repeated_boundary_words():
    assert strip_overlap("le chômage a baissé de", "a baissé de 2 % en 2023") == "2 % en 2023"
    assert strip_overlap("Nous avons créé des emplois.", "des emplois. Et vous le savez") == "Et vous le savez"
    assert strip_overlap("une phrase complète ici", "une phrase complète ici") == ""


def test_strip_overlap_needs_two_words():
    assert strip_overlap("rien à voir", "avec la suite du débat") == "avec la suite du débat"
    assert strip_overlap("il parle de", "de tout autre chose") == "de tout autre chose"


def test_relative_time_claims_are_detected():
    # cas réels du cache : vrais un jour, faux le lendemain
    assert has_relative_time("Le budget Lecornu est un sujet de débat ce soir.")
    assert has_relative_time("Il existe actuellement en France un mouvement social sans porte-parole")
    assert has_relative_time("Des émeutes ont agité la France il y a un an, sans réponse de fond.")
    assert has_relative_time("Éric Zemmour est crédité de 3 à 4% des intentions de vote à la date du débat.")
    assert has_relative_time("Aujourd'hui, une augmentation de 100 euros coûte 500 euros")
    # dates absolues : pas concernées
    assert not has_relative_time("En 1980, la dépense publique représentait 50% du PIB en France.")
    assert not has_relative_time("Le détroit d'Hormuz est bloqué en septembre 2026")
    assert not has_relative_time("La loi de 1905 consacre la liberté religieuse")


def test_build_transcript_merges_turns():
    entries = [("Intervenant A", "Bonjour."), ("Intervenant A", "Je commence."), ("Intervenant B", "Non.")]
    assert build_transcript(entries) == "Intervenant A: Bonjour. Je commence.\nIntervenant B: Non."


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

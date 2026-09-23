"""Noms de locuteurs (server/names.py) et stockage de la banque de voix
(server/voice_store.py).

Lancer : python tests/test_voices.py   (ou python -m pytest tests)"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import voice_store  # noqa: E402
from server.names import canonical_name, name_matches, norm_name  # noqa: E402

BANK = ["Jordan Bardella", "Marine Le Pen", "Éric Zemmour", "Jean-François Copé"]


def test_name_normalisation_and_matching():
    assert norm_name("Éric  Zemmour (Reconquête)") == "eric zemmour"
    assert name_matches("Eric Zemmour", "Éric Zemmour")
    assert name_matches("Bardella", "Jordan Bardella")
    assert name_matches("Jordan Bardella (RN)", "Jordan Bardella")
    assert name_matches("J. Bardella", "Jordan Bardella")
    assert name_matches("Le Pen", "Marine Le Pen")
    assert not name_matches("Jordan", "Jordan Bardella")          # prénom seul : non
    assert not name_matches("Marion Maréchal", "Marine Le Pen")
    assert name_matches("François Copé", "Jean-François Copé")    # prénom composé abrégé


def test_canonical_name_prefers_bank_key_then_guest():
    guests = ["Jordan Bardella", "Gabriel Attal"]
    assert canonical_name("Bardella", guests, BANK) == "Jordan Bardella"
    assert canonical_name("eric zemmour", [], BANK) == "Éric Zemmour"
    assert canonical_name("Attal", guests, BANK) == "Gabriel Attal"      # pas en banque → invité
    assert canonical_name("Inconnu Total", guests, BANK) == "Inconnu Total"


def test_voice_store_roundtrip_and_slug_collision():
    d = tempfile.mkdtemp(prefix="fct_voices_")
    voice_store.VOICES_DIR, voice_store.INDEX_PATH = d, os.path.join(d, "index.json")
    a = voice_store.save_embedding("Éric Zemmour", np.ones(4), auto=True)
    b = voice_store.save_embedding("Eric Zemmour", np.zeros(4), auto=False)
    assert a != b  # même slug, deux fichiers distincts
    assert np.allclose(np.load(os.path.join(d, a)), 1)
    assert set(voice_store.load_index()) == {"Éric Zemmour", "Eric Zemmour"}
    # réenregistrer un nom réutilise son fichier
    assert voice_store.save_embedding("Éric Zemmour", np.full(4, 2.0)) == a
    assert voice_store.remove("Eric Zemmour") and not os.path.exists(os.path.join(d, b))
    assert not voice_store.remove("Absent")
    assert list(voice_store.load_index()) == ["Éric Zemmour"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

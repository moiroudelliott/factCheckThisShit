"""Génération d'une session (generate_session.py) : une seule à la fois, et
reprise d'une génération coupée — sans audio, modèle ni réseau.

Lancer : python tests/test_generate.py   (ou python -m pytest tests)"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_session as gen  # noqa: E402


def _ev(t, **m):
    return {"t": t, "m": m}


def test_only_one_generation_at_a_time():
    """Cas vécu : deux générations du même débat en parallèle, ~730 appels
    Mistral perdus. Le verrou du système se libère seul si le processus
    meurt : jamais de verrou fantôme après un ordinateur éteint."""
    path = os.path.join(tempfile.mkdtemp(), "gen.lock")
    first = gen.acquire_lock(path)
    assert first is not None
    assert gen.acquire_lock(path) is None      # une deuxième génération est refusée
    first.close()                              # fin (ou mort) de la première
    again = gen.acquire_lock(path)
    assert again is not None
    again.close()


def test_resume_redoes_the_last_minute_and_drops_the_ending():
    events = [_ev(0.0, type="connection_status"), _ev(100.0, type="talking_points", points=[]),
              _ev(500.0, type="fact_check_result", id="a"), _ev(560.0, type="speaker_live", speaker="Intervenant A"),
              _ev(600.0, type="finalizing"), _ev(600.0, action="captureEnded")]
    t0, kept = gen.resume_plan(events, margin=60)
    assert t0 == 540.0
    assert [e["t"] for e in kept] == [0.0, 100.0, 500.0]   # la dernière minute est refaite, la fin écartée


def test_labels_after_a_resume_never_collide_with_earlier_ones():
    """Le backend repart de « Intervenant A » à la reprise : sans décalage, une
    autre voix hériterait du label (et du nom) d'un débatteur."""
    kept = [_ev(1, type="speaker_live", speaker="Intervenant A"),
            _ev(2, type="talking_points", points=[{"qui": "Intervenant C", "qui_label": "Intervenant C"}]),
            _ev(3, type="speaker_map", map={"Intervenant B": "Jordan Bardella"})]
    offset = gen.label_offset(kept)
    assert offset == 3
    m = gen.shift_labels({"type": "talking_points", "points": [{"qui": "Gabriel Attal", "qui_label": "Intervenant A"}]}, offset)
    assert m["points"][0] == {"qui": "Gabriel Attal", "qui_label": "Intervenant D"}  # un nom reste un nom
    assert gen.shift_labels({"type": "speaker_map", "map": {"Intervenant B": "X"}}, offset)["map"] == {"Intervenant E": "X"}
    assert gen.shift_labels({"type": "speaker_live", "speaker": "Intervenant Z"}, offset)["speaker"] == "Intervenant 29"
    assert gen.shift_labels({"type": "speaker_live", "speaker": "Intervenant A"}, 0)["speaker"] == "Intervenant A"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

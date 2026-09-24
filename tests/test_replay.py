"""Relecture sur le site (publish_session.py, site/relecture.html) : copies
de l'overlay à jour, nettoyage des sessions publiées.

Lancer : python tests/test_replay.py   (ou python -m pytest tests)"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import publish_session as ps  # noqa: E402


def _export(events, **video):
    return {"format": "source-session", "version": 1, "exported": "2026-09-24", "offset": 0,
            "video": {"youtube": "M2_wEDek554", "title": "Débat", **video}, "events": events}


POINT = {"id": "a1", "type": "affirmation", "texte": "Le chômage a baissé de 2 points", "qui": "Intervenant A",
         "qui_label": "Intervenant A", "citation": "le chômage a baissé", "vt": 40.0,
         "ts": 1758700000.0, "said_at": 1758699990.0, "receivedAt": 1, "quiEpoch": 0}


def test_site_overlay_is_in_sync_with_the_extension():
    """Après une modification de l'extension : python publish_session.py"""
    for src, dst in ps.SYNCED:
        assert os.path.exists(dst), f"{dst} manquant — lancer publish_session.py"
        with open(src, "rb") as a, open(dst, "rb") as b:
            assert a.read() == b.read(), f"{os.path.relpath(dst, ROOT)} en retard — lancer publish_session.py"


def test_session_is_cleaned_and_sorted():
    s = ps.validate(_export([
        {"t": 55.0, "m": {"type": "fact_check_result", "id": "a1", "verdict": "vrai", "confiance": 80,
                          "explication": "e", "source": "Insee", "url": "https://www.insee.fr/", "extra": "x"}},
        {"t": 50.0, "m": {"type": "talking_points", "points": [POINT]}},
        {"t": 51.0, "m": {"type": "ping"}},                                        # inconnu : retiré
        {"t": 52.0, "m": {"type": "speaker_live", "speaker": "Intervenant A", "text": "…"}},
        {"t": None, "m": {"type": "speaker_live", "speaker": "Intervenant B"}},  # position inconnue
    ]))
    assert [e["m"]["type"] for e in s["events"]] == ["talking_points", "speaker_live", "fact_check_result"]
    point = s["events"][0]["m"]["points"][0]
    # horodatages unix du direct et champs internes retirés
    assert set(point) == {"id", "type", "texte", "qui", "qui_label", "citation", "vt"}
    assert s["events"][1]["m"] == {"type": "speaker_live", "speaker": "Intervenant A"}
    assert "extra" not in s["events"][2]["m"]


def test_everything_the_live_overlay_showed_is_kept():
    """Messages de la puce, arrêt et fin réels, verdicts de la finalisation."""
    s = ps.validate(_export([
        {"t": 50.0, "m": {"type": "talking_points", "points": [POINT]}},
        {"t": 60.0, "m": {"type": "mistral_rate_limited", "attempt": 1, "max": 3, "wait": 2}},
        {"t": 61.0, "m": {"type": "server_warning", "message": "Recherche web indisponible"}},
        {"t": 62.0, "m": {"type": "connection_status", "status": "reconnecting"}},
        {"t": 90.0, "m": {"action": "captureEnded", "reason": "user"}},
        {"t": 95.0, "m": {"type": "fact_check_result", "id": "a1", "verdict": "faux", "confiance": 70}},
        {"t": 99.0, "m": {"type": "session_done", "complete": True}},
    ]))
    kinds = [e["m"].get("type") or e["m"].get("action") for e in s["events"]]
    assert kinds == ["talking_points", "mistral_rate_limited", "server_warning", "connection_status",
                     "captureEnded", "fact_check_result", "session_done"]
    assert s["events"][4]["m"] == {"action": "captureEnded", "reason": "user"}


def test_offset_shifts_positions_and_statements():
    s = ps.validate(dict(_export([{"t": 50.0, "m": {"type": "talking_points", "points": [POINT]}}]), started=30.0),
                    offset=12.5)
    assert s["events"][0]["t"] == 62.5
    assert s["events"][0]["m"]["points"][0]["vt"] == 52.5
    assert s["started"] == 42.5  # l'overlay apparaît là où l'analyse a démarré


def test_invalid_exports_are_refused():
    for bad in ({"format": "autre"}, _export([], youtube="https://youtu.be/x"), _export([])):
        try:
            ps.validate(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepté à tort : {bad}")


def test_published_sessions_are_valid():
    index = ps.load_index()
    for entry in index.get("sessions", []):
        path = os.path.join(ps.SESSIONS, f"{entry['id']}.json")
        assert os.path.exists(path), f"{path} listé dans index.json mais absent"
        with open(path, encoding="utf-8") as f:
            published = json.load(f)
        assert ps.validate(published)["events"] == published["events"]  # déjà nettoyée


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

"""Petites tâches (classement par thème) : modèle local d'abord, API Mistral
en relais ; identification des voix arrêtée une fois les noms stables, et
garde-fou sur ses votes — sans modèle, réseau ni serveur.

Lancer : python tests/test_small_tasks.py   (ou python -m pytest tests)"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ni Whisper, ni empreintes vocales, ni socket.io
_app = types.ModuleType("server.app")
_app.DIARIZATION = True
_app.speaker_encoder = None
_app.socketio = types.SimpleNamespace(emit=lambda *a, **k: None)
sys.modules.setdefault("server.app", _app)

from server import factcheck, voices  # noqa: E402
from server.config import IDENT_GIVE_UP, MISTRAL_SMALL_MODEL  # noqa: E402
from server.state import session_map_state, session_map_votes, session_voice_locked  # noqa: E402


def test_small_tasks_use_the_local_model_then_fall_back_to_the_api():
    calls = []
    factcheck._local_down_until[0] = 0.0
    factcheck.call_local_llm = lambda prompt, model, timeout=None, json_mode=True: calls.append(("local", model)) or "{}"
    factcheck.call_mistral_api = lambda prompt, sid=None, model=None, timeout=None: calls.append(("api", model)) or "{}"
    factcheck.call_small_task("x")
    assert calls == [("local", factcheck.LOCAL_LLM_MODEL)]

    # Ollama éteint : l'API prend le relais, et on ne réessaie pas le local à chaque appel
    def down(*a, **k):
        calls.append(("local", "panne"))
        raise ConnectionError("Ollama éteint")
    factcheck.call_local_llm = down
    calls.clear()
    factcheck.call_small_task("x")
    factcheck.call_small_task("y")
    assert calls == [("local", "panne"), ("api", MISTRAL_SMALL_MODEL), ("api", MISTRAL_SMALL_MODEL)]
    factcheck._local_down_until[0] = 0.0


def test_identification_stops_once_names_are_settled():
    """Cas vécu : 188 appels d'identification sur un débat de 2 h, alors que
    les trois débatteurs étaient nommés depuis longtemps."""
    sid = "s1"
    labels = ["Intervenant A", "Intervenant B", "Intervenant C", "Intervenant D"]
    session_voice_locked[sid] = {"Intervenant D"}              # reconnu à la voix
    session_map_votes[sid] = {"Intervenant A": {"Gabriel Attal": 3},                   # stable
                              "Intervenant B": {"Jordan Bardella": 3, "Gabriel Attal": 2}}  # encore disputé
    session_map_state[sid] = {"flushes": 0, "inflight": False, "asked": {"Intervenant C": IDENT_GIVE_UP - 1}}
    assert voices.identification_pending(sid, labels) == ["Intervenant B", "Intervenant C"]
    # le présentateur, que personne ne sait nommer : abandonné après IDENT_GIVE_UP appels
    session_map_state[sid]["asked"]["Intervenant C"] = IDENT_GIVE_UP
    assert voices.identification_pending(sid, labels) == ["Intervenant B"]
    # une nouvelle voix relance l'identification
    assert voices.identification_pending(sid, labels + ["Intervenant E"]) == ["Intervenant B", "Intervenant E"]


def test_whoever_pronounces_a_name_is_not_that_person():
    """Erreur classique (modèle local, 6 cas sur 15) : « Olivier Faure, votre
    programme… » attribué à l'intervieweur. Le vote est écarté, sauf si
    l'on se présente soi-même."""
    excerpts = ["Intervenant A: Olivier Faure, le programme du Nouveau Front populaire prévoit un SMIC à 1 600 euros.\n"
                "Intervenant B: Oui, et nous l'assumons.",
                "Intervenant C: Bonsoir, je suis Marion Maréchal, et je veux parler d'immigration."]
    said = voices.said_by_label(excerpts)
    assert said["Intervenant A"].startswith("Olivier Faure, le programme")
    assert voices.speaker_named_in_citation("Olivier Faure", said["Intervenant A"])     # vote écarté
    assert not voices.speaker_named_in_citation("Olivier Faure", said["Intervenant B"])
    assert voices.introduces_self("Marion Maréchal", said["Intervenant C"])               # vote gardé
    assert not voices.introduces_self("Olivier Faure", said["Intervenant A"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

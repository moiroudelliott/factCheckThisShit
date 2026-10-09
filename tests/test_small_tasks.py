"""Petites tâches (classement par thème) : modèle local d'abord, API Mistral
en relais ; identification des voix arrêtée une fois les noms stables,
garde-fou sur ses votes, et nom retenu sans concurrent en fin de session —
sans modèle, réseau ni serveur.

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


def test_identification_prompt_gives_roles_and_the_guard_still_applies():
    """Cas vécu (débat LCI) : Manuel Bompard, que Thévenot interpelle
    (« votre prise de parole, M. Bompard ») et qui parle des « insoumis »,
    n'a reçu qu'un vote en une vingtaine d'appels. Le prompt donne la
    fonction des invités et dit que le nom revient aussi à celui à qui l'on
    répond ; le garde-fou du code écarte toujours le nom prononcé par le
    label lui-même."""
    sid = "lci"
    voices.session_speakers[sid] = types.SimpleNamespace(sums=[0, 0, 0, 0], counts=[3, 3, 3, 3])
    voices.session_excerpts[sid] = [
        "Intervenant C: Le gouvernement a supprimé des milliers de postes, voilà la réalité.\n"
        "Intervenant D: Je suis assez étonnée par votre prise de parole, M. Bompard, ça n'appelait à aucune polémique.",
        "Intervenant C: Madame Thévenot, nous, les insoumis, on a au moins un mérite.",
    ]
    voices.session_contexts[sid] = {"emission": "LCI", "guests": ["Prisca Thévenot", "Manuel Bompard", "Darius Rochebin"],
                                    "roles": {"Manuel Bompard": "député LFI", "Darius Rochebin": "présentateur"}}
    voices.session_speaker_map[sid], voices.session_map_votes[sid] = {}, {}
    voices.session_voice_locked[sid] = set()
    voices.session_map_state[sid] = {"flushes": 0, "inflight": True}
    prompts = []
    real_call, real_enroll = voices.call_mistral_api, voices.auto_enroll_voices
    voices.auto_enroll_voices = lambda sid: None  # jamais d'écriture dans la vraie banque de voix
    voices.call_mistral_api = lambda prompt, **k: prompts.append(prompt) or (
        '{"Intervenant C": "Bompard", "Intervenant D": "Manuel Bompard"}')
    try:
        voices.identify_speakers(sid)
    finally:
        voices.call_mistral_api, voices.auto_enroll_voices = real_call, real_enroll
    assert "Manuel Bompard (député LFI)" in prompts[0] and "Darius Rochebin (présentateur)" in prompts[0]
    assert "celui à qui il répond" in prompts[0]
    # C : vote compté (nom ramené à sa forme complète) ; D prononce « M. Bompard » : vote écarté
    assert voices.session_map_votes[sid] == {"Intervenant C": {"Manuel Bompard": 1}}
    assert voices.session_map_state[sid]["inflight"] is False


def test_a_lone_uncontested_vote_names_the_label_at_the_end():
    """Fin de session : un label dont l'unique vote va à un invité déclaré,
    que nul autre label ne porte ni ne dispute, reçoit ce nom (sans appel)."""
    guests = ["Prisca Thévenot", "Jean-Philippe Tanguy", "Manuel Bompard", "Darius Rochebin"]
    confirmed = {"Intervenant A": "Darius Rochebin", "Intervenant B": "Jean-Philippe Tanguy",
                 "Intervenant D": "Prisca Thévenot"}
    votes = {"Intervenant A": {"Darius Rochebin": 5, "Manuel Bompard": 1},  # nom confirmé ailleurs : pas un concurrent
             "Intervenant B": {"Jean-Philippe Tanguy": 4}, "Intervenant D": {"Prisca Thévenot": 3},
             "Intervenant C": {"Manuel Bompard": 1}}
    assert voices.names_by_elimination(votes, confirmed, set(), guests) == {"Intervenant C": "Manuel Bompard"}
    # deux noms candidats : rien
    assert voices.names_by_elimination({**votes, "Intervenant C": {"Manuel Bompard": 1, "Prisca Thévenot": 1}},
                                       confirmed, set(), guests) == {}
    # nom déjà porté par un autre label
    assert voices.names_by_elimination({**votes, "Intervenant C": {"Prisca Thévenot": 1}},
                                       confirmed, set(), guests) == {}
    # disputé par un autre label resté anonyme
    assert voices.names_by_elimination({**votes, "Intervenant E": {"Manuel Bompard": 1}},
                                       confirmed, set(), guests) == {}
    # invité non déclaré (cas vécu : « François Ruffin », absent de l'émission), ou label reconnu à la voix
    assert voices.names_by_elimination({"Intervenant C": {"François Ruffin": 1}}, {}, set(), guests) == {}
    assert voices.names_by_elimination(votes, confirmed, set(), [g for g in guests if g != "Manuel Bompard"]) == {}
    assert voices.names_by_elimination(votes, confirmed, {"Intervenant C"}, guests) == {}

    # Appliquée à la session : la correspondance est complétée et émise
    sid, emitted = "fin", []
    voices.session_contexts[sid] = {"guests": guests}
    voices.session_speaker_map[sid] = dict(confirmed)
    voices.session_map_votes[sid] = votes
    voices.session_voice_locked[sid] = set()
    real_emit = voices.socketio.emit
    voices.socketio.emit = lambda event, data=None, **k: emitted.append((event, data))
    try:
        assert voices.settle_by_elimination(sid) == {"Intervenant C": "Manuel Bompard"}
    finally:
        voices.socketio.emit = real_emit
    assert voices.session_speaker_map[sid]["Intervenant C"] == "Manuel Bompard"
    assert emitted and emitted[0][0] == "speaker_map" and emitted[0][1]["map"]["Intervenant C"] == "Manuel Bompard"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

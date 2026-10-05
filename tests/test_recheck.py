"""Contre-vérification des « faux » (server/factcheck.recheck_false) et phrase
de source exigée (server/sources.quoted_in) — réponses de Mistral simulées,
sans réseau ni serveur.

Lancer : python tests/test_recheck.py   (ou python -m pytest tests)"""

import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Vérification seule : ni Whisper ni socket.io
_app = types.ModuleType("server.app")
_app.DIARIZATION = True
_app.socketio = types.SimpleNamespace(emit=lambda *a, **k: None)
sys.modules.setdefault("server.app", _app)

from server import factcheck  # noqa: E402
from server.sources import quoted_in  # noqa: E402

LCP = "https://lcp.fr/actualites/legislatives-2024"
EVIDENCE = {
    "results": [{"title": "Législatives 2024 : les équilibres", "href": LCP,
                 "body": "Le groupe LFI passe de 75 députés avant la dissolution à 71 après les législatives."}],
    "academic": [], "official": [], "known": [], "series": [], "ballots": [],
}
CLAIM = "Il y a moins de députés LFI qu'avant la dissolution"
FIRST = {"verdict": "faux", "confiance": 90, "url": LCP, "source": "LCP",
         "explication": "LFI compte 71 députés après les législatives.", "contredit_par": "LCP : 71 députés LFI en 2024"}


def _judge(*answers):
    """Rejoue judge() avec les réponses successives de Mistral (verdict, puis contre-vérification)."""
    replies = iter(json.dumps(a, ensure_ascii=False) for a in answers)
    factcheck.call_mistral_api = lambda prompt, **k: next(replies)
    return factcheck.judge(CLAIM, json.loads(json.dumps(EVIDENCE)), {}, qui="Gabriel Attal", periode="2024-2026")


def test_quote_must_be_in_the_evidence():
    text = "[1] [PRESSE ÉTABLIE] Titre — Le groupe LFI passe de 75 députés avant la dissolution à 71."
    assert quoted_in("le groupe LFI passe de 75 députés", text)
    assert quoted_in("« Le groupe LFI passe de 75 députés avant la dissolution… à 71 »", text)  # coupure
    assert not quoted_in("Le groupe LFI passe de 70 députés", text)       # chiffre changé
    assert not quoted_in("seul le budget 2026 a été adopté par le 49.3", text)  # mémoire du modèle
    assert not quoted_in("LFI passe", text)                               # trop court pour prouver


def test_false_stays_only_if_the_blind_review_contradicts_with_a_real_quote():
    phrase = "Le groupe LFI passe de 75 députés avant la dissolution à 71 après les législatives."
    assert _judge(FIRST, {"relation": "contredit", "phrase_source": phrase})["verdict"] == "faux"
    # citation introuvable dans les preuves : jamais « faux »
    r = _judge(FIRST, {"relation": "contredit", "phrase_source": "LFI a gagné des sièges en 2024"})
    assert r["verdict"] == "non_verifiable"
    # la source donne raison à l'orateur
    r = _judge(FIRST, {"relation": "confirme", "phrase_source": phrase, "explication": "75 puis 71 : baisse."})
    assert r["verdict"] == "vrai" and r["explication"] == "75 puis 71 : baisse."
    assert _judge(FIRST, {"relation": "nuance", "phrase_source": phrase})["verdict"] == "partiellement_vrai"
    assert _judge(FIRST, {"relation": "ne_tranche_pas", "phrase_source": ""})["verdict"] == "non_verifiable"


def test_other_verdicts_are_not_rechecked():
    calls = []
    factcheck.call_mistral_api = lambda prompt, **k: calls.append(prompt) or json.dumps(
        {"verdict": "vrai", "confiance": 90, "url": LCP, "explication": "Baisse de 75 à 71."})
    assert factcheck.judge(CLAIM, json.loads(json.dumps(EVIDENCE)), {})["verdict"] == "vrai"
    assert len(calls) == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")

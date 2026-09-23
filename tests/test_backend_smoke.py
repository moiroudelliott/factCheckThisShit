"""Test de fumée du backend complet, SANS GPU ni réseau : Whisper, ECAPA,
Mistral et la recherche sont simulés, mais les vrais handlers Socket.IO,
le buffer, la dédup, le fact-check, le cache et l'arrêt propre tournent.

Lancer : python tests/test_backend_smoke.py   (ou python -m pytest tests)

Aucun fichier du dépôt n'est touché : cache et banque de voix vivent dans
un dossier temporaire."""

import json
import os
import sys
import tempfile
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="fct_smoke_")
os.environ["FACTCHECK_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["MISTRAL_API_KEY"] = "test"  # load_dotenv n'écrase pas une variable déjà définie
os.environ["BACKEND_TOKEN"] = ""

import server.config as cfg  # noqa: E402
cfg.VOICES_DIR = os.path.join(TMP, "voices")  # banque de voix jetable

# ── Whisper simulé : chaque chunk renvoie la réplique suivante du script ──
SCRIPT = [
    "Le chômage a baissé de deux points depuis 2017 et nous avons créé un million d'emplois dans l'industrie.",
    "La dette publique atteint 3 000 milliards d'euros, c'est un record historique pour notre pays et nos enfants.",
    "Le budget de la défense atteint 50 milliards d'euros en 2025, et il faudra encore l'augmenter fortement.",
    "Nous devons protéger les Français, voilà notre engagement pour les prochaines années de mandat.",
]


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _FakeWhisper:
    calls = 0

    def __init__(self, *a, **k):
        pass

    def transcribe(self, path, **k):
        i = _FakeWhisper.calls
        _FakeWhisper.calls += 1
        segs = [_Seg(0.2, 6.0, SCRIPT[i])] if i < len(SCRIPT) else []
        return iter(segs), None


fw = types.ModuleType("faster_whisper")
fw_audio = types.ModuleType("faster_whisper.audio")
fw.WhisperModel = _FakeWhisper
fw_audio.decode_audio = lambda path: (np.random.RandomState(1).randn(16000 * 10) * 0.1).astype(np.float32)
fw.audio = fw_audio
sys.modules["faster_whisper"] = fw
sys.modules["faster_whisper.audio"] = fw_audio

# ── ECAPA simulé : une seule voix ─────────────────────────────────────────
import torch  # noqa: E402

sb, sb_inf, sb_spk = (types.ModuleType(n) for n in
                      ("speechbrain", "speechbrain.inference", "speechbrain.inference.speaker"))


class _FakeEncoder:
    @classmethod
    def from_hparams(cls, **k):
        return cls()

    def encode_batch(self, t):
        return torch.ones(1, 1, 192)


sb_spk.EncoderClassifier = _FakeEncoder
sys.modules.update({"speechbrain": sb, "speechbrain.inference": sb_inf, "speechbrain.inference.speaker": sb_spk})

# ── Mistral + recherche simulés ───────────────────────────────────────────
import requests  # noqa: E402

LEMONDE = "https://www.lemonde.fr/economie/chomage-2017"
PROMPTS = []


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.headers = payload, status, {}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def _fake_post(url, json=None, **k):
    prompt = json["messages"][0]["content"]
    PROMPTS.append(prompt)
    if "Extrais les talking points" in prompt:
        content = [
            {"type": "affirmation", "texte": "Le chômage a baissé de 2 points depuis 2017", "qui": "Intervenant A"},
            {"type": "affirmation", "texte": "Le chômage a augmenté depuis 2017", "qui": "Intervenant A"},
            {"type": "subjectif", "texte": "Il faut protéger les Français", "qui": "Intervenant A"},
        ]
    elif "fact-checker" in prompt:
        content = {"verdict": "Partiellement vrai", "confiance": 80, "explication": "Selon l'Insee…",
                   "source": "INSEE, Le Monde", "url": LEMONDE}
    elif "identifies les locuteurs" in prompt:
        content = {"Intervenant A": "Bardella"}
    else:
        content = {"intervenants": []}
    return _Resp({"choices": [{"message": {"content": __import__("json").dumps(content, ensure_ascii=False)}}]})


def _fake_get(url, params=None, **k):
    if "/search" in url and "archives-ouvertes" not in url:
        return _Resp({"results": [
            {"url": "https://www.facebook.com/page/posts/1", "title": "Post", "content": "…"},
            {"url": LEMONDE, "title": "Chômage : les chiffres", "content": "Le taux a baissé…"},
        ]})
    return _Resp({})


requests.post = _fake_post
requests.get = _fake_get

import eventlet  # noqa: E402
from server.routes import app, socketio  # noqa: E402


def _wait_for(client, event, timeout=15):
    got = []
    for _ in range(int(timeout / 0.1)):
        eventlet.sleep(0.1)
        got += client.get_received()
        if any(m["name"] == event for m in got):
            return got
    raise AssertionError(f"{event} jamais reçu — reçu: {[m['name'] for m in got]}")


def test_full_session():
    client = socketio.test_client(app)
    assert client.is_connected()
    client.emit("set_context", {"emission": "Débat", "guests": "Jordan Bardella\nGabriel Attal",
                                "date": "2024-06-27", "description": ""})
    client.emit("start_transcription")
    for _ in range(len(SCRIPT)):
        client.emit("audio_chunk", b"\x00" * 64)
    client.emit("stop_transcription")
    got = _wait_for(client, "session_done", timeout=20)
    names = [m["name"] for m in got]

    segments = [m["args"][0] for m in got if m["name"] == "transcript_segment"]
    assert len(segments) == len(SCRIPT) and all(s["speaker"] == "Intervenant A" for s in segments)

    points = [p for m in got if m["name"] == "talking_points" for p in m["args"][0]["points"]]
    textes = [p["texte"] for p in points]
    # la contre-affirmation n'est plus jetée comme doublon
    assert "Le chômage a baissé de 2 points depuis 2017" in textes
    assert "Le chômage a augmenté depuis 2017" in textes

    results = [m["args"][0] for m in got if m["name"] == "fact_check_result"]
    assert results, names
    r = results[0]
    assert r["verdict"] == "partiellement_vrai"          # normalisé
    assert r["url"] == LEMONDE and r["source"] == "Le Monde"  # nom cohérent avec le lien
    evidence = next(p for p in PROMPTS if "fact-checker" in p)
    assert "facebook.com" not in evidence                # réseau social exclu
    assert "2024" in json.dumps(evidence, ensure_ascii=False)  # replay : année de la vidéo

    done = next(m["args"][0] for m in got if m["name"] == "session_done")
    assert done["complete"] is True
    client.disconnect()


def test_only_the_extension_origin_is_accepted():
    http = app.test_client()
    ext = "chrome-extension://" + "a" * 32
    evil = "https://site-quelconque.example"
    # Préflight CORS de /analyze_video : autorisé pour l'extension seulement
    ok = http.options("/analyze_video", headers={"Origin": ext, "Access-Control-Request-Method": "POST"})
    ko = http.options("/analyze_video", headers={"Origin": evil, "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("Access-Control-Allow-Origin") == ext
    assert "Access-Control-Allow-Origin" not in ko.headers
    # Handshake Socket.IO depuis une page tierce : refusé
    r = http.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": evil})
    assert r.status_code == 400
    r = http.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": ext})
    assert r.status_code == 200


if __name__ == "__main__":
    test_full_session()
    print("ok  test_full_session")
    test_only_the_extension_origin_is_accepted()
    print("ok  test_only_the_extension_origin_is_accepted")

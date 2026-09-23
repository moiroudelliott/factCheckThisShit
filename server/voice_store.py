"""Stockage de la banque d'empreintes vocales (voices/index.json + un .npy
par personne) — partagé par le backend (auto-enrôlement) et les outils CLI
(enroll.py, harvest_voices.py, remove_voice.py), qui avaient chacun leur
copie de ce code.

- index.json est relu juste avant chaque écriture puis remplacé
  atomiquement : lancer harvest_voices.py pendant un débat ne fait plus
  perdre une entrée écrite entre-temps par le backend.
- deux noms au même slug (« Éric Zemmour » / « Eric Zemmour ») n'écrasent
  plus le même fichier .npy."""

import json
import os
import re
import tempfile
import time
import unicodedata

import numpy as np

from server.config import VOICES_DIR

INDEX_PATH = os.path.join(VOICES_DIR, "index.json")


def slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "voix"


def load_index() -> dict:
    if not os.path.exists(INDEX_PATH):
        return {}
    try:
        with open(INDEX_PATH, encoding="utf-8") as f:
            index = json.load(f)
        return index if isinstance(index, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_index(index: dict):
    os.makedirs(VOICES_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=VOICES_DIR, prefix=".index-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INDEX_PATH)


def save_embedding(name: str, emb, **meta) -> str:
    """Enregistre (ou remplace) l'empreinte de `name`. Retourne le fichier .npy."""
    os.makedirs(VOICES_DIR, exist_ok=True)
    index = load_index()
    fn = (index.get(name) or {}).get("file")
    if not fn:
        used = {m.get("file") for n, m in index.items() if n != name}
        base, i = slug(name), 2
        fn = base + ".npy"
        while fn in used:
            fn, i = f"{base}-{i}.npy", i + 1
    np.save(os.path.join(VOICES_DIR, fn), emb)
    index = load_index()  # relu : ne pas écraser une entrée ajoutée entre-temps
    index[name] = {"file": fn, **meta, "updated": time.time()}
    _write_index(index)
    return fn


def remove(name: str) -> bool:
    index = load_index()
    meta = index.pop(name, None)
    if meta is None:
        return False
    npy = os.path.join(VOICES_DIR, meta.get("file", ""))
    if meta.get("file") and os.path.exists(npy) \
            and not any(m.get("file") == meta["file"] for m in index.values()):
        os.remove(npy)
    _write_index(index)
    return True

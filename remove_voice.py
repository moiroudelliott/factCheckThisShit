"""Retire une empreinte vocale de la banque (voices/) — utile pour retester
l'identification (LLM, "non identifié") sur quelqu'un déjà enrôlé.

Usage:
    python remove_voice.py "Jordan Bardella"
    python remove_voice.py --list

Le nom doit correspondre exactement à une clé de voices/index.json (voir
--list). Supprime l'entrée de l'index ET le fichier .npy associé. Effectif au
prochain démarrage du backend.
"""

import sys
import os
import json

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
INDEX_PATH = os.path.join(VOICES_DIR, "index.json")


def load_index() -> dict:
    if not os.path.exists(INDEX_PATH):
        return {}
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    index = load_index()

    if sys.argv[1] == "--list":
        if not index:
            print("(banque vide)")
        for name in sorted(index):
            print(f"  {name}")
        return

    name = sys.argv[1].strip()
    if name not in index:
        print(f"✗ \"{name}\" n'est pas dans la banque. Noms disponibles (--list):")
        for n in sorted(index):
            print(f"  {n}")
        sys.exit(1)

    meta = index.pop(name)
    npy_path = os.path.join(VOICES_DIR, meta.get("file", ""))
    if os.path.exists(npy_path):
        os.remove(npy_path)

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"✔ \"{name}\" retiré de la banque ({len(index)} restante(s)).")
    print("  Effectif au prochain démarrage du backend.")


if __name__ == "__main__":
    main()

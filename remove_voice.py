"""Retire une empreinte vocale de la banque (voices/) — utile pour retester
l'identification (LLM, "non identifié") sur quelqu'un déjà enrôlé, ou pour
effacer une empreinte enregistrée sous le mauvais nom.

Usage:
    python remove_voice.py "Jordan Bardella"
    python remove_voice.py --list

Le nom doit correspondre exactement à une clé de voices/index.json (voir
--list). Supprime l'entrée de l'index ET le fichier .npy associé. Pris en
compte à la prochaine connexion de l'extension (la banque est rechargée à
chaque connexion, inutile de redémarrer le backend).
"""

import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from server import voice_store  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    index = voice_store.load_index()

    if sys.argv[1] == "--list":
        if not index:
            print("(banque vide)")
        for name in sorted(index):
            print(f"  {name}")
        return

    name = sys.argv[1].strip()
    if not voice_store.remove(name):
        print(f"✗ \"{name}\" n'est pas dans la banque. Noms disponibles (--list):")
        for n in sorted(index):
            print(f"  {n}")
        sys.exit(1)

    print(f"✔ \"{name}\" retiré de la banque ({len(index) - 1} restante(s)).")
    print("  Pris en compte à la prochaine connexion de l'extension.")


if __name__ == "__main__":
    main()

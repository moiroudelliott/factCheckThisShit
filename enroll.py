"""Enrôle l'empreinte vocale d'une personnalité dans la banque de voix (voices/).

Usage:
    python enroll.py "Gabriel Attal" audio.mp3 [debut_s] [duree_s]

L'audio peut être dans n'importe quel format (mp3, wav, mp4, webm, m4a…).
Choisir un passage de 30-60 secondes où SEULE la personne parle
(interview posée, discours — pas un débat avec brouhaha).

Exemple:
    python enroll.py "Jordan Bardella" itw_bardella.mp4 15 45
    → empreinte calculée sur les secondes 15 à 60 du fichier.
"""

import sys
import os

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

SR = 16000
SLICE_S = 5  # l'empreinte finale = moyenne d'embeddings par tranches de 5 s (plus robuste)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    name = sys.argv[1].strip()
    path = sys.argv[2]
    start = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    dur = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0

    if not os.path.exists(path):
        print(f"✗ Fichier introuvable: {path}")
        sys.exit(1)

    print("Chargement des modèles…")
    from faster_whisper.audio import decode_audio
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    wav = decode_audio(path)
    seg = wav[int(start * SR):int((start + dur) * SR)]
    if len(seg) < SR * 10:
        print(f"✗ Seulement {len(seg) / SR:.1f} s d'audio utilisable — il en faut au moins 10.")
        sys.exit(1)

    enc = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": "cpu"},
    )

    embs = []
    step = SR * SLICE_S
    with torch.no_grad():
        for off in range(0, len(seg) - step + 1, step):
            piece = seg[off:off + step]
            e = enc.encode_batch(torch.from_numpy(piece).float().unsqueeze(0)).squeeze().cpu().numpy()
            embs.append(e / (np.linalg.norm(e) + 1e-8))
    emb = np.mean(embs, axis=0)

    from server import voice_store
    voice_store.save_embedding(name, emb, auto=False)

    print(f"✔ Empreinte enregistrée: {name} ({len(embs)} tranche(s) de {SLICE_S} s)")
    print("  Prise en compte à la prochaine connexion de l'extension (inutile de redémarrer le backend).")


if __name__ == "__main__":
    main()

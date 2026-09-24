"""Alimente automatiquement la banque de voix (voices/) depuis YouTube.

Pour chaque personnalité, le programme :
 1. cherche des interviews sur YouTube (yt-dlp, sans clé API),
 2. télécharge l'audio de plusieurs vidéos DIFFÉRENTES,
 3. sépare les voix de chaque vidéo (embeddings ECAPA + clustering),
 4. isole LA voix commune à toutes les vidéos — les journalistes changent
    d'une interview à l'autre, la personnalité est la seule constante,
 5. enregistre l'empreinte dans voices/ (chargée par backend.py à la
    prochaine session).

Usage:
    python harvest_voices.py "Gabriel Attal"
    python harvest_voices.py "Gabriel Attal" "Marine Le Pen" "Jordan Bardella"
    python harvest_voices.py --liste politiciens.txt      # un nom par ligne
    python harvest_voices.py "Nom" --videos 3 --force
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

from server import voice_store

# Console Windows en cp1252 : forcer l'UTF-8 pour ne pas planter sur ✔/─/↓
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SR = 16000
WIN_S = 3.0          # fenêtre d'analyse vocale
CLUSTER_T = 0.42     # seuil de clustering intra-vidéo
CROSS_T = 0.50       # seuil de correspondance de voix ENTRE vidéos
MIN_SHARE = 0.12     # part de parole min pour qu'un cluster soit candidat
AUDIO_START_S = 60   # on saute l'intro/jingle
AUDIO_MAX_S = 420    # ~6 minutes d'audio analysées par vidéo


def safe(s: str) -> str:
    """Texte imprimable sans risque sur une console Windows cp1252."""
    return str(s).encode("ascii", "replace").decode()


# YouTube exige de résoudre des défis JavaScript (paquet yt-dlp-ejs + un
# moteur JS, Deno ou Node.js) : sans eux, téléchargement refusé (HTTP 403)
_YTDLP_BASE = ["--js-runtimes", "deno", "--js-runtimes", "node"]
_force_ipv4 = None


def ytdlp(*args, timeout=180):
    global _force_ipv4
    if _force_ipv4 is None:  # IPv6 annoncé mais cassé : 8 à 40 s perdues par connexion (server/network.py)
        from server.network import ipv6_works
        _force_ipv4 = ipv6_works() is False
    try:
        return subprocess.run(
            [sys.executable, "-m", "yt_dlp", *_YTDLP_BASE, *(["-4"] if _force_ipv4 else []), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "timeout")


def search_interviews(name: str, pool: int) -> list:
    """Vidéos candidates (4-60 min) via plusieurs requêtes, avec UNE vidéo max
    par chaîne : des chaînes différentes = des journalistes différents, donc
    une intersection de voix plus discriminante."""
    seen, chans, out = set(), set(), []
    for query in (f"{name} interview", f"{name} invité"):
        r = ytdlp(f"ytsearch{pool * 2}:{query}", "--flat-playlist",
                  "--print", "%(id)s|%(duration)s|%(channel)s|%(title)s")
        for line in (r.stdout or "").splitlines():
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            vid, dur, chan, title = parts
            try:
                dur = float(dur)
            except (TypeError, ValueError):
                continue
            if not (240 <= dur <= 3600) or vid in seen:
                continue
            seen.add(vid)
            if chan and chan in chans:
                continue
            chans.add(chan)
            out.append((vid, title))
            if len(out) >= pool:
                return out
    return out


def download_audio(vid: str, dest_dir: str):
    out = os.path.join(dest_dir, f"{vid}.audio")
    ytdlp(f"https://www.youtube.com/watch?v={vid}", "-f", "bestaudio/best",
          "-o", out, "--quiet", "--no-warnings", "--no-playlist")
    for fn in os.listdir(dest_dir):
        if fn.startswith(f"{vid}.audio"):
            return os.path.join(dest_dir, fn)
    return None


def analyse_video(path: str, enc, torch) -> list:
    """Sépare les voix d'une vidéo. Retourne [(centroïde normalisé, part de parole)]."""
    from faster_whisper.audio import decode_audio
    wav = decode_audio(path)
    wav = wav[AUDIO_START_S * SR:AUDIO_MAX_S * SR]
    if len(wav) < SR * 60:
        return []

    step = int(WIN_S * SR)
    wins = [wav[o:o + step] for o in range(0, len(wav) - step + 1, step)]
    rms = np.array([float(np.sqrt((w ** 2).mean())) for w in wins])
    med = float(np.median(rms[rms > 0])) if np.any(rms > 0) else 0.0
    voiced = [w for w, r in zip(wins, rms) if med and r >= 0.25 * med]

    sums, counts = [], []
    with torch.no_grad():
        for w in voiced:
            e = enc.encode_batch(torch.from_numpy(w).float().unsqueeze(0)).squeeze().cpu().numpy()
            e = e / (np.linalg.norm(e) + 1e-8)
            best, best_sim = -1, -1.0
            for i, (s, c) in enumerate(zip(sums, counts)):
                cen = s / c
                cen = cen / (np.linalg.norm(cen) + 1e-8)
                sim = float(np.dot(e, cen))
                if sim > best_sim:
                    best, best_sim = i, sim
            if best >= 0 and best_sim >= CLUSTER_T:
                sums[best] += e
                counts[best] += 1
            else:
                sums.append(e.copy())
                counts.append(1)

    total = sum(counts) or 1
    clusters = []
    for s, c in zip(sums, counts):
        if c / total >= MIN_SHARE:
            cen = s / c
            clusters.append((cen / (np.linalg.norm(cen) + 1e-8), c / total))
    return clusters


def common_voice(per_video: list):
    """La voix présente dans plusieurs vidéos = la personnalité.
    Chaque vidéo sert tour à tour d'ancre : une vidéo hors-sujet (quelqu'un qui
    parle DE la personne) ne peut plus faire échouer la détection.
    Retourne (embedding, nb de vidéos où la voix apparaît)."""
    per_video = [p for p in per_video if p]
    if not per_video:
        return None, 0
    if len(per_video) == 1:
        cen, _ = max(per_video[0], key=lambda cs: cs[1])
        return cen, 1

    # Voix requise dans ≥2 vidéos (chaînes différentes → journalistes
    # différents), ≥3 quand on a beaucoup de matière
    required = 1 if len(per_video) <= 3 else 2
    best_emb, best_key = None, (-1, -1.0)
    for a, anchor in enumerate(per_video):
        for c0, share0 in anchor:
            matched, shares = [c0], share0
            for b, other in enumerate(per_video):
                if b == a:
                    continue
                cand = max(other, key=lambda cs: float(np.dot(c0, cs[0])))
                if float(np.dot(c0, cand[0])) >= CROSS_T:
                    matched.append(cand[0])
                    shares += cand[1]
            n_match = len(matched) - 1
            if n_match >= required and (n_match, shares) > best_key:
                emb = np.mean(matched, axis=0)
                best_emb = emb / (np.linalg.norm(emb) + 1e-8)
                best_key = (n_match, shares)
    return best_emb, (best_key[0] + 1 if best_emb is not None else 0)


def save_voice(name: str, emb):
    voice_store.save_embedding(name, emb, auto=True, via="harvest")


def already_enrolled(name: str) -> bool:
    return name in voice_store.load_index()


def harvest(name: str, n_videos: int, enc, torch) -> bool:
    print(f"\n── {safe(name)} " + "─" * max(1, 50 - len(name)))
    pool = search_interviews(name, max(6, n_videos * 2))
    if not pool:
        print("  ✗ aucune interview trouvée")
        return False
    tmp = tempfile.mkdtemp(prefix="fct_harvest_")
    try:
        per_video = []
        emb, n_seen = None, 0
        max_analysed = n_videos + 2  # marge pour les vidéos hors-sujet

        for vid, title in pool:
            if len(per_video) >= max_analysed:
                break
            # Assez de matière ? tenter la voix commune avant de télécharger plus
            if len(per_video) >= min(n_videos, 2):
                emb, n_seen = common_voice(per_video)
                if emb is not None and n_seen >= 2:
                    break
            print(f"  ↓ {vid} — {safe(title)[:60]}")
            path = download_audio(vid, tmp)
            if not path:
                print("    (échec du téléchargement, ignorée)")
                continue
            clusters = analyse_video(path, enc, torch)
            print(f"    {len(clusters)} voix distincte(s) détectée(s)")
            if clusters:
                per_video.append(clusters)
            time.sleep(1)

        if emb is None or n_seen < 2:
            emb, n_seen = common_voice(per_video)

        if emb is None:
            print("  ✗ aucune voix commune fiable entre les vidéos — personne non enrôlée")
            return False
        if n_seen < 2 and len(per_video) > 1:
            print("  ✗ voix commune introuvable — personne non enrôlée")
            return False
        if n_seen < 2:
            print("  ⚠ une seule vidéo exploitable — empreinte enregistrée avec fiabilité réduite")
        save_voice(name, emb)
        print(f"  ✔ empreinte enregistrée (voix confirmée dans {n_seen} vidéo(s))")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def harvest_from_url(name: str, url: str, enc, torch) -> bool:
    """Enrôlement depuis une vidéo choisie à la main : la voix DOMINANTE est
    prise pour la personnalité. Utiliser une interview posée où la personne
    parle la majorité du temps (pas un débat)."""
    print(f"\n── {safe(name)} (URL fournie) " + "─" * 30)
    tmp = tempfile.mkdtemp(prefix="fct_harvest_")
    try:
        out = os.path.join(tmp, "manual.audio")
        ytdlp(url, "-f", "bestaudio/best", "-o", out,
              "--quiet", "--no-warnings", "--no-playlist", timeout=300)
        path = None
        for fn in os.listdir(tmp):
            if fn.startswith("manual.audio"):
                path = os.path.join(tmp, fn)
                break
        if not path:
            print("  ✗ échec du téléchargement")
            return False
        clusters = analyse_video(path, enc, torch)
        if not clusters:
            print("  ✗ pas assez d'audio exploitable")
            return False
        cen, share = max(clusters, key=lambda cs: cs[1])
        save_voice(name, cen)
        print(f"  ✔ empreinte enregistrée (voix dominante : {share * 100:.0f}% du temps de parole)")
        if share < 0.5:
            print("  ⚠ cette voix parle moins de la moitié du temps — vérifie que c'est bien une interview de la personne")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Alimente la banque de voix depuis YouTube.")
    ap.add_argument("noms", nargs="*", help="Personnalités à enrôler")
    ap.add_argument("--liste", help="Fichier texte, un nom par ligne (# = commentaire)")
    ap.add_argument("--videos", type=int, default=3, help="Vidéos analysées par personne (défaut 3)")
    ap.add_argument("--url", help="Enrôler depuis cette vidéo précise (un seul nom requis)")
    ap.add_argument("--force", action="store_true", help="Ré-enrôle même si déjà en banque")
    args = ap.parse_args()

    names = list(args.noms)
    if args.liste:
        with open(args.liste, encoding="utf-8") as f:
            names += [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    if not names:
        ap.print_help()
        sys.exit(1)

    print("Chargement du modèle de voix (ECAPA, CPU)…")
    import torch
    from speechbrain.inference.speaker import EncoderClassifier
    enc = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cpu"})

    if args.url:
        if len(names) != 1:
            print("✗ --url demande exactement un nom")
            sys.exit(1)
        sys.exit(0 if harvest_from_url(names[0], args.url, enc, torch) else 1)

    done = skipped = failed = 0
    for name in names:
        if not args.force and already_enrolled(name):
            print(f"— {safe(name)}: déjà en banque (utiliser --force pour refaire)")
            skipped += 1
            continue
        try:
            if harvest(name, args.videos, enc, torch):
                done += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ erreur: {type(e).__name__}: {safe(e)}")
            failed += 1

    print(f"\nBilan: {done} enrôlée(s), {skipped} déjà en banque, {failed} échec(s).")
    if done:
        print("Les nouvelles empreintes seront chargées à la prochaine session du backend.")


if __name__ == "__main__":
    main()

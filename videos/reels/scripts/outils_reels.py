"""Outils des réels « moment » : préparer un extrait, écrire la légende, vérifier un réel.

    python scripts/build_reels.py extrait <id YouTube> <début s> <durée s> <nom>
    python scripts/build_reels.py legende <réel>
    python scripts/build_reels.py verifier <réel>
    python scripts/build_reels.py rendre <réel>      # construit, lint, rend dans renders/a-publier
    python scripts/build_reels.py etat               # à publier / déjà en ligne

extrait  : télécharge le passage (yt-dlp), le réencode avec une image clé par seconde
           (HyperFrames cale l'image exacte) et le transcrit mot à mot (Whisper, en local).
           Fichiers hors dépôt : assets/clips/<nom>.mp4, assets/words/<nom>.words.json.
legende  : écrit social/legendes/<réel>.txt au format convenu, depuis la fiche publiée
           (explication et source de l'IA telles quelles, mention de l'IA).
verifier : contrôle ce qu'un script ne garantit pas — la citation au mot près, le début
           de l'extrait (pas de bout de mot), les changements de plan pendant la phrase,
           la légende — et fait une planche du cadrage à regarder : renders/verif/<réel>.jpg.
"""
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave

import build_reels as b

ROOT = b.ROOT
LEGENDES = os.path.normpath(os.path.join(ROOT, "..", "..", "social", "legendes"))
REGISTRE = os.path.normpath(os.path.join(ROOT, "..", "..", "social", "publications.json"))
A_PUBLIER, PUBLIEES = os.path.join(ROOT, "renders", "a-publier"), os.path.join(ROOT, "renders", "publiees")
VERDICT_LEGENDE = {"vrai": "✅ Vrai", "faux": "❌ Faux", "trompeur": "⚠️ Trompeur", "partiellement_vrai": "◐ Partiellement vrai"}


# ── Extrait ──────────────────────────────────────────────────────────────────
LOUD = "I=-16:TP=-1.5:LRA=11"                              # même niveau que les extraits existants (−16 LUFS)


def _loudnorm_args(src):
    """Filtre de normalisation en deux passes (mesure, puis gain linéaire) vers −16 LUFS."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-af", f"loudnorm={LOUD}:print_format=json",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(out[out.rindex("{"):out.rindex("}") + 1])
    return (f"loudnorm={LOUD}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def normaliser(nom):
    """Remet le son d'un extrait déjà téléchargé à −16 LUFS (vidéo inchangée)."""
    path = os.path.join(ROOT, "assets", "clips", f"{nom}.mp4")
    tmp = path + ".tmp.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c:v", "copy", "-af", _loudnorm_args(path),
                    "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, path)
    print(f"✓ assets/clips/{nom}.mp4 : son normalisé à −16 LUFS")


def extrait(vid, debut, duree, nom):
    debut, duree = float(debut), float(duree)
    clips, words = os.path.join(ROOT, "assets", "clips"), os.path.join(ROOT, "assets", "words")
    os.makedirs(clips, exist_ok=True)
    os.makedirs(words, exist_ok=True)
    out = os.path.join(clips, f"{nom}.mp4")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "yt_dlp", "-q", "--no-playlist",
                        "-f", "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]",
                        "--download-sections", f"*{debut}-{debut + duree}", "--force-keyframes-at-cuts",
                        "--merge-output-format", "mp4", "-o", os.path.join(tmp, "brut.%(ext)s"),
                        f"https://www.youtube.com/watch?v={vid}"], check=True)
        src = next(os.path.join(tmp, f) for f in os.listdir(tmp) if f.startswith("brut."))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-r", "30", "-g", "30", "-keyint_min", "30", "-pix_fmt", "yuv420p", "-af", _loudnorm_args(src),
                        "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], check=True)
    from faster_whisper import WhisperModel
    model = WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(out, language="fr", word_timestamps=True, vad_filter=False)
    ws = [{"w": w.word, "s": round(debut + w.start, 2), "e": round(debut + w.end, 2)} for s in segs for w in s.words]
    with open(os.path.join(words, f"{nom}.words.json"), "w", encoding="utf-8") as f:
        json.dump({"vid": vid, "start": debut, "end": round(debut + duree, 2), "words": ws}, f, ensure_ascii=False)
    print(f"✓ assets/clips/{nom}.mp4 et assets/words/{nom}.words.json ({len(ws)} mots, t0 = {debut})")
    print("  Les horodatages des mots sont ceux de la vidéo YouTube (t0 = début de l'extrait).")


# ── Légende ──────────────────────────────────────────────────────────────────
def _fiche(debat):
    with open(os.path.join(b.SITE, "sessions", f"{debat}.fiche.json"), encoding="utf-8") as f:
        return json.load(f)


def legende_text(reel):
    m = b.MOMENTS[reel]
    fi = _fiche(m["debat"])
    a = next(x for x in fi["affirmations"] if x["id"] == m["aff"])
    tags = " ".join(["#factcheck", "#vraioufaux"] + m["tags"])
    return (f"« {m['quote']} » — {m['contexte']}.\n\n"
            f"{VERDICT_LEGENDE[a['verdict']]} : {a['explication']}\nSource : {a['source']}\n\n"
            f"Ce débat compte {fi['totaux']['affirmations']} affirmations vérifiées par notre IA, sources à l'appui. "
            f"La fiche complète du débat : lien en bio 👉 vachecker.info\n\n{tags}\n")


def legende(reel):
    os.makedirs(LEGENDES, exist_ok=True)
    text = legende_text(reel)
    with open(os.path.join(LEGENDES, f"{reel}.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"✓ social/legendes/{reel}.txt\n\n{text}")


# ── Vérification ─────────────────────────────────────────────────────────────
def _levels(clip, start, dur, step=0.02):
    """Niveau (dB) du son de l'extrait par tranches de 20 ms, à partir de start (temps de l'extrait)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "a.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}",
                        "-i", os.path.join(ROOT, "assets", "clips", clip["src"]), "-ac", "1", "-ar", "16000", wav], check=True)
        w = wave.open(wav)
        n = w.getnframes()
        d = struct.unpack("<%dh" % n, w.readframes(n))
        w.close()
    k = int(16000 * step)
    return [20 * math.log10(math.sqrt(sum(x * x for x in d[i:i + k]) / k) / 32768 + 1e-9) for i in range(0, n - k + 1, k)]


def _cuts(clip):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", os.path.join(ROOT, "assets", "clips", clip["src"]), "-an",
                          "-vf", "select='gt(scene,0.25)',showinfo", "-f", "null", "-"], capture_output=True, text=True).stderr
    return [clip["t0"] + float(x) for x in re.findall(r"pts_time:([\d.]+)", out)]


def _norm(s):
    s = s.lower().replace("’", "'").replace("'", " ")
    return re.sub(r"[^\w\s]", " ", s).split()


def verifier(reel):
    m = b.MOMENTS[reel]
    clip = b.CLIPS[m["clip"]]
    a, bb = m["a"], m["b"]
    speech = bb - a
    guess, hold, hook = b.moment_timing(m, dict(b.card_from_fiche(m["debat"], m["aff"]), quote=m["quote"]))
    dur = b.guess_start(speech, hook) + guess + hold + 0.35  # durée de la scène « moment »
    m0 = a - 0.12                                          # début de l'extrait dans la vidéo
    ok = True

    def bad(msg):
        nonlocal ok
        ok = False
        print("  ✗ " + msg)

    print(f"Réel « {reel} » — {clip['who']}, {a:.2f} → {bb:.2f} ({speech:.1f} s)")

    # 1. La citation de l'accroche : les mots exacts, dans l'ordre, pendant la phrase
    said = _norm(" ".join(t["w"] for t in b.tokens(m["clip"], a - 1.0, bb + 1.0)))
    frags = [f for f in re.split(r"\s*\[…\]\s*", re.sub(r"\[[^\]…]*\]", "", m["quote"])) if f.strip()]
    pos, missing = 0, []
    for frag in frags:
        words = _norm(frag)
        hit = next((i for i in range(pos, len(said) - len(words) + 1) if said[i:i + len(words)] == words), None)
        if hit is None:
            missing.append(frag)
        else:
            pos = hit + len(words)
    if missing:
        bad("citation : pas dans la transcription, mot pour mot et dans l'ordre : " + " | ".join(missing)
            + "\n    (corriger quote, ou la transcription si elle se trompe — FIXES / RETIME)")
    else:
        print("  ✓ citation : mots exacts de l'orateur")

    # 2. Le début : l'extrait doit partir d'un silence (sinon on entend la fin du mot d'avant).
    #    Seules les premières 40 ms comptent : ensuite, c'est l'attaque du premier mot.
    lv = _levels(clip, m0 - clip["t0"], 0.04)
    heard = max(lv)
    if heard > -30:
        back = _levels(clip, m0 - clip["t0"] - 1.5, 1.6)
        quiet = [m0 - 1.5 + i * 0.02 for i, db in enumerate(back) if db < -36]
        tip = f" ; silence le plus proche avant : {quiet[-1]:.2f} s → essayer a = {quiet[-1] + 0.12:.2f}" if quiet else \
            " ; pas de silence juste avant : la phrase est enchaînée, choisir la plus petite coupure (≈ −40 dB)"
        bad(f"début : un son s'entend dès la première image ({heard:.0f} dB){tip}")
    else:
        print(f"  ✓ début : l'extrait commence dans un silence ({heard:.0f} dB)")

    # 3. La fin de la phrase (les sous-titres s'arrêtent à b ; le débat continue ensuite, étouffé)
    last = max((t["e"] for t in b.tokens(m["clip"], a, bb)), default=bb)
    if last > bb + 0.05:
        bad(f"fin : le dernier mot finit après b ({last:.2f} > {bb:.2f})")
    else:
        print("  ✓ fin : la phrase finit avant b")

    # 4. Les changements de plan pendant la phrase et sous la carte
    starts = [t for t, _ in clip.get("recrop", [])]
    cuts = [t for t in _cuts(clip) if m0 < t < m0 + dur]
    for t in cuts:
        where = "pendant la phrase" if t < bb + 0.15 else "sous « Vrai ou faux ? » / la carte"
        recadre = any(abs(t - s) < 0.4 for s in starts)
        line = f"plan qui change à {t:.2f} s ({where}){' — recadré (recrop)' if recadre else ''}"
        if t < bb - 0.5 and not recadre:
            bad(line + " : vérifier sur la planche que l'orateur reste dans le cadre, sinon ajouter un recrop")
        elif t < bb + 0.15 and not recadre:
            print("  ⚠ " + line + " : en toute fin de phrase, acceptable si la planche le confirme")
        else:
            print("  · " + line)

    # 5. La planche : ce que montre le cadre, aux moments clés et dans chaque plan
    times = sorted({round(a + 0.3, 2), round((a + bb) / 2, 2), round(bb - 0.2, 2)}
                   | {round((x + y) / 2, 2) for x, y in zip([m0] + cuts, cuts + [m0 + dur])})
    sheet = os.path.join(ROOT, "renders", "verif", f"{reel}.jpg")
    os.makedirs(os.path.dirname(sheet), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i, t in enumerate(times):
            crop = clip["crop"]
            for tc, c in clip.get("recrop", []):
                if t >= tc:
                    crop = c
            x, y, w, h = crop
            f = os.path.join(tmp, f"{i:02d}.png")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t - clip['t0']:.2f}", "-i",
                            os.path.join(ROOT, "assets", "clips", clip["src"]), "-frames:v", "1",
                            "-vf", f"crop={w}:{h}:{x}:{y},scale=324:300", f], check=True)
            frames.append(f)
        args = sum((["-i", f] for f in frames), [])
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex",
                        "".join(f"[{i}]" for i in range(len(frames))) + f"hstack={len(frames)}", sheet], check=True)
    print(f"  → regarder {os.path.relpath(sheet, ROOT)} (instants {', '.join(f'{t:.1f}' for t in times)} s) :")
    print("    l'orateur doit être visible et centré pendant la phrase, sans logo ni bandeau de chaîne.")

    # 6. La légende : celle du modèle (social/legendes, écrite par « legende »)
    path = os.path.join(LEGENDES, f"{reel}.txt")
    if not os.path.exists(path):
        bad(f"légende absente : python scripts/build_reels.py legende {reel}")
    elif open(path, encoding="utf-8").read() != legende_text(reel):
        bad(f"légende différente du modèle (mention de l'IA, explication et source mot pour mot) : "
            f"python scripts/build_reels.py legende {reel}")
    else:
        print("  ✓ légende au format convenu")

    # 7. La fiche de fin
    if os.path.exists(b.fiche_png(m["debat"])):
        print("  ✓ fiche verticale du débat capturée")
    else:
        print(f"  · fiche du débat pas encore capturée (faite au premier build)")

    print("✓ vérifications passées — reste à regarder la planche et le rendu" if ok else "✗ à corriger avant de rendre")
    return ok


# ── Rendu et état des publications ───────────────────────────────────────────
def _publies():
    with open(REGISTRE, encoding="utf-8") as f:
        return {p["reel"]: p for p in json.load(f)["publications"] if p["plateforme"] == "instagram"}


def rendre(reel):
    """Construit le réel, le vérifie (lint) et le rend dans renders/a-publier."""
    deja = _publies().get(reel)
    if deja:
        print(f"⚠ « {reel} » est déjà en ligne ({deja['lien']}) : ce rendu ne se publiera qu'avec --republier")
    b.build(reel)
    npx = "npx.cmd" if os.name == "nt" else "npx"
    lint = subprocess.run([npx, "--yes", "hyperframes@0.8.152", "lint"], cwd=ROOT, capture_output=True, text=True)
    if " 0 errors" not in lint.stdout + lint.stderr:
        print(lint.stdout[-2000:] + lint.stderr[-2000:])
        raise SystemExit("✗ lint : erreurs à corriger avant de rendre")
    os.makedirs(A_PUBLIER, exist_ok=True)
    out = os.path.join(A_PUBLIER, f"vachecker-{reel}.mp4")
    subprocess.run([npx, "--yes", "hyperframes@0.8.152", "render", "-o", out], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    meas = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", out, "-af", "ebur128", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", meas)[-1])
    flag = "" if -19 <= lufs <= -13 else "  ⚠ hors de −19…−13 : normaliser l'extrait (normaliser <extrait>) et rendre de nouveau"
    print(f"✓ {os.path.relpath(out, ROOT)} — son {lufs:.1f} LUFS{flag} — à regarder avant toute publication")


def etat():
    pub = _publies()
    todo = sorted(f for f in os.listdir(A_PUBLIER) if f.endswith(".mp4")) if os.path.isdir(A_PUBLIER) else []
    print("À publier (renders/a-publier) :")
    for f in todo:
        reel = f[len("vachecker-"):-4]
        note = f"  ⚠ déjà en ligne : {pub[reel]['lien']}" if reel in pub else ""
        leg = "" if os.path.exists(os.path.join(LEGENDES, f"{reel}.txt")) else "  (légende à écrire : legende)"
        print(f"  · {reel}{leg}{note}")
    if not todo:
        print("  (rien)")
    print("Déjà en ligne (social/publications.json) :")
    for reel, p in sorted(pub.items(), key=lambda x: x[1]["date"]):
        print(f"  ✓ {reel} — {p['date'][:10]} — {p['lien']}" + (f" ({p['note']})" if p.get("note") else ""))

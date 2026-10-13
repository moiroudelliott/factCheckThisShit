"""Réels verticaux (1080 × 1920) de vachecker.info, pour Instagram et TikTok.

Usage :
    python scripts/build_reels.py presentation   # réel épinglé (≈ 27 s)
    python scripts/build_reels.py zemmour        # un moment, puis la fiche du débat (≈ 27 s)
    python scripts/build_reels.py fiche <id>     # (re)capture la fiche verticale sombre d'un débat

La fiche de fin est une capture de la page du site
(resume.html?s=<id>&format=vertical&theme=sombre), en 1080 × 1920 :
assets/fiches/<id>-vertical-sombre.png.

Écrit compositions/<réel>-*.html et index.html (le réel choisi), puis :
    npx hyperframes render -o renders/<réel>.mp4

Mise en page commune TikTok / Instagram : rien d'utile au-dessus de y ≈ 230
(onglets), sous y ≈ 1480 (légende, musique) ni à droite sous y ≈ 900 (boutons).
L'extrait est recadré sur l'orateur dans une fenêtre 1080 × 1000 ; les
sous-titres vont dessous, sur le fond, jamais sur les bandeaux des chaînes.
Textes des cartes : tels que publiés dans les fiches du site.
"""

import html
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "..", "..", "site")
FICHES = os.path.join(ROOT, "assets", "fiches")
COMP = os.path.join(ROOT, "compositions")
os.makedirs(COMP, exist_ok=True)
W, H = 1080, 1920
FRAME_Y, FRAME_H = 330, 1000          # fenêtre de l'extrait
CAP_Y = FRAME_Y + FRAME_H + 22        # sous-titres
CARD_Y = 850                          # la carte couvre le bas de l'extrait et les sous-titres

ACCENT = {"attente": "#9aa6b4", "vrai": "#73c076", "partiel": "#d4ac32", "trompeur": "#eba941", "faux": "#ee6060"}
FOOT_COLOR = {"vrai": "#73c076", "partiel": "#d4ac32", "trompeur": "#eba941", "faux": "#f9786a"}
RED = "#ee6060"
BG = "#0d0e12"
FLAG = "background: linear-gradient(90deg, #0055a4 0 33.34%, #fff 33.34% 66.67%, #ef4135 66.67%);"
SIGNATURE = "Projet français, 100&nbsp;% souverain"

FONTS = """
@font-face { font-family: 'SRC Archivo'; font-style: normal; font-weight: 100 900; src: url('assets/fonts/archivo-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Newsreader'; font-style: normal; font-weight: 200 800; src: url('assets/fonts/newsreader-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Newsreader'; font-style: italic; font-weight: 200 800; src: url('assets/fonts/newsreader-italic-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Plex Mono'; font-style: normal; font-weight: 400; src: url('assets/fonts/ibm-plex-mono-400-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Plex Mono'; font-style: normal; font-weight: 500; src: url('assets/fonts/ibm-plex-mono-500-latin.woff2') format('woff2'); }
"""

# Extraits : début de l'extrait dans la vidéo source (s, même repère que les mots)
# et fenêtre de recadrage (x, y, largeur, hauteur) en pixels de la source 1920 × 1080.
# recrop : [(instant du changement de plan, nouvelle fenêtre)] quand la réalisation change de cadre.
CLIPS = {
    "zemmour": dict(src="bfm-zemmour-terroristes-2421.mp4", words="bfm-zemmour-terroristes-2421.words.json", t0=2421.5,
                    crop=(890, 50, 820, 760), recrop=[(2460.667, (706, 0, 994, 920)), (2467.0, (890, 50, 820, 760))],
                    who="Éric Zemmour", where="BFMTV · 23 septembre 2021"),
    "bardella": dict(src="leg-bardella-tva-1380.mp4", words="leg-bardella-tva-1380.words.json", t0=0.0,
                     crop=(544, 0, 972, 900), who="Jordan Bardella", where="France 2 · 27 juin 2024"),
    # LCI : le logo, le nom à l'écran et le bandeau du bas restent hors cadre
    "bompard": dict(src="lci-bompard-onu-1019.mp4", words="lci-bompard-onu-1019.words.json", t0=1019.0,
                    crop=(1105, 142, 672, 622), who="Manuel Bompard", where="LCI · 2 octobre 2026"),
    "bompard-salaire": dict(src="lci-bompard-salaire-1878.mp4", words="lci-bompard-salaire-1878.words.json", t0=0.0,
                            crop=(1105, 142, 672, 622), who="Manuel Bompard", where="LCI · 2 octobre 2026"),
    "attal": dict(src="fj-attal-farage-171.mp4", words="fj-attal-farage-171.words.json", t0=171.0,
                  crop=(460, 20, 960, 889), who="Gabriel Attal", where="Franc-jeu · 20 septembre 2026"),
}

# ── Musique du teaser : « No Title » (102 BPM) ────────────────────────────────
# Noire = 0,5883 s, croche = 0,2942 s (grille de hyperframes beats). Le drop
# frappe à 85,713 s dans le morceau, après un creux de 84,2 à 85,7 s.
MUSIC_SRC = "assets/music/no-title.mp3"
Q = 0.58835
E = Q / 2
DROP = 85.713

# Moments : bornes de la phrase (repère des mots, mesurées au mot près), carte telle que publiée
CARDS = {
    "zemmour": dict(verdict="faux", tag="FAUX", ts="40:56", claim="Tous les terroristes sont musulmans",
                    body="La France a connu des attentats terroristes non islamistes (ex : attentats d'extrême droite, séparatistes corses, etc.).",
                    source="Wikipédia", foot="✗ démenti · 90%"),
    "bardella": dict(verdict="trompeur", tag="TROMPEUR", ts="23:04",
                     claim="Dès l'été, baisser la TVA de 20 % à 5,5 % sur l'électricité, le gaz, le fioul et le carburant",
                     body="La directive européenne sur la TVA autorise un taux réduit sur l'électricité et le gaz. Pour le fioul et les carburants, en revanche, une TVA à 5,5 % serait contraire au droit européen.",
                     source="Les Surligneurs", foot="⚠ trompeur · 90%"),
    "bompard": dict(verdict="vrai", tag="VRAI", ts="17:34",
                    claim="L'ONU a pointé un problème structurel de racisme dans la police française",
                    body="Le Haut-Commissariat des Nations unies aux droits de l’homme a effectivement pointé en juin 2023 des « profonds problèmes de racisme et de discrimination raciale parmi les forces de l’ordre » en France.",
                    source="Le Monde", foot="✓ confirmé · 95%"),
}

FIXES = {"fuel,": "fioul,", "Sommige,": "insoumise,", "Unies": "unies"}


def esc(s):
    return html.escape(s, quote=False)


def words_of(clip):
    path = os.path.join(ROOT, "assets", "words", CLIPS[clip]["words"])
    d = json.load(open(path, encoding="utf-8"))
    return d["words"] if isinstance(d, dict) else d


def tokens(clip, a, b):
    """Mots prononcés entre a et b (repère des mots), apostrophes et virgules recollées."""
    out = []
    for w in words_of(clip):
        if not (a - 0.05 <= w["s"] <= b + 0.05):
            continue
        t = w["w"].strip()
        if not t or t in ("–", "-", "—"):
            continue
        t = FIXES.get(t, t).replace("'", "’")
        if out and (t[0] in "’-,." or t in ("%",)) and t != "%":
            out[-1]["w"] += t
            out[-1]["e"] = w["e"]
        else:
            out.append({"w": t, "s": w["s"], "e": w["e"]})
    return out


def groups(toks, max_chars=30):
    """Paquets de mots affichés ensemble : coupés aux ponctuations, aux pauses, ou à ~30 signes."""
    gs, cur = [], []
    for t in toks:
        if cur:
            text = " ".join(x["w"] for x in cur)
            pause = t["s"] - cur[-1]["e"] > 0.6
            if len(text) + 1 + len(t["w"]) > max_chars or pause or cur[-1]["w"][-1] in ".?!":
                gs.append(cur)
                cur = []
        cur.append(t)
        if t["w"][-1] == "," and len(" ".join(x["w"] for x in cur)) > 14:
            gs.append(cur)
            cur = []
    if cur:
        gs.append(cur)
    return gs


def scene(sid, dur, style, markup, script, bg=BG):
    p = f'[data-composition-id="{sid}"]'
    return f"""<!doctype html>
<html lang="fr">
<head><meta charset="UTF-8" /></head>
<body>
<template id="{sid}-template">
<div data-composition-id="{sid}" data-width="{W}" data-height="{H}" data-duration="{dur}">
<style>
{FONTS}
{p} {{ position: absolute; inset: 0; overflow: hidden; background: {bg}; color: #fff; font-family: 'SRC Archivo', sans-serif; }}
{p} .abs {{ position: absolute; }}
{p} .glow {{ position: absolute; inset: 0; background: radial-gradient(900px 700px at 15% 95%, rgba(163,23,44,0.30), transparent 70%),
  radial-gradient(800px 600px at 90% 10%, rgba(238,96,96,0.10), transparent 70%); }}
{p} .logo {{ font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; line-height: 1; white-space: nowrap; }}
{p} .logo b {{ font-weight: 500; color: {RED}; }}
{p} .flag {{ display: inline-block; flex: none; border-radius: 4px; {FLAG} box-shadow: 0 0 0 2px rgba(255,255,255,0.15); }}
{style.replace('§', p)}
</style>
{markup}
<script>
(function () {{
  const tl = gsap.timeline({{ paused: true }});
{script}
  window.__timelines['{sid}'] = tl;
}})();
</script>
</div>
</template>
</body>
</html>
"""


def write(name, text):
    with open(os.path.join(COMP, name + ".html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def inner_box(crop):
    x, y, cw, ch = crop
    k = W / cw
    return dict(left=round(-x * k, 1), top=round(-y * k, 1), width=round(1920 * k, 1), height=round(1080 * k, 1))


def recrops(clip, a, dur, target, at):
    """Changements de plan pendant la scène : la fenêtre de recadrage suit la réalisation.
    at : début de la scène dans le réel. Le changement est calé sur la première image
    du rendu (30 i/s) qui montre déjà le nouveau plan."""
    js = []
    for tc, crop in clip.get("recrop", []):
        f = math.ceil((tc + 0.14 - a + at) * 30 - 1e-6)
        t = round(f / 30 - at - 0.005, 3)
        if 0 < t < dur:
            box = ", ".join(f"{k}: '{v}px'" for k, v in inner_box(crop).items())
            js.append(f"  tl.set('{target}', {{ {box} }}, {t});")
    return ("\n" + "\n".join(js)) if js else ""


# ── Scène « moment » ─────────────────────────────────────────────────────────
def moment(sid, clip_id, a, b, guess, hold, track=3, card_id=None, at=0.0):
    """a, b : début et fin de la phrase (repère des mots). Renvoie (durée, mots minutés)."""
    clip, c = CLIPS[clip_id], CARDS[card_id or clip_id]
    v = c["verdict"]
    speech = round(b - a, 2)
    g0 = round(speech + 0.15, 2)            # « Vrai ou faux ? »
    c0 = round(g0 + guess, 2)               # la carte tombe
    dur = round(c0 + hold + 0.35, 2)
    x, y, cw, ch = clip["crop"]
    k = W / cw
    lane = {"version": 1, "lanes": [{"target": "volume", "points": [
        {"t": 0, "v": 0}, {"t": 0.12, "v": 1}, {"t": round(speech + 0.1, 2), "v": 1}, {"t": round(speech + 0.55, 2), "v": 0.16},
        {"t": round(c0 + 0.6, 2), "v": 0.07}, {"t": round(dur - 0.3, 2), "v": 0.07}, {"t": dur, "v": 0}]}]}
    media_start = round(a - 0.12 - clip["t0"], 2)
    style = f"""§ .head {{ left: 72px; top: 196px; right: 72px; display: flex; align-items: baseline; justify-content: space-between; }}
§ .head .logo {{ font-size: 44px; }}
§ .head .q {{ font-size: 26px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: rgba(255,255,255,0.55); }}
§ .who {{ left: 72px; top: 262px; font-size: 30px; color: rgba(255,255,255,0.62); }}
§ .who b {{ color: #fff; font-weight: 700; }}
§ .frame {{ left: 0; top: {FRAME_Y}px; width: {W}px; height: {FRAME_H}px; overflow: hidden; background: #000; }}
§ .inner {{ position: absolute; left: {-x * k:.1f}px; top: {-y * k:.1f}px; width: {1920 * k:.1f}px; height: {1080 * k:.1f}px; }}
§ video {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
§ .shade {{ position: absolute; inset: 0; background: rgba(8,9,12,0.62); opacity: 0; }}
§ .guess {{ left: 0; width: {W}px; top: {FRAME_Y + 330}px; text-align: center; }}
§ .guess .t {{ font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 128px; line-height: 1; }}
§ .guess .bar {{ margin: 44px auto 0; width: 420px; height: 10px; border-radius: 5px; background: rgba(255,255,255,0.18); overflow: hidden; }}
§ .guess .bar i {{ display: block; height: 100%; background: #fff; transform-origin: 0 50%; }}
§ .card {{ left: 60px; top: {CARD_Y}px; width: 960px; border-radius: 30px; overflow: hidden; --accent: {ACCENT[v]};
  background: linear-gradient(180deg, rgba(22,24,29,0.97), rgba(15,16,20,0.97)); border: 2px solid rgba(255,255,255,0.10);
  box-shadow: 0 40px 110px rgba(0,0,0,0.6), 0 14px 70px color-mix(in srgb, var(--accent) 22%, transparent); }}
§ .card .bar {{ height: 7px; background: var(--accent); }}
§ .card .in {{ padding: 30px 38px 34px; }}
§ .card .top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; }}
§ .card .cb {{ font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 30px; color: rgba(255,255,255,0.85); }}
§ .card .cb .d {{ font-style: normal; font-size: 18px; margin-right: 10px; color: var(--accent); }}
§ .card .cb b {{ font-weight: 500; color: var(--accent); }}
§ .card .tag {{ display: inline-flex; align-items: center; gap: 12px; padding: 9px 22px; border-radius: 999px; font-weight: 800; font-size: 30px; letter-spacing: 0.08em;
  color: var(--accent); background: color-mix(in srgb, var(--accent) 16%, transparent); border: 3px solid color-mix(in srgb, var(--accent) 55%, transparent); }}
§ .card .tag i {{ width: 14px; height: 14px; border-radius: 50%; background: var(--accent); }}
§ .card .meta {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
§ .card .nm {{ font-size: 24px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }}
§ .card .ts {{ font-family: 'SRC Plex Mono', monospace; font-size: 22px; color: rgba(255,255,255,0.7); border: 2px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 2px 12px; }}
§ .card .claim {{ margin: 0 0 22px; font-family: 'SRC Newsreader', serif; font-style: italic; font-size: 46px; line-height: 1.25; color: #fff; }}
§ .card .body {{ margin: 0; font-size: 31px; line-height: 1.45; color: rgba(255,255,255,0.84); }}
§ .card .foot {{ display: flex; justify-content: space-between; gap: 20px; margin-top: 24px; padding-top: 22px; border-top: 2px solid rgba(255,255,255,0.09); font-size: 24px; }}
§ .card .src {{ font-family: 'SRC Plex Mono', monospace; color: rgba(255,255,255,0.7); }}
§ .card .vd {{ font-weight: 600; color: {FOOT_COLOR[v]}; }}"""
    markup = f"""<div class="glow"></div>
<div class="abs head" id="{sid}-head"><span class="logo">vachecker<b>.info</b></span><span class="q">Vrai ou faux&nbsp;?</span></div>
<div class="abs who" id="{sid}-who"><b>{esc(clip["who"])}</b> · {esc(clip["where"])}</div>
<div class="abs frame" data-layout-allow-overflow><div class="inner" id="{sid}-in" data-layout-allow-overflow>
  <video id="{sid}-video" class="clip" src="assets/clips/{clip["src"]}" data-start="0" data-duration="{dur}" data-media-start="{media_start}"
    data-track-index="{track}" playsinline data-has-audio="true" data-automation='{json.dumps(lane)}'></video>
</div><div class="shade" id="{sid}-shade"></div></div>
<div class="abs guess" id="{sid}-guess"><div class="t">Vrai ou faux&nbsp;?</div><div class="bar"><i id="{sid}-gbar"></i></div></div>
<div class="abs card" id="{sid}-card">
  <div class="bar"></div>
  <div class="in">
    <div class="top"><span class="cb"><span class="d">◆</span>vachecker<b>.info</b></span><span class="tag" id="{sid}-tag"><i></i>{c["tag"]}</span></div>
    <div class="meta"><span class="nm">{esc(clip["who"])}</span><span class="ts">▶ {c["ts"]}</span></div>
    <p class="claim">« {esc(c["claim"])} »</p>
    <p class="body" id="{sid}-body">{esc(c["body"])}</p>
    <div class="foot" id="{sid}-foot"><span class="src">{esc(c["source"])} ↗</span><span class="vd">{c["foot"]}</span></div>
  </div>
</div>"""
    script = f"""  tl.fromTo(['#{sid}-head', '#{sid}-who'], {{ opacity: 0, y: -12 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: 'power3.out', stagger: 0.08 }}, 0.05);
  gsap.set(['#{sid}-guess', '#{sid}-card'], {{ opacity: 0 }});
  // Vrai ou faux ? — le temps de deviner
  tl.to('#{sid}-shade', {{ opacity: 1, duration: 0.35, ease: 'power1.out' }}, {g0});
  tl.fromTo('#{sid}-guess', {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: 'back.out(1.8)' }}, {g0});
  tl.fromTo('#{sid}-gbar', {{ scaleX: 1 }}, {{ scaleX: 0, duration: {guess}, ease: 'none' }}, {g0});
  tl.to('#{sid}-guess', {{ opacity: 0, scale: 1.05, duration: 0.22, ease: 'power1.in' }}, {round(c0 - 0.12, 2)});
  // La carte tombe
  tl.fromTo('#{sid}-card', {{ opacity: 0, y: -50, filter: 'blur(8px)' }}, {{ opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.6, ease: 'expo.out' }}, {c0});
  tl.fromTo('#{sid}-tag', {{ scale: 1.7, rotation: -6 }}, {{ scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(2.2)' }}, {round(c0 + 0.15, 2)});
  tl.fromTo(['#{sid}-body', '#{sid}-foot'], {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: 'power2.out', stagger: 0.12 }}, {round(c0 + 0.35, 2)});
  tl.to('#{sid}-shade', {{ opacity: 0.35, duration: 0.5 }}, {c0});""" + recrops(clip, a, dur, f"#{sid}-in", at)
    write(sid, scene(sid, dur, style, markup, script))
    # Mots minutés depuis le début de la scène (sous-titres)
    toks = [{"w": t["w"], "s": round(t["s"] - a + 0.12, 2), "e": round(t["e"] - a + 0.12, 2)} for t in tokens(clip_id, a, b)]
    return dur, toks, g0


# ── Scène « citation » (teaser) : l'orateur et sa phrase, sans vérification ──
def quote(sid, clip_id, a, b, dur, track, at=0.0):
    """a, b : bornes de la phrase (repère des mots) ; dur : calée sur la grille de la musique."""
    clip = CLIPS[clip_id]
    x, y, cw, ch = clip["crop"]
    k = W / cw
    speech = round(b - a, 2)
    lane = {"version": 1, "lanes": [{"target": "volume", "points": [
        {"t": 0, "v": 0}, {"t": 0.06, "v": 1}, {"t": round(speech + 0.08, 2), "v": 1}, {"t": round(speech + 0.22, 2), "v": 0},
        {"t": dur, "v": 0}]}]}
    media_start = round(a - 0.12 - clip["t0"], 2)
    style = f"""§ .nm {{ left: 72px; top: 178px; font-size: 64px; font-weight: 800; letter-spacing: -0.015em; line-height: 1; }}
§ .wh {{ left: 74px; top: 258px; font-size: 30px; color: rgba(255,255,255,0.6); }}
§ .frame {{ left: 0; top: {FRAME_Y}px; width: {W}px; height: {FRAME_H}px; overflow: hidden; background: #000; }}
§ .inner {{ position: absolute; left: {-x * k:.1f}px; top: {-y * k:.1f}px; width: {1920 * k:.1f}px; height: {1080 * k:.1f}px; }}
§ video {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}"""
    markup = f"""<div class="glow"></div>
<div class="abs nm" id="{sid}-nm">{esc(clip["who"])}</div>
<div class="abs wh" id="{sid}-wh">{esc(clip["where"])}</div>
<div class="abs frame" id="{sid}-fr" data-layout-allow-overflow><div class="inner" id="{sid}-in" data-layout-allow-overflow>
  <video id="{sid}-video" class="clip" src="assets/clips/{clip["src"]}" data-start="0" data-duration="{dur}" data-media-start="{media_start}"
    data-track-index="{track}" playsinline data-has-audio="true" data-automation='{json.dumps(lane)}'></video>
</div></div>"""
    script = f"""  tl.fromTo('#{sid}-nm', {{ opacity: 0, x: -30 }}, {{ opacity: 1, x: 0, duration: 0.3, ease: 'power3.out' }}, 0);
  tl.fromTo('#{sid}-wh', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.3 }}, 0.1);
  tl.fromTo('#{sid}-in', {{ scale: 1.06 }}, {{ scale: 1, duration: {dur}, ease: 'none' }}, 0);""" + recrops(clip, a, dur, f"#{sid}-in", at)
    write(sid, scene(sid, dur, style, markup, script))
    toks = [{"w": t["w"], "s": round(t["s"] - a + 0.12, 2), "e": round(t["e"] - a + 0.12, 2)} for t in tokens(clip_id, a, b)]
    return toks


def vraifaux(sid, dur):
    """Pendant le creux avant le drop : « Vrai ? » / « Faux ? » en alternance, sur les noires."""
    style = f"""§ .w {{ left: 0; width: {W}px; top: 760px; text-align: center; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500;
  font-size: 210px; line-height: 1; opacity: 0; }}
§ .v {{ color: {ACCENT["vrai"]}; }}
§ .f {{ color: {ACCENT["faux"]}; }}"""
    labels = [("Vrai&nbsp;?", "v"), ("Faux&nbsp;?", "f"), ("Vrai&nbsp;?", "v"), ("Faux&nbsp;?", "f")]
    markup = '<div class="glow"></div>\n' + "\n".join(f'<div class="abs w {c}" id="vf-{i}">{t}</div>' for i, (t, c) in enumerate(labels))
    js = []
    for i in range(4):
        t0 = round(i * Q, 3)
        js.append(f"  tl.fromTo('#vf-{i}', {{ opacity: 0, scale: 1.25 }}, {{ opacity: 1, scale: 1, duration: 0.16, ease: 'power3.out' }}, {t0});")
        if i < 3:
            js.append(f"  tl.set('#vf-{i}', {{ opacity: 0 }}, {round((i + 1) * Q, 3)});")
    js.append(f"  tl.to('#vf-3', {{ opacity: 0, scale: 0.92, duration: 0.18, ease: 'power2.in' }}, {round(dur - 0.18, 3)});")
    write(sid, scene(sid, dur, style, markup, "\n".join(js)))


# ── Sous-titres (une seule piste) ────────────────────────────────────────────
def captions(sid, total, spans):
    """spans : [(début de scène, mots, fin d'affichage)] — un paquet de mots à la fois, le mot dit en jaune."""
    items, js = [], []
    n = 0
    for start, toks, stop in spans:
        gs = groups(toks)
        for gi, g in enumerate(gs):
            g_in = round(start + g[0]["s"], 2)
            g_out = round(start + (gs[gi + 1][0]["s"] if gi + 1 < len(gs) else min(stop, g[-1]["e"] + 0.4)), 2)
            ws = "".join(f'<span id="{sid}-w{n}-{i}">{esc(t["w"])}</span> ' for i, t in enumerate(g))
            items.append(f'<div class="grp" id="{sid}-g{n}">{ws.strip()}</div>')
            js.append(f"  tl.set('#{sid}-g{n}', {{ opacity: 1 }}, {g_in}); tl.set('#{sid}-g{n}', {{ opacity: 0 }}, {g_out});")
            for i, t in enumerate(g):
                js.append(f"  tl.set('#{sid}-w{n}-{i}', {{ color: '#f5c84c' }}, {round(start + t['s'], 2)});"
                          f" tl.set('#{sid}-w{n}-{i}', {{ color: '#ffffff' }}, {round(start + t['e'] + 0.05, 2)});")
            n += 1
    style = f"""§ {{ background: transparent; }}
§ .grp {{ position: absolute; left: 90px; width: 900px; top: {CAP_Y}px; text-align: center; opacity: 0;
  font-weight: 800; font-size: 50px; line-height: 1.18; letter-spacing: -0.005em; color: #fff; text-shadow: 0 3px 14px rgba(0,0,0,0.6); }}"""
    write(sid, scene(sid, total, style, "\n".join(items), "\n".join(js), bg="transparent"))


# ── Écrans du réel de présentation ───────────────────────────────────────────
def hook(sid, dur=3.0):
    style = """§ .l { left: 72px; right: 72px; font-weight: 800; letter-spacing: -0.02em; line-height: 1.02; }
§ .l1 { top: 560px; font-size: 92px; color: rgba(255,255,255,0.72); }
§ .l2 { top: 680px; font-size: 150px; }
§ .l2 b { color: #ee6060; font-weight: 800; }
§ .l3 { top: 900px; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 118px; letter-spacing: -0.01em; }"""
    markup = """<div class="glow"></div>
<div class="abs l l1" id="h-1">Un débat télé,</div>
<div class="abs l l2" id="h-2"><b id="h-n">195</b> affirmations.</div>
<div class="abs l l3" id="h-3">Qui vérifie&nbsp;?</div>"""
    script = f"""  const n = document.getElementById('h-n'); const c = {{ v: 0 }};
  tl.fromTo('#h-1', {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power3.out' }}, 0.05);
  tl.fromTo('#h-2', {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power3.out' }}, 0.35);
  tl.fromTo(c, {{ v: 0 }}, {{ v: 195, duration: 0.9, ease: 'power2.out', onUpdate: () => {{ n.textContent = String(Math.round(c.v)); }} }}, 0.35);
  tl.fromTo('#h-3', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: 0.45, ease: 'back.out(1.8)' }}, 1.45);
  tl.to(['#h-1', '#h-2', '#h-3'], {{ opacity: 0, y: -20, duration: 0.3, ease: 'power2.in' }}, {dur - 0.35});"""
    write(sid, scene(sid, dur, style, markup, script))
    return dur


def promise(sid, dur=2.8):
    style = """§ .lg { left: 72px; top: 620px; font-size: 128px; }
§ .tx { left: 76px; right: 90px; top: 800px; font-size: 54px; line-height: 1.28; color: rgba(255,255,255,0.88); }
§ .tx b { color: #fff; font-weight: 700; }"""
    markup = """<div class="glow"></div>
<div class="abs logo lg" id="p-lg">vachecker<b>.info</b></div>
<div class="abs tx" id="p-tx">vérifie <b>chaque affirmation</b> d’un débat, <b>en direct</b>, sources à l’appui.</div>"""
    script = f"""  tl.fromTo('#p-lg', {{ opacity: 0, clipPath: 'inset(-30% 100% -30% -5%)' }}, {{ opacity: 1, clipPath: 'inset(-30% -10% -30% -5%)', duration: 0.6, ease: 'power3.inOut' }}, 0.05);
  tl.fromTo('#p-tx', {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: 'power3.out' }}, 0.55);
  tl.to(['#p-lg', '#p-tx'], {{ opacity: 0, duration: 0.3, ease: 'power2.in' }}, {dur - 0.35});"""
    write(sid, scene(sid, dur, style, markup, script))
    return dur


def method(sid, dur=4.8):
    steps = [("01", "Écouter", "Le débat est transcrit en direct."),
             ("02", "Repérer", "L’IA relève chiffres, dates et faits."),
             ("03", "Vérifier", "Le verdict cite toujours sa source.")]
    style = """§ .k { left: 72px; top: 330px; font-family: 'SRC Plex Mono', monospace; font-size: 30px; letter-spacing: 0.14em; text-transform: uppercase; color: rgba(255,255,255,0.6); }
§ .st { left: 72px; right: 72px; display: grid; grid-template-columns: 150px 1fr; column-gap: 26px; align-items: baseline; }
§ .st .n { font-weight: 800; font-size: 96px; line-height: 1; letter-spacing: -0.03em; color: #ee6060; }
§ .st .h { font-family: 'SRC Newsreader', serif; font-size: 84px; line-height: 1; }
§ .st .d { grid-column: 2; margin-top: 14px; font-size: 38px; line-height: 1.3; color: rgba(255,255,255,0.7); }"""
    markup = '<div class="glow"></div>\n<div class="abs k" id="m-k">Comment on vérifie</div>\n' + "\n".join(
        f'<div class="abs st" id="m-s{i}" style="top:{430 + i * 300}px;"><span class="n">{n}</span><span class="h">{h}</span><span class="d">{d}</span></div>'
        for i, (n, h, d) in enumerate(steps))
    script = f"""  tl.fromTo('#m-k', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.4 }}, 0.05);
  [0, 1, 2].forEach((i) => tl.fromTo('#m-s' + i, {{ opacity: 0, x: -40 }}, {{ opacity: 1, x: 0, duration: 0.5, ease: 'power3.out' }}, 0.25 + i * 0.45));
  tl.to(['#m-k', '#m-s0', '#m-s1', '#m-s2'], {{ opacity: 0, duration: 0.3 }}, {dur - 0.35});"""
    write(sid, scene(sid, dur, style, markup, script))
    return dur


def ending(sid, dur, line, tm=1.9, type_at=0.45, bio="Lien en bio"):
    """« Va checker l'info ! » se replie en vachecker.info (lettres placées d'après la chasse de la police)."""
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    phrase, url, fs = "Va checker l’info !", "vachecker.info", 112
    font = instancer.instantiateVariableFont(TTFont(os.path.join(ROOT, "assets/fonts/newsreader-italic-latin.woff2")),
                                             {"wght": 500, "opsz": min(72, fs)})
    cmap, hmtx, upm = font.getBestCmap(), font["hmtx"], font["head"].unitsPerEm

    def xs(text):
        out, x = [], 0.0
        for ch in text:
            out.append(round(x, 2))
            x += hmtx[cmap[ord(ch)]][0] * fs / upm
        return out, round(x, 2)

    px, pw = xs(phrase)
    ux, uw = xs(url)
    target = {0: 0, 1: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8, 12: 9, 13: 10, 14: 11, 15: 12, 16: 13}
    drop, red = (11, 18), (13, 14, 15, 16)
    left = round((W - pw) / 2, 1)
    shift = round((pw - uw) / 2, 1)          # l'adresse finit centrée
    chars = [f'<span class="ch" id="e-p{i}" style="left:{px[i]:.2f}px;">{c}</span>' for i, c in enumerate(phrase) if c not in "  "]
    chars += ['<span class="ch" id="e-v" style="left:0px;">v</span>', f'<span class="ch red" id="e-dot" style="left:{ux[9]:.2f}px;">.</span>']
    typed = [i for i, c in enumerate(phrase) if c not in "  "]
    mv = []
    for i in typed:
        if i in drop:
            mv.append(f"tl.to('#e-p{i}', {{ opacity: 0, y: 40, duration: 0.3, ease: 'power2.in' }}, {tm});")
        elif i == 12:
            mv.append(f"tl.to('#e-p{i}', {{ x: {ux[9] - px[12]:.2f}, y: {fs * 0.42:.0f}, opacity: 0, duration: 0.8, ease: 'power3.inOut' }}, {tm + 0.1});")
        elif abs(ux[target[i]] - px[i]) > 0.01:
            mv.append(f"tl.to('#e-p{i}', {{ x: {ux[target[i]] - px[i]:.2f}, duration: 0.8, ease: 'power3.inOut' }}, {tm + 0.1});")
    style = f"""§ .q {{ left: 0; width: {W}px; top: 700px; text-align: center; font-size: 56px; font-weight: 500; color: rgba(255,255,255,0.62); }}
§ .morph {{ left: {left}px; top: 790px; width: {pw:.0f}px; height: {fs * 1.25:.0f}px; }}
§ .ch {{ position: absolute; top: 0; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: {fs}px; line-height: 1.25; color: #fff; white-space: pre; }}
§ .ch.red {{ color: {RED}; }}
§ .ln {{ left: 0; width: {W}px; top: 1000px; text-align: center; font-size: 40px; color: rgba(255,255,255,0.86); }}
§ .bio {{ left: 0; width: {W}px; top: 1075px; text-align: center; font-family: 'SRC Plex Mono', monospace; font-size: 30px; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(255,255,255,0.55); }}
§ .made {{ left: 0; width: {W}px; top: 1300px; display: flex; justify-content: center; align-items: center; gap: 16px; font-size: 32px; color: rgba(255,255,255,0.7); }}
§ .made .flag {{ width: 45px; height: 30px; }}"""
    markup = f"""<div class="glow"></div>
<div class="abs q" id="e-q">Un doute&nbsp;?</div>
<div class="abs morph" id="e-m">{"".join(chars)}</div>
<div class="abs ln" id="e-ln">{line}</div>
<div class="abs bio" id="e-bio">{bio}</div>
<div class="abs made" id="e-made"><span class="flag"></span>{SIGNATURE}</div>"""
    script = f"""  gsap.set(['#e-v', '#e-dot'], {{ opacity: 0 }});
  tl.fromTo('#e-q', {{ opacity: 0, y: 14 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: 'power3.out' }}, 0.1);
  {typed}.forEach((i, k) => tl.fromTo('#e-p' + i, {{ opacity: 0 }}, {{ opacity: 1, duration: 0.05 }}, {type_at} + k * 0.035));
  tl.to('#e-q', {{ opacity: 0, y: -12, duration: 0.35, ease: 'power2.in' }}, {tm});
  {chr(10).join('  ' + x for x in mv).strip()}
  tl.to('#e-p0', {{ opacity: 0, duration: 0.3 }}, {tm + 0.3});
  tl.to('#e-v', {{ opacity: 1, duration: 0.3 }}, {tm + 0.3});
  tl.to('{", ".join(f"#e-p{i}" for i in red)}', {{ color: '{RED}', duration: 0.5 }}, {tm + 0.4});
  tl.fromTo('#e-dot', {{ opacity: 0, y: -20 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: 'back.out(2)' }}, {tm + 0.8});
  tl.to('#e-m', {{ x: {shift}, y: -90, duration: 0.7, ease: 'power3.inOut' }}, {tm + 1.0});
  tl.fromTo(['#e-ln', '#e-bio'], {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: -90, duration: 0.5, ease: 'power2.out', stagger: 0.12 }}, {tm + 1.4});
  tl.fromTo('#e-made', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5 }}, {tm + 1.9});"""
    write(sid, scene(sid, dur, style, markup, script))
    return dur


# ── Fiche du débat, format vertical sombre (capture du site) ─────────────────
CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
          "google-chrome", "chromium", "chrome"]


def fiche_png(debat):
    return os.path.join(FICHES, f"{debat}-vertical-sombre.png")


def capture_fiche(debat):
    """Capture resume.html?format=vertical&theme=sombre en 1080 × 1920, servi en local."""
    browser = next((c for c in CHROME if os.path.exists(c) or shutil.which(c)), None)
    if not browser:
        raise SystemExit("✗ Chrome ou Edge introuvable pour capturer la fiche")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                               "--directory", os.path.abspath(SITE)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/sessions/index.json", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        os.makedirs(FICHES, exist_ok=True)
        out = fiche_png(debat)
        url = f"{base}/resume.html?s={debat}&format=vertical&theme=sombre&export=1"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--window-size={W},{H}", "--virtual-time-budget=6000", f"--screenshot={out}", url],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        server.terminate()
    print(f"✓ fiche capturée : {os.path.relpath(out, ROOT)}")
    return out


def fiche(sid, debat, dur=5.0):
    """La fiche du débat monte du bas, comme un écran de téléphone, puis se rapproche."""
    if not os.path.exists(fiche_png(debat)):
        capture_fiche(debat)
    k = 0.86
    style = f"""§ .sheet {{ left: {W * (1 - k) / 2:.0f}px; top: 120px; width: {W}px; height: {H}px; transform-origin: 0 0; }}
§ .sheet .in {{ position: absolute; inset: 0; border-radius: 34px; overflow: hidden; background: {BG};
  box-shadow: 0 50px 140px rgba(0,0,0,0.7), 0 0 0 2px rgba(255,255,255,0.08); }}
§ .sheet img {{ display: block; width: {W}px; height: {H}px; }}"""
    markup = f"""<div class="glow"></div>
<div class="abs sheet" id="fi-s"><div class="in" id="fi-in"><img src="assets/fiches/{debat}-vertical-sombre.png" alt=""></div></div>"""
    script = f"""  tl.fromTo('#fi-s', {{ opacity: 0, y: 420, scale: {k - 0.06} }}, {{ opacity: 1, y: 0, scale: {k}, duration: 0.8, ease: 'expo.out' }}, 0);
  tl.to('#fi-s', {{ scale: {k + 0.025}, x: -{W * 0.0125:.1f}, duration: {round(dur - 1.1, 2)}, ease: 'sine.inOut' }}, 0.8);
  tl.to('#fi-s', {{ opacity: 0, y: -40, duration: 0.3, ease: 'power2.in' }}, {round(dur - 0.3, 2)});"""
    write(sid, scene(sid, dur, style, markup, script))
    return dur


def cta(sid, dur=3.6):
    style = f"""§ .t {{ left: 0; width: {W}px; top: 640px; text-align: center; font-size: 52px; font-weight: 600; color: rgba(255,255,255,0.8); }}
§ .lg {{ left: 0; width: {W}px; top: 740px; text-align: center; font-size: 132px; }}
§ .bio {{ left: 0; width: {W}px; top: 930px; text-align: center; font-family: 'SRC Plex Mono', monospace; font-size: 32px; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(255,255,255,0.6); }}
§ .made {{ left: 0; width: {W}px; top: 1300px; display: flex; justify-content: center; align-items: center; gap: 16px; font-size: 32px; color: rgba(255,255,255,0.7); }}
§ .made .flag {{ width: 45px; height: 30px; }}"""
    markup = f"""<div class="glow"></div>
<div class="abs t" id="c-t">La fiche complète du débat</div>
<div class="abs logo lg" id="c-lg">vachecker<b>.info</b></div>
<div class="abs bio" id="c-bio">Lien en bio</div>
<div class="abs made" id="c-made"><span class="flag"></span>{SIGNATURE}</div>"""
    script = """  tl.fromTo('#c-t', { opacity: 0, y: 16 }, { opacity: 1, y: 0, duration: 0.45, ease: 'power3.out' }, 0.1);
  tl.fromTo('#c-lg', { opacity: 0, scale: 0.94 }, { opacity: 1, scale: 1, duration: 0.55, ease: 'back.out(1.6)' }, 0.35);
  tl.fromTo('#c-bio', { opacity: 0 }, { opacity: 1, duration: 0.4 }, 0.8);
  tl.fromTo('#c-made', { opacity: 0 }, { opacity: 1, duration: 0.4 }, 1.0);"""
    write(sid, scene(sid, dur, style, markup, script))
    return dur


# ── Montage ──────────────────────────────────────────────────────────────────
def build(reel):
    # Un seul réel à la fois dans compositions/ : chaque construction régénère les siens
    for f in os.listdir(COMP):
        if f.endswith(".html"):
            os.remove(os.path.join(COMP, f))
    scenes, spans, t = [], [], 0.0
    music = None
    if reel == "presentation":
        return build_teaser()
    elif reel == "zemmour":
        plan = [("moment", ("zemmour", 2451.95, 2461.18, 2.0, 7.0)),
                ("fiche", lambda s: fiche(s, "IBZZAtCeH0k")),
                ("cta", lambda s: cta(s))]
    else:
        raise SystemExit(f"réel inconnu : {reel}")
    for name, spec in plan:
        sid = f"{reel}-{name}"
        if callable(spec):
            dur = spec(sid)
        else:
            clip_id, a, b, guess, hold = spec
            dur, toks, g0 = moment(sid, clip_id, a, b, guess, hold, track=3 + len(spans), at=t)
            spans.append((t, toks, g0))
        scenes.append((sid, t, dur))
        t = round(t + dur, 2)
    write_index(reel, scenes, spans, t, None)


def write_index(reel, scenes, spans, total, music):
    """music : None, ou (début dans le morceau, enveloppe [(t, volume)]) pour une musique sous tout le réel."""
    cap_sid = f"{reel}-sous-titres"
    captions(cap_sid, total, spans)
    hosts = "\n".join(
        f'      <div id="el-{sid}" data-composition-id="{sid}" data-composition-src="compositions/{sid}.html" data-start="{s:.3f}" data-duration="{d}"'
        f' data-track-index="1" data-track-kind="graphics" data-width="{W}" data-height="{H}"></div>' for sid, s, d in scenes)
    audio = ""
    if music:
        m_in, env = music
        lane = {"version": 1, "lanes": [{"target": "volume", "points": [{"t": t, "v": v} for t, v in env]}]}
        audio = (f'\n      <audio id="bgm" src="{MUSIC_SRC}" data-start="0" data-duration="{total}" data-media-start="{m_in}"'
                 f" data-track-index=\"9\" data-automation='{json.dumps(lane)}'></audio>")
    index = f"""<!doctype html>
<html lang="fr">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>vachecker.info — réel {reel}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: {W}px; height: {H}px; overflow: hidden; background: {BG}; }}
      #root {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: {BG}; }}
      [data-composition-id="main"] > div[data-composition-src] {{ position: absolute; inset: 0; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="{W}" data-height="{H}">
{hosts}
      <div id="el-{cap_sid}" data-composition-id="{cap_sid}" data-composition-src="compositions/{cap_sid}.html" data-start="0" data-duration="{total}"
        data-track-index="2" data-track-kind="captions" data-width="{W}" data-height="{H}"></div>{audio}
    </div>
    <script>
      window.__timelines["main"] = gsap.timeline({{ paused: true }});
    </script>
  </body>
</html>
"""
    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(index)
    print(f"✓ réel « {reel} » : {len(scenes)} scènes, {total} s" + (f", musique à partir de {music[0]} s" if music else ""))


TEASER = [  # (orateur, début, fin de la phrase) — repère des mots
    ("bardella", 3.56, 6.84),                 # « Dès l'été, j'entends baisser la TVA de 20 % à 5,5 % »
    ("attal", 191.32, 193.88),                # « …que la France soit le réceptacle »
    ("bompard-salaire", 16.16, 17.98),        # « Vous n'avez pas augmenté le salaire des enseignants. »
    ("zemmour", 2456.76, 2461.18),            # « Et il y a heureusement des millions de musulmans qui ne sont pas
                                              #   terroristes. Seulement, tous les terroristes sont musulmans. » (de face, puis de dos)
]


def snap(d, unit=E):
    """Durée arrondie au-dessus sur la grille de la musique (croches)."""
    import math
    return round(math.ceil(d / unit - 1e-6) * unit, 3)


def build_teaser():
    """Teaser épinglé : accroche, quatre phrases, « Vrai ? Faux ? » dans le creux, drop sur la fin."""
    reel = "presentation"
    scenes, spans, t = [], [], 0.0
    d = snap(2.55)
    hook_text(f"{reel}-hook", d)
    scenes.append((f"{reel}-hook", t, d)); t = round(t + d, 3)
    for i, (clip_id, a, b) in enumerate(TEASER):
        sid = f"{reel}-q{i + 1}"
        d = snap(b - a + 0.3)
        toks = quote(sid, clip_id, a, b, d, track=3 + i, at=t)
        if clip_id == "attal":
            toks[0]["w"] = "…" + toks[0]["w"]
        if toks[-1]["w"].endswith(","):
            toks[-1]["w"] = toks[-1]["w"][:-1] + "…"
        spans.append((t, toks, d))
        scenes.append((sid, t, d)); t = round(t + d, 3)
    d = round(4 * Q, 3)
    vraifaux(f"{reel}-vraifaux", d)
    scenes.append((f"{reel}-vraifaux", t, d)); t = round(t + d, 3)
    drop_at = t
    d = 7.6
    ending(f"{reel}-fin", d, "On vérifie les débats, en direct.", tm=round(2 * Q, 3), type_at=0.05, bio="Abonne-toi · lien en bio")
    scenes.append((f"{reel}-fin", t, d)); t = round(t + d, 3)
    total = t
    music_in = round(DROP - drop_at, 3)
    q_end = scenes[-3][1] + scenes[-3][2]          # fin des citations
    env = [(0, 0), (0.25, 0.85), (round(scenes[1][1] - 0.1, 3), 0.85), (round(scenes[1][1] + 0.1, 3), 0.3),
           (round(q_end - 0.05, 3), 0.3), (round(q_end + 0.2, 3), 1.0), (round(total - 1.4, 3), 1.0), (total, 0)]
    music = (music_in, env)
    write_index(reel, scenes, spans, total, music)


def hook_text(sid, dur):
    style = """§ .l { left: 72px; right: 72px; line-height: 1.02; }
§ .l1 { top: 620px; font-size: 96px; font-weight: 800; letter-spacing: -0.02em; color: rgba(255,255,255,0.75); }
§ .l2 { top: 740px; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 150px; }"""
    markup = """<div class="glow"></div>
<div class="abs l l1" id="hk-1">Ils l’ont dit</div>
<div class="abs l l2" id="hk-2">à la télé…</div>"""
    script = f"""  tl.fromTo('#hk-1', {{ opacity: 0, y: 26 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power3.out' }}, 0.1);
  tl.fromTo('#hk-2', {{ opacity: 0, y: 26 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: 'power3.out' }}, {round(0.1 + 2 * Q, 3)});
  tl.to(['#hk-1', '#hk-2'], {{ opacity: 0, y: -20, duration: 0.2, ease: 'power2.in' }}, {round(dur - 0.2, 3)});"""
    write(sid, scene(sid, dur, style, markup, script))


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "fiche":
        capture_fiche(sys.argv[2])
    else:
        build(sys.argv[1] if len(sys.argv) > 1 else "zemmour")

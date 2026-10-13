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


# Débuts de mots mal placés par la transcription, recalés sur le son (énergie par 20 ms)
RETIME = {"attal": {191.32: 191.74}}      # « que » : Attal marque une pause après « anglaise »


def words_of(clip):
    path = os.path.join(ROOT, "assets", "words", CLIPS[clip]["words"])
    d = json.load(open(path, encoding="utf-8"))
    ws = d["words"] if isinstance(d, dict) else d
    fix = RETIME.get(clip, {})
    return [dict(w, s=fix.get(round(w["s"], 2), w["s"])) for w in ws]


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
def moment(sid, clip_id, a, b, guess, hold, track=3, card_id=None, at=0.0, hook=0.0):
    """a, b : début et fin de la phrase (repère des mots). Renvoie (durée, mots minutés).
    hook : durée de l'accroche (« Zemmour : « … » Vrai ou faux ? ») posée sur l'extrait
    dès la première image — c'est aussi la miniature du réel."""
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
§ .hshade {{ position: absolute; inset: 0; background: rgba(8,9,12,0.74); }}
§ .hook {{ left: 60px; width: {W - 120}px; top: {FRAME_Y}px; height: {FRAME_H}px; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 30px; text-align: center; }}
§ .hook .hw {{ font-size: 58px; font-weight: 800; letter-spacing: -0.01em; color: rgba(255,255,255,0.85); }}
§ .hook .hq {{ font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 100px; line-height: 1.06; }}
§ .hook .hv {{ font-size: 76px; font-weight: 800; letter-spacing: -0.01em; color: #f5c84c; }}
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
    hook_markup = (f"""
<div class="abs hook" id="{sid}-hook"><div class="hw">{esc(clip["who"].split()[-1])}&nbsp;:</div>
  <div class="hq">«&nbsp;{esc(c["claim"])}&nbsp;»</div><div class="hv">Vrai ou faux&nbsp;?</div></div>""" if hook else "")
    markup = f"""<div class="glow"></div>
<div class="abs head" id="{sid}-head"><span class="logo">vachecker<b>.info</b></span><span class="q">Vrai ou faux&nbsp;?</span></div>
<div class="abs who" id="{sid}-who"><b>{esc(clip["who"])}</b> · {esc(clip["where"])}</div>
<div class="abs frame" data-layout-allow-overflow><div class="inner" id="{sid}-in" data-layout-allow-overflow>
  <video id="{sid}-video" class="clip" src="assets/clips/{clip["src"]}" data-start="0" data-duration="{dur}" data-media-start="{media_start}"
    data-track-index="{track}" playsinline data-has-audio="true" data-automation='{json.dumps(lane)}'></video>
</div><div class="shade" id="{sid}-shade"></div>{f'<div class="hshade" id="{sid}-hshade"></div>' if hook else ''}</div>{hook_markup}
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
    # Avec une accroche, l'en-tête est là dès la première image (la miniature)
    head_in = "" if hook else f"""  tl.fromTo(['#{sid}-head', '#{sid}-who'], {{ opacity: 0, y: -12 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: 'power3.out', stagger: 0.08 }}, 0.05);
"""
    script = head_in + f"""  gsap.set(['#{sid}-guess', '#{sid}-card'], {{ opacity: 0 }});
  // Vrai ou faux ? — le temps de deviner
  tl.to('#{sid}-shade', {{ opacity: 1, duration: 0.35, ease: 'power1.out' }}, {g0});
  tl.fromTo('#{sid}-guess', {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: 'back.out(1.8)' }}, {g0});
  tl.fromTo('#{sid}-gbar', {{ scaleX: 1 }}, {{ scaleX: 0, duration: {guess}, ease: 'none' }}, {g0});
  tl.to('#{sid}-guess', {{ opacity: 0, scale: 1.05, duration: 0.22, ease: 'power1.in' }}, {round(c0 - 0.12, 2)});
  // La carte tombe
  tl.fromTo('#{sid}-card', {{ opacity: 0, y: -50, filter: 'blur(8px)' }}, {{ opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.6, ease: 'expo.out' }}, {c0});
  tl.fromTo('#{sid}-tag', {{ scale: 1.7, rotation: -6 }}, {{ scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(2.2)' }}, {round(c0 + 0.15, 2)});
  tl.fromTo(['#{sid}-body', '#{sid}-foot'], {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: 'power2.out', stagger: 0.12 }}, {round(c0 + 0.35, 2)});
  tl.to('#{sid}-shade', {{ opacity: 0.35, duration: 0.5 }}, {c0});""" + (f"""
  // Accroche : là dès la première image, puis elle s'efface sur l'extrait
  tl.fromTo('#{sid}-hook', {{ scale: 1.04 }}, {{ scale: 1, duration: 0.6, ease: 'power2.out' }}, 0);
  tl.to('#{sid}-hook', {{ opacity: 0, y: -40, duration: 0.3, ease: 'power2.in' }}, {round(hook - 0.3, 2)});
  tl.to('#{sid}-hshade', {{ opacity: 0, duration: 0.45, ease: 'power1.out' }}, {round(hook - 0.3, 2)});""" if hook else "") + recrops(clip, a, dur, f"#{sid}-in", at)
    write(sid, scene(sid, dur, style, markup, script))
    # Mots minutés depuis le début de la scène (sous-titres)
    toks = [{"w": t["w"], "s": round(t["s"] - a + 0.12, 2), "e": round(t["e"] - a + 0.12, 2)} for t in tokens(clip_id, a, b)]
    return dur, toks, g0


# ── Scène « citation » (teaser) : l'orateur et sa phrase, sans vérification ──
def quote(sid, clip_id, a, b, dur, track, at=0.0, hold=0.08, tail=0.22):
    """a, b : bornes de la phrase (repère des mots) ; dur : calée sur la grille de la musique.
    hold, tail : le son reste plein jusqu'à speech + hold, puis s'éteint à speech + tail
    (plus serré quand quelqu'un d'autre enchaîne aussitôt)."""
    clip = CLIPS[clip_id]
    x, y, cw, ch = clip["crop"]
    k = W / cw
    speech = round(b - a, 2)
    lane = {"version": 1, "lanes": [{"target": "volume", "points": [
        {"t": 0, "v": 0}, {"t": 0.06, "v": 1}, {"t": round(speech + hold, 2), "v": 1}, {"t": round(speech + tail, 2), "v": 0},
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


def verdicts(sid, dur):
    """Pendant le creux avant le drop : les quatre verdicts, un mot à la fois, en alternance sur les noires."""
    words = [("Vrai&nbsp;?", "#73c076", 210), ("Partiellement<br>vrai&nbsp;?", "#d4ac32", 148),
             ("Trompeur&nbsp;?", "#f08a3c", 168), ("Faux&nbsp;?", "#ee6060", 210)]
    style = """§ .w { left: 0; width: 100%; top: 0; height: 100%; display: flex; align-items: center; justify-content: center; text-align: center;
  font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; line-height: 0.95; opacity: 0; }"""
    markup = '<div class="glow"></div>\n' + "\n".join(
        f'<div class="abs w" id="vd-{i}" style="color: {c}; font-size: {fs}px;">{t}</div>' for i, (t, c, fs) in enumerate(words))
    js = []
    for i in range(4):
        t0 = round(i * Q, 3)
        js.append(f"  tl.fromTo('#vd-{i}', {{ opacity: 0, scale: 1.25 }}, {{ opacity: 1, scale: 1, duration: 0.16, ease: 'power3.out' }}, {t0});")
        if i < 3:
            js.append(f"  tl.set('#vd-{i}', {{ opacity: 0 }}, {round((i + 1) * Q, 3)});")
    js.append(f"  tl.to('#vd-3', {{ opacity: 0, scale: 0.92, duration: 0.18, ease: 'power2.in' }}, {round(dur - 0.18, 3)});")
    write(sid, scene(sid, dur, style, markup, "\n".join(js)))


# ── Outro commune : « va. », le point devient une coche verte, avec un « ding » ─
SFX_CHECK = "assets/sfx/check.wav"
LOGO_JSON = os.path.join(ROOT, "..", "..", "social", "logo-va.json")
GREEN = "#73c076"


def check_sound():
    """Le « ding » de la coche, synthétisé ici (deux notes, mi6 puis si6) : aucun fichier sous licence."""
    path = os.path.join(ROOT, SFX_CHECK)
    if os.path.exists(path):
        return
    import struct
    import wave
    sr, n = 48000, int(0.9 * 48000)
    notes = [(0.0, 1318.51, 0.55), (0.075, 1975.53, 0.45)]
    out = []
    for i in range(n):
        t, v = i / sr, 0.0
        for t0, f, a in notes:
            if t >= t0:
                u = t - t0
                env = a * min(1.0, u / 0.004) * math.exp(-u * 7.5)
                v += env * (math.sin(2 * math.pi * f * u) + 0.25 * math.sin(4 * math.pi * f * u) * math.exp(-u * 12))
        out.append(v)
    g = 0.355 / max(abs(x) for x in out)            # crête vers −9 dBFS : discret
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<hh", int(x * g * 32767), int(x * g * 32767)) for x in out))


def logo_outro(sid, dur=3.0, check_at=1.5, track=40):
    """Outro : le logo seul. Le « va. » classique, puis la coche naît du point rouge (même
    épaisseur que lui : on ne voit pas la jointure), se trace, passe au vert ; le logo glisse
    pour rester centré. Un « ding » discret quand la coche est finie (check_at)."""
    import re
    check_sound()
    with open(LOGO_JSON, encoding="utf-8") as f:
        logo = json.load(f)
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", logo["dot"])]
    xs, ys = nums[0::2], nums[1::2]
    dcx, dcy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    r = min(max(xs) - min(xs), max(ys) - min(ys)) / 2      # rayon du point
    h = logo["height"]
    slant = math.tan(math.radians(12))

    def pt(dx, dy):                                          # depuis le centre du point, penché comme l'italique
        x, y = dcx + dx * h, dcy + dy * h
        return x + (dcy - y) * slant, y

    p1, p2, p3 = pt(0, 0), pt(0.09, 0.09), pt(0.42, -0.50)
    k = 0.5                                                  # trait plus fin que le point (≈ les pleins des lettres)
    sw = 2 * r * k
    length = math.dist(p1, p2) + math.dist(p2, p3)
    # cadrage : la forme finale (« va » + coche) centrée ; au départ, le « va. » seul est centré
    x0, y0, y1 = -logo["width"] / 2, -h / 2, h / 2
    x_end = max(p3[0] + sw / 2, max(xs))
    s = 520 / (x_end - x0)
    tx, ty = W / 2 - s * (x0 + x_end) / 2, H / 2 - 40 - s * (y0 + y1) / 2
    shift = s * (x_end - max(xs)) / 2                       # décalage du « va. » seul vers la droite
    draw = 0.62
    svg = f"""<svg class="mk" width="{W}" height="{H}" viewBox="0 0 {W} {H}" aria-hidden="true"><g transform="translate({tx:.1f} {ty:.1f}) scale({s:.4f})">
  <path fill="#fff" d="{logo["v"]} {logo["a"]}"/>
  <path id="lo-ck" fill="none" stroke="{RED}" stroke-width="{sw:.1f}" stroke-linecap="round" stroke-linejoin="round"
    d="M{p1[0]:.1f} {p1[1]:.1f} L{p2[0]:.1f} {p2[1]:.1f} L{p3[0]:.1f} {p3[1]:.1f}" stroke-dasharray="{length:.1f} {length + 2 * sw:.1f}" stroke-dashoffset="{length:.1f}"/>
  <path id="lo-dot" fill="{RED}" d="{logo["dot"]}"/>
</g></svg>"""
    style = f"""§ .wrap {{ left: 0; top: 0; width: {W}px; height: {H}px; }}
§ .mk {{ position: absolute; inset: 0; }}"""
    markup = (f'<div class="abs wrap" id="lo-w">{svg}</div>\n'
              f'<audio id="{sid}-ding" src="{SFX_CHECK}" data-start="{round(check_at - 0.02, 3)}" data-duration="0.9" '
              f'data-track-index="{track}" data-hf-media-start-basis="local"></audio>')
    c0 = round(check_at - draw, 3)
    js = [f"  tl.fromTo('#lo-w', {{ opacity: 0, scale: 0.97, x: {shift:.1f} }}, {{ opacity: 1, scale: 1, duration: 0.55, ease: 'power3.out' }}, 0);",
          # la coche sort du point : le point se resserre à la largeur du trait, le trait part, le point s'efface
          f"  tl.to('#lo-dot', {{ scale: {k}, transformOrigin: '50% 50%', duration: 0.2, ease: 'power2.inOut' }}, {round(c0 - 0.24, 3)});",
          f"  tl.to('#lo-ck', {{ strokeDashoffset: 0, duration: {draw}, ease: 'power3.out' }}, {c0});",
          f"  tl.to('#lo-dot', {{ opacity: 0, duration: 0.05, ease: 'none' }}, {round(c0 + 0.01, 3)});",
          # tracée en rouge, elle passe au vert d'un coup, avec le ding
          f"  tl.to('#lo-ck', {{ stroke: '{GREEN}', duration: 0.18, ease: 'power1.inOut' }}, {round(check_at - 0.08, 3)});",
          f"  tl.to('#lo-w', {{ x: 0, duration: {draw + 0.1}, ease: 'power2.inOut' }}, {c0});",
          f"  tl.to('#lo-w', {{ scale: 1.015, duration: 0.18, yoyo: true, repeat: 1, ease: 'sine.inOut' }}, {check_at});"]
    write(sid, scene(sid, dur, style, markup, "\n".join(js), bg=BG))
    return dur


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


def ending(sid, dur, line, tm=1.9, type_at=0.45, bio="Lien en bio", made=True, leave=False):
    """« Va checker l'info ! » se replie en vachecker.info (lettres placées d'après la chasse de la police).
    bio=None, made=False : seulement l'adresse et la ligne ; leave : tout s'efface à la fin (une scène suit)."""
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
<div class="abs ln" id="e-ln">{line}</div>""" + (f"""
<div class="abs bio" id="e-bio">{bio}</div>""" if bio else "") + (f"""
<div class="abs made" id="e-made"><span class="flag"></span>{SIGNATURE}</div>""" if made else "")
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
  tl.fromTo({['#e-ln', '#e-bio'] if bio else ['#e-ln']}, {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: -90, duration: 0.5, ease: 'power2.out', stagger: 0.12 }}, {tm + 1.4});""" + (f"""
  tl.fromTo('#e-made', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5 }}, {tm + 1.9});""" if made else "") + (f"""
  tl.to(['#e-m', '#e-ln'], {{ opacity: 0, y: -130, duration: 0.25, ease: 'power2.in' }}, {round(dur - 0.25, 3)});""" if leave else "")
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


MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def intro_fiche(sid, debat, dur=2.6):
    """Avant la fiche : on passe de la phrase au débat entier (chiffres lus dans la fiche publiée)."""
    with open(os.path.join(SITE, "sessions", f"{debat}.fiche.json"), encoding="utf-8") as f:
        fi = json.load(f)
    n = fi["totaux"]["affirmations"]
    y, m, d = (int(x) for x in fi["date"].split("-"))
    titre = fi["titre"].replace(" — ", " · ")
    style = """§ .ln { left: 72px; right: 72px; text-align: center; opacity: 0; }
§ .k { top: 620px; font-size: 54px; font-weight: 700; color: rgba(255,255,255,0.75); }
§ .num { top: 700px; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 250px; line-height: 1; color: #ee6060; font-variant-numeric: tabular-nums; }
§ .lbl { top: 960px; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 92px; line-height: 1; }
§ .sub { top: 1110px; font-size: 34px; line-height: 1.4; color: rgba(255,255,255,0.62); }"""
    markup = f"""<div class="glow"></div>
<div class="abs ln k" id="if-k">Et sur tout le débat&nbsp;?</div>
<div class="abs ln num" id="if-n">{n}</div>
<div class="abs ln lbl" id="if-l">affirmations vérifiées</div>
<div class="abs ln sub" id="if-t">{esc(titre)}<br>{d} {MOIS[m - 1]} {y}</div>"""
    js = ["  tl.fromTo('#if-k', { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.45, ease: 'power3.out' }, 0.1);",
          "  tl.fromTo('#if-n', { opacity: 0, y: 24 }, { opacity: 1, y: 0, duration: 0.45, ease: 'power3.out' }, 0.35);",
          # le nombre monte jusqu'au total du débat
          f"  const nEl = document.getElementById('if-n'); const cnt = {{ v: 0 }};",
          f"  tl.fromTo(cnt, {{ v: 0 }}, {{ v: {n}, duration: 0.8, ease: 'power2.out', onUpdate: () => {{ nEl.textContent = String(Math.round(cnt.v)); }} }}, 0.35);",
          "  tl.fromTo('#if-l', { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.45, ease: 'power3.out' }, 0.6);",
          "  tl.fromTo('#if-t', { opacity: 0 }, { opacity: 1, duration: 0.45, ease: 'power2.out' }, 0.95);",
          f"  tl.to(['#if-k', '#if-n', '#if-l', '#if-t'], {{ opacity: 0, y: -40, duration: 0.3, ease: 'power2.in', stagger: 0.04 }}, {round(dur - 0.35, 3)});"]
    write(sid, scene(sid, dur, style, markup, "\n".join(js)))
    return dur


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
    elif reel == "outro":
        plan = [("logo", lambda s: logo_outro(s))]
    elif reel == "zemmour":
        plan = [("moment", ("zemmour", 2451.95, 2461.18, 2.0, 7.0, 2.4)),
                ("transition", lambda s: intro_fiche(s, "IBZZAtCeH0k")),
                ("fiche", lambda s: fiche(s, "IBZZAtCeH0k")),
                ("cta", lambda s: cta(s)),
                ("logo", lambda s: logo_outro(s))]
    else:
        raise SystemExit(f"réel inconnu : {reel}")
    for name, spec in plan:
        sid = f"{reel}-{name}"
        if callable(spec):
            dur = spec(sid)
        else:
            clip_id, a, b, guess, hold, *hook = spec
            dur, toks, g0 = moment(sid, clip_id, a, b, guess, hold, track=3 + len(spans), at=t, hook=hook[0] if hook else 0.0)
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
    ("attal", 191.74, 197.09,                 # « …que la France soit le réceptacle, reçoive l'intégralité des
     {"hold": 0.12, "tail": 0.17}),           #   personnes migrantes au Royaume-Uni. » — la journaliste enchaîne à 197,16
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
    hook_mosaic(f"{reel}-hook", d)
    scenes.append((f"{reel}-hook", t, d)); t = round(t + d, 3)
    for i, (clip_id, a, b, *opt) in enumerate(TEASER):
        sid = f"{reel}-q{i + 1}"
        d = snap(b - a + 0.3)
        toks = quote(sid, clip_id, a, b, d, track=3 + i, at=t, **(opt[0] if opt else {}))
        if clip_id == "attal":
            toks[0]["w"] = "…" + toks[0]["w"]
        if toks[-1]["w"].endswith(","):
            toks[-1]["w"] = toks[-1]["w"][:-1] + "…"
        spans.append((t, toks, d))
        scenes.append((sid, t, d)); t = round(t + d, 3)
    d = round(4 * Q, 3)
    verdicts(f"{reel}-verdicts", d)
    scenes.append((f"{reel}-verdicts", t, d)); t = round(t + d, 3)
    drop_at = t
    # Après le drop, une phrase musicale entière : 8 mesures de 4 temps, 2 par scène
    bars2 = round(8 * Q, 3)
    ending(f"{reel}-fin", bars2, "On vérifie les débats, en direct.", tm=round(2 * Q, 3), type_at=0.05,
           bio=None, made=False, leave=True)
    scenes.append((f"{reel}-fin", t, bars2)); t = round(t + bars2, 3)
    for name, fn in (("promesse", kinetic), ("qui", qui), ("outro", outro)):
        fn(f"{reel}-{name}", bars2)
        scenes.append((f"{reel}-{name}", t, bars2)); t = round(t + bars2, 3)
    # Outro commune (logo seul) : la musique s'éteint à la fin de la phrase musicale, juste avant
    main_end = t
    d = logo_outro(f"{reel}-logo")
    scenes.append((f"{reel}-logo", t, d)); t = round(t + d, 3)
    total = t
    music_in = round(DROP - drop_at, 3)
    q_end = scenes[4][1] + scenes[4][2]           # fin des citations (après l'accroche et 4 citations)
    env = [(0, 0), (0.25, 0.85), (round(scenes[1][1] - 0.1, 3), 0.85), (round(scenes[1][1] + 0.1, 3), 0.3),
           (round(q_end - 0.05, 3), 0.3), (round(q_end + 0.2, 3), 1.0), (round(main_end - 1.2, 3), 1.0), (round(main_end + 0.3, 3), 0), (total, 0)]
    music = (music_in, env)
    write_index(reel, scenes, spans, total, music)


# Les quatre visages, plein cadre (2 × 2), en vidéo muette : fenêtres portrait (x, y, l, h)
# calées sur le visage, hors logos et bandeaux. starts : début dans l'extrait (s).
FACES = [("bardella", "Bardella", (826, 40, 427, 760)), ("attal", "Attal", (747, 40, 427, 760)),
         ("bompard-salaire", "Bompard", (1243, 140, 354, 630)), ("zemmour", "Zemmour", (1137, 120, 427, 760))]
TW, TH = W // 2, H // 2


def mosaic(sid, dur, starts, track, over, over_style, names=False, pop=False, spots=()):
    """Mosaïque des quatre orateurs. spots : [(instant, indice du visage)] où il s'éclaire ;
    pop : les visages entrent un par un, sur les temps ; over : texte par-dessus."""
    tiles, style = [], [f"§ .tile {{ width: {TW}px; height: {TH}px; overflow: hidden; background: #000; }}",
                         "§ .tile video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }",
                         "§ .tile .dim { position: absolute; inset: 0; background: #08090c; opacity: 0.55; }",
                         # noms près du centre : bas des tuiles du haut, haut des tuiles du bas
                         "§ .tile .nm { position: absolute; left: 0; right: 0; text-align: center; font-size: 44px; font-weight: 800; letter-spacing: 0.02em; text-shadow: 0 2px 18px rgba(0,0,0,0.8); }",
                         "§ .tile .nm.up { bottom: 44px; }",
                         "§ .tile .nm.down { top: 44px; }",
                         "§ .band { left: 0; width: 100%; top: 0; height: 100%; background: radial-gradient(1000px 520px at 50% 50%, rgba(8,9,12,0.82), rgba(8,9,12,0.35) 60%, transparent 85%); }"]
    for i, (clip_id, name, (x, y, cw, ch)) in enumerate(FACES):
        clip = CLIPS[clip_id]
        k = TW / cw
        style.append(f"§ #{sid}-t{i} {{ left: {(i % 2) * TW}px; top: {(i // 2) * TH}px; }}")
        style.append(f"§ #{sid}-i{i} {{ position: absolute; left: {-x * k:.1f}px; top: {-y * k:.1f}px; width: {1920 * k:.1f}px; height: {1080 * k:.1f}px; }}")
        tiles.append(f"""<div class="abs tile" id="{sid}-t{i}" data-layout-allow-overflow><div id="{sid}-i{i}" data-layout-allow-overflow>
  <video id="{sid}-v{i}" class="clip" src="assets/clips/{clip["src"]}" data-start="0" data-duration="{dur}" data-media-start="{starts[i]}"
    data-track-index="{track + i}" muted playsinline></video></div><div class="dim" id="{sid}-d{i}"></div>{f'<div class="nm {"up" if i < 2 else "down"}" id="{sid}-n{i}">{name}</div>' if names else ''}</div>""")
    js = []
    if pop:
        js.append(f"  gsap.set({[f'#{sid}-t{i}' for i in range(4)]}, {{ opacity: 0 }});")
        for i in range(4):
            js.append(f"  tl.fromTo('#{sid}-t{i}', {{ opacity: 0, scale: 1.15 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: 'power3.out' }}, {round(i * Q, 3)});")
    for t, i in spots:
        js.append(f"  tl.to('#{sid}-d{i}', {{ opacity: 0.02, duration: 0.08 }}, {t});")
        js.append(f"  tl.to('#{sid}-d{i}', {{ opacity: 0.55, duration: 0.25 }}, {round(t + Q * 0.85, 3)});")
    markup = "\n".join(tiles) + f'\n<div class="abs band"></div>\n{over}'
    return "\n".join(style) + "\n" + over_style, markup, js


def hook_mosaic(sid, dur):
    """Accroche : les quatre visages et « Ils l'ont dit à la télé… », dès la première image (la miniature)."""
    over = """<div class="abs l l1" id="hk-1">Ils l’ont dit</div>
<div class="abs l l2" id="hk-2">à la télé…</div>"""
    over_style = """§ .l { left: 0; width: 100%; text-align: center; line-height: 1.02; text-shadow: 0 4px 40px rgba(0,0,0,0.6); }
§ .l1 { top: 770px; font-size: 104px; font-weight: 800; letter-spacing: -0.02em; }
§ .l2 { top: 890px; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 170px; color: #fff; }"""
    style, markup, js = mosaic(sid, dur, [4.0, 20.0, 15.5, 30.0], 20, over, over_style,
                               spots=[(round(i * Q, 3), i) for i in range(4)])
    js += [f"  tl.fromTo('#hk-2', {{ scale: 1 }}, {{ scale: 1.07, duration: 0.09, yoyo: true, repeat: 1, ease: 'power2.out' }}, {round(2 * Q, 3)});",
           f"  tl.to(['#hk-1', '#hk-2'], {{ opacity: 0, y: -20, duration: 0.2, ease: 'power2.in' }}, {round(dur - 0.2, 3)});"]
    write(sid, scene(sid, dur, style, markup, "\n".join(js)))


def kinetic(sid, dur):
    """Après le drop : la promesse se construit, une ligne sur les temps forts."""
    lines = [("Chaque affirmation", "k1", 0), ("vérifiée", "k2", 2), ("en direct,", "k3", 4), ("sources à l’appui.", "k4", 6)]
    style = """§ .k { left: 0; width: 100%; text-align: center; line-height: 1; opacity: 0; }
§ .k1 { top: 640px; font-size: 86px; font-weight: 800; letter-spacing: -0.02em; }
§ .k2 { top: 750px; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 190px; color: #ee6060; }
§ .k3 { top: 980px; font-size: 86px; font-weight: 800; letter-spacing: -0.02em; }
§ .k4 { top: 1100px; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 92px; color: rgba(255,255,255,0.86); }"""
    markup = '<div class="glow"></div>\n' + "\n".join(f'<div class="abs k {c}" id="kn-{c}">{t}</div>' for t, c, _ in lines)
    js = [f"  tl.fromTo('#kn-{c}', {{ opacity: 0, scale: 1.35 }}, {{ opacity: 1, scale: 1, duration: 0.18, ease: 'power3.out' }}, {round(b * Q, 3)});"
          for _, c, b in lines]
    js.append(f"  tl.to({[f'#kn-{c}' for _, c, _ in lines]}, {{ opacity: 0, y: -40, duration: 0.22, ease: 'power2.in', stagger: 0.03 }}, {round(dur - 0.3, 3)});")
    write(sid, scene(sid, dur, style, markup, "\n".join(js)))


def qui(sid, dur):
    """Les quatre visages reviennent, un par temps, puis « Qui dit vrai ? »."""
    over = '<div class="abs qq" id="qq">Qui dit vrai&nbsp;?</div>'
    over_style = """§ .band { opacity: 0; }
§ .qq { left: 0; width: 100%; top: 880px; text-align: center; font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 150px; line-height: 1; opacity: 0;
  text-shadow: 0 4px 40px rgba(0,0,0,0.7); }"""
    spots = [(round((4 + i) * Q, 3), i) for i in range(4)]
    style, markup, js = mosaic(sid, dur, [7.0, 15.0, 10.5, 26.0], 30, over, over_style, names=True, pop=True, spots=spots)
    tiles = [f"#{sid}-t{i}" for i in range(4)]
    js += [f"  tl.to({[f'#{sid}-n{i}' for i in range(4)]}, {{ opacity: 0, duration: 0.15 }}, {round(4 * Q - 0.05, 3)});",
           f"  tl.to('#{sid}-band', {{ opacity: 1, duration: 0.2 }}, {round(4 * Q, 3)});",
           f"  tl.fromTo('#qq', {{ opacity: 0, scale: 1.3 }}, {{ opacity: 1, scale: 1, duration: 0.2, ease: 'power3.out' }}, {round(4 * Q, 3)});",
           f"  tl.to({['#qq'] + tiles}, {{ opacity: 0, duration: 0.25, ease: 'power2.in' }}, {round(dur - 0.25, 3)});"]
    write(sid, scene(sid, dur, style, markup.replace('class="abs band"', f'class="abs band" id="{sid}-band"'), "\n".join(js)))


def outro(sid, dur):
    """Le logo revient, sur les temps ; « Abonne-toi », la signature."""
    style = f"""§ .lg {{ left: 0; width: {W}px; top: 760px; text-align: center; font-size: 150px; }}
§ .bio {{ left: 0; width: {W}px; top: 980px; text-align: center; font-family: 'SRC Plex Mono', monospace; font-size: 34px; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(255,255,255,0.7); }}
§ .made {{ left: 0; width: {W}px; top: 1300px; display: flex; justify-content: center; align-items: center; gap: 16px; font-size: 32px; color: rgba(255,255,255,0.7); }}
§ .made .flag {{ width: 45px; height: 30px; }}"""
    markup = f"""<div class="glow"></div>
<div class="abs logo lg" id="o-lg"><span id="o-lgi" style="display: inline-block;">vachecker<b>.info</b></span></div>
<div class="abs bio" id="o-bio">Abonne-toi · lien en bio</div>
<div class="abs made" id="o-made"><span class="flag"></span>{SIGNATURE}</div>"""
    js = ["  tl.fromTo('#o-lg', { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.35, ease: 'back.out(1.8)' }, 0);",
          f"  tl.fromTo('#o-bio', {{ opacity: 0, y: 14 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: 'power3.out' }}, {round(Q, 3)});",
          f"  tl.fromTo('#o-made', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4 }}, {round(2 * Q, 3)});"]
    # Le logo bat la mesure jusqu'à la fin
    for b in range(2, int(dur / Q)):
        js.append(f"  tl.to('#o-lgi', {{ scale: 1.045, duration: 0.07, yoyo: true, repeat: 1, ease: 'power2.out' }}, {round(b * Q, 3)});")
    write(sid, scene(sid, dur, style, markup, "\n".join(js)))


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

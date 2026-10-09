"""Génère les scènes de la démo SOURCÉ (compositions/*.html) et index.html.

Les quatre moments partagent un même modèle : l'extrait du débat (son
d'origine, baissé sous la carte), le badge de l'orateur, la puce
« analyse en direct », la carte de vérification de l'extension (reprise de
extension/overlay.css et content.js) et la rangée de pastilles de verdict.
Relancer après toute modification : python scripts/build_scenes.py
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMP = os.path.join(ROOT, "compositions")
os.makedirs(COMP, exist_ok=True)

ACCENT = {"pending": "#9aa6b4", "vrai": "#73c076", "partiel": "#d4ac32", "trompeur": "#eba941", "faux": "#ee6060"}
FOOT_COLOR = {"vrai": "#73c076", "partiel": "#d4ac32", "trompeur": "#eba941", "faux": "#f9786a"}
DOT_ORDER = ["faux", "partiel", "trompeur", "vrai"]

FONTS = """
@font-face { font-family: 'SRC Archivo'; font-style: normal; font-weight: 100 900; src: url('assets/fonts/archivo-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Newsreader'; font-style: normal; font-weight: 200 800; src: url('assets/fonts/newsreader-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Newsreader'; font-style: italic; font-weight: 200 800; src: url('assets/fonts/newsreader-italic-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Plex Mono'; font-style: normal; font-weight: 400; src: url('assets/fonts/ibm-plex-mono-400-latin.woff2') format('woff2'); }
@font-face { font-family: 'SRC Plex Mono'; font-style: normal; font-weight: 500; src: url('assets/fonts/ibm-plex-mono-500-latin.woff2') format('woff2'); }
"""

# Les scènes, dans l'ordre, avec leur durée (s)
SCENES = [
    ("s01-volume", 5.0),
    ("s02-promesse", 5.0),
    ("m03-faure", None),
    ("m04-bardella", None),
    ("m05-attal", None),
    ("m06-bompard", None),
    ("s07-fiche", 16.0),
    ("s08-fin", 8.0),
]

MOMENTS = {
    "m03-faure": dict(
        src="leg-faure-meloni-3608.mp4", media_start=10.85, card_in=13.9, hold=12.0, index=0, verdict="faux", tag="FAUX",
        who="Olivier Faure", ts="1:00:29",
        claim="Giorgia Meloni a régularisé 450 000 sans-papiers en Italie",
        body="Les 452 000 sont des entrées de travailleurs recrutés à l'étranger, autorisées par le décret « flussi » de 2023 pour 2023-2025 : pas une régularisation de sans-papiers. Une partie a servi de régularisation de fait, au moins 150 000 personnes selon le quotidien Avvenire.",
        source="Il Fatto Quotidiano", foot="✗ démenti · 85%", wipe_in=True,
        speakers=[(0, "Olivier Faure"), (16.4, "Jordan Bardella")],
    ),
    "m04-bardella": dict(
        src="leg-bardella-essence-1346.mp4", media_start=14.1, card_in=5.7, hold=11.0, index=1, verdict="partiel", tag="PARTIEL",
        who="Jordan Bardella", ts="22:40",
        claim="Les taxes représentent 60 à 70 % du prix du carburant à la pompe",
        body="En 2024, l'accise et la TVA ont représenté en moyenne 54 % du prix du gazole et du SP95-E10, selon le ministère chargé de l'énergie : plus de la moitié du prix, mais moins que les 60 à 70 % annoncés.",
        source="Assemblée nationale", foot="≈ nuancé · 85%",
        speakers=[(0, "Jordan Bardella")], scrim="linear-gradient(90deg, rgba(0,0,0,0) 38%, rgba(0,0,0,0.62) 100%)",
    ),
    "m05-attal": dict(
        src="leg-attal-binationaux-160.mp4", media_start=7.5, card_in=16.6, hold=14.0, duck_at=19.9, index=2, verdict="trompeur", tag="TROMPEUR",
        who="Gabriel Attal", ts="2:48",
        claim="Jordan Bardella présente les binationaux comme plus corruptibles et moins dignes de confiance pour occuper des postes à responsabilité",
        body="Le RN voulait réserver quelques dizaines d'emplois très sensibles (défense, nucléaire, renseignement) aux Français sans autre nationalité, au nom du risque d'ingérence étrangère. Jordan Bardella n'a pas dit que les binationaux étaient « plus corruptibles », et la mesure ne visait pas les postes à responsabilité en général.",
        source="Public Sénat", foot="⚠ trompeur · 80%",
        speakers=[(0, "Gabriel Attal"), (16.7, "Jordan Bardella"), (19.9, "Gabriel Attal")],
    ),
    "m06-bompard": dict(
        src="lci-bompard-decret-1890.mp4", media_start=14.4, card_in=14.1, hold=11.0, index=3, verdict="vrai", tag="VRAI",
        who="Manuel Bompard", ts="31:44",
        claim="Un décret du 22 février 2024 signé par Gabriel Attal a prévu 700 millions d'euros de coupes budgétaires dans l'enseignement scolaire",
        body="Le décret du 21 février 2024, publié le 22 et signé par Gabriel Attal, a annulé 10 milliards d'euros de crédits, dont 692 millions pour l'enseignement scolaire.",
        source="Légifrance", foot="✓ confirmé · 95%",
        speakers=[(0, "Manuel Bompard"), (16.1, "Prisca Thévenot"), (19.7, "Manuel Bompard")],
        # recadrage : le bandeau défilant de LCI (sans rapport) sort du cadre
        crop="transform: scale(1.17); transform-origin: 20% 0;",
        outro_to_fiche=True,
    ),
}


def moment_dur(m):
    """Fin de lecture de la carte + sortie (et, pour le dernier moment, le passage vers la fiche)."""
    end = m["card_in"] + 0.9 + m["hold"]
    return round(end + (1.9 if m.get("outro_to_fiche") else 0.55), 2)


def volume_points(m, dur):
    """Son du débat : plein pendant la phrase, puis baissé sous la carte jusqu'à un murmure
    (la suite du débat n'est pas vérifiée dans la vidéo)."""
    fade_in = 0.5 if m.get("wipe_in") else 0.12
    duck = m.get("duck_at", m["card_in"] + 0.6)
    return [(0, 0), (fade_in, 1), (duck, 1), (round(duck + 0.5, 2), 0.25), (round(duck + 3.5, 2), 0.08), (round(dur - 0.5, 2), 0.08), (dur, 0)]


SCENES = [(sid, dur if dur is not None else moment_dur(MOMENTS[sid])) for sid, dur in SCENES]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dots_html(sid, index):
    out = []
    for i, v in enumerate(DOT_ORDER):
        on = i < index
        fill = f'<span class="dot-fill" id="{sid}-dot{i}" style="background:{ACCENT[v]};{"" if on or i == index else "opacity:0;"}"></span>'
        out.append(f'<i class="dot">{fill if (on or i == index) else ""}</i>')
    return "".join(out)


def moment_html(sid, dur, m):
    v = m["verdict"]
    automation = json.dumps({"version": 1, "lanes": [{"target": "volume", "points": [{"t": t, "v": val} for t, val in volume_points(m, dur)]}]})
    p = f'[data-composition-id="{sid}"]'
    crop = m.get("crop", "")
    wipe = f'<div class="wipe" id="{sid}-wipe" data-layout-allow-overflow></div>' if m.get("wipe_in") else ""
    outro = f'<div class="wipe-out" id="{sid}-wipeout" data-layout-allow-overflow></div>' if m.get("outro_to_fiche") else ""
    ci = m["card_in"]
    resolve = ci + 0.9
    card_out = round(resolve + m["hold"], 2)
    reps = lambda cycle: max(0, int(dur // cycle) - 1)
    js_outro = ""
    if m.get("outro_to_fiche"):
        js_outro = f"""
  // Sortie vers la fiche : le blanc arrive de la droite, les pastilles glissent au centre
  tl.to(['#{sid}-badge', '#{sid}-chip'], {{ opacity: 0, duration: 0.3, ease: 'power1.in' }}, {dur - 1.45:.2f});
  tl.fromTo('#{sid}-wipeout', {{ xPercent: 100 }}, {{ xPercent: 0, duration: 0.8, ease: 'power3.inOut' }}, {dur - 1.3:.2f});
  tl.to('#{sid}-dots', {{ x: 869, y: 407, scale: 2.5, duration: 0.95, ease: 'power3.inOut' }}, {dur - 1.25:.2f});"""
    else:
        js_outro = ""
    wipe_js = f"tl.fromTo('#{sid}-wipe', {{ xPercent: 0 }}, {{ xPercent: -100, duration: 0.7, ease: 'power3.inOut' }}, 0);" if m.get("wipe_in") else ""
    over_in = 0.55 if m.get("wipe_in") else 0.0
    speakers = m["speakers"]
    uniq = []
    for _, n in speakers:
        if n not in uniq:
            uniq.append(n)
    names_html = "".join(f'<span id="{sid}-name{i}">{esc(n)}</span>' for i, n in enumerate(uniq))
    sw = []
    for i, n in enumerate(uniq):
        sw.append(f"tl.set('#{sid}-name{i}', {{ opacity: {1 if n == speakers[0][1] else 0} }}, 0);")
    for (t, n), (_, prev) in zip(speakers[1:], speakers):
        a, b = uniq.index(prev), uniq.index(n)
        sw.append(f"tl.fromTo('#{sid}-name{a}', {{ opacity: 1 }}, {{ opacity: 0, duration: 0.18, immediateRender: false }}, {t:.2f});")
        sw.append(f"tl.fromTo('#{sid}-name{b}', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.22, immediateRender: false }}, {t + 0.12:.2f});")
        sw.append(f"tl.fromTo('#{sid}-badge', {{ scale: 1 }}, {{ scale: 1.045, duration: 0.2, ease: 'power2.out', yoyo: true, repeat: 1, immediateRender: false }}, {t:.2f});")
    switch_js = "\n  ".join(sw)
    return f"""<!doctype html>
<html lang="fr">
<head><meta charset="UTF-8" /></head>
<body>
<template id="{sid}-template">
<div data-composition-id="{sid}" data-width="1920" data-height="1080" data-duration="{dur}">
<style>
{FONTS}
{p} {{ position: absolute; inset: 0; overflow: hidden; background: #0b0c0f; font-family: 'SRC Archivo', sans-serif; color: #fff; }}
{p} .vwrap {{ position: absolute; inset: 0; overflow: hidden; {crop} }}
{p} video {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
{p} .scrim {{ position: absolute; inset: 0; background: {m.get('scrim', 'linear-gradient(90deg, rgba(0,0,0,0) 52%, rgba(0,0,0,0.30) 100%)')}; }}
{p} .names {{ display: grid; }}
{p} .names > span {{ grid-area: 1 / 1; white-space: nowrap; }}
{p} .wipe, {p} .wipe-out {{ position: absolute; inset: 0; background: #ffffff; z-index: 5; }}
{p} .wipe-out {{ z-index: 3; }}
{p} .pill {{ position: absolute; background: rgba(16,18,22,0.72); border: 2px solid rgba(255,255,255,0.12); box-shadow: 0 6px 22px rgba(0,0,0,0.4); border-radius: 999px; z-index: 4; }}
{p} .badge {{ top: 24px; left: 24px; display: flex; align-items: flex-end; gap: 15px; padding: 17px 26px 17px 22px; font-weight: 600; font-size: 24px; line-height: 1; color: rgba(255,255,255,0.94); }}
{p} .eq {{ display: flex; align-items: flex-end; gap: 4px; height: 22px; }}
{p} .eq i {{ display: block; width: 5px; height: 22px; border-radius: 2px; background: #e0324f; transform-origin: 50% 100%; }}
{p} .chip {{ top: 24px; right: 24px; height: 64px; display: flex; align-items: center; gap: 15px; padding: 0 24px 0 22px; }}
{p} .ping {{ position: relative; width: 14px; height: 14px; }}
{p} .ping i {{ position: absolute; inset: 0; border-radius: 50%; background: #73c076; display: block; }}
{p} .brand {{ font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 26px; }}
{p} .brand b, {p} .cbrand b {{ font-weight: 500; }}
{p} .brand b {{ color: #e0324f; }}
{p} .chip .sub {{ font-size: 19px; color: rgba(255,255,255,0.7); }}
{p} .dots {{ top: 112px; left: 24px; width: 134px; height: 42px; display: flex; align-items: center; gap: 10px; padding: 0 18px; transform-origin: 50% 50%; }}
{p} .dot {{ position: relative; display: block; width: 16px; height: 16px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.35); box-sizing: border-box; }}
{p} .dot-fill {{ position: absolute; inset: -2px; border-radius: 50%; display: block; }}
{p} .card {{ position: absolute; top: 104px; right: 24px; width: 650px; border-radius: 27px; overflow: hidden; z-index: 4;
  background: linear-gradient(180deg, rgba(22,24,29,0.93), rgba(15,16,20,0.92)); border: 2px solid rgba(255,255,255,0.1);
  box-shadow: 0 40px 110px rgba(0,0,0,0.55), 0 12px 60px color-mix(in srgb, var(--accent) 14%, transparent); color: rgba(255,255,255,0.95); }}
{p} .bar {{ position: relative; height: 5px; overflow: hidden; }}
{p} .bar .fill {{ position: absolute; inset: 0; background: var(--accent); }}
{p} .bar .shimmer {{ position: absolute; top: 0; left: 0; width: 30%; height: 100%; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.6), transparent); }}
{p} .inner {{ padding: 26px 32px 28px; }}
{p} .head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
{p} .cbrand {{ font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; font-size: 23px; color: rgba(255,255,255,0.8); }}
{p} .cbrand .d {{ font-style: normal; font-size: 15px; margin-right: 8px; color: var(--accent); }}
{p} .cbrand b {{ color: var(--accent); }}
{p} .tag {{ display: grid; justify-items: end; }}
{p} .tag > span {{ grid-area: 1 / 1; display: inline-flex; align-items: center; gap: 11px; padding: 7px 17px 7px 15px; border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 15%, transparent); border: 2px solid color-mix(in srgb, var(--accent) 42%, transparent);
  color: var(--accent); font-weight: 700; font-size: 18px; letter-spacing: 0.08em; white-space: nowrap; }}
{p} .spin {{ display: block; width: 15px; height: 15px; border-radius: 50%; border: 2.5px solid rgba(255,255,255,0.25); border-top-color: rgba(255,255,255,0.85); box-sizing: border-box; }}
{p} .tdot {{ display: block; width: 10px; height: 10px; border-radius: 50%; background: var(--accent); }}
{p} .meta {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 9px; }}
{p} .who {{ font-size: 18px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }}
{p} .ts {{ font-family: 'SRC Plex Mono', monospace; font-size: 17px; color: rgba(255,255,255,0.72); border: 2px solid rgba(255,255,255,0.12); background: rgba(255,255,255,0.07); border-radius: 8px; padding: 2px 10px; }}
{p} .claim {{ margin: 0 0 20px; font-family: 'SRC Newsreader', serif; font-style: italic; font-size: 32px; line-height: 1.32; color: rgba(255,255,255,0.97); }}
{p} .stack {{ display: grid; }}
{p} .stack > * {{ grid-area: 1 / 1; }}
{p} .checking {{ display: flex; align-items: flex-start; gap: 14px; font-size: 21px; color: rgba(255,255,255,0.62); padding-top: 4px; }}
{p} .checking .spin {{ width: 22px; height: 22px; border-width: 3px; }}
{p} .body {{ margin: 0; font-size: 22px; line-height: 1.5; color: rgba(255,255,255,0.8); }}
{p} .foot {{ display: flex; justify-content: space-between; gap: 16px; margin-top: 20px; padding-top: 20px; border-top: 2px solid rgba(255,255,255,0.09); font-size: 18px; }}
{p} .src {{ font-family: 'SRC Plex Mono', monospace; color: rgba(255,255,255,0.68); border-bottom: 1px dotted rgba(255,255,255,0.35); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
{p} .verdict {{ font-weight: 500; white-space: nowrap; color: {FOOT_COLOR[v]}; }}
</style>

<div class="vwrap" data-layout-allow-overflow>
  <video id="{sid}-video" class="clip" src="assets/clips/{m['src']}" data-start="0" data-duration="{dur}" data-media-start="{m['media_start']}"
    data-track-index="{2 + m['index']}" playsinline data-has-audio="true" data-automation='{automation}'></video>
</div>
<div class="scrim"></div>
{outro}
<div class="pill badge" id="{sid}-badge"><span class="eq"><i id="{sid}-eq0"></i><i id="{sid}-eq1"></i><i id="{sid}-eq2"></i></span><span class="names">{names_html}</span></div>
<div class="pill dots" id="{sid}-dots">{dots_html(sid, m['index'])}</div>
<div class="pill chip" id="{sid}-chip"><span class="ping"><i id="{sid}-ring"></i><i></i></span><span class="brand">SOURC<b>É</b></span><span class="sub">analyse en direct</span></div>

<div class="card" id="{sid}-card" style="--accent: {ACCENT['pending']};">
  <div class="bar"><div class="fill"></div><div class="shimmer" id="{sid}-shimmer" data-layout-allow-overflow></div></div>
  <div class="inner">
    <div class="head">
      <span class="cbrand"><span class="d">◆</span>SOURC<b>É</b></span>
      <span class="tag">
        <span id="{sid}-tagp"><span class="spin" id="{sid}-spin1"></span>VÉRIFICATION</span>
        <span id="{sid}-tagv"><span class="tdot"></span>{m['tag']}</span>
      </span>
    </div>
    <div class="meta"><span class="who">{esc(m['who'])}</span><span class="ts">▶ {m['ts']}</span></div>
    <p class="claim">« {esc(m['claim'])} »</p>
    <div class="stack">
      <div class="checking" id="{sid}-checking"><span class="spin" id="{sid}-spin2"></span><span>Recoupement des sources…</span></div>
      <p class="body" id="{sid}-body">{esc(m['body'])}</p>
    </div>
    <div class="foot" id="{sid}-foot"><span class="src">{esc(m['source'])} ↗</span><span class="verdict">{m['foot']}</span></div>
  </div>
</div>
{wipe}

<script>
(function () {{
  const tl = gsap.timeline({{ paused: true }});
  {wipe_js}
  // Calque de l'extension : badge, puce, pastilles
  tl.fromTo('#{sid}-badge', {{ opacity: 0, y: -10 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power3.out' }}, {over_in});
  tl.fromTo('#{sid}-chip', {{ opacity: 0, y: -10 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power3.out' }}, {over_in + 0.06:.2f});
  tl.fromTo('#{sid}-dots', {{ opacity: 0, y: -10 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power3.out' }}, {over_in + 0.12:.2f});
  [0, 1, 2].forEach((i) => {{
    tl.fromTo('#{sid}-eq' + i, {{ scaleY: 0.3 }}, {{ scaleY: 1, duration: 0.5, ease: 'sine.inOut', yoyo: true, repeat: {reps(0.5)} }}, i * 0.2);
  }});
  tl.fromTo('#{sid}-ring', {{ scale: 0.6, opacity: 0.8 }}, {{ scale: 2.4, opacity: 0, duration: 1.8, ease: 'power1.out', repeat: {reps(1.8)} }}, 0);
  tl.fromTo('#{sid}-dot{m['index']}', {{ scale: 0, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.45, ease: 'back.out(2.2)' }}, {resolve + 0.05:.2f});

  // La carte entre (overlay.css : translateX(34px) scale(.97) blur(7px), ×1,7)
  tl.fromTo('#{sid}-card', {{ opacity: 0, x: 58, scale: 0.97, filter: 'blur(7px)' }},
    {{ opacity: 1, x: 0, scale: 1, filter: 'blur(0px)', duration: 0.62, ease: 'expo.out' }}, {ci});
  tl.fromTo(['#{sid}-spin1', '#{sid}-spin2'], {{ rotation: 0 }}, {{ rotation: 720, duration: 1.4, ease: 'none' }}, {ci - 0.1:.2f});
  tl.fromTo('#{sid}-shimmer', {{ xPercent: -120 }}, {{ xPercent: 420, duration: 2.04, ease: 'sine.inOut', repeat: 2, repeatDelay: 1.36 }}, {ci + 0.3:.2f});
  tl.fromTo('#{sid}-tagv', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25 }}, {resolve:.2f});
  tl.fromTo('#{sid}-body', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power2.out' }}, {resolve + 0.05:.2f});
  tl.fromTo('#{sid}-foot', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: 'power2.out' }}, {resolve + 0.15:.2f});
  tl.fromTo('#{sid}-tagp', {{ opacity: 1 }}, {{ opacity: 0, duration: 0.2 }}, {resolve:.2f});
  tl.fromTo('#{sid}-checking', {{ opacity: 1 }}, {{ opacity: 0, duration: 0.2 }}, {resolve:.2f});
  tl.fromTo('#{sid}-card', {{ '--accent': '{ACCENT['pending']}' }}, {{ '--accent': '{ACCENT[v]}', duration: 0.55, ease: 'power1.inOut', immediateRender: false }}, {resolve:.2f});

  // Sortie de la carte avant la coupe
  tl.to('#{sid}-card', {{ opacity: 0, x: 58, scale: 0.97, filter: 'blur(7px)', duration: 0.45, ease: 'power2.in' }}, {card_out:.2f});{js_outro}

  // Qui parle
  {switch_js}

  window.__timelines['{sid}'] = tl;
}})();
</script>
</div>
</template>
</body>
</html>
"""


def write(name, html):
    with open(os.path.join(COMP, name + ".html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(html)


def paper_scene(sid, dur, style, markup, script):
    p = f'[data-composition-id="{sid}"]'
    return f"""<!doctype html>
<html lang="fr">
<head><meta charset="UTF-8" /></head>
<body>
<template id="{sid}-template">
<div data-composition-id="{sid}" data-width="1920" data-height="1080" data-duration="{dur}">
<style>
{FONTS}
{p} {{ position: absolute; inset: 0; overflow: hidden; background: #ffffff; color: #1c1c1c; font-family: 'SRC Archivo', sans-serif; }}
{p} .abs {{ position: absolute; }}
{p} .mono {{ font-family: 'SRC Plex Mono', monospace; font-size: 22px; letter-spacing: 0.12em; text-transform: uppercase; color: #5e5e5e; }}
{p} .wordmark {{ font-family: 'SRC Newsreader', serif; font-style: italic; font-weight: 500; color: #1c1c1c; line-height: 1; }}
{p} .wordmark b {{ font-weight: 500; color: #a3172c; }}
{p} .rule {{ display: block; background: #a3172c; transform-origin: 0 50%; }}
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


# ── 01 · Le volume ───────────────────────────────────────────────────────────
write("s01-volume", paper_scene("s01-volume", 5.0,
    """§ .big { left: 128px; top: 210px; font-weight: 800; font-size: 430px; line-height: 0.9; letter-spacing: -0.04em; font-variant-numeric: tabular-nums; }
§ .line { left: 140px; top: 680px; font-size: 46px; }
§ .q { left: 140px; top: 770px; font-family: 'SRC Newsreader', serif; font-style: italic; font-size: 72px; color: #a3172c; }""",
    """<div class="abs mono" id="s01-label" style="left:140px; top:150px;">Débat des législatives · France 2 · 27 juin 2024</div>
<div class="abs big" id="s01-num">195</div>
<span class="abs rule" id="s01-rule" style="left:140px; top:640px; width:220px; height:4px;"></span>
<div class="abs line" id="s01-line">affirmations vérifiées en un seul débat de 1 h 58.</div>
<div class="abs q" id="s01-q">Qui a le temps de tout vérifier ?</div>""",
    """  const num = document.getElementById('s01-num');
  const counter = { v: 0 };
  tl.fromTo('#s01-label', { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.45, ease: 'power2.out' }, 0.15);
  tl.fromTo('#s01-num', { opacity: 0 }, { opacity: 1, duration: 0.3 }, 0.3);
  tl.fromTo(counter, { v: 0 }, { v: 195, duration: 1.2, ease: 'power2.out', onUpdate: () => { num.textContent = String(Math.round(counter.v)); } }, 0.3);
  tl.fromTo('#s01-rule', { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: 'power3.out' }, 1.45);
  tl.fromTo('#s01-line', { opacity: 0, x: -16 }, { opacity: 1, x: 0, duration: 0.5, ease: 'power3.out' }, 1.6);
  tl.fromTo('#s01-q', { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.6, ease: 'power3.out' }, 2.4);"""))

# ── 02 · La promesse ─────────────────────────────────────────────────────────
write("s02-promesse", paper_scene("s02-promesse", 5.0,
    """§ .wm { left: 140px; top: 190px; font-size: 190px; }
§ .phrase { left: 146px; top: 470px; width: 1300px; font-family: 'SRC Newsreader', serif; font-size: 64px; line-height: 1.2; }
§ .steps { left: 146px; top: 760px; display: flex; gap: 28px; align-items: center; }
§ .steps .st { color: #1c1c1c; }
§ .steps .ar { color: #a3172c; }
§ .content { position: absolute; inset: 0; }""",
    """<div class="content" id="s02-content">
<div class="abs wordmark wm" id="s02-wm">SOURC<b>É</b></div>
<div class="abs phrase" id="s02-phrase">Chaque affirmation vérifiée en direct, sources à l'appui, sur la vidéo.</div>
<div class="abs mono steps"><span class="st" id="s02-st0">01 Transcription</span><span class="ar" id="s02-ar0">→</span><span class="st" id="s02-st1">02 Recherche des sources</span><span class="ar" id="s02-ar1">→</span><span class="st" id="s02-st2">03 Verdict</span></div>
<span class="abs rule" id="s02-rule" style="left:146px; top:820px; width:1100px; height:2px; background:#e2e0da;"></span>
</div>""",
    """  tl.fromTo('#s02-wm', { clipPath: 'inset(-40% 100% -40% -5%)' }, { clipPath: 'inset(-40% -10% -40% -5%)', duration: 0.7, ease: 'power3.inOut' }, 0.15);
  tl.fromTo('#s02-phrase', { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.6, ease: 'power3.out' }, 0.9);
  tl.fromTo('#s02-rule', { scaleX: 0 }, { scaleX: 1, duration: 0.8, ease: 'power2.out' }, 1.6);
  ['#s02-st0', '#s02-ar0', '#s02-st1', '#s02-ar1', '#s02-st2'].forEach((sel, i) => {
    tl.fromTo(sel, { opacity: 0, x: 12 }, { opacity: 1, x: 0, duration: 0.35, ease: 'power2.out' }, 1.75 + i * 0.12);
  });
  tl.to('#s02-content', { opacity: 0, duration: 0.4, ease: 'power1.in' }, 4.55);"""))

# ── 03 → 06 · Les moments ────────────────────────────────────────────────────
for sid, dur in SCENES:
    if sid in MOMENTS:
        write(sid, moment_html(sid, dur, MOMENTS[sid]))

# ── 07 · La fiche ────────────────────────────────────────────────────────────
S = 1360 / 1920   # échelle de la capture
OX, OY = 100, 222  # position de la capture


def rect(x0, y0, x1, y1):
    return f"left:{OX + x0 * S:.0f}px; top:{OY + y0 * S:.0f}px; width:{(x1 - x0) * S:.0f}px; height:{(y1 - y0) * S:.0f}px;"


write("s07-fiche", paper_scene("s07-fiche", 16.0,
    f"""§ {{ background: #f6f5f1; }}
§ .content {{ position: absolute; inset: 0; }}
§ .title {{ left: 120px; top: 104px; font-family: 'SRC Newsreader', serif; font-size: 74px; line-height: 1.1; }}
§ .title i {{ color: #a3172c; }}
§ .shot {{ left: {OX}px; top: {OY}px; width: 1360px; height: 765px; border-radius: 14px; overflow: hidden; background: #fff;
  box-shadow: 0 30px 80px rgba(28,28,28,0.18), 0 0 0 2px #e2e0da; }}
§ .shot-in {{ position: absolute; inset: 0; transform-origin: 40% 40%; }}
§ .shot-in img {{ display: block; width: 100%; height: 100%; }}
§ .hl {{ position: absolute; border: 4px solid #a3172c; border-radius: 10px; box-shadow: 0 0 0 9999px rgba(246,245,241,0); }}
§ .num {{ position: absolute; top: -18px; left: -18px; width: 36px; height: 36px; border-radius: 50%; background: #a3172c; color: #fff;
  font-family: 'SRC Plex Mono', monospace; font-weight: 500; font-size: 20px; display: flex; align-items: center; justify-content: center; }}
§ .callout {{ left: 1500px; width: 380px; display: flex; gap: 16px; align-items: flex-start; }}
§ .callout .n {{ flex: none; width: 40px; height: 40px; border-radius: 50%; background: #a3172c; color: #fff; font-family: 'SRC Plex Mono', monospace;
  font-weight: 500; font-size: 22px; display: flex; align-items: center; justify-content: center; }}
§ .callout .t {{ font-family: 'SRC Newsreader', serif; font-size: 38px; line-height: 1.15; color: #1c1c1c; }}
§ .bigdots {{ left: 893px; top: 519px; width: 134px; height: 42px; display: flex; align-items: center; gap: 10px; padding: 0 18px; box-sizing: border-box;
  background: rgba(16,18,22,0.72); border: 2px solid rgba(255,255,255,0.12); border-radius: 999px; transform-origin: 50% 50%; z-index: 6; }}
§ .bigdots i {{ display: block; width: 16px; height: 16px; border-radius: 50%; }}""",
    f"""<div class="content" id="s07-content">
<div class="abs mono" id="s07-label" style="left:120px; top:66px;">Après le débat</div>
<div class="abs title" id="s07-title">À la fin du débat, <i>la fiche.</i></div>
<div class="abs shot" id="s07-shot"><div class="shot-in" id="s07-shotin" data-layout-allow-overflow><img src="assets/captures/resume-2V70R8-Om7c.png" alt="" /></div>
</div>
<div class="hl" id="s07-hl1" style="{rect(96, 262, 1824, 600)}"><span class="num">1</span></div>
<div class="hl" id="s07-hl2" style="{rect(96, 688, 990, 918)}"><span class="num">2</span></div>
<div class="hl" id="s07-hl3" style="{rect(1044, 688, 1824, 958)}"><span class="num">3</span></div>
<div class="abs callout" id="s07-c1" style="top:330px;"><span class="n">1</span><span class="t">Qui a été le plus exact</span></div>
<div class="abs callout" id="s07-c2" style="top:520px;"><span class="n">2</span><span class="t">Sur quels thèmes</span></div>
<div class="abs callout" id="s07-c3" style="top:710px;"><span class="n">3</span><span class="t">Les chiffres contestés</span></div>
</div>
<div class="abs bigdots" id="s07-dots">{"".join(f'<i style="background:{ACCENT[v]}"></i>' for v in DOT_ORDER)}</div>""",
    """  tl.fromTo('#s07-dots', { scale: 2.5, opacity: 1 }, { scale: 0.4, opacity: 0, duration: 0.6, ease: 'power3.in' }, 0.15);
  tl.fromTo('#s07-label', { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.4, ease: 'power2.out' }, 0.45);
  tl.fromTo('#s07-title', { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.6, ease: 'power3.out' }, 0.55);
  tl.fromTo('#s07-shot', { opacity: 0, y: 50 }, { opacity: 1, y: 0, duration: 0.9, ease: 'expo.out' }, 0.8);
  tl.fromTo('#s07-shotin', { scale: 1 }, { scale: 1.035, duration: 15, ease: 'none' }, 0.8);
  [['#s07-hl1', '#s07-c1', 3.0], ['#s07-hl2', '#s07-c2', 5.4], ['#s07-hl3', '#s07-c3', 7.8]].forEach(([hl, c, t]) => {
    tl.fromTo(hl, { opacity: 0, scale: 1.04 }, { opacity: 1, scale: 1, duration: 0.45, ease: 'power3.out' }, t);
    tl.fromTo(c, { opacity: 0, x: 24 }, { opacity: 1, x: 0, duration: 0.5, ease: 'power3.out' }, t + 0.1);
  });
  tl.to('#s07-content', { opacity: 0, duration: 0.5, ease: 'power1.in' }, 15.45);"""))

# ── 08 · Où la trouver ───────────────────────────────────────────────────────
URL = "source.codeminds.fr"
write("s08-fin", paper_scene("s08-fin", 8.0,
    """§ .content { position: absolute; inset: 0; }
§ .wm { left: 140px; top: 230px; font-size: 150px; transform-origin: 0 60%; }
§ .url { left: 146px; top: 470px; font-family: 'SRC Plex Mono', monospace; font-size: 64px; color: #a3172c; white-space: nowrap; }
§ .tagline { left: 146px; top: 610px; font-size: 40px; }
§ .fine { left: 146px; bottom: 80px; font-size: 24px; color: #5e5e5e; }""",
    f"""<div class="content" id="s08-content">
<div class="abs wordmark wm" id="s08-wm">SOURC<b>É</b></div>
<div class="abs url" id="s08-url">{"".join(f'<span id="s08-c{i}">{c}</span>' for i, c in enumerate(URL))}</div>
<span class="abs rule" id="s08-rule" style="left:146px; top:556px; width:{len(URL) * 38.4:.0f}px; height:4px;"></span>
<div class="abs tagline" id="s08-tag">Débats en relecture · fiches · méthode</div>
<div class="abs fine" id="s08-fine">Montage : délai de vérification raccourci. Verdicts tels que publiés sur le site. Extraits : France 2, LCI.</div>
</div>""",
    f"""  tl.fromTo('#s08-wm', {{ opacity: 0, y: 16 }}, {{ opacity: 1, y: 0, duration: 0.6, ease: 'power3.out' }}, 0.2);
  tl.fromTo('#s08-wm', {{ scale: 1 }}, {{ scale: 1.012, duration: 1.6, ease: 'sine.inOut', yoyo: true, repeat: 3, immediateRender: false }}, 0.9);
  for (let i = 0; i < {len(URL)}; i++) {{
    tl.fromTo('#s08-c' + i, {{ opacity: 0 }}, {{ opacity: 1, duration: 0.05 }}, 0.8 + i * 0.045);
  }}
  tl.fromTo('#s08-rule', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.55, ease: 'power3.out' }}, {0.8 + len(URL) * 0.045:.2f});
  tl.fromTo('#s08-tag', {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: 'power2.out' }}, 2.2);
  tl.fromTo('#s08-fine', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.6 }}, 2.7);
  tl.to('#s08-content', {{ opacity: 0, duration: 0.6, ease: 'power1.in' }}, 7.35);"""))

# ── index.html ───────────────────────────────────────────────────────────────
t = 0.0
slots = []
for sid, dur in SCENES:
    slots.append(f"""      <div id="el-{sid}" data-composition-id="{sid}" data-composition-src="compositions/{sid}.html"
        data-start="{t:.2f}" data-duration="{dur}" data-track-index="1" data-width="1920" data-height="1080"></div>""")
    t = round(t + dur, 2)
total = t

# ── Musique : « Jungle Waves » (dimmysad, Pixabay, licence Pixabay) ─────────
# 176 BPM, drop à 13,092 s dans le morceau. Morceau A : le drop tombe à 10 s,
# sur l'ouverture du premier extrait. Morceau B : reprise 8 mesures plus tôt
# (32 temps = 10,918 s), sous la parole de Faure, pour que la vraie fin du
# morceau accompagne l'écran final.
MUSIC_SRC = "assets/music/jungle-waves.mp3"
SPLICE = 22.283                      # temps vidéo du raccord
PIECES = [(0.0, 3.092, SPLICE), (SPLICE, 14.457, total)]   # (début vidéo, début morceau, fin vidéo)
HIGH, READ, LOW = 0.42, 0.2, 0.07    # ouverture/fiche ; lecture des cartes ; sous la parole

starts = {}
acc = 0.0
for sid, dur in SCENES:
    starts[sid] = acc
    acc = round(acc + dur, 2)
env = [(0.0, 0.0), (0.8, HIGH), (10.0, HIGH), (10.5, LOW)]
moment_ids = [sid for sid, _ in SCENES if sid in MOMENTS]
for i, sid in enumerate(moment_ids):
    m, t0 = MOMENTS[sid], starts[sid]
    duck = t0 + m.get("duck_at", m["card_in"] + 0.6)
    env += [(round(duck, 2), LOW), (round(duck + 1.0, 2), READ)]
    if i + 1 < len(moment_ids):
        nxt = starts[moment_ids[i + 1]]
        env += [(round(nxt - 0.25, 2), READ), (round(nxt, 2), LOW)]
    else:
        card_out = t0 + m["card_in"] + 0.9 + m["hold"]
        env += [(round(card_out, 2), READ), (round(starts["s07-fiche"], 2), HIGH)]
env += [(round(total - 3.4, 2), HIGH), (total, 0.0)]


def env_at(t):
    for (a, va), (b, vb) in zip(env, env[1:]):
        if a <= t <= b:
            return va if b == a else va + (vb - va) * (t - a) / (b - a)
    return env[-1][1]


audio_tags = []
for n, (v0, m0, v1) in enumerate(PIECES):
    pts = [(0.0, round(env_at(v0), 3))] + [(round(t - v0, 3), v) for t, v in env if v0 < t < v1] + [(round(v1 - v0, 3), round(env_at(v1), 3))]
    # raccord sans clic : 40 ms de fondu de part et d'autre
    if n > 0:
        pts[0] = (0.0, 0.0)
        pts.insert(1, (0.04, round(env_at(v0 + 0.04), 3)))
    if n < len(PIECES) - 1:
        pts[-1] = (round(v1 - v0, 3), 0.0)
        pts.insert(-1, (round(v1 - v0 - 0.04, 3), round(env_at(v1 - 0.04), 3)))
    lane = json.dumps({"version": 1, "lanes": [{"target": "volume", "points": [{"t": t, "v": v} for t, v in pts]}]})
    audio_tags.append(f"""      <audio id="bgm-{n}" src="{MUSIC_SRC}" data-start="{v0:.3f}" data-duration="{v1 - v0:.3f}" data-media-start="{m0}"
        data-track-index="{10 + n}" data-automation='{lane}'></audio>""")

index = f"""<!doctype html>
<html lang="fr">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <title>Démo SOURCÉ</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1920px; height: 1080px; overflow: hidden; background: #ffffff; }}
      #root {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: #ffffff; }}
      [data-composition-id="main"] > div[data-composition-src] {{ position: absolute; inset: 0; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="1920" data-height="1080">
{chr(10).join(slots)}
{chr(10).join(audio_tags)}
    </div>
    <script>
      window.__timelines["main"] = gsap.timeline({{ paused: true }});
    </script>
  </body>
</html>
"""
with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8", newline="\n") as f:
    f.write(index)
print(f"✓ {len(SCENES)} scènes, {total} s")

"""Le « va. » de vachecker.info en tracés vectoriels (Newsreader italique, graisse 500).

    python social/logo.py

Écrit site/favicon.svg (variante claire E, tuile blanche), site/apple-touch-icon.png,
site/favicon.ico, site/media/logo-va-sombre.svg et logo-va-clair.svg (la pastille des
fiches résumées : C en thème sombre, E en thème clair) et social/logo-va.json (les tracés).
Les PNG passent par Chrome (rendu) et Pillow (.ico).
"""
import json
import os
import subprocess
import sys

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
FONT = os.path.join(SITE, "fonts", "newsreader-italic-latin.woff2")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DARK, RED_LIGHT = "#1c1c1c", "#a3172c"


def outlines():
    """Tracés de v, a et du point, posés côte à côte (approche −0,02 em comme sur le site),
    ramenés à une boîte d'encre centrée en (0, 0), en unités de la police, y vers le bas."""
    font = instancer.instantiateVariableFont(TTFont(FONT), {"wght": 500, "opsz": 72})
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    upm = font["head"].unitsPerEm
    placed, x = [], 0.0
    for ch in "va.":
        name = cmap[ord(ch)]
        placed.append((ch, name, x))
        x += hmtx[name][0] - 0.02 * upm
    bounds = BoundsPen(glyphs)
    for _, name, dx in placed:
        glyphs[name].draw(TransformPen(bounds, (1, 0, 0, 1, dx, 0)))
    x0, y0, x1, y1 = bounds.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    paths = {}
    for ch, name, dx in placed:
        pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
        glyphs[name].draw(TransformPen(pen, (1, 0, 0, -1, dx - cx, cy)))
        paths["dot" if ch == "." else ch] = pen.getCommands()
    return {"width": round(x1 - x0, 1), "height": round(y1 - y0, 1), "v": paths["v"], "a": paths["a"], "dot": paths["dot"]}


def mark(logo, size, ink, ink_color, dot_color, dx=0.0):
    """Le « va. » centré dans un carré size × size, l'encre sur une fraction ink de la largeur."""
    s = size * ink / logo["width"]
    return (f'<g transform="translate({size / 2 + dx * size:.1f} {size / 2:.1f}) scale({s:.4f})">'
            f'<path fill="{ink_color}" d="{logo["v"]} {logo["a"]}"/><path fill="{dot_color}" d="{logo["dot"]}"/></g>')


def main():
    logo = outlines()
    with open(os.path.join(ROOT, "social", "logo-va.json"), "w", encoding="utf-8") as f:
        json.dump(logo, f, ensure_ascii=False)
    # Favicon : la variante claire (E) sur une tuile blanche arrondie, l'encre bien plus grande
    # que sur la photo de profil pour rester lisible à 16 px
    fav = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#fff"/>'
           f'{mark(logo, 64, 0.80, DARK, RED_LIGHT)}</svg>\n')
    with open(os.path.join(SITE, "favicon.svg"), "w", encoding="utf-8", newline="\n") as f:
        f.write(fav)
    # Pastilles des fiches résumées : C (sombre, lueur rouge comme les réels) et E (claire, filet)
    badges = {
        "sombre": ('<defs><radialGradient id="g" cx="18%" cy="100%" r="75%"><stop offset="0" stop-color="#a3172c" stop-opacity=".42"/>'
                   '<stop offset="1" stop-color="#a3172c" stop-opacity="0"/></radialGradient></defs>'
                   '<circle cx="540" cy="540" r="540" fill="#0d0e12"/><circle cx="540" cy="540" r="540" fill="url(#g)"/>'
                   '<circle cx="540" cy="540" r="534" fill="none" stroke="#fff" stroke-opacity=".12" stroke-width="12"/>'
                   + mark(logo, 1080, 0.59, "#ffffff", "#ee6060")),
        "clair": ('<circle cx="540" cy="540" r="540" fill="#fff"/>'
                  '<circle cx="540" cy="540" r="534" fill="none" stroke="#e2e0da" stroke-width="12"/>'
                  + mark(logo, 1080, 0.59, DARK, RED_LIGHT)),
    }
    for name, body in badges.items():
        with open(os.path.join(SITE, "media", f"logo-va-{name}.svg"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080">{body}</svg>\n')
    # Icône d'écran d'accueil (iOS) : carré plein, sans transparence ; .ico pour les robots
    tmp = os.path.join(ROOT, "social", "_icone.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write('<!doctype html><html><body style="margin:0;background:#fff">'
                f'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180">'
                f'<rect width="180" height="180" fill="#fff"/>{mark(logo, 180, 0.74, DARK, RED_LIGHT)}</svg></body></html>')
    png = os.path.join(SITE, "apple-touch-icon.png")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--window-size=180,180", f"--screenshot={png}", "file:///" + tmp.replace("\\", "/")],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.remove(tmp)
    from PIL import Image
    Image.open(png).convert("RGBA").save(os.path.join(SITE, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
    print("✓ site/favicon.svg, site/apple-touch-icon.png, site/favicon.ico, site/media/logo-va-{sombre,clair}.svg, social/logo-va.json")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

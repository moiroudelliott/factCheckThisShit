"""Publication des réels de vachecker.info sur Instagram (API Instagram, connexion Instagram).

    python social/publier_instagram.py verifier                 # la clé marche ? quel compte ?
    python social/publier_instagram.py rafraichir               # renouvelle la clé (valable 60 jours)
    python social/publier_instagram.py publier REEL.mp4 --legende LEGENDE.txt [--miniature 0] [--confirme] [--republier]

Sans --confirme, rien n'est envoyé : le script affiche ce qui serait publié. Chaque
publication se fait après l'accord explicite de l'utilisateur, réel par réel.
Les vidéos à publier sont dans videos/reels/renders/a-publier ; une fois en ligne, la vidéo
passe dans renders/publiees et la publication s'inscrit dans social/publications.json
(un réel déjà inscrit est refusé, sauf --republier).

La clé est lue dans .env (INSTAGRAM_TOKEN) et n'est jamais affichée (4 derniers caractères
au plus). Avec la connexion Instagram, Meta va chercher la vidéo à une adresse publique :
elle est copiée le temps de la publication sur le serveur Hostinger (alias SSH
« source-site », comme deploy_site.py) sous un nom aléatoire, puis effacée.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
REGISTRE = os.path.join(ROOT, "social", "publications.json")
RENDERS = os.path.join(ROOT, "videos", "reels", "renders")      # a-publier/ → publiees/ une fois en ligne
API = "https://graph.instagram.com/v23.0"
SSH_HOST = os.environ.get("SOURCE_SSH_HOST", "source-site")
REMOTE_TMP = "domains/source.codeminds.fr/public_html/tmp-reels"
PUBLIC_TMP = "https://source.codeminds.fr/tmp-reels"


# ── Clé ──────────────────────────────────────────────────────────────────────
def read_env():
    values = {}
    if os.path.exists(ENV):
        with open(ENV, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def token():
    t = read_env().get("INSTAGRAM_TOKEN", "")
    if not t:
        raise SystemExit("✗ INSTAGRAM_TOKEN absent de .env (tableau de bord Meta → « Générer un token »)")
    return t


def write_token(new):
    with open(ENV, encoding="utf-8") as f:
        lines = f.read().splitlines()
    lines = [l for l in lines if not l.startswith("INSTAGRAM_TOKEN=")] + [f"INSTAGRAM_TOKEN={new}"]
    with open(ENV, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


# ── Registre des publications ────────────────────────────────────────────────
def registre():
    with open(REGISTRE, encoding="utf-8") as f:
        return json.load(f)


def deja_publie(reel):
    return next((p for p in registre()["publications"] if p["reel"] == reel and p["plateforme"] == "instagram"), None)


def noter(reel, lien, video):
    """Inscrit la publication au registre et range la vidéo dans renders/publiees."""
    reg = registre()
    reg["publications"].append({"reel": reel, "plateforme": "instagram",
                                "date": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "lien": lien})
    with open(REGISTRE, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(reg, ensure_ascii=False, indent=2) + "\n")
    if os.path.basename(os.path.dirname(video)) == "a-publier":
        dest = os.path.join(RENDERS, "publiees", os.path.basename(video))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(video, dest)
        print(f"✓ vidéo rangée dans {os.path.relpath(dest, ROOT)}")
    print(f"✓ inscrit dans social/publications.json")


def reel_de(video):
    name = os.path.splitext(os.path.basename(video))[0]
    return name[len("vachecker-"):] if name.startswith("vachecker-") else name


# ── Appels ───────────────────────────────────────────────────────────────────
def call(method, path, **params):
    """Appel à l'API ; les erreurs sont affichées sans l'adresse (qui contient la clé)."""
    params["access_token"] = token()
    data = urllib.parse.urlencode(params).encode()
    url = (path if path.startswith("http") else f"{API}/{path}")
    req = urllib.request.Request(url + ("" if method == "POST" else "?" + data.decode()),
                                 data=data if method == "POST" else None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
            msg = f"{err.get('type', '')} {err.get('code', '')} : {err.get('message', '')}"
        except ValueError:
            msg = str(e.code)
        raise SystemExit(f"✗ Instagram a refusé ({method} {path.split('?')[0].replace(API, '')}) — {msg}")


def me():
    return call("GET", "me", fields="user_id,username")


# ── Commandes ────────────────────────────────────────────────────────────────
def verifier(_):
    m = me()
    print(f"✓ clé valide (…{token()[-4:]}) — compte @{m.get('username')} (id {m.get('user_id')})")
    lim = call("GET", f"{m['user_id']}/content_publishing_limit", fields="quota_usage,config").get("data", [{}])[0]
    if lim:
        print(f"  publications sur 24 h : {lim.get('quota_usage', '?')} / {lim.get('config', {}).get('quota_total', '?')}")


def rafraichir(_):
    r = call("GET", "https://graph.instagram.com/refresh_access_token", grant_type="ig_refresh_token")
    write_token(r["access_token"])
    print(f"✓ clé renouvelée (…{r['access_token'][-4:]}), valable {int(r.get('expires_in', 0)) // 86400} jours — .env mis à jour")


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    return j["streams"][0]["width"], j["streams"][0]["height"], float(j["format"]["duration"])


def publier(a):
    video = os.path.abspath(a.video)
    with open(a.legende, encoding="utf-8") as f:
        caption = f.read().strip()
    w, h, dur = probe(video)
    tags = sum(1 for word in caption.split() if word.startswith("#"))
    problems = [p for p, bad in [
        ("légende de plus de 2 200 caractères", len(caption) > 2200),
        ("plus de 30 hashtags", tags > 30),
        ("durée hors de 3 s – 15 min", not 3 <= dur <= 900),
        ("pas au format vertical 9:16", abs(w / h - 9 / 16) > 0.01),
    ] if bad]
    print(f"Réel      : {os.path.relpath(video, ROOT)} — {w}×{h}, {dur:.1f} s, {os.path.getsize(video) / 1e6:.1f} Mo")
    print(f"Miniature : image à {a.miniature} ms")
    print(f"Légende   : {len(caption)} caractères, {tags} hashtags\n" + "\n".join("  │ " + l for l in caption.splitlines()))
    reel = reel_de(video)
    deja = deja_publie(reel)
    if deja and not a.republier:
        problems.append(f"« {reel} » est déjà en ligne depuis le {deja['date'][:10]} ({deja['lien']}) — pour une nouvelle "
                        "version, supprimer l'ancienne dans l'appli puis relancer avec --republier")
    if problems:
        raise SystemExit("✗ " + " ; ".join(problems))
    if not a.confirme:
        print("\n(essai : rien n'a été envoyé — ajouter --confirme pour publier)")
        return
    user = me()
    name = f"{secrets.token_hex(16)}.mp4"
    ssh = ["ssh", "-o", "BatchMode=yes", SSH_HOST]
    subprocess.run(ssh + [f"mkdir -p {REMOTE_TMP}"], check=True)
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", video, f"{SSH_HOST}:{REMOTE_TMP}/{name}"], check=True)
    try:
        print("↑ vidéo déposée temporairement sur le serveur")
        c = call("POST", f"{user['user_id']}/media", media_type="REELS", video_url=f"{PUBLIC_TMP}/{name}",
                 caption=caption, share_to_feed="true", thumb_offset=str(a.miniature))
        for _ in range(40):                       # Meta conseille d'interroger au plus 1 fois / min ; la vidéo est courte
            time.sleep(15)
            s = call("GET", c["id"], fields="status_code,status")
            if s.get("status_code") == "FINISHED":
                break
            if s.get("status_code") in ("ERROR", "EXPIRED"):
                raise SystemExit(f"✗ Instagram n'a pas pu traiter la vidéo : {s.get('status', s.get('status_code'))}")
        else:
            raise SystemExit("✗ traitement trop long (10 min) : rien n'a été publié")
        pub = call("POST", f"{user['user_id']}/media_publish", creation_id=c["id"])
        link = call("GET", pub["id"], fields="permalink").get("permalink", "")
        print(f"✓ publié sur @{user.get('username')} : {link}")
    finally:
        subprocess.run(ssh + [f"rm -f {REMOTE_TMP}/{name}"], check=False)
        print("✓ copie temporaire effacée du serveur")
    noter(reel, link, video)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Publication des réels de vachecker.info sur Instagram.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verifier").set_defaults(fn=verifier)
    sub.add_parser("rafraichir").set_defaults(fn=rafraichir)
    p = sub.add_parser("publier")
    p.add_argument("video")
    p.add_argument("--legende", required=True, help="fichier texte de la légende")
    p.add_argument("--miniature", type=int, default=0, help="image de couverture : position en millisecondes")
    p.add_argument("--confirme", action="store_true", help="publier pour de vrai (sinon : essai)")
    p.add_argument("--republier", action="store_true", help="réel déjà en ligne : nouvelle version (l'ancienne supprimée dans l'appli)")
    p.set_defaults(fn=publier)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

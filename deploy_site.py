"""Met le site en ligne : Hostinger (source.codeminds.fr) par SSH, ou
Cloudflare Pages (vachecker.info).

Usage:
    python deploy_site.py --dry-run      # liste ce qui serait envoyé, sans rien toucher
    python deploy_site.py                # Hostinger : sauvegarde l'ancien site sur le serveur, puis le remplace
    python deploy_site.py --cloudflare   # Cloudflare Pages, projet « vachecker » (wrangler)

Cloudflare : CLOUDFLARE_API_TOKEN (permission « Cloudflare Pages : Modifier »)
et CLOUDFLARE_ACCOUNT_ID dans .env. Les fichiers de la liste sont copiés dans
un dossier temporaire, puis envoyés ; chaque envoi est une version que le
tableau de bord Cloudflare permet de rétablir. Pages refuse les fichiers de
plus de 25 Mio : vérifié avant l'envoi. Les fichiers statiques de Cloudflare
ne servent pas de morceaux de fichier (Range) : cloudflare/worker.js s'en
charge pour /media/*, sans quoi la vidéo ne se lit pas sur Safari.

N'envoie qu'une liste explicite de fichiers (pages, feuille de style,
scripts, polices, sessions publiées) : un fichier de travail déposé dans
site/ ne part jamais en ligne par accident. Sur le serveur, l'ancien site
est d'abord archivé dans ~/backups (les 5 dernières archives sont
gardées), puis le nouveau est extrait à côté et mis en place d'un seul
coup — aucun visiteur ne tombe sur un site à moitié copié.

Accès : alias SSH « source-site » (~/.ssh/config, clé ~/.ssh/source_hostinger
autorisée dans l'hPanel Hostinger). Remplaçable par SOURCE_SSH_HOST.
Avant l'envoi : publish_session.py (overlay et versions à jour).
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
HOST = os.environ.get("SOURCE_SSH_HOST", "source-site")
REMOTE_DIR = "domains/source.codeminds.fr"  # le site est servi depuis REMOTE_DIR/public_html
KEEP_BACKUPS = 5
PAGES_PROJECT = "vachecker"         # nom du Worker (site statique) sur Cloudflare
PAGES_MAX_BYTES = 25 * 1024 * 1024   # taille maximale d'un fichier statique
WRANGLER = "wrangler@4.149.0"
COMPAT_DATE = "2026-10-06"


def files_to_publish() -> list:
    """Chemins relatifs à site/, dans une liste explicite."""
    out = [f for f in sorted(os.listdir(SITE)) if f.endswith(".html")]
    out += ["style.css", "theme.js", "relecture.js", "fiche.js", "resume.js", "favicon.svg", "favicon.ico", "apple-touch-icon.png", "media/logo-va-sombre.svg", "media/logo-va-clair.svg", "robots.txt",
            "overlay/content.js", "overlay/overlay.css", "sessions/index.json",
            # Vidéo de démonstration de la page d'accueil (videos/demo-2min, version web)
            "media/demo-source.mp4", "media/demo-source.jpg",
            # Aperçu des liens partagés (og:image) et exemples de l'accueil (publish_session.py)
            "media/og.jpg", "sessions/exemples.json"]
    out += [f"fonts/{f}" for f in sorted(os.listdir(os.path.join(SITE, "fonts"))) if f.endswith(".woff2")]
    out += [f"fonts/licenses/{f}" for f in sorted(os.listdir(os.path.join(SITE, "fonts", "licenses")))]
    with open(os.path.join(SITE, "sessions", "index.json"), encoding="utf-8") as f:
        sessions = json.load(f).get("sessions", [])
    out += [f"sessions/{s['id']}.json" for s in sessions]
    # Fiche du débat (fiche.html), pour les sessions qui en ont une
    out += [f"sessions/{s['id']}.fiche.json" for s in sessions if s.get("fiche")]
    missing = [p for p in out if not os.path.isfile(os.path.join(SITE, p))]
    if missing:
        raise SystemExit(f"✗ fichiers manquants : {', '.join(missing)}")
    return out


def archive(paths: list) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in paths:
            info = tar.gettarinfo(os.path.join(SITE, p), arcname=p)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(os.path.join(SITE, p), "rb") as f:
                tar.addfile(info, f)
    return buf.getvalue()


REMOTE_SCRIPT = f"""set -e
cd {REMOTE_DIR}
mkdir -p ~/backups
stamp=$(date +%Y%m%d-%H%M%S)
if [ -d public_html ]; then tar czf ~/backups/source-$stamp.tgz public_html; fi
rm -rf public_html.new public_html.old
mkdir public_html.new
tar xzf - -C public_html.new
find public_html.new -type d -exec chmod 755 {{}} +
if [ -d public_html ]; then mv public_html public_html.old; fi
mv public_html.new public_html
rm -rf public_html.old
ls -1t ~/backups/source-*.tgz 2>/dev/null | tail -n +{KEEP_BACKUPS + 1} | xargs -r rm -f
echo "sauvegarde : ~/backups/source-$stamp.tgz"
echo "en ligne : $(find public_html -type f | wc -l) fichiers"
"""


def deploy_cloudflare(paths: list):
    """Copie la liste dans un dossier temporaire et l'envoie à Cloudflare Pages."""
    from dotenv import dotenv_values
    keys = {k: v for k, v in dotenv_values(os.path.join(ROOT, ".env")).items()
            if k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID") and v}
    if len(keys) < 2:
        raise SystemExit("✗ CLOUDFLARE_API_TOKEN et CLOUDFLARE_ACCOUNT_ID manquent dans .env")
    too_big = [p for p in paths if os.path.getsize(os.path.join(SITE, p)) > PAGES_MAX_BYTES]
    if too_big:
        raise SystemExit(f"✗ plus de 25 Mio, refusé par Cloudflare Pages : {', '.join(too_big)}")
    tmp = tempfile.mkdtemp(prefix="vachecker_")
    out = os.path.join(tmp, "site")   # wrangler écrit son dossier .wrangler à côté, pas dedans
    try:
        for p in paths:
            dst = os.path.join(out, p)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(SITE, p), dst)
        # Pages fait désormais partie de Workers : « wrangler deploy » d'un site
        # statique, avec un petit Worker pour /media/* seulement (lecture de la
        # vidéo par morceaux, cloudflare/worker.js). Configuration écrite dans le
        # dossier temporaire : wrangler ne détecte ni ne configure rien dans le dépôt.
        with open(os.path.join(ROOT, "cloudflare", "worker.js"), encoding="utf-8") as f:
            worker = f.read()
        sizes = {f"/{p}": os.path.getsize(os.path.join(SITE, p)) for p in paths if p.startswith("media/")}
        assert worker.count("const SIZES = {};") == 1, "cloudflare/worker.js : ligne SIZES introuvable"
        worker = worker.replace("const SIZES = {};", f"const SIZES = {json.dumps(sizes)};")
        with open(os.path.join(tmp, "worker.js"), "w", encoding="utf-8") as f:
            f.write(worker)
        with open(os.path.join(tmp, "wrangler.json"), "w", encoding="utf-8") as f:
            json.dump({"name": PAGES_PROJECT, "main": "worker.js", "compatibility_date": COMPAT_DATE,
                       "assets": {"directory": "site", "binding": "ASSETS", "run_worker_first": ["/media/*"]}}, f)
        env = {**os.environ, **keys, "WRANGLER_SEND_METRICS": "false"}
        npx = shutil.which("npx") or "npx"
        r = subprocess.run([npx, "--yes", WRANGLER, "deploy"], env=env, cwd=tmp)
        if r.returncode:
            raise SystemExit(f"✗ échec de l'envoi à Cloudflare (code {r.returncode})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Met le site en ligne (Hostinger par SSH, ou Cloudflare Pages).")
    ap.add_argument("--dry-run", action="store_true", help="Lister les fichiers sans rien envoyer")
    ap.add_argument("--cloudflare", action="store_true", help="Envoyer à Cloudflare Pages au lieu d'Hostinger")
    args = ap.parse_args()

    paths = files_to_publish()
    if args.cloudflare and not args.dry_run:
        print(f"{len(paths)} fichiers → Cloudflare Pages (projet {PAGES_PROJECT})")
        deploy_cloudflare(paths)
        return
    data = archive(paths)
    print(f"{len(paths)} fichiers, {len(data) // 1024} Ko compressés")
    if args.dry_run:
        for p in paths:
            print(f"  {p}")
        return
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, REMOTE_SCRIPT], input=data, capture_output=True)
    sys.stdout.write(r.stdout.decode("utf-8", "replace"))
    if r.returncode:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"✗ échec du déploiement (code {r.returncode})")
    print("✓ https://source.codeminds.fr")


if __name__ == "__main__":
    main()

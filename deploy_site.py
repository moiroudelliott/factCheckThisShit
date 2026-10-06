"""Met le site en ligne (source.codeminds.fr) par SSH.

Usage:
    python deploy_site.py --dry-run   # liste ce qui serait envoyé, sans rien toucher
    python deploy_site.py             # sauvegarde l'ancien site sur le serveur, puis le remplace

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
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
HOST = os.environ.get("SOURCE_SSH_HOST", "source-site")
REMOTE_DIR = "domains/source.codeminds.fr"  # le site est servi depuis REMOTE_DIR/public_html
KEEP_BACKUPS = 5


def files_to_publish() -> list:
    """Chemins relatifs à site/, dans une liste explicite."""
    out = [f for f in sorted(os.listdir(SITE)) if f.endswith(".html")]
    out += ["style.css", "relecture.js", "fiche.js", "favicon.svg", "robots.txt",
            "overlay/content.js", "overlay/overlay.css", "sessions/index.json"]
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


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Met le site en ligne par SSH.")
    ap.add_argument("--dry-run", action="store_true", help="Lister les fichiers sans rien envoyer")
    args = ap.parse_args()

    paths = files_to_publish()
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

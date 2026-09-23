"""Purge les verdicts douteux du cache de fact-checks (factcheck_cache.db).

Une purge ne peut jamais produire de mauvais verdict : l'affirmation sera
simplement revérifiée (recherche + Mistral) la prochaine fois qu'elle est
prononcée. Dans le doute, purger.

Usage:
    python purge_cache.py --dry-run                    # règles automatiques, sans rien supprimer
    python purge_cache.py                              # applique les règles automatiques
    python purge_cache.py --ids 67,94 --reason "verdict erroné" --ids 71 --reason "pas un fait"
                                                       # + identifiants choisis à la main, par groupe
    python purge_cache.py --list                       # affiche tout le cache (id, verdict, affirmation)

Règles automatiques (celles qu'applique aujourd'hui le backend avant de
mettre un verdict en cache — voir server/cache.py et server/sources.py) :
  - aucune URL de preuve (verdict « non sourcé »),
  - URL sur un domaine exclu (réseaux sociaux, médias sous sanctions de l'UE,
    satire…) ou de fiabilité faible (listes dans server/config.py),
  - verdict inconnu ou « non_verifiable », confiance absente ou < CACHE_MIN_CONF,
  - affirmation datée par rapport au jour même (« ce soir », « actuellement »…),
  - verdict signalé par un utilisateur (bouton ⚑ de l'extension ; motifs dans
    data/reports.jsonl).

Une copie de sauvegarde horodatée de la base est créée avant toute
suppression (factcheck_cache.backup-AAAAMMJJ-HHMMSS.db, ignorée par git).
Prise en compte au prochain démarrage du backend (le cache est chargé en
mémoire au démarrage).
"""

import argparse
import os
import sqlite3
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from server.config import CACHE_DB, CACHE_MIN_CONF  # noqa: E402
from server.sources import VERDICTS, is_excluded, is_low_reliability  # noqa: E402
from server.text_utils import has_relative_time  # noqa: E402


def auto_reason(verdict, conf, url, claim) -> str:
    if not url:
        return "non sourcé"
    if is_excluded(url):
        return "source exclue"
    if is_low_reliability(url):
        return "source de fiabilité faible"
    if verdict not in VERDICTS or verdict == "non_verifiable":
        return "verdict inutilisable"
    if not isinstance(conf, int) or conf < CACHE_MIN_CONF:
        return "confiance insuffisante"
    if has_relative_time(claim):
        return "daté par rapport au jour même"
    return ""


def main():
    ap = argparse.ArgumentParser(description="Purge les verdicts douteux du cache de fact-checks.")
    ap.add_argument("--dry-run", action="store_true", help="Afficher sans supprimer")
    ap.add_argument("--ids", action="append", default=[],
                    help="Identifiants à purger en plus, séparés par des virgules (répétable)")
    ap.add_argument("--reason", action="append", default=[],
                    help="Raison du groupe --ids de même rang (répétable)")
    ap.add_argument("--list", action="store_true", help="Lister tout le cache et quitter")
    args = ap.parse_args()

    if not os.path.exists(CACHE_DB):
        print(f"✗ Pas de cache à {CACHE_DB}")
        sys.exit(1)
    conn = sqlite3.connect(CACHE_DB)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(factchecks)")}
    reported_col = "reported_at" if "reported_at" in cols else "NULL"
    rows = conn.execute(f"SELECT id, claim, verdict, confiance, url, {reported_col} FROM factchecks ORDER BY id").fetchall()

    if args.list:
        for i, claim, verdict, conf, url, reported in rows:
            print(f"#{i:<4} {verdict:<19} {conf!s:>4}  {'⚑ ' if reported else ''}{claim[:110]}")
        return

    manual = {}  # id → raison
    for n, group in enumerate(args.ids):
        reason = args.reason[n] if n < len(args.reason) else "choisi à la main"
        for x in group.replace(" ", "").split(","):
            if x:
                manual[int(x)] = reason
    unknown = set(manual) - {r[0] for r in rows}
    if unknown:
        print(f"⚠ identifiant(s) absent(s) du cache, ignoré(s) : {sorted(unknown)}")

    to_purge = []
    for i, claim, verdict, conf, url, reported in rows:
        reason = "signalé par un utilisateur" if reported else auto_reason(verdict, conf, url, claim)
        reason = reason or manual.get(i, "")
        if reason:
            to_purge.append((i, reason, verdict, conf, claim))

    by_reason = {}
    for i, reason, verdict, conf, claim in to_purge:
        by_reason.setdefault(reason, []).append((i, verdict, conf, claim))
    for reason, items in by_reason.items():
        print(f"\n── {reason} ({len(items)})")
        for i, verdict, conf, claim in items:
            print(f"  #{i:<4} {verdict} {conf}  «{claim[:90]}»")
    print(f"\n{len(to_purge)} verdict(s) à purger sur {len(rows)}.")

    if args.dry_run or not to_purge:
        return
    backup = os.path.join(os.path.dirname(CACHE_DB), f"factcheck_cache.backup-{time.strftime('%Y%m%d-%H%M%S')}.db")
    with sqlite3.connect(backup) as dst:
        conn.backup(dst)
    conn.executemany("DELETE FROM factchecks WHERE id = ?", [(i,) for i, *_ in to_purge])
    conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM factchecks").fetchone()[0]
    print(f"✔ {len(to_purge)} verdict(s) purgé(s), {left} restant(s). Sauvegarde : {os.path.basename(backup)}")
    print("  Pris en compte au prochain démarrage du backend.")


if __name__ == "__main__":
    main()

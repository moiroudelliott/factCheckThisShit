"""Point d'entrée du backend — SOURCÉ.

Ce fichier ne fait QUE le monkey-patch eventlet (qui doit précéder tout
autre import, y compris celui du package server/) puis lance le serveur.
Toute la logique vit dans server/ (voir server/routes.py pour la vue
d'ensemble, ou ARCHITECTURE.md)."""

import eventlet
# Rendre les I/O réseau coopératives AVANT tout autre import : sans ça, chaque
# appel HTTP (Mistral ~3 s, recherche web ~1 s) gèle TOUT le serveur — y compris
# la réception des chunks audio. Source majeure de latence cumulée.
eventlet.monkey_patch(socket=True, select=True)
import eventlet.semaphore
import eventlet.tpool

import sys

# Certains terminaux Windows (codepage cp1252, pas de chcp 65001) plantent sur
# le moindre é/⚠️ dans un print() — jamais vu sur un terminal déjà en UTF-8,
# mais un crash au démarrage pour un caractère accentué est absurde à laisser.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from server.routes import app, socketio  # noqa: E402 — déclenche tout le chargement (modèles inclus)

if __name__ == "__main__":
    # 127.0.0.1 et non 0.0.0.0 : ce backend relaie vers l'API Mistral avec ta
    # clé et n'a pas vocation à être joignable depuis le réseau local.
    socketio.run(app, host="127.0.0.1", port=5000, debug=False)

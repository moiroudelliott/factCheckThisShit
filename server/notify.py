"""Avertissements visibles côté extension (message temporaire sur la puce).

Seul le 429 était remonté au client : une clé Mistral invalide, un crédit
épuisé ou une panne réseau laissaient la puce afficher « analyse en direct »
pendant qu'aucune carte n'arrivait jamais."""

import time

import requests

from server.app import socketio
from server.state import session_warned

WARN_REPEAT_S = 60  # un même message n'est pas réémis plus d'une fois par minute


def describe_error(e: Exception) -> str:
    if isinstance(e, requests.HTTPError) and e.response is not None:
        code = e.response.status_code
        if code == 401:
            return "clé Mistral invalide (401) — vérifie .env"
        if code == 402:
            return "crédit Mistral épuisé (402)"
        if code == 429:
            return "Mistral saturé — une analyse a été perdue"
        if code >= 500:
            return f"Mistral indisponible ({code})"
        return f"erreur Mistral ({code})"
    if isinstance(e, requests.Timeout):
        return "Mistral ne répond pas (délai dépassé)"
    if isinstance(e, requests.ConnectionError):
        return "Mistral injoignable (réseau)"
    return f"erreur d'analyse ({type(e).__name__})"


def warn_client(sid: str, message: str):
    now = time.time()
    seen = session_warned.setdefault(sid, {})
    if now - seen.get(message, 0) < WARN_REPEAT_S:
        return
    seen[message] = now
    socketio.emit("server_warning", {"message": message}, to=sid)

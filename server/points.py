"""Validation des talking points renvoyés par Mistral — fonctions pures,
sans modèle ni réseau (testables seules, cf. tests/).

Vérifiabilité : Mistral note chaque point de 0 à 10. Une « affirmation »
trop vague pour être vérifiée (« Il existe des fractures en France »,
« L'islam est une civilisation et une religion ») recevait quand même un
verdict — VRAI à 90 % sur une généralité, qui ne veut rien dire. Sous
CHECKWORTHY_MIN, elle devient « vague » : pas de fact-check, pas de carte,
visible au récap comme telle."""

import re

from server.config import CHECKWORTHY_MIN
from server.names import norm_name

_RAW_LABEL_RE = re.compile(r"^Intervenant ([A-Z]|\d+)$")


def verifiable_score(point: dict) -> int:
    """Note de vérifiabilité 0-10 donnée par Mistral ; absente ou illisible
    → 10 (on ne retire jamais une vérification faute de note)."""
    raw = point.get("verifiable")
    try:
        return max(0, min(10, int(round(float(raw)))))
    except (TypeError, ValueError):
        return 10


def apply_checkworthiness(point: dict) -> dict:
    point["verifiable"] = verifiable_score(point)
    if point.get("type") == "affirmation" and point["verifiable"] < CHECKWORTHY_MIN:
        point["type"] = "vague"
    return point


# ── Citation exacte ─────────────────────────────────────────────────────────
# Le point est une reformulation de Mistral, qui peut déformer le propos.
# Mistral renvoie aussi les mots exacts du passage (« citation ») : ils ne
# sont gardés que s'ils figurent VRAIMENT dans la transcription envoyée
# (même garde-fou que pour l'URL d'un verdict — jamais de citation
# inventée), et situent le propos dans le temps (segment qui la contient).

def norm_words(text: str) -> list:
    return re.findall(r"\w+", str(text or "").lower().replace("’", "'"))


def speaker_named_in_citation(qui: str, citation: str) -> bool:
    """La citation contient le nom de famille de la personne à qui on
    l'attribue : elle ne peut presque jamais être d'elle. Soit on
    l'interpelle (« La réponse est non, Marion Maréchal, c'est un sujet
    central »), soit on parle d'elle (« Gabriel Attal fait référence à… »,
    le présentateur). Cas vécus : la reconnaissance des voix se trompe dans
    les échanges rapides, et Mistral recopie l'annotation malgré la consigne."""
    if not qui or not citation or _RAW_LABEL_RE.match(qui):
        return False
    parts = norm_name(qui).split()
    surnames = [w for w in parts[1:] if len(w) >= 4] or [w for w in parts if len(w) >= 4]
    said = f" {norm_name(citation)} "
    return any(f" {w} " in said for w in surnames)


def validate_citation(citation, transcript: str) -> str:
    c = str(citation or "").strip().strip("«»\"“” ").strip()
    words = norm_words(c)
    if len(words) < 3:
        return ""
    haystack = " " + " ".join(norm_words(transcript)) + " "
    return c[:300] if " " + " ".join(words) + " " in haystack else ""


def citation_time(citation: str, entries: list):
    """Instant (unix) où la citation a été prononcée : celui du segment de
    transcription qui contient son début. None si introuvable."""
    words = norm_words(citation)[:6]
    if len(words) < 3:
        return None
    needle = " " + " ".join(words) + " "
    for entry in entries:
        if len(entry) > 2 and needle in " " + " ".join(norm_words(entry[1])) + " ":
            return entry[2]
    return None

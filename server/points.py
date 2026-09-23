"""Validation des talking points renvoyés par Mistral — fonctions pures,
sans modèle ni réseau (testables seules, cf. tests/).

Vérifiabilité : Mistral note chaque point de 0 à 10. Une « affirmation »
trop vague pour être vérifiée (« Il existe des fractures en France »,
« L'islam est une civilisation et une religion ») recevait quand même un
verdict — VRAI à 90 % sur une généralité, qui ne veut rien dire. Sous
CHECKWORTHY_MIN, elle devient « vague » : pas de fact-check, pas de carte,
visible au récap comme telle."""

from server.config import CHECKWORTHY_MIN


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

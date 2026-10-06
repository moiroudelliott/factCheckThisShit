"""Thèmes d'un débat — liste FIXE, la même pour tous les débats : des thèmes
inventés à chaque fois ne se compareraient pas d'un débat à l'autre.

Mistral range chaque point dans un thème dès l'extraction (champ « theme »,
sans appel supplémentaire) ; la fiche de fin de débat (server/summary.py)
en tire la frise et le tableau débatteur × thème."""

import re
import unicodedata

# identifiant → (libellé affiché, ce qu'il couvre — sert aussi au prompt)
THEMES = {
    "pouvoir_achat": ("Pouvoir d'achat", "salaires, prix, inflation, SMIC, primes"),
    "budget": ("Budget, dette et impôts", "fiscalité, taxes, dette, dépenses et déficit publics"),
    "economie": ("Économie et emploi", "croissance, taux de chômage, industrie, entreprises, commerce"),
    "energie": ("Énergie", "électricité, gaz, carburants, nucléaire, renouvelables"),
    "ecologie": ("Écologie et agriculture", "climat, environnement, agriculteurs, transports"),
    "immigration": ("Immigration et nationalité", "entrées, asile, expulsions, OQTF, binationaux, intégration"),
    "securite": ("Sécurité et justice", "délinquance, police, prisons, justice, terrorisme"),
    "social": ("Retraites et protection sociale", "retraites, CSG, allocations, assurance chômage, handicap"),
    "sante": ("Santé", "hôpital, médecins, déserts médicaux, médicaments"),
    "education": ("Éducation et jeunesse", "école, enseignants, université, jeunesse"),
    "international": ("Europe, international et défense", "Union européenne, Ukraine, Russie, armée, diplomatie"),
    "institutions": ("Institutions et vie politique", "Constitution, référendum, dissolution, élections, partis, alliances"),
    "societe": ("Société et libertés", "laïcité, discriminations, droits, logement, culture, médias"),
    "autre": ("Autre", "tout le reste"),
}


def _fold(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


_BY_LABEL = {_fold(label): tid for tid, (label, _) in THEMES.items()}


def normalize_theme(raw) -> str:
    """Identifiant de thème connu ; libellé accepté aussi (« Énergie » →
    « energie ») ; tout le reste → « autre »."""
    key = _fold(raw)
    if key in THEMES:
        return key
    return _BY_LABEL.get(key, "autre")


def themes_prompt_list() -> str:
    """« pouvoir_achat (salaires, prix…), budget (…) » — pour les prompts."""
    return ", ".join(f"{tid} ({hint})" for tid, (_, hint) in THEMES.items())

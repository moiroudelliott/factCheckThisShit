"""Règles sur les sources et les verdicts du fact-check — fonctions pures,
sans modèle ni réseau (testables seules, cf. tests/).

- quels domaines sont exclus, officiels, de presse établie, partisans ou
  de fiabilité faible (listes dans config.py, publiées sur le site) ;
- normalisation du verdict renvoyé par Mistral ;
- nom de source affiché, cohérent avec l'URL réellement liée ;
- confiance plafonnée quand aucune preuve n'est liée, ou quand la seule
  preuve est de fiabilité faible."""

import re
import time
import unicodedata
from urllib.parse import urlparse

from server.config import (
    EXCLUDED_SOURCE_DOMAINS, FACTCHECK_SECTIONS, LOW_RELIABILITY_DOMAINS, PARTISAN_DOMAINS,
)

# Hiérarchie de fiabilité des domaines — annotée dans le prompt pour que le
# verdict pèse une source officielle plus lourd qu'un blog. Noms de domaine
# (sous-domaines inclus), comparés à l'hôte de l'URL, jamais à l'URL entière.
TIER_OFFICIAL = (
    "insee.fr", "europa.eu", "legifrance.gouv.fr", "vie-publique.fr",
    "senat.fr", "assemblee-nationale.fr", "gouv.fr", "banque-france.fr", "ccomptes.fr",
    "oecd.org", "ocde.org", "ined.fr", "ademe.fr", "conseil-constitutionnel.fr", "conseil-etat.fr",
)
TIER_PRESS = (
    "afp.com", "lemonde.fr", "liberation.fr", "francetvinfo.fr", "radiofrance.fr",
    "franceinfo.fr", "lefigaro.fr", "lesechos.fr", "publicsenat.fr", "lcp.fr",
    "reuters.com", "latribune.fr", "ouest-france.fr", "bfmtv.com", "20minutes.fr",
    "courrierinternational.com", "la-croix.com", "sudouest.fr",
)

VERDICTS = ("vrai", "partiellement_vrai", "trompeur", "faux", "non_verifiable")
_VERDICT_ALIASES = {
    "partiel": "partiellement_vrai", "partiellement": "partiellement_vrai",
    "trompeuse": "trompeur", "non_verifie": "non_verifiable", "inverifiable": "non_verifiable",
}
UNSOURCED_MAX_CONF = 50  # plafond de confiance d'un verdict sans URL de preuve
LOW_RELIABILITY_MAX_CONF = 50  # … ou dont la preuve est de fiabilité faible (jamais mis en cache)

_SOURCE_STOP = {"les", "des", "via", "and", "the", "sur", "avec", "citant", "selon", "source", "sources", "site"}


def host(url: str) -> str:
    """Nom d'hôte d'une URL, sans « www. » (vide si l'URL est invalide)."""
    try:
        h = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def on_domain(h: str, domains) -> bool:
    """h est l'un des domaines, ou un de leurs sous-domaines — jamais une
    simple sous-chaîne (« notafp.com » n'est pas « afp.com », et un
    « ?ref=insee.fr » dans l'URL ne fait pas une source officielle)."""
    return bool(h) and any(h == d or h.endswith("." + d) for d in domains)


def is_excluded(url: str) -> bool:
    return on_domain(host(url), EXCLUDED_SOURCE_DOMAINS)


def is_low_reliability(url: str) -> bool:
    return on_domain(host(url), LOW_RELIABILITY_DOMAINS)


def is_factcheck_section(url: str) -> bool:
    """Article d'une rubrique de fact-checking (hôte + début du chemin)."""
    try:
        path = urlparse(url or "").path.rstrip("/")
    except ValueError:
        return False
    where = f"{host(url)}{path}/"
    return any(where.startswith(s.rstrip("/") + "/") for s in FACTCHECK_SECTIONS)


def source_tier(url: str) -> str:
    h = host(url)
    if is_factcheck_section(url):
        return "FACT-CHECK PUBLIÉ"
    if on_domain(h, TIER_OFFICIAL):
        return "SOURCE OFFICIELLE"
    if on_domain(h, TIER_PRESS):
        return "PRESSE ÉTABLIE"
    if on_domain(h, PARTISAN_DOMAINS):
        return "SOURCE PARTISANE"
    if on_domain(h, LOW_RELIABILITY_DOMAINS):
        return "FIABILITÉ FAIBLE"
    return "FIABILITÉ INCONNUE"


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def normalize_verdict(verdict) -> str:
    """« Vrai », « partiellement vrai », « Non vérifiable »… → clé attendue par
    l'extension ; toute valeur inconnue devient non_verifiable (jamais un
    verdict inventé par défaut)."""
    key = re.sub(r"[\s\-]+", "_", _ascii(verdict or "").strip())
    return key if key in VERDICTS else _VERDICT_ALIASES.get(key, "non_verifiable")


def source_label(claimed: str, url: str, kind: str = "") -> str:
    """Nom de source affiché sous le verdict. Mistral nomme parfois une
    autorité qui n'est PAS le site lié (« Ministère de l'Économie » pour un
    blog) : on ne garde que les parties du nom qui correspondent réellement
    au domaine de l'URL, sinon on affiche le domaine lui-même."""
    if not url:
        return "non sourcé"
    h = host(url)
    host_key = re.sub(r"[^a-z0-9]", "", h)
    kept = []
    for part in re.split(r"[,;/()]| et | - ", claimed or ""):
        part = re.sub(r"^(via|citant|selon)\s+", "", part.strip(" -–—.:"), flags=re.IGNORECASE)
        tokens = [t for t in re.findall(r"[a-z0-9]{3,}", _ascii(part)) if t not in _SOURCE_STOP]
        if tokens and any(t in host_key for t in tokens):
            kept.append(part)
    if kept:
        return ", ".join(kept)[:60]
    if kind == "academic":
        return f"étude académique ({h})"
    return h or "source"


def finalize_result(data: dict, results: list, academic: list, official: list, known: list = (),
                    structured: list = ()) -> dict:
    """Normalise la réponse Mistral : verdict connu, URL issue des résultats
    de recherche (jamais inventée ; http(s) uniquement — une URL javascript:
    serait un vecteur XSS), nom de source cohérent avec l'URL, confiance
    plafonnée sans preuve. La règle « verdict tranché = sources » du prompt
    n'est qu'une consigne ; ici elle est appliquée."""
    out = {"verdict": normalize_verdict(data.get("verdict")),
           "explication": str(data.get("explication") or "").strip()}
    kinds = {r.get("href"): "web" for r in results}
    kinds.update({r.get("href"): "academic" for r in academic})
    kinds.update({r.get("href"): "official" for r in official})
    outlets = {k.get("url"): k.get("outlet", "") for k in known}
    # Données structurées (Eurostat, votes de l'Assemblée) : l'émetteur est connu
    outlets.update({r.get("href"): r["title"].split(" — ")[0] for r in structured})
    kinds.update({u: "factcheck" for u in outlets})
    url = data.get("url", "")
    if not (isinstance(url, str) and url.startswith(("http://", "https://")) and url in kinds):
        url = ""
    out["url"] = url
    # Fact-check déjà publié : on nomme la rédaction (connue), pas ce que dit Mistral
    out["source"] = outlets[url] if url in outlets else source_label(str(data.get("source") or ""), url, kinds.get(url, ""))
    conf = data.get("confiance")
    conf = max(0, min(100, int(conf))) if isinstance(conf, (int, float)) else None
    if not url and conf is not None:
        conf = min(conf, UNSOURCED_MAX_CONF)
    # Un site militant ou conspirationniste ne suffit jamais seul à trancher
    if is_low_reliability(url) and conf is not None:
        conf = min(conf, LOW_RELIABILITY_MAX_CONF)
    out["confiance"] = conf
    return out


def video_year(context: dict) -> int:
    """Année des propos vérifiés : celle de la publication de la vidéo si
    connue (replay), sinon l'année en cours (direct)."""
    date = str((context or {}).get("date") or "")
    return int(date[:4]) if re.match(r"^(19|20)\d\d", date) else time.localtime().tm_year

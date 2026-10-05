"""Règles sur les sources et les verdicts du fact-check — fonctions pures,
sans modèle ni réseau (testables seules, cf. tests/).

- quels domaines sont exclus, officiels, de presse établie, partisans ou
  de fiabilité faible (listes dans config.py, publiées sur le site) ;
- normalisation du verdict renvoyé par Mistral ;
- nom de source affiché, cohérent avec l'URL réellement liée ;
- confiance plafonnée quand aucune preuve n'est liée, ou quand la seule
  preuve est de fiabilité faible."""

import html
import math
import re
import time
import unicodedata
from urllib.parse import urlparse

from server.config import (
    EXCLUDED_SOURCE_DOMAINS, FACTCHECK_SECTIONS, LOW_RELIABILITY_DOMAINS, PARTISAN_DOMAINS,
)
from server.text_utils import key_words

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

VERDICTS = ("vrai", "partiellement_vrai", "trompeur", "faux", "non_recoupe", "non_verifiable")
_VERDICT_ALIASES = {
    "partiel": "partiellement_vrai", "partiellement": "partiellement_vrai",
    "trompeuse": "trompeur", "non_verifie": "non_verifiable", "inverifiable": "non_verifiable",
    "non_recoupee": "non_recoupe",
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


def academic_relevant(claim: str, result: dict) -> bool:
    """Un résultat académique ne passe au prompt que s'il porte vraiment sur
    l'affirmation : au moins 2 mots-clés communs (titre + résumé), et 30 %
    de ceux de l'affirmation. Cas vécus : la fiche d'un catalogue de
    bibliothèque allemand citée comme preuve qu'« Attal a été Premier
    ministre », une étude voisine qui a fait conclure « faux » à tort."""
    words = key_words(claim)
    if not words:
        return False
    common = words & key_words(f"{result.get('title', '')} {result.get('body', '')}")
    # Des noms propres en commun (« Gabriel Attal ») ne disent rien du sujet :
    # il faut au moins un mot commun qui n'en soit pas un
    names = key_words(" ".join(re.findall(r"\b[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][\w'’-]*", claim)))
    return len(common) >= max(2, math.ceil(0.3 * len(words))) and bool(common - names)


def _strip_tags(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(str(text or "")))).strip()


def parse_brave_results(data: dict, max_results: int) -> list:
    """Réponse de l'API Brave Search → résultats au format de web_search
    ({title, body, href}), domaines exclus retirés, balises <strong> ôtées."""
    out = []
    for res in ((data or {}).get("web") or {}).get("results") or []:
        href = res.get("url") or ""
        if not href.startswith(("http://", "https://")) or is_excluded(href):
            continue
        body = _strip_tags(res.get("description"))
        extra = [_strip_tags(x) for x in (res.get("extra_snippets") or [])[:1]]
        out.append({"title": _strip_tags(res.get("title")), "body": " … ".join([body, *extra])[:450], "href": href})
        if len(out) >= max_results:
            break
    return out


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
    if on_domain(h, ("wikipedia.org",)):
        return "ENCYCLOPÉDIE"
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


_YEAR_RE = re.compile(r"\b((?:19|20)\d\d)\b")
_YEAR_RANGE_RE = re.compile(r"\b((?:19|20)\d\d)\s*(?:-|–|à|au)\s*((?:19|20)\d\d)\b")


def years_in(text: str) -> set:
    """Années citées dans un texte, intervalles compris (« 2017-2026 »)."""
    text = str(text or "")
    years = {int(y) for y in _YEAR_RE.findall(text)}
    for a, b in _YEAR_RANGE_RE.findall(text):
        a, b = int(a), int(b)
        if a < b <= a + 30:
            years.update(range(a, b + 1))
    return years


_STATEMENT_VERBS = (r"(?:annonce|declare|affirme|denonce|indique|assure|explique|precise|evoque|confirme"
                    r"|souligne|revendique|chiffre|estime)")
_CLAIM_ABOUT_STATEMENT_RE = re.compile(
    r"\b(?:a|ont|avait|aurait)\s+(?:\w+\s+)?(?:dit|declare|annonce|affirme|ecrit|tweete|promis|propose)\b|\bselon\b")


def self_sourced(explication: str, qui: str, claim: str = "") -> bool:
    """L'explication d'un verdict favorable ne fait que rapporter la
    déclaration de l'auteur de l'affirmation (« Édouard Geffray a
    effectivement annoncé que 24 établissements… ») : la preuve, c'est sa
    propre parole. Cas vécus : « 78 personnels blessés », « des militants
    d'ultragauche ont infiltré le mouvement », VRAI à 90-95 % sur ce seul
    fondement. Sauf si l'affirmation porte elle-même sur une déclaration
    (« Darmanin a déclaré sur RTL… », « selon Laurent Nuñez… »)."""
    parts = [w for w in re.findall(r"[a-z]+", _ascii(qui)) if len(w) >= 4]
    if not parts or _CLAIM_ABOUT_STATEMENT_RE.search(_ascii(claim)):
        return False
    said = _ascii(explication)
    for name in parts[1:] or parts:
        if re.search(rf"\b{name}\b[^.;]{{0,40}}\b(?:a|avait|ont)\s+(?:\w+\s+)?{_STATEMENT_VERBS}", said):
            return True
        if re.search(rf"\bselon\s+(?:\w+\s+){{0,4}}{name}\b", said):
            return True
    return False


_INAUDIBLE_RE = re.compile(r"\W*transcription\s+(?:douteuse|incomplete|incertaine|inaudible|erronee|confuse)")


def is_inaudible(explication: str) -> bool:
    """Explication d'un « non vérifiable » dû à la transcription (le prompt
    demande de commencer par « Transcription douteuse : »)."""
    return bool(_INAUDIBLE_RE.match(_ascii(explication)))


_ABSENCE_RE = re.compile(
    r"\W*(?:rien ne|les sources ne|pas de source|aucune source"
    r"|aucune?\b[^.]*?\b(?:ne|n')\s*(?:\w+\s+)?(?:confirme|mentionne|prevoi|indique|evoque|montre|prouve|etabli"
    r"|est\s+(?:pas\s+|nulle part\s+)?mentionne))")

_AUTHOR_SIDE_RE = re.compile(r"\b(?:ministere|ministre|gouvernement|matignon|elysee|partisane?|parti|son camp"
                             r"|lui-meme|elle-meme|ses propres|sa propre|son propre)\b")


def names_the_author(explication: str, qui: str) -> bool:
    """L'explication d'un « non recoupé » nomme la source de la déclaration :
    l'auteur lui-même (nom de famille) ou son camp (ministère, parti…)."""
    said = _ascii(explication)
    names = [w for w in re.findall(r"[a-z]+", _ascii(qui)) if len(w) >= 4]
    return bool(_AUTHOR_SIDE_RE.search(said)) or any(re.search(rf"\b{n}\b", said) for n in names[1:] or names)


def finalize_result(data: dict, results: list, academic: list, official: list, known: list = (),
                    structured: list = (), claim: str = "", qui: str = "", periode: str = "") -> dict:
    """Normalise la réponse Mistral : verdict connu, URL issue des résultats
    de recherche (jamais inventée ; http(s) uniquement — une URL javascript:
    serait un vecteur XSS), nom de source cohérent avec l'URL, confiance
    plafonnée sans preuve. La règle « verdict tranché = sources » du prompt
    n'est qu'une consigne ; ici elle est appliquée."""
    out = {"verdict": normalize_verdict(data.get("verdict")),
           "explication": str(data.get("explication") or "").strip()}
    # « vrai » alors que le modèle a lui-même relevé un élément contredit par
    # ses sources (cas vécu : « première baisse depuis 15-20 ans », VRAI à
    # 95 %, l'explication citant une baisse en 2020) : au mieux partiel
    if out["verdict"] == "vrai" and str(data.get("inexact") or "").strip():
        out["verdict"] = "partiellement_vrai"
    # Seule preuve : la parole de l'auteur lui-même
    if out["verdict"] in ("vrai", "partiellement_vrai") and self_sourced(out["explication"], qui, claim):
        out["verdict"] = "non_recoupe"
    # « faux » appuyé sur une autre année que celle dont parle l'affirmation
    # (cas vécus : « +1,2 milliard » pour le budget 2027 démenti par le budget
    # 2026 ; une visite « lundi dernier » démentie par une page de 2025)
    claim_years, source_years = years_in(periode), years_in(data.get("contredit_par"))
    if out["verdict"] == "faux" and claim_years and source_years and not claim_years & source_years:
        out["verdict"] = "non_verifiable"
        out["explication"] = (f"Les sources trouvées portent sur une autre période ({', '.join(map(str, sorted(source_years)))}) "
                              f"que celle de l'affirmation ({periode}).")
    # Plus bas, « faux » sans fait contraire tiré d'une source liée est
    # ramené à « non vérifiable » (cas vécu : FAUX 95 % sur « rien ne prouve
    # que… »)
    wants_false = out["verdict"] == "faux"
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
    if wants_false and not (url and str(data.get("contredit_par") or "").strip()):
        out["verdict"] = "non_verifiable"
    # « Aucune projection de l'Insee ne prévoit… » recopié comme fait contraire :
    # une absence de preuve, pas une contradiction (cas vécus avec Mistral Large)
    if out["verdict"] == "faux" and (_ABSENCE_RE.match(_ascii(data.get("contredit_par") or ""))
                                     or _ABSENCE_RE.match(_ascii(out["explication"]))):
        out["verdict"] = "non_verifiable"
    # « non recoupé » = un article rapporte la déclaration ; sans article lié,
    # c'est qu'aucune source ne traite du sujet (cas vécu : « non recoupé » à
    # 40 % sur un lien de cause à effet, sans aucune source)
    # … et l'explication doit dire de qui vient la déclaration (l'auteur, son
    # ministère, son parti) — sinon le modèle s'en sert comme d'un « non
    # vérifiable » (cas vécu : un lien de cause à effet « non recoupé »)
    if out["verdict"] == "non_recoupe" and not (url and names_the_author(out["explication"], qui)):
        out["verdict"] = "non_verifiable"
    # Propos mal transcrit (« Transcription douteuse : … ») : rien de vérifié,
    # rien à montrer — l'extension retire la carte (cas vécu : une carte
    # « Transcription incomplète » affichée sur la vidéo)
    if out["verdict"] == "non_verifiable" and is_inaudible(out["explication"]):
        out["inaudible"] = True
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


def related_verdicts(claim: str, previous: list, max_n: int = 4) -> list:
    """Verdicts déjà rendus dans le débat sur un sujet proche (au moins deux
    mots-clés communs hors noms propres), les plus récents d'abord."""
    words = key_words(claim) - key_words(" ".join(re.findall(r"\b[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][\w'’-]*", claim)))
    out = []
    for v in reversed(previous):
        if len(words & key_words(v.get("claim", ""))) >= 2:
            out.append(v)
            if len(out) >= max_n:
                break
    return out


def video_year(context: dict) -> int:
    """Année des propos vérifiés : celle de la publication de la vidéo si
    connue (replay), sinon l'année en cours (direct)."""
    date = str((context or {}).get("date") or "")
    return int(date[:4]) if re.match(r"^(19|20)\d\d", date) else time.localtime().tm_year

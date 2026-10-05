"""Petits utilitaires texte partagés : comparaison d'affirmations (dédup +
cache), filtre anti-hallucination Whisper, transcript annoté par locuteur,
nettoyage de description YouTube."""

import re
from typing import NamedTuple

_STOPWORDS = {
    'avec', 'aussi', 'alors', 'autre', 'autres', 'bien', 'mais', 'même',
    'nous', 'plus', 'pour', 'puis', 'quand', 'sans', 'sont', 'très',
    'tout', 'tous', 'toute', 'toutes', 'vers', 'vous', 'dans', 'ainsi',
    'avoir', 'être', 'faire', 'dire', 'cette', 'cela', 'comme', 'donc',
    'dont', 'elle', 'elles', 'entre', 'leur', 'leurs', 'celui', 'celle',
    'ceux', 'celles', 'depuis', 'quel', 'quelle', 'quels', 'quelles',
}


def key_words(text: str) -> set:
    """Mots de contenu (≥ 4 lettres, hors mots vides). Les nombres sont
    volontairement exclus : ils sont comparés à part, exactement (voir
    claim_signature)."""
    tokens = re.findall(r'\b[a-zàâçéèêëîïôùûüœ]{4,}\b', text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


# ── Comparaison d'affirmations ─────────────────────────────────────────────
# Le recouvrement de mots-clés seul ne voit pas ce qui change le SENS d'une
# affirmation : « le chômage a baissé » / « le chômage a augmenté », « a voté
# contre » / « a voté pour », « 43 milliards » / « 12 milliards », « a gelé »
# / « n'a jamais gelé » partagent presque tous leurs mots-clés. Deux
# affirmations ne sont donc « la même » que si, en plus du recouvrement :
#   - leurs nombres sont identiques,
#   - leur polarité (négation) est identique,
#   - elles emploient les mêmes mots de sens/direction (hausse, baisse,
#     double, contre, plus, moins…).

_NEGATION_RE = re.compile(r"(?<!\w)(?:ne|jamais|aucune?|nullement|guère)(?!\w)|(?<!\w)n['’]", re.IGNORECASE)

# Mots (exacts, ou par leur début) dont la présence d'un seul côté change le
# sens d'une affirmation
_POLARITY_WORDS = {'plus', 'moins', 'contre', 'davantage'}
_POLARITY_STEMS = (
    'hauss', 'baiss', 'augment', 'diminu', 'rédui', 'réduc', 'recul', 'chut', 'explos',
    'effondr', 'stagn', 'stab', 'progressé', 'progression', 'progresse',
    'doubl', 'tripl', 'quadrupl', 'moitié',
    'supérieur', 'inférieur', 'majorit', 'minorit',
    'favorable', 'défavorable', 'oppos', 'soutien', 'soutenu',
    'gagn', 'perd', 'pert', 'excédent', 'déficit', 'record',
    'interdi', 'autoris', 'obligatoire', 'légal', 'illégal',
)


class ClaimSig(NamedTuple):
    words: frozenset     # mots de contenu
    numbers: frozenset   # nombres normalisés ("3 000" → "3000", "5,50" → "5.5")
    negative: bool       # contient une négation
    polar: frozenset     # mots de sens/direction présents


def _norm_number(raw: str) -> str:
    s = raw.replace(',', '.')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s.lstrip('0') or '0'


def claim_signature(text: str) -> ClaimSig:
    low = str(text).lower()
    # Séparateurs de milliers ("3 000", "3 000", "1.000") → un seul nombre
    compact = re.sub(r'(?<=\d)[\s.](?=\d{3}(?!\d))', '', low)  # \s couvre aussi les espaces insécables
    numbers = frozenset(_norm_number(n) for n in re.findall(r'\d+(?:[.,]\d+)?', compact))
    tokens = re.findall(r'[a-zàâçéèêëîïôùûüœ]+', low)
    polar = frozenset(t for t in tokens if t in _POLARITY_WORDS or t.startswith(_POLARITY_STEMS))
    return ClaimSig(frozenset(key_words(low)), numbers, bool(_NEGATION_RE.search(low)), polar)


# Proportions dites en toutes lettres → pourcentage, pour reconnaître le même
# chiffre sous une autre forme (« un dixième des cours », « une heure de
# cours sur dix », « 10 % des heures » : vérifié 5 fois dans le même débat)
_FRACTIONS = (
    (re.compile(r"\bun dixième\b|\b(?:une?|1)(?:\s+[\w'’-]+){1,6}\s+sur\s+dix\b"), "10"),
    (re.compile(r"\bla moitié\b|\b(?:une?|1)(?:\s+[\w'’-]+){1,6}\s+sur\s+deux\b"), "50"),
    (re.compile(r"\bun quart\b|\b(?:une?|1)(?:\s+[\w'’-]+){1,6}\s+sur\s+quatre\b"), "25"),
    (re.compile(r"\bun tiers\b|\b(?:une?|1)(?:\s+[\w'’-]+){1,6}\s+sur\s+trois\b"), "33"),
)
_YEAR_NUMBER_RE = re.compile(r"^(?:19|20)\d\d$")
_APPROX_RE = re.compile(r"\bau (?:moins|plus)\b")


def figures(text: str) -> frozenset:
    """Chiffres d'une affirmation, années exclues, proportions en lettres
    converties (« un sur deux » → 50)."""
    low = str(text).lower()
    compact = re.sub(r'(?<=\d)[\s.](?=\d{3}(?!\d))', '', low)
    found = {_norm_number(n) for n in re.findall(r'\d+(?:[.,]\d+)?', compact)}
    found = {n for n in found if not _YEAR_NUMBER_RE.match(n)}
    for pattern, value in _FRACTIONS:
        if pattern.search(low):
            found.add(value)
    found.discard("1")  # le « un » de « un sur deux », ou « 1 » isolé : rien de distinctif
    return frozenset(found)


def same_figures(a: str, b: str) -> bool:
    """Même chiffre, même sujet : deux affirmations qui avancent exactement les
    mêmes chiffres (hors années), dans le même sens (hausse, baisse…) et avec
    au moins un mot-clé commun. Rattrape les reprises qu'un recouvrement de
    mots ne voit pas (« 78 personnels blessés lors de violences ciblant leur
    statut » / « 78 personnels blessés depuis le début des événements »)."""
    fa, fb = figures(a), figures(b)
    if not fa or fa != fb:
        return False
    # « au moins 24 » / « 24 » : une approximation, pas un sens différent
    sa, sb = (claim_signature(_APPROX_RE.sub(" ", t.lower())) for t in (a, b))
    return sa.polar == sb.polar and bool(sa.words & sb.words)


def same_idea(a: str, b: str, threshold: float = 0.5) -> bool:
    """Deux affirmations tirées du MÊME passage, par le même orateur, qui
    disent la même chose en variantes (« les naissances non européennes
    seront majoritaires en 2045 » / « les immigrés non européens seront
    majoritaires dans la population ») : mêmes chiffres hors années (souvent
    aucun), et la moitié des mots-clés en commun. Vérifiées en parallèle,
    elles recevaient des verdicts différents."""
    if figures(a) != figures(b):
        return False
    wa, wb = key_words(a), key_words(b)
    if len(wa) < 3 or len(wb) < 3:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


def claims_match(a: ClaimSig, b: ClaimSig, threshold: float) -> bool:
    """Vrai si a et b disent la même chose (reformulation), faux dès qu'un
    nombre, la négation ou un mot de sens diffère."""
    if a.numbers != b.numbers or a.negative != b.negative or a.polar != b.polar:
        return False
    if len(a.words) < 3 or len(b.words) < 3:
        return False
    return len(a.words & b.words) / min(len(a.words), len(b.words)) >= threshold


# Repères de temps plus fins que l'année : une affirmation qui en contient
# (« ce soir », « actuellement », « il y a un an ») ne vaut que pour le jour
# où elle est dite — son verdict ne doit pas être resservi par le cache,
# même rangé sous la bonne année.
_RELATIVE_TIME_RE = re.compile(
    r"\b(?:aujourd['’]hui|ce soir|ce matin|cet après-midi|avant-hier|hier|demain"
    r"|cette semaine|la semaine (?:dernière|prochaine)|ce mois-ci|le mois (?:dernier|prochain)"
    r"|actuellement|en ce moment|à l['’]heure actuelle|à ce jour|à la date du débat"
    r"|il y a (?:un|une|deux|trois|quelques|\d+) (?:jours?|semaines?|mois|ans?))\b",
    re.IGNORECASE,
)


def has_relative_time(text: str) -> bool:
    return bool(_RELATIVE_TIME_RE.search(str(text)))


def strip_overlap(prev: str, text: str, min_words: int = 2, max_words: int = 15) -> str:
    """Retire du début de `text` les mots qui répètent la fin de `prev`.
    Deux chunks audio se chevauchent de 1,5 s (pour ne jamais couper un mot) :
    Whisper retranscrit donc souvent la fin du chunk précédent au début du
    suivant (« …le chômage a baissé de » / « a baissé de 2 % en 2023 »), et le
    filtre à l'identique ne voyait pas ces doublons partiels. Au moins
    `min_words` mots identiques sont exigés (un « de » commun ne suffit pas)."""
    pw, tw = prev.split(), text.split()
    norm = lambda w: re.sub(r'\W', '', w.lower())  # noqa: E731
    pn, tn = [norm(w) for w in pw], [norm(w) for w in tw]
    for k in range(min(len(pn), len(tn), max_words), min_words - 1, -1):
        if pn[-k:] == tn[:k]:
            return " ".join(tw[k:])
    return text


def build_transcript(entries: list) -> str:
    """Transcript annoté par locuteur, tours de parole consécutifs fusionnés.
    entries : [(label, texte, …)] — les éléments suivants (horodatage) sont ignorés."""
    if not any(e[0] for e in entries):
        return " ".join(e[1] for e in entries)
    lines, cur_label, cur_texts = [], None, []
    for label, t, *_ in entries:
        label = label or cur_label or "Intervenant ?"
        if label != cur_label and cur_texts:
            lines.append(f"{cur_label}: {' '.join(cur_texts)}")
            cur_texts = []
        cur_label = label
        cur_texts.append(t)
    if cur_texts:
        lines.append(f"{cur_label}: {' '.join(cur_texts)}")
    return "\n".join(lines)


def clean_description(desc: str) -> str:
    """Garde l'info utile (intervenants, sujet, date) d'une description
    YouTube, vire le bruit (liens, hashtags, appels à s'abonner, réseaux
    sociaux)."""
    noise = ("http://", "https://", "www.", "abonnez-vous", "abonne-toi", "#",
             "twitter", "instagram", "facebook", "tiktok", "twitch", "discord")
    lines = []
    for line in desc.splitlines():
        l = line.strip()
        if l and not any(n in l.lower() for n in noise):
            lines.append(l)
    return " ".join(lines)[:600]


# ── Filtre anti-hallucination ──────────────────────────────────────────────
# Phrases hallucinées connues par Whisper sur du contenu YouTube/TV
HALLUCINATION_BLACKLIST = [
    "amara.org",
    "sous-titres réalisés",
    "sous-titrages réalisés",
    "sous-titrage st",
    "sous-titres par",
    "abonnez-vous",
    "merci d'avoir regardé",
    "transcription by",
    "subtitles by",
    "community captions",
    "♪",
    "music:",
]


def is_hallucination(text: str) -> bool:
    low = text.lower().strip()

    if any(pattern in low for pattern in HALLUCINATION_BLACKLIST):
        return True

    # Texte composé quasi-uniquement de tirets/ponctuation ("— — — — —", "...", etc.)
    meaningful = text.replace("—", "").replace("-", "").replace(".", "").replace(" ", "").replace("…", "").strip()
    if len(text.strip()) > 3 and len(meaningful) < len(text.strip()) * 0.2:
        return True

    return False

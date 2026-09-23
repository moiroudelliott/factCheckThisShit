"""Mots attendus par Whisper (paramètre `hotwords` de faster-whisper) —
fonctions pures, sans modèle.

Whisper écorche surtout les noms propres et les sigles (« Mereaux » pour
« maires ruraux », « Baïa » pour « abaya ») ; l'erreur se propage ensuite
jusqu'au verdict. `hotwords` lui indique les mots à attendre, mais la place
est limitée (~200 tokens) et une liste trop longue le pousse à les
inventer : on la construit donc par ordre de priorité, et on la coupe.

  1. intervenants déclarés (popup) — toujours en tête ;
  2. noms propres et sigles du titre / de la description de la vidéo ;
  3. noms propres appris pendant le débat (points extraits par Mistral) ;
  4. lexique de base, modifiable : vocabulaire.txt à la racine."""

import os
import re

LEXICON_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vocabulaire.txt")
MAX_HOTWORDS_CHARS = 450  # ≈ 180 tokens Whisper : sous la limite de 223, avec de la marge
MAX_LEARNED = 20          # noms propres appris pendant le débat, les plus récents

# Mots à majuscule qui ne sont pas des noms propres (débuts de phrase,
# vocabulaire des titres YouTube)
_NOT_PROPER = {
    "le", "la", "les", "un", "une", "des", "du", "de", "ce", "cet", "cette", "ces", "et", "ou",
    "en", "dans", "pour", "par", "sur", "avec", "sans", "au", "aux", "à", "il", "elle", "ils",
    "on", "nous", "vous", "qui", "que", "quoi", "comment", "pourquoi", "quand", "mais", "donc",
    "débat", "replay", "direct", "live", "émission", "vidéo", "suivez", "retrouvez", "regardez",
    "abonnez", "invité", "invités", "face", "entre", "notre", "votre", "leur", "tous", "toutes",
    "partie", "épisode", "intégrale", "extrait", "lundi", "mardi", "mercredi", "jeudi",
    "vendredi", "samedi", "dimanche", "législatives", "présidentielle", "élections", "européennes",
    "municipales", "régionales", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
}

_CAP = r"[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜŒ][\wàâçéèêëîïôùûüœ'’-]+"
_PROPER_RE = re.compile(rf"{_CAP}(?:\s+{_CAP})*")
_ACRONYM_RE = re.compile(r"\b(?:[A-Z]{2,6}|\d{2}\.\d)\b")


def load_lexicon(path: str = LEXICON_FILE) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    except OSError:
        return []


def proper_nouns(text: str) -> list:
    """Noms propres (suites de mots à majuscule) et sigles d'un texte, dans
    l'ordre d'apparition, sans doublon."""
    text = str(text or "")
    found = []
    for m in _PROPER_RE.finditer(text):
        term = m.group(0).strip(" '’-")
        words = term.split()
        # Un mot isolé à majuscule n'est retenu que s'il n'est pas un mot courant
        if len(words) == 1 and (term.lower() in _NOT_PROPER or len(term) < 3):
            continue
        # Mot courant en tête de groupe (« Suivez Jordan Bardella ») : on le retire
        while words and words[0].lower() in _NOT_PROPER:
            words = words[1:]
        if words:
            found.append(" ".join(words))
    found += _ACRONYM_RE.findall(text)
    return _dedupe(found)


def _dedupe(terms) -> list:
    seen, out = set(), []
    for t in terms:
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def learn(learned: list, text: str) -> list:
    """Ajoute les noms propres de `text` aux termes appris (les plus récents
    en fin de liste, MAX_LEARNED au plus)."""
    for term in proper_nouns(text):
        if term in learned:
            learned.remove(term)
        learned.append(term)
    del learned[:-MAX_LEARNED]
    return learned


def build_hotwords(guests=(), emission: str = "", description: str = "", learned=(), lexicon=None) -> str:
    """Chaîne `hotwords` pour Whisper, par ordre de priorité, coupée à
    MAX_HOTWORDS_CHARS."""
    lexicon = load_lexicon() if lexicon is None else lexicon
    # « Cour » (de « Cour des comptes ») ou « Rassemblement » seuls : morceaux
    # d'un terme du lexique, qui y figure déjà en entier
    lexicon_heads = {t.split()[0].lower() for t in lexicon if " " in t}
    found = [t for t in (*proper_nouns(f"{emission}. {description}"), *reversed(list(learned)))
             if " " in t or t.lower() not in lexicon_heads]
    ordered = _dedupe([*guests, *found, *lexicon])
    out, size = [], 0
    for term in ordered:
        if size + len(term) + 2 > MAX_HOTWORDS_CHARS:
            break
        out.append(term)
        size += len(term) + 2
    return ", ".join(out)


def is_hotword_echo(text: str, hotwords: str) -> bool:
    """Segment qui ne fait que réciter la liste de mots attendus : sur un
    passage sans parole, Whisper peut « lire » ses hotwords au lieu de se
    taire. Au moins 3 éléments, presque tous tirés de la liste."""
    if not hotwords:
        return False
    items = [i.strip(" .").lower() for i in re.split(r"[,;]", text) if i.strip(" .")]
    known = {h.strip().lower() for h in hotwords.split(",")}
    return len(items) >= 3 and sum(i in known for i in items) >= 0.8 * len(items)

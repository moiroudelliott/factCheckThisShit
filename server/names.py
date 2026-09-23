"""Comparaison de noms de personnes entre les invités déclarés (popup), la
banque de voix et les votes du LLM — fonctions pures.

Tout était comparé à l'identique : « Bardella » ou « Jordan Bardella (RN) »
dans la popup ne correspondait jamais à « Jordan Bardella » en banque, et la
voix, pourtant connue, n'était ni reconnue ni enrôlée."""

import re
import unicodedata


def norm_name(name: str) -> str:
    """« Éric  Zemmour (Reconquête) » → « eric zemmour »."""
    s = re.sub(r"\(.*?\)", " ", str(name or ""))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z]+", " ", s).split())


def name_matches(a: str, b: str) -> bool:
    """Même personne ? Égalité après normalisation, ou nom de famille seul
    (« Bardella » ~ « Jordan Bardella », « Le Pen » ~ « Marine Le Pen »), ou
    initiale du prénom (« J. Bardella »)."""
    na, nb = norm_name(a).split(), norm_name(b).split()
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) == len(nb):
        return na[-1] == nb[-1] and all(x[0] == y[0] for x, y in zip(na[:-1], nb[:-1]))
    short, long_ = (na, nb) if len(na) < len(nb) else (nb, na)
    return long_[-len(short):] == short


def canonical_name(name: str, guests, bank_names) -> str:
    """Forme de référence d'un nom : la clé de la banque si elle désigne sans
    ambiguïté la même personne, sinon l'invité déclaré correspondant (forme
    la plus complète), sinon le nom tel quel. Garde les clés de la banque
    cohérentes d'un débat à l'autre."""
    in_bank = [b for b in bank_names if name_matches(name, b)]
    if len(in_bank) > 1:
        in_bank = [b for b in in_bank if any(name_matches(b, g) for g in guests)]
    if len(in_bank) == 1:
        return in_bank[0]
    in_guests = [g for g in guests if name_matches(name, g)]
    if len(in_guests) == 1:
        full = in_guests[0]
        return full if len(norm_name(full)) >= len(norm_name(name)) else name
    return name


def matches_any(name: str, names) -> bool:
    return any(name_matches(name, n) for n in names)

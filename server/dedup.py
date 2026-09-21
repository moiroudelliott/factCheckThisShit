"""Déduplication sémantique locale des talking points (index inversé par
mot-clé, comparaison par recouvrement) — reste volontairement globale sur
toute la session (cf. flush_to_mistral), seul le coût du scan est réduit."""

from server.text_utils import key_words


def dupe_index_add(index: dict, words: set, idx: int) -> None:
    for w in words:
        index.setdefault(w, []).append(idx)


def is_duplicate_indexed(new_text: str, points: list, index: dict, threshold: float = 0.45) -> bool:
    new_w = key_words(new_text)
    if len(new_w) < 3:
        return False
    candidates = set()
    for w in new_w:
        candidates.update(index.get(w, ()))
    for idx in candidates:
        ex_w = key_words(points[idx]['texte'])
        if len(ex_w) < 3:
            continue
        overlap = len(new_w & ex_w)
        if overlap / min(len(new_w), len(ex_w)) >= threshold:
            return True
    return False

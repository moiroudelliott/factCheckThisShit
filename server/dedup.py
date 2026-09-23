"""Déduplication sémantique locale des talking points (index inversé par
mot-clé, comparaison par claims_match) — reste volontairement globale sur
toute la session (cf. flush_to_mistral), seul le coût du scan est réduit.

claims_match (voir text_utils) refuse de fusionner deux affirmations dont un
nombre, la négation ou un mot de sens diffère : sans ça, la réplique de
l'adversaire (« a voté pour » face à « a voté contre ») était jetée comme
doublon."""

from server.text_utils import claim_signature, claims_match


def dupe_index_add(index: dict, words: set, idx: int) -> None:
    for w in words:
        index.setdefault(w, []).append(idx)


def is_duplicate_indexed(new_text: str, points: list, index: dict, threshold: float = 0.45) -> bool:
    new_sig = claim_signature(new_text)
    if len(new_sig.words) < 3:
        return False
    candidates = set()
    for w in new_sig.words:
        candidates.update(index.get(w, ()))
    return any(claims_match(new_sig, claim_signature(points[idx]['texte']), threshold)
               for idx in candidates)

"""Petits utilitaires texte partagés : mots-clés (dédup + cache), filtre
anti-hallucination Whisper, transcript annoté par locuteur, nettoyage de
description YouTube."""

import re

_STOPWORDS = {
    'avec', 'aussi', 'alors', 'autre', 'autres', 'bien', 'mais', 'même',
    'nous', 'plus', 'pour', 'puis', 'quand', 'sans', 'sont', 'très',
    'tout', 'tous', 'toute', 'toutes', 'vers', 'vous', 'dans', 'ainsi',
    'avoir', 'être', 'faire', 'dire', 'cette', 'cela', 'comme', 'donc',
    'dont', 'elle', 'elles', 'entre', 'leur', 'leurs', 'celui', 'celle',
    'ceux', 'celles', 'depuis', 'quel', 'quelle', 'quels', 'quelles',
}


def key_words(text: str) -> set:
    tokens = re.findall(r'\b(?:[a-zàâçéèêëîïôùûü]{4,}|\d{3,})\b', text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


def build_transcript(entries: list) -> str:
    """Transcript annoté par locuteur, tours de parole consécutifs fusionnés."""
    if not any(label for label, _ in entries):
        return " ".join(t for _, t in entries)
    lines, cur_label, cur_texts = [], None, []
    for label, t in entries:
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

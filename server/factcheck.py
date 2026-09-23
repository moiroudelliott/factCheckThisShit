"""Extraction des talking points et fact-checking via Mistral, sourcé par
recherche web (SearxNG auto-hébergé), académique (HAL/OpenAlex) et
officielle (data.gouv.fr) — voir ARCHITECTURE.md pour le raisonnement
souveraineté."""

import json
import re
import time

import eventlet
import requests

from server.app import DIARIZATION, socketio
from server.config import (
    MISTRAL_API_KEY, MISTRAL_MODEL, MISTRAL_MAX_RETRIES, MISTRAL_RETRY_BASE_S, SEARXNG_URL,
)
from server.text_utils import _STOPWORDS
from server import cache
from server.notify import describe_error, warn_client
from server.sources import finalize_result, is_excluded, source_tier, video_year
from server.state import session_contexts

# ── Prompts ────────────────────────────────────────────────────────────────

MISTRAL_PROMPT_TEMPLATE = """Tu es un analyste de débats politiques français. Extrais les talking points du passage suivant.
{context_block}Transcription (~25 secondes de débat, possiblement coupée en début/fin):

\"\"\"{text}\"\"\"
{history_block}

MISSION: un passage de débat contient presque toujours 1 à 3 talking points. Extrais-les.
Ne retourne [] QUE si le passage est réellement vide de contenu politique (politesses, gestion de parole, phrases incompréhensibles). Un tableau vide doit rester RARE.

Réponds UNIQUEMENT avec un tableau JSON valide, sans markdown:
[{{"type": "TYPE", "texte": "le point condensé en une phrase claire", "qui": "qui l'a dit, ou chaîne vide", "verifiable": 8}}]

Types:
- "affirmation" = fait PRÉCIS et VÉRIFIABLE: chiffre, date, événement, vote, citation, fait historique ou économique.
  Ex: "BYD est le leader chinois de l'automobile électrique"
  Ex: "Les socialistes français et allemands se sont fait la guerre en 1914"
  Ex: "L'Union européenne impose la fin du moteur thermique en 2035"
  Ne sont PAS des affirmations (→ "subjectif"): généralités vagues ("Il existe des fractures en France"),
  définitions ou thèses ("L'islam est à la fois une civilisation et une religion"),
  évidences sans contenu ("L'accord de Paris a été signé à une époque antérieure").
- "argument" = raisonnement cause-effet ou proposition concrète.
  Ex: "Les fermetures d'usines s'expliquent d'abord par le niveau des charges sociales"
- "subjectif" = opinion, jugement de valeur, promesse vague — non vérifiable.
  Ex: "Le modèle économique actuel abandonne les classes populaires"
- "remarque" = accusation ou commentaire politique visant l'adversaire.
  Ex: "L'adversaire est accusé d'admirer des dirigeants hostiles aux intérêts de la France"
  Ex: "L'adversaire est accusé de fantasmer une France qui n'a jamais existé"
- "question" = interpellation directe sur un sujet politique
- "accord" / "désaccord" = convergence ou réfutation explicite d'un propos adverse

"verifiable" (0-10, pour chaque point) = peut-on le vérifier avec des sources ? 10 = chiffre, date, vote ou
événement précis ; 5 = fait réel mais flou ; 0 = opinion ou généralité.

RÈGLES:
1. {attribution_rule}
2. Ignore la pure gestion de plateau ("laissez-le parler", interruptions) — MAIS les attaques et accusations politiques entre débatteurs sont des points valides (type "remarque").
3. Ne répète pas un point déjà dans l'historique, même reformulé. Les points NOUVEAUX doivent toujours être extraits.
4. PRIORITÉ ABSOLUE aux faits vérifiables: si le passage contient un chiffre, une date ou un fait précis, il DOIT devenir un point de type "affirmation".
5. La transcription est automatique et contient parfois des erreurs phonétiques sur les noms propres et les sigles (ex: "Mereaux" pour "maires ruraux", "Baïa" pour "abaya") : quand la forme correcte est évidente d'après le contexte, écris-la correctement dans le point ; sinon garde le mot tel quel."""


FACTCHECK_PROMPT_TEMPLATE = """Tu es un fact-checker expert sur les données françaises et européennes. Nous sommes le {today}.
{context_block}
Affirmation à vérifier: "{claim}"

{evidence_block}

Évalue la véracité en te basant PRIORITAIREMENT sur les résultats de recherche ci-dessus (leur fiabilité est annotée), complétés par tes connaissances.
Réponds UNIQUEMENT avec un objet JSON valide, sans markdown:
{{"verdict": "VERDICT", "confiance": 85, "explication": "une phrase courte et précise", "source": "nom de la source (ex: INSEE, Eurostat, Le Monde)", "url": "URL du résultat de recherche utilisé, ou chaîne vide"}}

Verdicts disponibles:
- "vrai": affirmation exacte et vérifiable
- "partiellement_vrai": vrai mais incomplet ou imprécis
- "trompeur": techniquement vrai mais donne une fausse impression
- "faux": factuellement incorrect
- "non_verifiable": ni les résultats de recherche ni tes connaissances ne permettent de trancher

RÈGLES DE RIGUEUR:
- Un verdict tranché ("vrai", "faux", "trompeur") exige AU MOINS deux sources indépendantes concordantes, OU une SOURCE OFFICIELLE (INSEE, Eurostat, Légifrance, parlement…). Sinon: "partiellement_vrai" ou "non_verifiable".
- Pour une affirmation CAUSALE ou sociologique ("X provoque Y", "X n'a pas d'effet sur Y"), les SOURCES ACADÉMIQUES (études évaluées par les pairs) pèsent plus lourd que la presse et que tes intuitions. Ne les utilise que si elles portent réellement sur le sujet de l'affirmation.
- "confiance" (0-100) = ta certitude dans le verdict: ~90+ = sources officielles concordantes; ~70 = bien sourcé; ~50 = plausible mais mal sourcé; en dessous de 40, utilise plutôt "non_verifiable".
- Quand les sources donnent un chiffre exact, cite-le dans "explication".
- Une fiche de JEU DE DONNÉES (data.gouv.fr) prouve seulement qu'une donnée existe : elle ne confirme pas un chiffre à elle seule.
- "url" doit être COPIÉE depuis un des résultats de recherche fournis — jamais inventée. Si aucun résultat n'appuie ton verdict, url vide ET confiance ≤ 50.
- "source" = le nom du site de l'URL choisie (ex: "Le Monde" pour lemonde.fr), jamais une autorité que ce site se contente de citer.
- L'affirmation vient d'une transcription automatique : si elle contient manifestement une erreur de transcription (nom déformé, mot incompréhensible), ne la juge pas "faux" pour autant — réponds "non_verifiable" en commençant l'explication par "Transcription douteuse :".
- Sois honnête : en cas de doute réel, réponds "non_verifiable" plutôt que de deviner."""


VIDEO_ANALYSIS_PROMPT = """Voici les métadonnées d'une vidéo YouTube de débat ou plateau politique français.
Titre: {title}
Chaîne: {channel}
Date de publication: {publish_date}
Description: {description}

Liste les intervenants: les personnes qui PARLENT dans la vidéo (débatteurs, invités, journalistes ou animateurs identifiables). Pas les personnes seulement mentionnées comme sujet.
Réponds UNIQUEMENT avec un objet JSON, sans markdown:
{{"intervenants": ["Prénom Nom", "Prénom Nom"]}}
Si aucun intervenant identifiable: {{"intervenants": []}}"""


ATTRIBUTION_RULE_DIAR = (
    'La transcription est annotée par locuteur ("Intervenant A"… ou un nom réel une fois '
    'le locuteur identifié). Recopie EXACTEMENT cette annotation dans le champ "qui" — '
    "ne devine JAMAIS un nom toi-même. Les personnalités CITÉES dans le propos "
    "(Poutine, Orban…) peuvent apparaître dans le texte du point."
)
ATTRIBUTION_RULE_NODIAR = (
    'Impossible de savoir qui parle: laisse le champ "qui" vide et formule le point sans '
    'nom d\'intervenant (écris "l\'adversaire" ou formule sans sujet). Les personnalités '
    "CITÉES dans le propos (Poutine, Orban…) peuvent apparaître."
)


def build_context_block(context: dict) -> str:
    parts = []
    if context.get("emission"):
        parts.append(f"Émission: {context['emission']}")
    if context.get("guests"):
        parts.append(f"Intervenants: {', '.join(context['guests'])}")
    if context.get("date"):
        # Ancre temporelle : "hier", "cette année", "le dernier budget"… se
        # comprennent par rapport à la date de la vidéo, pas celle du visionnage
        parts.append(f"Date de publication de la vidéo: {context['date']} — les propos datent de cette période")
    if context.get("description"):
        parts.append(
            "Description de la vidéo (contexte de fond UNIQUEMENT — "
            f"n'en extrais jamais de talking point): {context['description']}"
        )
    if not parts:
        return ""
    return "Contexte de l'émission:\n" + "\n".join(f"- {p}" for p in parts) + "\n\n"


def build_history_block(recent_points: list) -> str:
    if not recent_points:
        return ""
    lines = ["\nTalking points déjà identifiés — NE PAS RÉPÉTER, même sous une formulation légèrement différente:"]
    for p in recent_points[-25:]:
        lines.append(f"- [{p['type']}] {p['texte']}")
    return "\n".join(lines)


# ── Appel Mistral (retry + backoff sur 429) ───────────────────────────────

def _retry_delay(resp, attempt: int) -> float:
    """Respecte l'en-tête Retry-After de l'API si fourni, sinon backoff
    exponentiel (2s, 4s, 8s…)."""
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return MISTRAL_RETRY_BASE_S * (2 ** attempt)


def call_mistral_api(prompt: str, sid: str = None) -> str:
    """sid (optionnel) : si fourni, un événement mistral_rate_limited est
    émis à cette session à chaque nouvelle tentative sur 429, pour que
    l'extension affiche l'attente au lieu de laisser l'utilisateur sans
    retour pendant le backoff."""
    for attempt in range(MISTRAL_MAX_RETRIES + 1):
        resp = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
            json={"model": MISTRAL_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=20,
        )
        if resp.status_code == 429 and attempt < MISTRAL_MAX_RETRIES:
            wait = _retry_delay(resp, attempt)
            print(f"[Mistral] 429 (rate limit) — nouvelle tentative {attempt + 1}/{MISTRAL_MAX_RETRIES} dans {wait:.0f}s")
            if sid:
                socketio.emit("mistral_rate_limited", {
                    "attempt": attempt + 1, "max": MISTRAL_MAX_RETRIES, "wait": round(wait),
                }, to=sid)
            eventlet.sleep(wait)  # eventlet.sleep (coopératif), jamais time.sleep : ne bloque pas la boucle
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def call_mistral(text: str, context: dict = None, recent_points: list = None, sid: str = None) -> list:
    prompt = MISTRAL_PROMPT_TEMPLATE.format(
        context_block=build_context_block(context or {}),
        text=text,
        history_block=build_history_block(recent_points or []),
        attribution_rule=ATTRIBUTION_RULE_DIAR if DIARIZATION else ATTRIBUTION_RULE_NODIAR,
    )
    content = call_mistral_api(prompt, sid=sid)
    print(f"[Mistral talking points] {content[:200]}")
    try:
        start = content.find('[')
        if start != -1:
            result, _ = json.JSONDecoder().raw_decode(content, start)
            if isinstance(result, list):
                # type/texte doivent être des strings non vides, sinon le rendu côté extension casse
                points = [p for p in result
                          if isinstance(p, dict)
                          and isinstance(p.get("type"), str) and p["type"].strip()
                          and isinstance(p.get("texte"), str) and p["texte"].strip()]
                for p in points:
                    p["qui"] = str(p.get("qui") or "").strip()[:48]
                return points
    except (json.JSONDecodeError, ValueError):
        pass
    return []


# ── Recherche : web (SearxNG), académique (HAL/OpenAlex), officielle (data.gouv.fr) ─

def web_search(query: str, max_results: int = 6) -> list:
    """Recherche web via l'instance SearxNG auto-hébergée (searxng/docker-compose.yml) —
    Brave + Mojeek uniquement (ni Google ni Bing, cf. searxng/config/settings.yml).
    Retourne [] si l'instance est injoignable, jamais d'appel direct à un moteur tiers.
    Les domaines de EXCLUDED_SOURCE_DOMAINS (réseaux sociaux, désinformation
    notoire) sont écartés avant d'atteindre le prompt."""
    try:
        r = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "language": "fr"},
            timeout=8,
        )
        r.raise_for_status()
        out = []
        for res in r.json().get("results", []):
            href = res.get("url", "")
            if is_excluded(href):
                continue
            out.append({"title": res.get("title", ""), "body": res.get("content", ""), "href": href})
            if len(out) >= max_results:
                break
        return out
    except Exception as e:
        print(f"[Search error] {type(e).__name__}: {e}")
        return []


def _scholar_query(claim: str) -> str:
    """Les moteurs académiques (Solr) marchent aux mots-clés, pas aux phrases:
    on garde les mots significatifs du claim, dans l'ordre."""
    tokens = re.findall(r"[a-zàâçéèêëîïôùûü]{4,}|\d{2,}", claim.lower())
    words = [t for t in tokens if t not in _STOPWORDS]
    return " ".join(words[:6])


def scholar_search(claim: str, max_results: int = 4) -> list:
    """Études académiques pour ancrer les claims sociologiques/causaux.
    HAL (archive ouverte française, fort en sciences sociales FR) + OpenAlex
    (index scientifique mondial). APIs publiques, gratuites, sans clé.
    Les deux APIs sont interrogées en parallèle (greenlets eventlet)."""
    query = _scholar_query(claim)
    if len(query.split()) < 2:
        return []
    hal = eventlet.spawn(_hal_search, query)
    openalex = eventlet.spawn(_openalex_search, query)
    return (hal.wait() + openalex.wait())[:max_results]


def _hal_search(query: str) -> list:
    out = []
    try:
        r = requests.get(
            "https://api.archives-ouvertes.fr/search/",
            params={"q": query, "rows": 2, "fl": "title_s,abstract_s,uri_s,producedDateY_i"},
            timeout=6,
        )
        for doc in r.json().get("response", {}).get("docs", []):
            title = (doc.get("title_s") or [""])[0]
            abstract = (doc.get("abstract_s") or [""])[0]
            uri = doc.get("uri_s", "")
            year = doc.get("producedDateY_i", "")
            if title and uri:
                out.append({"title": f"{title} ({year}, HAL)", "body": abstract[:300], "href": uri})
    except Exception as e:
        print(f"[Scholar HAL] {type(e).__name__}: {e}")
    return out


def _openalex_search(query: str) -> list:
    out = []
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={"search": query, "per-page": 2},
            timeout=6,
        )
        for w in r.json().get("results", []):
            title = w.get("display_name") or ""
            year = w.get("publication_year", "")
            url = (w.get("primary_location") or {}).get("landing_page_url") or w.get("id", "")
            # OpenAlex stocke les résumés en index inversé — reconstruction
            abstract = ""
            inv = w.get("abstract_inverted_index")
            if inv:
                pos = {}
                for word, idxs in inv.items():
                    for i in idxs:
                        pos[i] = word
                abstract = " ".join(pos[i] for i in sorted(pos))[:300]
            if title and url:
                out.append({"title": f"{title} ({year}, OpenAlex)", "body": abstract, "href": url})
    except Exception as e:
        print(f"[Scholar OpenAlex] {type(e).__name__}: {e}")
    return out


def datagouv_search(claim: str, max_results: int = 3) -> list:
    """Jeux de données officiels français (catalogue data.gouv.fr, API publique
    sans clé) pertinents pour l'affirmation. Contrairement à web_search (qui
    passe par un agrégateur de moteurs tiers), c'est une source française
    interrogée directement : aucun intermédiaire, aucune ambiguïté de
    souveraineté."""
    query = _scholar_query(claim)
    if len(query.split()) < 2:
        return []
    try:
        r = requests.get(
            "https://www.data.gouv.fr/api/1/datasets/",
            params={"q": query, "page_size": max_results},
            timeout=6,
        )
        out = []
        for d in r.json().get("data", []):
            title = d.get("title") or ""
            page = d.get("page") or ""
            desc = (d.get("description") or "").replace("\n", " ")[:300]
            if title and page:
                out.append({"title": f"{title} (data.gouv.fr)", "body": desc, "href": page})
        return out
    except Exception as e:
        print(f"[data.gouv.fr] {type(e).__name__}: {e}")
        return []


def build_evidence_block(results: list, academic: list = None, official: list = None) -> str:
    if not results and not academic and not official:
        return "Aucun résultat de recherche disponible — base-toi sur tes connaissances uniquement."
    lines = ["Résultats de recherche (fiabilité annotée):"]
    i = 0
    for r in (official or []):
        i += 1
        lines.append(f"[{i}] [JEU DE DONNÉES OFFICIEL — fiche du catalogue data.gouv.fr, ne contient pas "
                     f"forcément le chiffre] {(r.get('title') or '').strip()} — "
                     f"{(r.get('body') or '').strip()[:300]}\n    URL: {(r.get('href') or '').strip()}")
    for r in (academic or []):
        i += 1
        lines.append(f"[{i}] [SOURCE ACADÉMIQUE] {(r.get('title') or '').strip()} — "
                     f"{(r.get('body') or '').strip()[:300]}\n    URL: {(r.get('href') or '').strip()}")
    for r in (results or []):
        i += 1
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()[:300]
        href = (r.get("href") or "").strip()
        lines.append(f"[{i}] [{source_tier(href)}] {title} — {body}\n    URL: {href}")
    return "\n".join(lines)


def call_mistral_factcheck(claim: str, context: dict = None, sid: str = None) -> dict:
    context = context or {}
    # Replay d'un débat passé : sans l'année, la recherche remonte les
    # chiffres d'aujourd'hui pour juger des propos d'alors
    year = video_year(context)
    web_query = f"{claim} {year}" if year < time.localtime().tm_year and str(year) not in claim else claim
    # Les trois recherches en parallèle (greenlets) : en série, leurs timeouts
    # s'additionnaient (jusqu'à ~26 s avant même l'appel Mistral)
    jobs = (eventlet.spawn(web_search, web_query), eventlet.spawn(scholar_search, claim),
            eventlet.spawn(datagouv_search, claim))
    results, academic, official = (j.wait() for j in jobs)
    print(f"[Search] {len(results)} web + {len(academic)} académique(s) + {len(official)} data.gouv.fr pour «{claim[:50]}»")
    prompt = FACTCHECK_PROMPT_TEMPLATE.format(
        today=time.strftime("%d/%m/%Y"),
        context_block=build_context_block(context),
        claim=claim,
        evidence_block=build_evidence_block(results, academic, official),
    )
    content = call_mistral_api(prompt, sid=sid)
    print(f"[FactCheck résultat] {content[:150]}")
    try:
        start = content.find('{')
        if start != -1:
            data, _ = json.JSONDecoder().raw_decode(content, start)
            if isinstance(data, dict) and "verdict" in data:
                return finalize_result(data, results, academic, official)
    except (json.JSONDecodeError, ValueError):
        pass
    return {"verdict": "non_verifiable", "confiance": None, "explication": "Réponse du modèle illisible.",
            "source": "", "url": "", "indisponible": True}


def fact_check_affirmation(sid: str, claim_id: str, claim_text: str):
    print(f"[FactCheck] «{claim_text[:60]}»")
    context = session_contexts.get(sid, {})
    year = video_year(context)
    # Claim déjà vérifié (cette session ou une précédente) → verdict instantané
    cached = cache.lookup(claim_text, year)
    if cached:
        print(f"[FactCheck] cache hit → {cached['verdict']} ({cached.get('confiance')}%)")
        socketio.emit("fact_check_result", {"id": claim_id, **cached}, to=sid)
        return
    try:
        result = call_mistral_factcheck(claim_text, context=context, sid=sid)
        cache.store(claim_text, result, year)
        socketio.emit("fact_check_result", {"id": claim_id, **result}, to=sid)
    except Exception as e:
        print(f"[FactCheck error] {type(e).__name__}: {e}")
        warn_client(sid, describe_error(e))
        # Toujours émettre un résultat, sinon la carte côté extension reste
        # bloquée en spinner et gèle toute la file d'affichage
        # « indisponible » : l'extension l'affiche comme une panne, pas comme
        # un verdict « non vérifiable » (qui est une conclusion de fond)
        socketio.emit("fact_check_result", {
            "id": claim_id,
            "verdict": "non_verifiable",
            "indisponible": True,
            "explication": "Vérification indisponible (erreur technique).",
            "source": "",
            "url": "",
        }, to=sid)

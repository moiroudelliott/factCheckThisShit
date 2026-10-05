"""Extraction des talking points et fact-checking via Mistral, sourcé par
recherche web (SearxNG auto-hébergé), académique (HAL/OpenAlex) et
officielle (data.gouv.fr) — voir ARCHITECTURE.md pour le raisonnement
souveraineté."""

import json
import re
import time

import eventlet
import eventlet.semaphore
import requests

from server.app import DIARIZATION, socketio
from server.articles import fetch_passage
from server.config import (
    PASSAGE_MAX_CHARS, ARTICLE_FETCH_MAX, BRAVE_API_KEY, BRAVE_API_MIN_GAP_S, FACTCHECK_RECHECK_FALSE, MISTRAL_API_KEY,
    MISTRAL_FACTCHECK_MODEL, MISTRAL_FACTCHECK_TIMEOUT_S, MISTRAL_MODEL, MISTRAL_MAX_RETRIES, MISTRAL_RETRY_BASE_S,
    MISTRAL_TIMEOUT_S, SEARXNG_URL,
)
from server.text_utils import _STOPWORDS
from server import cache, indicators, known_factchecks, votes
from server.notify import describe_error, warn_client
from server.sources import (
    academic_relevant, finalize_result, is_excluded, normalize_verdict, parse_brave_results, quoted_in,
    related_verdicts, source_tier, video_year,
)
from server.state import session_contexts, session_verdicts

# ── Prompts ────────────────────────────────────────────────────────────────

MISTRAL_PROMPT_TEMPLATE = """Tu es un analyste de débats politiques français. Extrais les talking points du passage suivant.
{context_block}Transcription (~25 secondes de débat, possiblement coupée en début/fin):

\"\"\"{text}\"\"\"
{history_block}

MISSION: un passage de débat contient presque toujours 1 à 3 talking points. Extrais-les.
Ne retourne [] QUE si le passage est réellement vide de contenu politique (politesses, gestion de parole, phrases incompréhensibles). Un tableau vide doit rester RARE.

Réponds UNIQUEMENT avec un tableau JSON valide, sans markdown:
[{{"type": "TYPE", "texte": "le point condensé en une phrase claire", "qui": "qui l'a dit, ou chaîne vide", "verifiable": 8, "enjeu": 7, "periode": "année ou période visée (affirmations), ou chaîne vide", "citation": "les mots exacts du passage", "recherche": "mots-clés de recherche (affirmations)"}}]

Types:
- "affirmation" = fait PRÉCIS et VÉRIFIABLE: chiffre, date, événement, vote, citation, fait historique ou économique.
  Ex: "BYD est le leader chinois de l'automobile électrique"
  Ex: "Les socialistes français et allemands se sont fait la guerre en 1914"
  Ex: "L'Union européenne impose la fin du moteur thermique en 2035"
  Ne sont PAS des affirmations (→ "subjectif"): généralités vagues ("Il existe des fractures en France"),
  définitions ou thèses ("L'islam est à la fois une civilisation et une religion"),
  évidences sans contenu ("L'accord de Paris a été signé à une époque antérieure"),
  appartenance, mérite ou intention ("France Inter appartient à tous les Français",
  "J'ai empêché le RN d'avoir la majorité"), rappel banal de l'orateur sur lui-même
  ("L'année où j'étais Premier ministre"), jugement ou ressenti général ("La réforme Blanquer a créé
  beaucoup de souffrance", "Parcoursup génère de l'angoisse"), simple démenti de l'adversaire
  ("C'est faux", "ce n'est pas vrai" → "désaccord" ; l'affirmation, c'est le chiffre qu'il avance ensuite),
  tendance sans chiffre ni repère ("le port de l'abaya a explosé", "la violence augmente" → "vague"),
  ce que l'orateur dit de ses propres propos ou positions ("j'ai déjà répondu sur ce sujet", "je m'oppose à LFI"
  → "remarque"), conviction ou principe ("la loi fait le nombre" → "subjectif"), question ou phrase inachevée
  ("Et on est plus proche de 500 ou…" → "question").
- "argument" = raisonnement cause-effet ou proposition concrète.
  Ex: "Les fermetures d'usines s'expliquent d'abord par le niveau des charges sociales"
- "subjectif" = opinion, jugement de valeur, promesse vague — non vérifiable.
  Ex: "Le modèle économique actuel abandonne les classes populaires"
- "remarque" = accusation ou commentaire politique visant l'adversaire, SANS fait vérifiable. Une accusation qui
  s'appuie sur un fait précis (un tweet, un vote, une phrase citée, un chiffre, un événement daté : « Louis Boyard a
  publié des tweets appelant au blocage », « Mélenchon a parlé de discipline de combat ») est une "affirmation".
  Ex: "L'adversaire est accusé d'admirer des dirigeants hostiles aux intérêts de la France"
  Ex: "L'adversaire est accusé de fantasmer une France qui n'a jamais existé"
- "question" = interpellation directe sur un sujet politique
- "accord" / "désaccord" = convergence ou réfutation explicite d'un propos adverse

"verifiable" (0-10, pour chaque point) = peut-on le vérifier avec des sources ? 10 = chiffre, date, vote ou
événement précis ; 5 = fait réel mais flou ; 0 = opinion ou généralité.
"enjeu" (0-10, pour chaque point) = importance du point pour le public. Sois exigeant : dans un débat, une
minorité de points seulement mérite une vérification, et la note doit les distinguer.
  9-10 = chiffre clé ou fait au cœur du désaccord : bilan, budget, statistique nationale, vote, accusation
         factuelle grave contre l'adversaire ou son camp ;
  7-8  = fait national précis qui appuie un argument important ;
  4-6  = fait local ou cas isolé, témoignage, exemple d'illustration, procédure générale (« une enquête est
         ouverte si un policier dérape »), calendrier ou rendez-vous (« une mobilisation est prévue mardi ») ;
  0-3  = agenda ou parcours de l'orateur (« j'étais dans un lycée lundi »), existence d'une institution
         (« le Conseil national lycéen existe »), évidence, ressenti général présenté comme un fait.
"periode" (affirmations) = l'année ou la période dont parle l'affirmation, déduite des mots prononcés et de la
date du débat : « l'année prochaine » dit en octobre 2026 → "2027" ; « depuis 2017 » → "2017-2026". En automne,
« le budget » sans autre précision désigne le projet de budget de l'année suivante. Chaîne vide si rien ne la
précise : ne mets JAMAIS l'année du débat par défaut (« la France compte 7 % de musulmans » → ""). Mets aussi cette
année dans "recherche".
"citation" (pour chaque point) = les mots EXACTS de la transcription où le point est dit (8 à 30 mots),
recopiés sans rien changer ni corriger — pas de reformulation, pas de nom de locuteur.
"recherche" (affirmations seulement) = 4 à 10 mots-clés pour trouver dans la presse ou les statistiques
de quoi vérifier ce point : sujet, chiffre, noms, période — pas une phrase.
Ex: "entrées immigrés France 2024 baisse Insee", "Attal déclaration politique générale accueillir moins mieux".

RÈGLES:
1. {attribution_rule}
2. Ignore la pure gestion de plateau ("laissez-le parler", interruptions) — MAIS les attaques et accusations politiques entre débatteurs sont des points valides (type "remarque").
3. Ne répète pas un point déjà dans l'historique, même reformulé ou repris par l'autre débatteur : « un dixième des cours », « une heure sur dix » et « 10 % des heures » sont le même fait. S'il ajoute un élément NOUVEAU (un autre chiffre, une précision), relève seulement cet élément. Une idée = un point : ne découpe pas une même affirmation en variantes (« les naissances seront majoritaires en 2045 », « les immigrés seront majoritaires », « majoritaires en 2070 » dits dans la même tirade = un seul point, avec tous ses éléments). Les points NOUVEAUX doivent toujours être extraits.
4. PRIORITÉ ABSOLUE aux faits vérifiables: si le passage contient un chiffre, une date ou un fait précis, il DOIT devenir un point de type "affirmation".
5. La transcription est automatique et contient parfois des erreurs phonétiques sur les noms propres et les sigles (ex: "Mereaux" pour "maires ruraux", "Baïa" pour "abaya") : quand la forme correcte est évidente d'après le contexte, écris-la correctement dans le point ; sinon garde le mot tel quel.
6. N'ajoute JAMAIS au "texte" du point un élément qui n'a pas été prononcé : ni date, ni chiffre, ni lieu, ni nom (l'année déduite d'une date relative va dans "periode" et "recherche", pas dans "texte").
7. Une affirmation doit se comprendre seule : si elle renvoie à un contexte absent (« le candidat en question », « cette loi »), complète-le d'après le passage ou l'historique ; sinon note "verifiable" ≤ 3. Ne devine JAMAIS ce que mesure un chiffre : si le passage ne dit pas clairement de quoi il s'agit (« on était à 27 » : élèves par classe ? taux ? ; « il y en a 1 600 en tout » : postes supprimés ? vacants ? ; « il va augmenter de 1,2 milliard » : quel budget ?), reprends les mots prononcés et note "verifiable" ≤ 3. N'ajoute pas un mot qui change le sens (« vacants », « constitutionnel ») pour rendre la phrase complète. Si l'orateur attribue son chiffre à une source (« selon l'INED », « d'après l'article »), garde-la dans le point : c'est elle qu'on vérifiera. Garde le périmètre prononcé (« au lycée », « dans le second degré », « dans le public ») : « 30 élèves par classe au lycée » n'est pas « 30 élèves par classe en France ».
8. Un nom prononcé pour interpeller (« Monsieur Attal, vous… ») désigne la personne À QUI l'on parle, jamais celle qui parle (voir règle 1).
9. Ignore tout ce que dit le présentateur ou le journaliste (questions, relances, résumés, rappels de l'actualité — il pose les questions, présente les invités, distribue la parole, même s'il reste « Intervenant X ») et les témoignages diffusés en reportage (élèves, passants) : on ne relève que les propos des débatteurs et invités politiques. En particulier, l'ouverture de l'émission (« Bonsoir… », « le mouvement lycéen prend de l'ampleur, les violences se sont répétées… », « le gouvernement accuse… ») est le présentateur qui pose le sujet, même si l'annotation y met le nom d'un invité : aucun point.
10. Dans une "remarque", garde le sens de l'accusation : c'est "qui" qui accuse, jamais l'inverse."""


FACTCHECK_PROMPT_TEMPLATE = """Tu es un fact-checker expert sur les données françaises et européennes. Nous sommes le {today}.
{context_block}
Affirmation à vérifier: "{claim}"
{speaker_block}{periode_block}{citation_block}
{evidence_block}
{session_block}

Évalue la véracité en te basant PRIORITAIREMENT sur les résultats de recherche ci-dessus (leur fiabilité est annotée), complétés par tes connaissances. Un « Extrait de l'article » vient de l'article lui-même : il prime sur le résumé du moteur de recherche.
Réponds UNIQUEMENT avec un objet JSON valide, sans markdown:
{{"verdict": "VERDICT", "confiance": 85, "explication": "une phrase courte et précise", "inexact": "l'élément de l'affirmation que les sources contredisent (chiffre, date, période, superlatif, attribution), ou chaîne vide", "contredit_par": "pour faux : le fait précis d'une source fournie qui contredit l'affirmation, sinon chaîne vide", "source": "nom de la source (ex: INSEE, Eurostat, Le Monde)", "url": "URL du résultat de recherche utilisé, ou chaîne vide"}}

Verdicts disponibles:
- "vrai": affirmation exacte et vérifiable
- "partiellement_vrai": vrai mais incomplet ou imprécis
- "trompeur": techniquement vrai mais donne une fausse impression
- "faux": factuellement incorrect
- "non_recoupe": les sources ne font que rapporter la déclaration de l'auteur de l'affirmation (ou de son ministère, son parti, son camp) — aucune source indépendante ne la confirme ni ne la contredit. Uniquement dans ce cas : sans article qui rapporte sa déclaration, c'est "non_verifiable"
- "non_verifiable": ni les résultats de recherche ni tes connaissances ne permettent de trancher

RÈGLES DE RIGUEUR:
- "non_verifiable" est RÉSERVÉ au cas où AUCUNE source fournie ne traite du sujet de l'affirmation. Dès qu'une source fiable donne un chiffre ou un fait sur le MÊME sujet, pour la MÊME période, tu DOIS trancher en comparant : "vrai" s'il concorde ; "partiellement_vrai" si l'écart est faible ou ne porte que sur un détail ; "trompeur" si c'est exact mais présenté de façon à fausser l'impression ; "faux" si la source dit autre chose (autre chiffre, autre date, fait différent). Cite le chiffre ou le fait de la source dans "explication".
- UNE source fiable qui traite précisément du sujet suffit pour trancher : SOURCE OFFICIELLE, FACT-CHECK PUBLIÉ, DONNÉE OFFICIELLE ou PRESSE ÉTABLIE. Plusieurs sources concordantes augmentent la confiance.
- "faux" exige un fait précis tiré d'une source FOURNIE, que tu recopies dans "contredit_par" avec son année (ex : « Insee : 375 000 entrées d'immigrés en 2022 »). « Rien ne prouve que… » ou « aucune source ne confirme » n'est PAS une contradiction : c'est "non_verifiable". Tes seules connaissances ne suffisent jamais pour "faux".
- SOURCE CITÉE PAR L'ORATEUR : si l'affirmation attribue un chiffre, une prévision ou un fait à une source (« selon les projections de l'INED », « d'après l'Insee », « l'article du Figaro dit que… »), c'est ce que dit CETTE source qu'on vérifie. Elle dit autre chose (autre date, autre chiffre, un scénario parmi d'autres présenté comme LA projection) → "trompeur" ; la source citée ne publie rien de tel et le chiffre vient d'ailleurs (un think tank, une tribune) → "trompeur" si le fond existe ailleurs, "faux" si personne ne le dit. "non_verifiable" seulement si aucune source fournie ne parle de cette prévision ou de ce chiffre. Pour une prévision (« majoritaires en 2045 »), l'année annoncée fait partie de l'affirmation : une source qui dit « après 2050 » la contredit.
- MÊME PÉRIODE, MÊME CHOSE : une source ne contredit l'affirmation que si elle porte sur la même année (ou le même budget, la même rentrée) et mesure la même chose. Un chiffre d'une autre année, un autre périmètre (premier degré / second degré, public / privé), un cumul comparé à un chiffre annuel, des euros courants comparés à des euros constants ne sont PAS une contradiction : cherche dans les autres sources celle de la bonne période ; à défaut, "non_verifiable" en expliquant l'écart. Si les chiffres de la source, additionnés sur la période dont parle l'orateur, atteignent son chiffre, ce n'est pas "faux". Exemple : « 10 % des enfants sont victimes de violences sexuelles » (au cours de l'enfance) n'est PAS contredit par « 160 000 enfants victimes chaque année ».
- Si la reformulation et le propos exact ne parlent pas de la même chose (reformulation « taux de réussite de 27 % » pour le propos « on était à 27 » ; « postes vacants » pour « il y en a 1 600 en tout »), juge le propos exact ; si l'on ne sait pas ce que mesure son chiffre ou de quoi il parle, "non_verifiable" en commençant par "Transcription douteuse :".
- Un cas précis (« dans ce lycée, il n'y avait pas de proviseur adjoint », « le ministre était à Créteil lundi ») ne se prouve ni ne se dément par un autre cas : une source sur un autre établissement, une autre ville ou une autre date ne compte pas.
- Une norme, une loi ou un objectif (ce qui DEVRAIT être : « le décret fixe 20 °C ») ne contredit pas un constat (ce qui EST : « les radiateurs sont bloqués »).
- DÉCLARATION ≠ PREUVE : un article qui rapporte que l'auteur de l'affirmation l'a dite (« le ministre a annoncé 78 blessés », « X a dénoncé l'infiltration du mouvement ») prouve qu'il l'a dite, pas qu'elle est vraie — son ministère, son parti ou son camp ne comptent pas non plus comme source indépendante. Si c'est tout ce que disent les sources, réponds "non_recoupe" et nomme la source réelle du chiffre dans "explication" (« chiffre du ministère, non recoupé »). Sont des confirmations indépendantes : une enquête ou une vérification de presse, une donnée d'un service statistique (Insee, DEPP, Dares…), un bilan d'une autre autorité (préfecture, parquet, région). Exception : si l'affirmation porte sur ce que quelqu'un a dit (« Darmanin a déclaré sur RTL… », « Alma Dufour affirme que… ») ou cite elle-même sa source (« selon Laurent Nuñez »), l'article qui rapporte cette déclaration suffit : c'est le fait qu'il l'ait dit qu'on vérifie ("vrai" s'il l'a dit), pas l'exactitude de ce qu'il a dit.
- Lis l'affirmation dans son sens le plus plausible dans le débat : « Attal a interdit l'abaya », dit à propos de l'école, veut dire à l'école — ne la juge pas fausse pour une portée qu'elle ne revendique pas.
- Vérifie CHAQUE élément : chiffre, date, période, superlatif (« record », « première fois depuis 20 ans », « jamais »), et à qui l'action est attribuée. Recopie dans "inexact" tout élément que tes sources contredisent. Si le fait PRINCIPAL est vrai et que seul un détail est faux (superlatif, arrondi, date approchée) : "partiellement_vrai" — ex : « les entrées ont baissé en 2024, une première depuis 15 ans » alors que la baisse est réelle mais qu'il y en a eu une en 2020 → "partiellement_vrai". "faux" seulement si le fait principal est contredit. Si "inexact" n'est pas vide, le verdict ne peut pas être "vrai".
- DERNIÈRE DONNÉE DISPONIBLE : un chiffre présenté comme actuel (« par an », « aujourd'hui ») se compare à la dernière donnée publiée par une source indépendante (Insee, Eurostat, DEPP…). Le seul fait que cette donnée date d'un an ou deux n'est pas une réserve : si elle concorde, "vrai" (« environ 350 000 entrées par an » face aux 347 000 de l'Insee pour 2023). Cette règle ne change rien aux autres : une inexactitude réelle (une note de service présentée comme une loi, un lieu, un chiffre faux) reste "partiellement_vrai", et un chiffre que seuls l'orateur ou son ministère avancent reste "non_recoupe".
- Ne pinaille pas : un mot ou une préposition de différence (« discipline du combat » / « de combat »), un synonyme (LBD / flashball), un arrondi (« 1 600 » pour 1 588) ne rendent pas une affirmation inexacte.
- Une hausse en euros courants inférieure à l'inflation est une baisse en euros constants : « des coupes dans le budget » face à un budget en hausse nominale mais inférieure à l'inflation est "trompeur" ou "partiellement_vrai", jamais "faux" ; « le budget est en hausse » dans ce cas est vrai en valeur, trompeur en volume.
- Une mesure décidée ou annoncée par un ministre dans son domaine lui est attribuable (« X a interdit… » est vrai si X, ministre compétent, l'a décidée), même si le gouvernement est dirigé par un autre.
- Une SOURCE ACADÉMIQUE ne prouve un fait d'actualité (qui a fait quoi, quand) que si son résumé le dit explicitement.
- Pour une affirmation CAUSALE ou sociologique ("X provoque Y", "X n'a pas d'effet sur Y"), les SOURCES ACADÉMIQUES (études évaluées par les pairs) pèsent plus lourd que la presse et que tes intuitions. Ne les utilise que si elles portent réellement sur le sujet de l'affirmation.
- "confiance" (0-100) = ta certitude dans le verdict: ~90+ = sources officielles concordantes; ~70 = une source fiable précise; ~50 = plausible mais mal sourcé.
- Quand les sources donnent un chiffre exact, cite-le dans "explication".
- Un FACT-CHECK DÉJÀ PUBLIÉ (rédaction de vérification) ou un article annoté FACT-CHECK PUBLIÉ fait autorité s'il porte sur la MÊME affirmation (même chiffre, même période) : reprends sa conclusion et son URL. S'il porte sur un sujet voisin, ignore-le.
- Une DONNÉE OFFICIELLE (Eurostat) donne la série exacte : compare-la au chiffre avancé en vérifiant l'année, le périmètre (France / UE) et la définition (dette au sens de Maastricht, chômage au sens du BIT, SMIC brut ou net…).
- Un VOTE OFFICIEL (Assemblée nationale) prouve un vote s'il porte bien sur le texte dont parle l'affirmation (vérifie le titre et la date du scrutin) ; sinon ignore-le.
- Une fiche de JEU DE DONNÉES (data.gouv.fr) prouve seulement qu'une donnée existe : elle ne confirme pas un chiffre à elle seule.
- Une ENCYCLOPÉDIE (Wikipédia) établit les faits simples et datés (qui a occupé quelle fonction, quand une mesure a été prise ou un texte adopté) ; pour un chiffre ou une statistique, préfère la source officielle ou la presse, et ne tranche pas sur Wikipédia seule.
- Une SOURCE PARTISANE (site d'un parti ou mouvement politique) prouve seulement ce que ce parti dit ou propose (programme, communiqué, candidat investi) — jamais un fait ou un chiffre, et elle ne compte pas comme source indépendante.
- Une source de FIABILITÉ FAIBLE (site militant, conspirationniste ou agrégateur) ne suffit jamais seule et ne compte pas comme source indépendante : ne la choisis comme "url" que faute de mieux, avec confiance ≤ 50.
- "url" doit être COPIÉE depuis un des résultats de recherche fournis — jamais inventée. Si aucun résultat n'appuie ton verdict, url vide ET confiance ≤ 50.
- "source" = le nom du site de l'URL choisie (ex: "Le Monde" pour lemonde.fr), jamais une autorité que ce site se contente de citer.
- L'affirmation vient d'une transcription automatique : si elle contient manifestement une erreur de transcription (nom déformé, mot incompréhensible, chiffre invraisemblable pour le sujet comme une « dépense publique de 54 milliards »), ne la juge pas "faux" pour autant — réponds "non_verifiable" en commençant l'explication par "Transcription douteuse :". Ne l'utilise pas pour une affirmation simplement incomplète ou sortie de son contexte.
- Ne devine pas, mais ne te dérobe pas : sans AUCUNE source sur le sujet, "non_verifiable" ; avec une source sur le sujet, tranche."""


RECHECK_FALSE_PROMPT_TEMPLATE = """Tu compares une affirmation prononcée dans un débat politique à des sources.
{context_block}
Affirmation : "{claim}"
{speaker_block}{periode_block}{citation_block}
{evidence_block}

Question : que disent les sources du FAIT PRINCIPAL de l'affirmation (l'acteur, le chiffre ou le fait, la période) ? Si l'affirmation attribue ce fait à une source nommée (« selon les projections de l'INED »), le fait principal est « cette source dit cela » : une source qui en donne une autre version (autre date, autre auteur, simple scénario) le contredit ou le nuance. Pour une prévision, l'année annoncée fait partie du fait principal.
- "contredit" : une phrase des sources rend le fait principal impossible — même acteur, même mesure, même période — de sorte que l'affirmation et cette phrase ne peuvent pas être vraies en même temps. Un niveau ne contredit pas une évolution (« 71 députés aujourd'hui » ne dit pas s'il y en avait plus avant) ; le maintien d'une mesure ne contredit pas la baisse de son montant ; un chiffre sur un autre périmètre ne contredit rien ;
- "confirme" : une phrase des sources dit la même chose (un arrondi ou un ordre de grandeur juste compte comme une confirmation) ;
- "nuance" : le fait principal est juste, mais un détail est faux ou exagéré (date, chiffre approché, superlatif) ;
- "ne_tranche_pas" : aucune phrase ne parle précisément de ce fait principal (un résultat global ne dit rien du vote d'un groupe ; une mesure voisine ne dit rien d'un chiffre ; l'absence d'une information n'est pas une contradiction).
Juge ce qui a réellement été dit (propos exact) si la reformulation s'en écarte. Tes propres connaissances ne comptent pas : seulement les phrases des sources.

Réponds UNIQUEMENT avec un objet JSON, sans markdown :
{{"relation": "contredit", "phrase_source": "la phrase des sources, recopiée mot pour mot", "incompatibilite": "pour « contredit » : pourquoi l'affirmation et cette phrase ne peuvent pas être vraies en même temps", "explication": "une phrase courte et précise"}}
"phrase_source" est obligatoire pour "contredit", "confirme" et "nuance" : elle sera vérifiée. Si tu ne peux pas expliquer l'incompatibilité en une phrase, ce n'est pas "contredit"."""

VIDEO_ANALYSIS_PROMPT = """Voici les métadonnées d'une vidéo YouTube de débat ou plateau politique français.
Titre: {title}
Chaîne: {channel}
Date de publication: {publish_date}
Description: {description}

Liste les intervenants: les personnes qui PARLENT dans la vidéo (débatteurs, invités, journalistes ou animateurs identifiables). Pas les personnes seulement mentionnées comme sujet.
Ajoute entre parenthèses la fonction ACTUELLE de chacun si le titre ou la description la donnent (« ministre de l'Éducation nationale », « député LFI », « présentateur ») ; sinon, le nom seul. N'invente pas de fonction.
Réponds UNIQUEMENT avec un objet JSON, sans markdown:
{{"intervenants": ["Prénom Nom (fonction)", "Prénom Nom"]}}
Si aucun intervenant identifiable: {{"intervenants": []}}"""


ATTRIBUTION_RULE_DIAR = (
    'La transcription est annotée par locuteur ("Intervenant A"… ou un nom réel une fois '
    'le locuteur identifié). Recopie EXACTEMENT cette annotation dans le champ "qui" — '
    "ne devine JAMAIS un nom toi-même. Les personnalités CITÉES dans le propos "
    "(Poutine, Orban…) peuvent apparaître dans le texte du point. "
    "DEUX EXCEPTIONS, où l'annotation est fausse (la reconnaissance des voix se trompe "
    "dans les échanges rapides) : (a) la réplique interpelle par son nom la personne "
    "annotée (« Marion Maréchal, c'est un sujet central » dans une réplique annotée "
    'Marion Maréchal) → "qui" vide ; (b) la réplique parle de la personne annotée à la '
    "3e personne (« Gabriel Attal fait référence à… » dans une réplique annotée Gabriel "
    "Attal) → c'est le présentateur qui résume : n'en fais AUCUN point."
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
        # Noms seuls : avec leur fonction, le modèle tenait pour « non recoupé »
        # tout ce que dit un ministre, même rapporté par la presse (banc
        # d'essai : 95 → 92). La fonction ne sert qu'au garde-fou du code.
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


def build_history_block(points: list) -> str:
    """Les 25 derniers points, plus toutes les affirmations plus anciennes du
    débat : avec les 25 derniers seulement, « une heure de cours sur dix »
    relevée à 9 min revenait à 32, 33 et 34 min, vérifiée chaque fois."""
    if not points:
        return ""
    lines = []
    older = [p for p in points[:-25] if p.get("type") in ("affirmation", "secondaire")][-60:]
    if older:
        lines.append("\nAffirmations relevées plus tôt dans le débat — déjà traitées, NE PAS les relever à nouveau, "
                     "même reformulées ou reprises par l'autre débatteur:")
        lines += [f"- {p['texte']}" for p in older]
    lines.append("\nTalking points déjà identifiés — NE PAS RÉPÉTER, même sous une formulation légèrement différente:")
    for p in points[-25:]:
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


def call_mistral_api(prompt: str, sid: str = None, model: str = None, timeout: float = None) -> str:
    """sid (optionnel) : si fourni, un événement mistral_rate_limited est
    émis à cette session à chaque nouvelle tentative sur 429, pour que
    l'extension affiche l'attente au lieu de laisser l'utilisateur sans
    retour pendant le backoff."""
    model = model or MISTRAL_MODEL
    for attempt in range(MISTRAL_MAX_RETRIES + 1):
        started = time.monotonic()
        resp = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=timeout or MISTRAL_TIMEOUT_S,
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
        body = resp.json()
        usage = body.get("usage") or {}
        print(f"[Mistral] {model} : {time.monotonic() - started:.1f} s, "
              f"{usage.get('prompt_tokens', '?')} + {usage.get('completion_tokens', '?')} tokens")
        return body["choices"][0]["message"]["content"]


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

SEARCH_DOWN_MESSAGE = ("Recherche web éteinte (SearxNG) : les verdicts seront rarement sourcés — "
                       "lance Docker puis « docker compose up -d » dans searxng/")


def search_available() -> bool:
    """La recherche web est-elle disponible (API Brave, ou SearxNG qui
    répond) ? Sans elle, presque aucun verdict n'a de source : cas vécu, une
    session entière à « non vérifié, non sourcé » sans que rien ne le signale
    ailleurs que dans le terminal au démarrage."""
    if BRAVE_API_KEY:
        return True
    try:
        return requests.get(f"{SEARXNG_URL}/healthz", timeout=1.5).ok
    except Exception:
        return False


BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
_brave_lock = eventlet.semaphore.Semaphore(1)
_brave_last = [0.0]
_brave_warned = set()


def brave_api_search(query: str, max_results: int = 4) -> list:
    """API officielle de Brave Search (clé BRAVE_API_KEY) : au plus une
    requête par seconde (offre gratuite), les appels simultanés attendent
    leur tour. [] en cas d'erreur — la recherche continue avec Wikipédia."""
    if not BRAVE_API_KEY:
        return []
    with _brave_lock:
        wait = _brave_last[0] + BRAVE_API_MIN_GAP_S - time.monotonic()
        if wait > 0:
            eventlet.sleep(wait)
        _brave_last[0] = time.monotonic()
    try:
        r = requests.get(
            BRAVE_API_URL,
            params={"q": query, "count": max_results, "country": "FR", "search_lang": "fr",
                    "safesearch": "moderate"},
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            timeout=8,
        )
        if r.status_code in (401, 403, 422, 429):
            kind = "limite de requêtes atteinte" if r.status_code == 429 else "clé refusée (BRAVE_API_KEY dans .env)"
            if kind not in _brave_warned or r.status_code == 429:
                print(f"[Brave API] {kind} — HTTP {r.status_code}")
                _brave_warned.add(kind)
            return []
        r.raise_for_status()
        return parse_brave_results(r.json(), max_results)
    except Exception as e:
        print(f"[Brave API] {type(e).__name__}: {e}")
        return []


def web_search(query: str, max_results: int = 6) -> list:
    """Recherche web, ni Google ni Bing : l'API officielle de Brave Search si
    une clé est configurée (presse, sites officiels), plus Wikipédia via
    l'instance SearxNG auto-hébergée (searxng/config/settings.yml). Sans clé,
    SearxNG interroge aussi Brave, comme un navigateur — ce qui se fait
    bloquer. Les domaines de EXCLUDED_SOURCE_DOMAINS (réseaux sociaux, médias
    sous sanctions de l'UE, satire… — voir config.py) sont écartés avant
    d'atteindre le prompt."""
    if BRAVE_API_KEY:
        found = brave_api_search(query, max(1, max_results - 2))
        seen = {r["href"] for r in found}
        wiki = searxng_search(query, 2, engines="wikipedia fr")
        return found + [w for w in wiki if w["href"] not in seen]
    return searxng_search(query, max_results)


def searxng_search(query: str, max_results: int = 6, engines: str = "") -> list:
    """Instance SearxNG locale ; [] si elle est injoignable."""
    try:
        params = {"q": query, "format": "json", "language": "fr"}
        if engines:
            params["engines"] = engines
        r = requests.get(
            f"{SEARXNG_URL}/search",
            params=params,
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
    found = hal.wait() + openalex.wait()
    return [r for r in found if academic_relevant(claim, r)][:max_results]


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
            # Articles et chapitres avec résumé seulement : sans ce filtre,
            # des fiches de catalogue de bibliothèque sortaient comme « études »
            params={"search": query, "per-page": 3,
                    "filter": "has_abstract:true,type:article|review|preprint|book-chapter"},
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


def build_evidence_block(results: list, academic: list = None, official: list = None, known: list = None,
                         indicators: list = None, votes: list = None) -> str:
    if not any((results, academic, official, known, indicators, votes)):
        return "Aucun résultat de recherche disponible — base-toi sur tes connaissances uniquement."
    lines = ["Résultats de recherche (fiabilité annotée):"]
    i = 0
    for k in (known or []):
        i += 1
        date = time.strftime("%d/%m/%Y", time.localtime(k["published"])) if k.get("published") else "date inconnue"
        lines.append(f"[{i}] [FACT-CHECK DÉJÀ PUBLIÉ — {k['outlet']}, {date}] {k['title']} — "
                     f"{k.get('summary', '')[:300]}\n    URL: {k['url']}")
    for tag, items in (("DONNÉE OFFICIELLE — Eurostat", indicators), ("VOTE OFFICIEL — Assemblée nationale", votes)):
        for r in (items or []):
            i += 1
            lines.append(f"[{i}] [{tag}] {r['title']} — {r['body']}\n    URL: {r['href']}")
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
        extrait = (r.get("extrait") or "").strip()
        lines.append(f"[{i}] [{source_tier(href)}] {title} — {body}"
                     + (f"\n    Extrait de l'article : {extrait}" if extrait else "")
                     + f"\n    URL: {href}")
    return "\n".join(lines)


def build_session_block(previous: list) -> str:
    """Verdicts déjà rendus dans ce débat sur un sujet proche. Sans eux, chaque
    vérification repartait de zéro : « +1,2 milliard » servait de référence
    à 28:51 et était jugé faux à 29:11, avec un budget d'une autre année."""
    if not previous:
        return ""
    lines = ["Verdicts déjà rendus dans ce débat sur un sujet proche — reste cohérent avec eux (mêmes chiffres de "
             "référence, même année), sauf si une source fournie montre qu'ils se trompaient :"]
    for v in previous:
        lines.append(f"- « {v['claim']} » → {v['verdict']} : {v['explication']} ({v['source']})")
    return "\n".join(lines) + "\n"


def gather_evidence(claim: str, context: dict = None, query: str = "", periode: str = "") -> dict:
    """Toutes les preuves d'une affirmation : recherche web (avec le passage
    utile des premiers articles), académique, data.gouv.fr, fact-checks
    publiés, séries Eurostat, scrutins. Séparé du jugement pour pouvoir
    figer les preuves et rejouer le verdict seul (bench_verdicts.py)."""
    context = context or {}
    # Replay d'un débat passé : sans l'année, la recherche remonte les
    # chiffres d'aujourd'hui pour juger des propos d'alors
    year = video_year(context)
    # Mots-clés fournis à l'extraction : la phrase entière (« Gabriel Attal a
    # déclaré à la tribune de l'Assemblée nationale que… ») ramenait des
    # résultats vagues, et le verdict tombait en « non vérifiable »
    base = query.strip() if isinstance(query, str) and 3 <= len(query.strip()) <= 150 else claim
    web_query = f"{base} {year}" if year < time.localtime().tm_year and str(year) not in base else base
    # Les trois recherches en parallèle (greenlets) : en série, leurs timeouts
    # s'additionnaient (jusqu'à ~26 s avant même l'appel Mistral)
    jobs = (eventlet.spawn(web_search, web_query), eventlet.spawn(scholar_search, claim),
            eventlet.spawn(datagouv_search, claim))
    results, academic, official = (j.wait() for j in jobs)
    # Les premiers articles, ouverts en parallèle : seules les phrases qui
    # parlent de l'affirmation sont gardées (server/articles.py)
    reads = [(r, eventlet.spawn(fetch_passage, r["href"], claim, base, periode))
             for r in results[:ARTICLE_FETCH_MAX]]
    for r, job in reads:
        passage = job.wait()
        if passage:
            r["extrait"] = passage
    # Données locales, instantanées : jamais d'appel réseau pendant un fact-check
    known = known_factchecks.search(claim)  # index des rédactions de fact-checking
    series = indicators.evidence(claim)     # séries Eurostat préchargées
    ballots = votes.search(claim)           # scrutins de l'Assemblée nationale
    print(f"[Search] {len(known)} fact-check(s) publié(s) + {len(series)} série(s) Eurostat + {len(ballots)} "
          f"scrutin(s) + {len(results)} web ({sum(bool(r.get('extrait')) for r in results)} article(s) lu(s)) + "
          f"{len(academic)} académique(s) + {len(official)} data.gouv.fr pour «{claim[:50]}»")
    return {"results": results, "academic": academic, "official": official, "known": known,
            "series": series, "ballots": ballots}


def _json_object(content: str):
    start = (content or "").find("{")
    if start == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(content, start)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _claim_blocks(qui: str, periode: str, citation: str, passage: str = "") -> dict:
    # Passage autour du propos : à quoi renvoie « selon les projections », de
    # quoi parle « il va augmenter de 1,2 milliard » (cas vécus : sans lui, la
    # source dont l'orateur venait de parler était invisible à la vérification)
    passage = " ".join(str(passage or "").split())[:PASSAGE_MAX_CHARS]
    return {
        "speaker_block": f"Auteur de l'affirmation : {qui}\n" if qui else "",
        "periode_block": f"Période visée : {periode}\n" if periode else "",
        # Propos d'origine : l'affirmation est une reformulation, qui peut
        # durcir ou déformer ce qui a réellement été dit
        "citation_block": ((f"Propos exact (transcription automatique) : « {citation} » — juge ce qui a été "
                            "réellement dit si la reformulation ci-dessus s'en écarte.\n") if citation else "")
                          + (f"Passage du débat autour de ce propos (contexte : de quoi parle l'orateur, quelle "
                             f"source il cite) : « {passage} »\n" if passage else ""),
    }


def judge(claim: str, evidence: dict, context: dict = None, sid: str = None, citation: str = "",
          qui: str = "", periode: str = "", previous: list = (), passage: str = "") -> dict:
    """Verdict de Mistral sur des preuves déjà réunies, normalisé par
    finalize_result, puis contre-vérifié s'il conclut « faux »."""
    context = context or {}
    # Auteur : sans lui, « le ministre a annoncé 78 blessés » servait de
    # preuve à l'affirmation… du ministre
    named = qui if qui and not re.match(r"^Intervenant ([A-Z]|\d+)$", qui) else ""
    # Fonction de l'auteur, pour le garde-fou du code seulement : sans elle,
    # « confirmé par le gouvernement » validait le chiffre… du ministre lui-même
    role = (context.get("roles") or {}).get(named, "")
    ev = evidence
    evidence_block = build_evidence_block(ev["results"], ev["academic"], ev["official"], ev["known"],
                                          ev["series"], ev["ballots"])
    prompt = FACTCHECK_PROMPT_TEMPLATE.format(
        today=time.strftime("%d/%m/%Y"),
        context_block=build_context_block(context),
        claim=claim,
        evidence_block=evidence_block,
        session_block=build_session_block(list(previous)),
        **_claim_blocks(named, periode, citation, passage),
    )
    content = call_mistral_api(prompt, sid=sid, model=MISTRAL_FACTCHECK_MODEL, timeout=MISTRAL_FACTCHECK_TIMEOUT_S)
    print(f"[FactCheck résultat] {content[:150]}")
    data = _json_object(content)
    if not data or "verdict" not in data:
        return {"verdict": "non_verifiable", "confiance": None, "explication": "Réponse du modèle illisible.",
                "source": "", "url": "", "indisponible": True}
    finalize = lambda d: finalize_result(d, ev["results"], ev["academic"], ev["official"], ev["known"],  # noqa: E731
                                         ev["series"] + ev["ballots"], claim=claim, qui=named, periode=periode,
                                         role=role)
    result = finalize(data)
    if result["verdict"] == "faux" and FACTCHECK_RECHECK_FALSE:
        result = recheck_false(claim, data, result, evidence_block, context, sid, named, periode, citation, finalize,
                               passage)
    return result


def recheck_false(claim: str, data: dict, result: dict, evidence_block: str, context: dict, sid: str,
                  qui: str, periode: str, citation: str, finalize, passage: str = "") -> dict:
    """Second examen d'un « faux », le verdict le plus accusateur, fait à
    l'aveugle : le modèle ne voit pas le verdict proposé (relu, un « faux »
    était presque toujours confirmé, même quand la source donnait raison à
    l'orateur — « LFI est passée de 75 à 71 députés » jugé faux à « LFI a
    perdu des députés »). Il dit seulement si les sources contredisent,
    confirment, nuancent ou ne tranchent pas, phrase à l'appui. « Faux » ne
    reste que si les deux examens concordent ET que la phrase figure mot pour
    mot dans les preuves. En cas d'erreur de l'appel, le premier verdict est
    gardé."""
    prompt = RECHECK_FALSE_PROMPT_TEMPLATE.format(
        context_block=build_context_block(context),
        claim=claim,
        evidence_block=evidence_block,
        **_claim_blocks(qui, periode, citation, passage),
    )
    try:
        review = _json_object(call_mistral_api(prompt, sid=sid, model=MISTRAL_FACTCHECK_MODEL,
                                               timeout=MISTRAL_FACTCHECK_TIMEOUT_S))
    except Exception as e:
        print(f"[Contre-vérification] {type(e).__name__}: {e} — premier verdict gardé")
        return result
    if not review:
        return result
    print(f"[Contre-vérification] {json.dumps(review, ensure_ascii=False)[:400]}")
    relation = str(review.get("relation") or "").strip().lower()
    quoted = quoted_in(str(review.get("phrase_source") or ""), evidence_block)
    if relation == "contredit" and quoted:
        print("[Contre-vérification] « faux » confirmé")
        return result
    verdict = {"confirme": "vrai", "nuance": "partiellement_vrai"}.get(relation) if quoted else None
    # L'explication du second examen dit ce qui manque (« aucune source ne
    # compare le nombre de députés avant et après ») : plus utile qu'un repli
    # générique — sauf s'il prétendait contredire sans phrase vérifiable
    explication = "" if relation == "contredit" else str(review.get("explication") or "").strip()
    revised = finalize({
        **data,
        "verdict": verdict or "non_verifiable",
        "explication": explication or "Les sources consultées ne contredisent pas nettement l'affirmation.",
        "contredit_par": "",
        "inexact": "" if verdict == "vrai" else data.get("inexact", ""),
    })
    print(f"[Contre-vérification] « faux » → {revised['verdict']}")
    return revised


def call_mistral_factcheck(claim: str, context: dict = None, sid: str = None, citation: str = "",
                           query: str = "", qui: str = "", periode: str = "", previous: list = (),
                           passage: str = "") -> dict:
    evidence = gather_evidence(claim, context, query, periode)
    return judge(claim, evidence, context, sid, citation, qui, periode, previous, passage)


def fact_check_affirmation(sid: str, claim_id: str, claim_text: str, citation: str = "", query: str = "",
                           qui: str = "", periode: str = "", passage: str = ""):
    print(f"[FactCheck] «{claim_text[:60]}»")
    context = session_contexts.get(sid, {})
    year = video_year(context)
    done = session_verdicts.setdefault(sid, [])
    # Claim déjà vérifié (cette session ou une précédente) → verdict instantané
    cached = cache.lookup(claim_text, year)
    if cached:
        print(f"[FactCheck] cache hit → {cached['verdict']} ({cached.get('confiance')}%)")
        done.append({"claim": claim_text, "qui": qui, **cached})
        socketio.emit("fact_check_result", {"id": claim_id, **cached}, to=sid)
        return
    try:
        started = time.monotonic()
        result = call_mistral_factcheck(claim_text, context=context, sid=sid, citation=citation, query=query,
                                        qui=qui, periode=periode, previous=related_verdicts(claim_text, done, qui=qui),
                                        passage=passage)
        print(f"[FactCheck] {result['verdict']} en {time.monotonic() - started:.1f} s")
        cache.store(claim_text, result, year)
        if not result.get("indisponible"):
            done.append({"claim": claim_text, "qui": qui, **result})
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

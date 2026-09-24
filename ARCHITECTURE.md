# SOURCÉ — Architecture technique

Ce document décrit le fonctionnement interne du pipeline SOURCÉ : capture audio, transcription, diarisation, extraction de talking points, fact-checking, et affichage. Il suit `backend.py` (point d'entrée), le package `server/` qui contient toute la logique, et `extension/content.js`, `extension/offscreen.js`, `extension/background.js`.

Les références pointent vers des **fonctions** (`fichier` → `fonction`) plutôt que des numéros de ligne, qui se périment à chaque modification.

## Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Capture audio → chunks](#2-capture-audio--chunks)
3. [Réception serveur](#3-réception-serveur--verrouillage-par-session-pas-de-blocage-global)
4. [Structure d'un chunk transcrit](#4-ce-que-produit-un-chunk--structure-exacte)
5. [Diarisation et identification](#5-diarisation--le-clustering-incrémental-et-ses-seuils)
6. [Buffer → Mistral (flush) et arrêt propre](#6-buffer--mistral--la-logique-de-flush)
7. [Extraction → déduplication → dispatch](#7-extraction-mistral--déduplication--dispatch)
8. [Fact-check](#8-fact-check--cache--recherche--verdict)
9. [Événements socket.io](#9-table-complète-des-événements-socketio)
10. [Affichage (content.js)](#10-machine-à-états-daffichage-contentjs)
11. [Constantes du pipeline](#11-toutes-les-constantes-numériques-du-pipeline)
12. [Tests](#12-tests)

---

## 1. Vue d'ensemble

Quatre composants, trois frontières réseau :

```
[Onglet vidéo]  --tabCapture-->  [offscreen.js]  --socket.io-->  [server/routes.py]  --HTTP-->  [Mistral / SearxNG / HAL / OpenAlex / data.gouv.fr
       ^                                                               |                          / flux RSS des fact-checkers / Eurostat / open data AN]
       |_______________forwardToContent (via background.js)___________|
       |
[content.js : state machine + DOM, calque dans le lecteur vidéo]
```

L'onglet vidéo est YouTube (support complet : calque dans `#movie_player`, pubs, repères sur la barre de progression) ou n'importe quel autre site avec un lecteur — france.tv, LCP, Public Sénat, Twitch, Dailymotion… (§10).

- **`extension/background.js`** — service worker MV3, **sans état mémoire** (Chrome le tue après ~30 s d'inactivité) : tout vit dans `chrome.storage.session` (`getState`). Il orchestre : injecter `content.js`, créer le document offscreen, relayer les messages, transmettre les signalements de verdict (`reportVerdict` → `POST /report_verdict`), et **arrêter la capture** si l'onglet est fermé, change de vidéo (navigation SPA, autre page du site) ou quitte le site (`tabs.onUpdated`). Identifiant de ce qu'on analyse (`videoIdFromUrl`) : l'id de la vidéo YouTube, sinon origine + chemin de la page.
- **`extension/offscreen.js`** — le seul endroit où l'audio existe. Un document offscreen a accès à `MediaRecorder`/`AudioContext`, ce qu'un service worker n'a pas. Une **session** par capture (`startSession`) : connexion, tabId, timers. À l'arrêt, l'audio est coupé tout de suite mais la session attend que le backend ait fini d'analyser (`stopSession`).
- **`backend.py`** — point d'entrée : monkey-patch eventlet puis lance `server/routes.py`. Toute la logique vit dans le package `server/` :
  - `server/app.py` — app Flask/SocketIO (origines autorisées : l'extension Chrome uniquement), repli IPv4 (`network.py`), chargement des modèles (Whisper avec repli GPU → CPU, ECAPA) **depuis le disque d'abord** — sans quoi faster-whisper et SpeechBrain interrogent Hugging Face à chaque démarrage pour chercher une nouvelle version (cas vécu : 169 s avec une IPv6 cassée, 9 s pour tout le démarrage depuis le disque) ; téléchargement seulement si un modèle manque —, diagnostics de démarrage.
  - `server/network.py` — test de l'IPv6 au démarrage et repli IPv4 (§8).
  - `server/config.py` — constantes et variables d'environnement.
  - `server/state.py` — dicts `session_*` (un `sid` socket.io = un débat en cours = un jeu complet de structures).
  - `server/voices.py` — diarisation (`SpeakerTracker`), banque d'empreintes, identification des locuteurs (vote LLM + match acoustique).
  - `server/names.py` — comparaison de noms de personnes (invités ↔ banque ↔ votes).
  - `server/voice_store.py` — lecture/écriture de la banque (`voices/index.json` + `.npy`), partagé avec les outils CLI.
  - `server/factcheck.py` — appels Mistral, recherches web/académique/officielle en parallèle, prompts.
  - `server/sources.py` — règles pures sur les sources et les verdicts (domaines exclus/officiels/presse/rubriques de fact-checking, verdict normalisé, nom de source, plafond de confiance).
  - `server/vocabulary.py` — mots attendus par Whisper (`hotwords`) : invités, noms propres du titre/de la description, noms appris pendant le débat, lexique `vocabulaire.txt`.
  - `server/points.py` — règles pures sur les points extraits : note de vérifiabilité (« trop vague »), validation de la citation exacte et instant où elle a été dite.
  - `server/known_factchecks.py` — index local (SQLite) des articles des rédactions de fact-checking, alimenté par leurs flux RSS.
  - `server/indicators.py` — séries officielles Eurostat (chômage, dette, déficit, inflation…) déclenchées par les mots de l'affirmation.
  - `server/votes.py` — scrutins publics de l'Assemblée nationale (open data, législatures 16 et 17) : qui a voté quoi.
  - `server/cache.py` / `server/dedup.py` / `server/text_utils.py` — cache SQLite des fact-checks, déduplication, comparaison d'affirmations et utilitaires texte.
  - `server/notify.py` — avertissements `server_warning` vers l'extension.
  - `server/routes.py` — routes Flask + handlers Socket.IO, la couche d'orchestration qui relie tout ça.
- **`extension/content.js`** — state machine d'affichage, aucune logique métier (tout arrive déjà décidé du backend).

---

## 2. Capture audio → chunks

`offscreen.js` lance **deux flux de `MediaRecorder` en parallèle** sur le même `mediaStream` (`startRecorder`, `startProbe`, via `makeRecorder`) :

| Flux | Durée | Chevauchement | Event émis | Usage |
|---|---|---|---|---|
| Transcription | `CHUNK_MS=10000` | `OVERLAP_MS=1500` | `audio_chunk` | Whisper + diarisation complète |
| Sonde locuteur | `PROBE_MS=2500` | aucun | `speaker_probe` | badge "qui parle" temps réel |

Le chevauchement de 1,5 s n'est pas cosmétique : chaque enregistreur **planifie son successeur au démarrage**, pas dans `onstop` — `setTimeout(startRecorder, CHUNK_MS - OVERLAP_MS)`. Ça garantit qu'un mot à cheval sur la frontière de deux chunks est capturé en entier par au moins l'un des deux ; la redondance est retirée côté serveur (`strip_overlap`, §4).

Chaque chunk est un blob WebM envoyé en binaire brut (`ArrayBuffer`) — pas de JSON, pas de base64.

**Publicités** (YouTube) : le content script détecte `#movie_player.ad-showing` et le signale (`adState` → `setAdState`). Tout enregistreur qui a entendu une pub est marqué `tainted` et n'envoie rien : l'audio d'une pub n'est jamais transcrit ni vérifié.

---

## 3. Réception serveur : verrouillage par session, pas de blocage global

`routes.py` → `handle_audio_chunk` :

1. Acquiert `session_chunk_locks[sid]` (un `eventlet.semaphore.Semaphore(1)` par session) — **sérialise les chunks d'UNE session** (le `SpeakerTracker` n'est pas thread-safe) **sans bloquer les autres sessions**.
2. Écrit le blob sur disque (`tempfile`), calcule `chunk_offset = chunk_abs_time - session_start` (position dans le débat) et `chunk_abs_time = time.time()` (horodatage unix absolu).
3. Délègue tout le calcul lourd à `_transcribe_and_diarize` via `eventlet.tpool.execute(...)` — **thread natif**, pas le greenlet eventlet.

C'est le point le plus important de toute l'architecture : `eventlet.monkey_patch()` (`backend.py`) ne coopérativise que les I/O réseau, jamais le calcul CPU/GPU. Sans `tpool`, Whisper (GPU) et ECAPA (CPU) gèleraient tout le serveur, y compris la réception des chunks des autres sessions.

Une erreur de transcription part en `server_warning` (message temporaire sur la puce) au lieu d'être seulement journalisée.

---

## 4. Ce que produit un chunk : structure exacte

`_transcribe_and_diarize` retourne une liste de dicts, un par segment Whisper :

```python
{"text": str, "speaker": "Intervenant A" | "", "start": float, "end": float, "abs_time": float,
 "said_at": float}  # instant unix approximatif où le segment a été prononcé (abs_time − CHUNK_S + seg.start)
```

Pipeline interne par segment :

1. **Whisper** (`large-v3-turbo`, repli `medium` sur GPU puis `small` sur CPU si pas de GPU CUDA — avec un message clair au démarrage) — `vad_filter=True`, `no_speech_threshold=0.45`, `compression_ratio_threshold=2.4` (filtre les répétitions hallucinées), et **`hotwords`** : les mots que Whisper doit s'attendre à entendre (voir ci-dessous).
2. **Filtre hallucination** (`text_utils.is_hallucination`) — liste noire (`amara.org`, `sous-titres réalisés`…) + heuristique ponctuation (`meaningful/len < 0.2` → rejeté). Un segment qui ne fait que réciter la liste de hotwords (`vocabulary.is_hotword_echo` : ≥ 3 éléments, ≥ 80 % tirés de la liste) est rejeté aussi — sur un passage sans parole, Whisper peut « lire » ses mots attendus.
3. **Chevauchement** (`text_utils.strip_overlap`) — un segment qui commence dans la zone de chevauchement (`seg.start < CHUNK_OVERLAP_S + 0.5`) perd les mots qui répètent la fin du segment précédent (au moins 2 mots identiques). Sans ça, Mistral recevait des bouts de phrase en double.
4. **Filtre doublon local** — comparaison au texte exact des 5 derniers segments (`session_history[sid]`).
5. **Diarisation** (`voices.speaker_label`) — découpe l'échantillon audio du segment (`seg.start:seg.end` en samples 16 kHz), encode via ECAPA-TDNN → embedding 192-d → `SpeakerTracker.assign()`.

Chaque segment part immédiatement en `emit("transcript_segment", r)` — **avant** tout traitement Mistral. Côté extension, seul `speaker` est utilisé (l'offscreen ne relaie pas le texte), et seulement si aucune sonde n'est arrivée récemment (voir §10).

**Mots attendus (`hotwords`)** — `vocabulary.build_hotwords`, recalculé à `set_context` et après chaque extraction (`_refresh_hotwords`). Par priorité, jusqu'à `MAX_HOTWORDS_CHARS=450` caractères (≈ 180 tokens, sous la limite de 223 du prompt Whisper) :

1. les **intervenants déclarés** ;
2. les **noms propres du titre et de la description** de la vidéo (`proper_nouns` : suites de mots à majuscule hors début de phrase, sigles, « 49.3 ») ;
3. les **noms propres appris pendant le débat** dans les points extraits (`learn`, les `MAX_LEARNED=20` plus récents) — « Lecornu », « Fessenheim » ;
4. le **lexique** `vocabulaire.txt` (sigles, institutions, partis, termes souvent mal transcrits), modifiable à la main.

Un mot seul qui n'est que la tête d'un terme du lexique (« Cour » pour « Cour des comptes ») est écarté : Whisper le forçait partout.

---

## 5. Diarisation : le clustering incrémental et ses seuils

`SpeakerTracker` (`voices.py`) ne fait aucun apprentissage préalable — c'est du clustering en ligne pur, par similarité cosinus à des centroïdes.

| Paramètre | Valeur | Rôle |
|---|---|---|
| `DIARIZATION_THRESHOLD` | 0.34 | cosinus min pour rattacher un segment à un locuteur existant |
| `MIN_NEW_SPEAKER_SEC` | 2.0 s | un segment plus court **ne peut jamais** créer un nouveau locuteur (anti "locuteur fantôme" sur une interjection) |
| `MAX_SPEAKERS` | 12 | au-delà, rattachement forcé au plus proche |
| `PROBE_MATCH_T` | 0.28 | seuil (plus tolérant) pour les sondes temps réel, et plancher de plausibilité pour rattacher un segment court à un cluster existant |

Mapping mental : **chaque locuteur = un vecteur `sums[i]` (somme des embeddings) + un compteur `counts[i]`** ; le centroïde est recalculé à la volée (`sums[i]/counts[i]`), jamais stocké.

**Segment court.** Un segment sous `MIN_NEW_SPEAKER_SEC` n'est rattaché au cluster le plus proche que si `best_sim >= PROBE_MATCH_T` ; sinon il hérite du dernier locuteur actif (`self.last`). Cas réel qui a motivé la règle : le premier « oui » de Jean-François Copé happé par le cluster de Sébastien Chenu.

**La sonde ne touche jamais `tracker.last`.** Elle entend le locuteur de *maintenant*, alors que les segments Whisper traités ensuite datent de 10-15 s plus tôt : lui laisser écrire `last` faisait hériter les segments courts du mauvais nom (et depuis un autre thread). La sonde est en lecture seule (`SpeakerTracker.match`).

Les labels (`Intervenant A`, `B`…) sont **anonymes par construction**. Deux mécanismes les associent à un vrai nom :

**a) Vote LLM** (`identify_speakers`) — le backend envoie les 6 derniers extraits annotés à Mistral avec la liste des invités. Un nom n'est **confirmé qu'à 2 votes concordants et strictement majoritaires**. Les noms votés sont ramenés à leur forme de référence (`names.canonical_name` : clé de la banque, sinon invité déclaré) — « Bardella » et « Jordan Bardella » sont un seul candidat. L'identification tourne toutes les 2 analyses tant qu'un label est anonyme, puis **toutes les 4 tant qu'un nom non verrouillé peut encore être corrigé** (elle s'arrêtait auparavant dès que chaque label avait un nom, même faux).

**b) Empreinte acoustique** (`match_clusters_to_bank`) — compare le centroïde de session à la banque (`voices/`). Seuls les **invités déclarés** peuvent être retenus (comparaison de noms tolérante, `names.name_matches`), mais la comparaison se fait contre **toute la banque** : l'invité doit être la meilleure correspondance de toute la banque, avec `VOICE_MATCH_THRESHOLD=0.45` et une marge `VOICE_MATCH_MARGIN=0.08` sur la 2e (seuil relevé de `VOICE_MATCH_SOLO_BONUS` si la banque ne contient qu'une voix). Restreindre aussi la comparaison aux invités supprimait la marge dès qu'un seul était en banque. Une fois matché, le label est **verrouillé** (`session_voice_locked`) — la voix prime sur l'inférence textuelle.

**c) Auto-enrôlement** (`auto_enroll_voices`) — une empreinte en banque est définitive (reconnaissance acoustique verrouillée aux débats suivants), donc exigences renforcées : nom confirmé, invité déclaré, ≥ `VOICE_ENROLL_MIN_SEGMENTS` segments, **≥ `VOICE_ENROLL_MIN_VOTES` votes concordants et aucun vote contraire**. Appelée après chaque chunk, à chaque confirmation et à la déconnexion (idempotente).

Chaque changement de mapping émet `speaker_map` → l'extension **renomme rétroactivement** les cartes via le champ `quiLabel` de chaque point. Comme Mistral reçoit le transcript avec les noms déjà substitués, son champ `qui` est souvent un nom : `flush_to_mistral` le ramène à son label d'origine grâce à l'instantané de la correspondance utilisée (`smap_used`), sans quoi une attribution erronée n'était jamais corrigeable.

`speaker_map` transmet aussi `enrolled` (noms déjà en banque) et `enrollable` (noms que le backend pourra enrôler) : l'extension n'affiche l'indicateur « empreinte vocale… » que pour ces derniers.

---

## 6. Buffer → Mistral : la logique de flush

`session_buffers[sid]` accumule les `(speaker, text, said_at)` de chaque chunk. Le buffer part à Mistral (`_flush_buffer` → `flush_to_mistral`) quand :

```python
dense  = word_count >= MIN_WORDS(30) and (elapsed_since_flush >= FLUSH_INTERVAL(22s) or word_count >= MAX_BUFFER_WORDS(55))
paused = not new_text_in_this_chunk and word_count >= MIN_WORDS_ON_PAUSE(12) and elapsed_since_flush >= FLUSH_INTERVAL
```

`paused` couvre les pauses, fins de tirade et pubs : le buffer n'était auparavant vidé qu'à l'arrivée de NOUVEAU texte.

**Arrêt propre** : à l'arrêt, l'offscreen attend l'envoi du dernier chunk puis émet `stop_transcription`. `on_stop` attend la fin du chunk en cours, envoie le buffer restant s'il fait au moins `MIN_WORDS_ON_STOP` mots, puis `_finish_session` attend que plus aucune analyse ni fact-check ne soit en vol (`session_pending`, tâches lancées via `_spawn_tracked`, attente max `FINISH_TIMEOUT_S`) avant d'émettre `session_done`. Les ~20 dernières secondes d'un débat ne sont plus perdues.

Le texte accumulé passe par `build_transcript()` qui fusionne les tours de parole consécutifs du même locuteur en un bloc `"Intervenant A: ... \nIntervenant B: ..."` — c'est **ce texte annoté** qui devient le prompt Mistral.

`ts = buf["start_abs"]` — l'horodatage unix du **premier** chunk du buffer. Côté extension, il est converti en position vidéo **à la réception du point** grâce à l'échantillonnage de la lecture (voir §10).

---

## 7. Extraction Mistral → déduplication → dispatch

`call_mistral()` renvoie une liste de `{"type", "texte", "qui", "verifiable", "citation"}` :

- **`verifiable`** (0-10) — à quel point l'affirmation est vérifiable par des faits (chiffre, date, vote, fait daté). Une affirmation notée sous `CHECKWORTHY_MIN=6` devient `type: "vague"` (`points.apply_checkworthiness`) : affichée « TROP VAGUE », jamais envoyée au fact-check. Le prompt donne des contre-exemples (« il existe des fractures en France », « nous avons un projet »…). Note absente → l'affirmation est vérifiée, comme avant.
- **`citation`** — les mots exacts prononcés, recopiés de la transcription (le champ `texte` est une reformulation). `points.validate_citation` ne la garde que si elle y figure vraiment, mot pour mot à la normalisation près (casse, accents, ponctuation) ; sinon elle est vidée. Gardée, elle **date le propos** : `points.citation_time` retrouve le segment qui la contient et en prend le `said_at` (plus fin que le début du buffer). Elle est affichée « Mot pour mot » au récap et à l'export (pas sur la carte) et passée au fact-check (§8).

**Attribution** — `qui` recopie l'annotation de locuteur de la transcription, qui se trompe dans les échanges rapides. Garde-fou (`points.speaker_named_in_citation`) : si la citation contient le nom de famille de la personne à qui on l'attribue, elle ne peut presque jamais être d'elle — soit on l'interpelle (« La réponse est non, Marion Maréchal, c'est un sujet central »), soit on parle d'elle (« Gabriel Attal fait référence à… », le présentateur) : le nom est retiré plutôt qu'affiché faux. Le prompt d'extraction décrit aussi ces deux cas, interdit d'ajouter une date, un chiffre ou un nom non prononcé, et classe en opinion l'appartenance, le mérite ou l'intention (« France Inter appartient à tous les Français »).

Deux couches de dédup, **jamais une seule**, qui partagent la même règle de comparaison (`text_utils.claim_signature` / `claims_match`, portée à l'identique dans `content.js`) :

> Deux affirmations ne sont « la même » que si leurs **nombres** (normalisés : « 3 000 » = « 3000 »), leur **négation** (« ne », « n' », « jamais », « aucun »…) et leurs **mots de sens** (hausse, baisse, double, contre, plus, moins…) sont identiques — ET si le recouvrement de leurs mots-clés dépasse le seuil.

Sans les trois premières conditions, « a voté **contre** » et « a voté **pour** », « **50** milliards » et « **80** milliards », « le chômage a **baissé** » et « a **augmenté** » étaient fusionnés : la réplique de l'adversaire disparaissait.

1. **Dédup serveur** (`dedup.is_duplicate_indexed`) — index inversé mot-clé → indices (`session_dupe_index[sid]`), seuil de recouvrement 0.45.
2. **Dédup d'affichage extension** (`isDuplicateOfAny` + `isNearDupeOfShown`) — même règle, seuil 0.6 : `isDuplicateOfAny` compare à **tout** `S.points` (survit à un redémarrage backend), `isNearDupeOfShown` aux 6 dernières cartes affichées.

Chaque point unique reçoit un `id = uuid4().hex[:8]` — **c'est cet id qui relie `talking_points` et `fact_check_result`**. Seuls les points `type == "affirmation"` déclenchent `fact_check_affirmation` en tâche de fond (les `vague` et `subjectif` jamais).

---

## 8. Fact-check : cache → recherche → verdict

`fact_check_affirmation` :

```
cache.lookup(claim, année de la vidéo)  →  hit ?  → emit verdict instantané
        │ miss
        ▼
EN PARALLÈLE (greenlets) :
  web_search (SearxNG, 6 résultats, + année si replay, domaines exclus filtrés)
  scholar_search (HAL + OpenAlex, eux-mêmes en parallèle)
  datagouv_search (catalogue data.gouv.fr)
ET EN LOCAL (en mémoire, instantané — jamais d'appel réseau) :
  known_factchecks.search (articles déjà publiés par les rédactions de fact-checking)
  indicators.evidence (séries Eurostat préchargées, si l'affirmation parle chômage, dette, inflation…)
  votes.search (scrutins de l'Assemblée nationale, si l'affirmation parle d'un vote)
        │
        ▼
bloc de preuves, dans cet ordre (factcheck.build_evidence_block) :
  FACT-CHECK DÉJÀ PUBLIÉ / DONNÉE OFFICIELLE — Eurostat / VOTE OFFICIEL — Assemblée nationale
  / JEU DE DONNÉES OFFICIEL (data.gouv.fr, « fiche de catalogue ») / SOURCE ACADÉMIQUE
  / résultats web annotés par domaine (sources.source_tier, sur le NOM D'HÔTE) :
    FACT-CHECK PUBLIÉ (rubrique de fact-checking) / SOURCE OFFICIELLE / PRESSE ÉTABLIE
    / SOURCE PARTISANE / FIABILITÉ FAIBLE / FIABILITÉ INCONNUE
        │
        ▼
prompt Mistral (+ propos exact si citation validée, §7) → JSON {verdict, confiance, explication, source, url}
        │
        ▼
sources.finalize_result :
  - verdict normalisé (« Vrai », « partiellement vrai »… → clé connue ; inconnu → non_verifiable)
  - url EXACTEMENT une des href retournées (sinon vidée — anti-hallucination + anti-XSS javascript:)
  - nom de source cohérent avec l'URL (sinon le domaine) — « Ministère de l'Économie » ne s'affiche
    plus au-dessus d'un lien vers un blog ; pour un fact-check publié, le nom de la rédaction ;
    pour une preuve structurée, « Eurostat » / « Assemblée nationale »
  - pas d'URL de preuve → confiance plafonnée à 50 %, source « non sourcé »
  - preuve de FIABILITÉ FAIBLE → confiance plafonnée à 50 % (jamais mise en cache)
        │
        ▼
cache.store (seulement si sourcé, confiance ≥ 60 et verdict ≠ non_verifiable)
```

**Recherches en parallèle** : en série, leurs timeouts s'additionnaient (jusqu'à ~26 s avant même l'appel Mistral) et dépassaient le délai d'attente de l'extension.

**Repli IPv4** (`network.py`) : une box peut annoncer l'IPv6 (adresse et passerelle attribuées) sans que rien ne sorte. Navigateurs et curl essaient l'IPv4 en parallèle ; Python attend l'échec de chaque adresse IPv6 : 8 à 40 s perdues à chaque connexion vers Mistral, OpenAlex, Eurostat… `network.configure` teste l'IPv6 une fois au démarrage (1,5 s au plus, vers api.mistral.ai) et, s'il ne passe pas, force l'IPv4 pour toutes les requêtes (`urllib3.util.connection.allowed_gai_family`). `FORCE_IPV4` : `auto` (défaut), `1`, `0`. Cas vécu : Mistral 16-40 s → 0,5 s, OpenAlex 12 s → 0,7 s.

**Fact-checks déjà publiés** (`known_factchecks.py`) — un article des Décodeurs, de CheckNews, de « Vrai ou fake » (franceinfo), de Fake off (20 Minutes) ou des Surligneurs qui porte sur la même affirmation passe **en tête** des preuves : c'est un travail de vérification humain, sourcé. Les flux RSS (`config.FACTCHECK_FEEDS`) sont relus toutes les heures (`FACTCHECK_FEEDS_REFRESH_S`) par une tâche de fond et indexés dans `factchecks_index.db` (SQLite, gardé entre redémarrages). Correspondance : au moins 3 mots-clés communs couvrant 40 % de ceux de l'affirmation, nombres communs en bonus ; 2 articles au plus. Côté web, un résultat dans une rubrique de fact-checking (`config.FACTCHECK_SECTIONS` : hôte + début de chemin) est annoté `FACT-CHECK PUBLIÉ`. Le prompt demande de reprendre sa conclusion s'il porte sur la même affirmation (même chiffre, même période), et de l'ignorer s'il porte sur un sujet voisin.

**Séries officielles Eurostat** (`indicators.py`) — 16 indicateurs pour la France (chômage, chômage des jeunes, inflation, dette en % du PIB et en euros, déficit, dépense publique, prélèvements obligatoires, emploi des seniors, PIB par habitant, croissance, pauvreté, immigration, demandes d'asile, salaire minimum brut, émissions de gaz à effet de serre), chacun déclenché par une expression régulière sur l'affirmation (3 au plus). L'API JSON-stat d'Eurostat renvoie la série depuis 2015 (`decode_jsonstat` → `format_series` : « 2017 9,4 · 2024 7,4 »). Les 16 séries sont **téléchargées en tâche de fond** au démarrage puis chaque jour (`start` → `refresh`, ~7 s en tout) et gardées sur disque (`data/eurostat.json`, relu au démarrage) : `evidence` ne lit que ce cache. Appelée pendant le fact-check, l'API ajoutait 16 à 33 s à chaque affirmation concernée. Le lien de preuve est la page du jeu de données. Le prompt demande de comparer le chiffre avancé à la série en vérifiant l'année, le périmètre (France / UE) et la définition (dette au sens de Maastricht, chômage au sens du BIT, SMIC brut ou net).

**Votes de l'Assemblée nationale** (`votes.py`) — l'open data de l'Assemblée (tous les scrutins publics des législatures 16 et 17, ~12 500, et les députés) est téléchargé au démarrage dans `data/assemblee/` puis rafraîchi chaque semaine (`AN_REFRESH_S`) ; l'index est gardé en pickle (rechargement ~0,2 s). Une affirmation qui parle de vote (« a voté contre », « s'est abstenu »…) et nomme un député (nom complet, ou nom de famille s'il est unique) ou un groupe (`GROUP_ALIASES` : « le RN », « les Insoumis », « LR »…) est comparée aux titres des scrutins (racines des mots, synonymes, années et mois cités, bonus au vote sur l'ensemble d'un texte). La preuve donne la position du député ou le décompte du groupe, avec le lien du scrutin ; le prompt ne la retient que si le scrutin porte bien sur le texte dont parle l'affirmation (titre et date). Les groupes de la 16e législature, absents du fichier des députés actuels, sont déduits de leurs membres. `AN_VOTES=0` désactive le tout (tests).

**Garde-fous du verdict** — cas vécus sur un débat Attal / Maréchal :
- « faux » exige une source fournie qui contredit explicitement l'affirmation ; les seules connaissances du modèle ne suffisent jamais (il avait conclu « faux » à 90 % sur l'interdiction de l'abaya par Attal, sur la foi d'une étude hors sujet) ;
- la réponse contient un champ `inexact` (élément contredit par les sources : chiffre, période, superlatif, attribution) ; `finalize_result` ramène un « vrai » à « partiellement vrai » quand il n'est pas vide (un « VRAI 95 % » dont l'explication citait une baisse en 2020 contre « une première depuis 15-20 ans ») ;
- une mesure décidée par un ministre dans son domaine lui est attribuable ;
- les résultats académiques ne passent au prompt que s'ils portent sur l'affirmation (`sources.academic_relevant` : 2 mots-clés communs et 30 %, dont au moins un qui ne soit pas un nom propre), et OpenAlex ne renvoie que des articles avec résumé (une fiche de catalogue de bibliothèque était citée comme preuve qu'« Attal a été Premier ministre »).

**Recherche web éteinte** — sans SearxNG (Docker éteint), presque aucun verdict n'a de source. `/health` renvoie `web_search` : la popup prévient avant le lancement, et `start_transcription` envoie un `server_warning` sur la puce (`factcheck.search_available`).

**Propos exact** — quand la citation a été validée (§7), le prompt reçoit les mots prononcés à côté de la reformulation : Mistral juge ce qui a été dit, pas le résumé. Si l'affirmation contient manifestement une erreur de transcription (nom déformé, mot incompréhensible), il répond `non_verifiable` avec une explication qui commence par « Transcription douteuse : » au lieu de conclure « faux ».

**Signalements** — le bouton ⚑ d'une carte ou du récap envoie `POST /report_verdict` (via le service worker : seule l'origine de l'extension est acceptée) avec un motif parmi `verdict_faux`, `mauvaise_source`, `pas_un_fait`, `transcription`, `locuteur`. Le signalement est ajouté à `data/reports.jsonl` et, sauf pour un mauvais locuteur, le verdict **sort du cache** (`cache.mark_reported`) : il n'est plus jamais resservi, et `purge_cache.py` le supprime.

**Politique des sources** (`config.py`, publiée en entier sur le site, section « Sources » — `tests/test_sources.py` vérifie que la page et le code concordent). Principe : une source n'est **exclue** que sur un critère vérifiable, jamais pour sa ligne politique ; les sites militants, quel que soit leur bord, sont gardés mais annotés.

- **Exclues** (`EXCLUDED_SOURCES`, filtrées avant le prompt), par critère : réseaux sociaux et plateformes vidéo (ce ne sont pas des sources, et la « preuve » y est souvent la déclaration même qu'on vérifie) ; médias d'État sous sanctions de l'UE (règlement (UE) 2022/350) ; statut de presse refusé par la CPPAP pour atteinte à la santé publique ; satire revendiquée.
- **`FIABILITÉ FAIBLE`** (`LOW_RELIABILITY_DOMAINS`) : sites militants, conspirationnistes ou agrégateurs sans rédaction. Le prompt interdit de s'appuyer sur eux seuls, et `finalize_result` plafonne à 50 % la confiance d'un verdict dont ils sont la preuve.
- **`SOURCE PARTISANE`** (`PARTISAN_DOMAINS`) : sites des partis et mouvements. Ils prouvent ce qu'un parti dit ou propose (programme, communiqué), jamais un fait ; pas de plafond, puisqu'ils sont la bonne preuve pour « tel parti propose X ».

Pour ajouter ou reclasser un site : modifier `config.py` **et** la section « Sources » de `site/index.html` (le test échoue sinon), puis `purge_cache.py` pour retirer les verdicts qui s'appuyaient dessus.

**Souveraineté de la recherche web** — `web_search()` n'appelle pas un moteur tiers directement : elle interroge une instance **SearxNG auto-hébergée** (`searxng/docker-compose.yml`, `127.0.0.1:8080`), configurée pour ne solliciter que Brave et Mojeek (`searxng/config/settings.yml`). `datagouv_search()` interroge en direct l'API publique de `data.gouv.fr`. Si SearxNG est injoignable, `web_search()` retourne `[]` (dégradé, jamais bloquant).

**Replays** : l'année de la vidéo (`sources.video_year`, depuis la date de publication) est ajoutée à la requête web, et le cache range chaque verdict sous cette année : un replay de 2024 et un direct de 2026 ne partagent pas leurs verdicts.

Le cache (`factcheck_cache.db`, SQLite, colonnes `video_year` et `reported_at` ajoutées par migration) est à **deux niveaux** : la table persiste entre redémarrages, `_cache_mem` en est une copie en RAM pour le matching flou (`claims_match`, `CACHE_SIM_THRESHOLD=0.75`, `CACHE_TTL_DAYS=30` vérifié à chaque lookup). Ne sont jamais mis en cache les verdicts non sourcés ni ceux d'une affirmation datée par rapport au jour même (« ce soir », « actuellement », « il y a un an » — `text_utils.has_relative_time`). `purge_cache.py` applique ces mêmes règles aux verdicts déjà en base, plus une liste d'identifiants choisis à la main (sauvegarde automatique avant suppression).

**Retry sur 429** (`call_mistral_api`) — jusqu'à `MISTRAL_MAX_RETRIES=3` tentatives, délai = `Retry-After` si fourni, sinon `2s, 4s, 8s`, avec `mistral_rate_limited` au client à chaque tentative. **Les autres erreurs Mistral** (clé invalide 401, crédit épuisé 402, 5xx, timeout, réseau) partent en `server_warning` (`notify.describe_error`), et un fact-check en échec est émis avec `indisponible: true` — jamais confondu avec un vrai « non vérifiable ».

---

## 9. Table complète des événements socket.io

| Event | Sens | Payload | Effet côté extension |
|---|---|---|---|
| `set_context` | C→S | `{emission, guests, date, description}` | — |
| `start_transcription` | C→S | — | `session_starts[sid] = now` |
| `audio_chunk` | C→S | `ArrayBuffer` (WebM) | pipeline complet §3-7 |
| `speaker_probe` | C→S | `ArrayBuffer` (WebM, 2.5 s) | badge live |
| `stop_transcription` | C→S | — | dernier buffer analysé, puis `session_done` |
| `transcript_segment` | S→C | `{text, speaker, start, end, abs_time, said_at}` | badge, seulement sans sonde récente |
| `speaker_live` | S→C | `{speaker}` | badge (fait foi) |
| `talking_points` | S→C | `{points: [{id, ts, type, texte, qui, qui_label, verifiable?, citation, said_at}]}` | `addPoint()` par point |
| `fact_check_result` | S→C | `{id, verdict, confiance, explication, source, url, indisponible?}` | `onFactCheck()` |
| `speaker_map` | S→C | `{map, enrolled: [nom], enrollable: [nom]}` | renommage rétroactif, indicateur d'empreinte |
| `voice_enrolled` | S→C | `{name}` | fin de l'indicateur « capture de l'empreinte… » |
| `voice_not_in_bank` | S→C | `{labels: [...]}` | « Locuteur non identifié » tout de suite |
| `mistral_rate_limited` | S→C | `{attempt, max, wait}` | message temporaire sur la puce |
| `server_warning` | S→C | `{message}` | message temporaire sur la puce (anti-répétition 60 s) |
| `session_done` | S→C | `{complete}` | fin de la finalisation |

Messages internes à l'extension (offscreen → content, via background) en plus des relais ci-dessus : `connection_status`, `session_reset` (reconnexion = nouvelle session backend, les labels repartent de zéro), `finalizing`, `session_done`. Background → content : `showOverlay`, `captureEnded` (`reason` : `user`, `navigation`, `tab_closed`). Content → background : `contentReady` (restauration après F5), `adState`, `stopCapture`, `reportVerdict`.

Routes HTTP (extension uniquement, `X-Backend-Token` si `BACKEND_TOKEN` est défini) : `GET /health`, `POST /analyze_video` (détection des intervenants depuis le titre et la description), `POST /report_verdict` (§8).

---

## 10. Machine à états d'affichage (content.js)

Un seul objet `S` porte tout l'état : `points` (Map id→{point, fc}, ordre d'insertion = ordre du récap), `queue` (ids en attente de carte), `current` (la carte affichée), `phase` (`live` → `stopping` → `ended`) et **`gen`** — un compteur incrémenté à chaque `teardown()` qui invalide tous les `setTimeout` en vol (`later()` vérifie `g === S.gen`).

**Où s'affiche l'overlay** : un calque `#fct-layer` **dans `#movie_player`** (`overlayRoot`) — tout en haut de la vidéo : badge à gauche, puce à droite et cartes juste dessous. Le bas droit reste à YouTube (paramètres, logo de la chaîne, boutons rapides du plein écran). En plein écran, le badge s'efface tant que YouTube affiche le titre de la vidéo (contrôles visibles), puis revient en haut. Container queries : cartes compactes sur lecteur étroit, plus courtes sur lecteur bas, rien dans le mini-lecteur. Le récap est un panneau de page, sous le masthead.

**Hors YouTube** (`IS_YOUTUBE` faux) : le calque est posé en `position: fixed` sur le rectangle du **plus grand lecteur visible** (`playerTarget` : balise `<video>`, sinon iframe d'un lecteur externe comme Dailymotion — proportions de vidéo exigées, iframes reCAPTCHA/pubs écartées), recalé au défilement, au redimensionnement et chaque seconde (`followPlayer`, `sampleVideo`). En plein écran d'un conteneur, le calque passe dans l'élément plein écran ; si c'est la vidéo ou l'iframe elle-même, rien ne peut s'afficher par-dessus. Sans lecteur trouvé, repli en calque de page. Pas de pubs ni de repères de barre de progression ; horodatages et bouton ▶ seulement si la vidéo est une balise `<video>` de la page (inaccessible dans une iframe) ; liens horodatés de l'export réservés à YouTube. La popup lit titre, chaîne, description et date dans les balises Open Graph. Les sites déclarés dans le manifest (france.tv, francetvinfo.fr, lcp.fr, publicsenat.fr, twitch.tv, dailymotion.com) retrouvent l'overlay après F5 ; ailleurs, l'extension fonctionne via `activeTab`, et un rechargement arrête l'analyse.

**Carte** : type (AFFIRMATION, SUBJECTIF, TROP VAGUE), locuteur, reformulation, verdict, explication, source. La citation exacte (« Mot pour mot ») n'est qu'au récap et à l'export : sur la carte, elle surchargeait l'écran. La confiance n'est affichée que pour un vrai verdict (`confText`) : « non vérifié · 30 % » ne voulait rien dire ; un verdict sans lien de preuve est exporté « (sans source) ». Bouton ⚑ (carte et récap) : menu de motifs → `reportVerdict` (§8), puis confirmation (ou « Backend injoignable ») dans le menu.

**Repères sur la barre de progression** (YouTube, `renderMarkers`) : un trait par affirmation, à la couleur de son verdict, dans `.ytp-progress-bar` ; purement visuel (`pointer-events: none`). Pour un direct, la barre couvre la plage lisible (`video.seekable`), pas une durée.

Cycle de vie d'une carte :

```
showCard (spinner)
   → resolveCurrent (verdict dès que `fc` existe — immédiat si en cache, à réception de
     fact_check_result, ou après FC_WAIT_MS=30000 avec un état « EN ATTENTE » que le vrai
     verdict remplacera au récap)
   → maintien HOLD_FACT_MS=13000 (8000 si d'autres cartes attendent)
   → exitCurrent (animation EXIT_MS=560)
   → GAP_MS=220
   → pump() reprend la file (en sautant les affirmations reçues il y a plus de MAX_CARD_AGE_MS)
```

`MAX_QUEUE=8` : au-delà, les plus anciennes affirmations en attente quittent la file d'affichage (elles restent au récap). Récap ouvert : la file est gelée, et un verdict qui arrive ne lance pas le décompte de la carte cachée.

**Horodatages** : la position de lecture est échantillonnée chaque seconde (`sampleVideo` : temps, vitesse, lecture/pause, hors pubs) ; à la réception d'un point, `videoTimeAt(said_at)` — l'instant du propos retrouvé grâce à sa citation exacte, sinon `ts − 8 s` — en déduit la position vidéo, **figée** dans `point.vt`. Juste malgré pause, saut, vitesse ×1,5. Bouton ▶ sur la carte et au récap, désactivé si l'onglet a changé de vidéo.

**Badge** : les sondes (`speaker_live`) font foi ; les segments Whisper ne l'alimentent que si aucune sonde n'est arrivée depuis `PROBE_FRESH_MS=6000`.

**Arrêt** : Stop (puce ou popup) → `captureEnded` → phase `stopping` (« finalisation des derniers verdicts… »), puis `session_done` → phase `ended`. L'overlay reste, le récap est consultable et exportable ; ✕ ferme. Les affirmations restées sans verdict passent « indisponible ».

**Persistance** : le récap est sauvegardé dans `chrome.storage.local` (`scheduleSave`) ; après un F5 pendant l'analyse, `contentReady` remet l'overlay et le récap (`restoreRecap`).

**Enregistrement et relecture** : chaque message qui change l'affichage (`TAPE_TYPES` : points, verdicts, locuteurs, empreintes vocales, messages de la puce — connexion, Mistral saturé, avertissements —, arrêt, finalisation et fin de l'analyse) est gardé dans `S.tape` avec la position vidéo à laquelle il est arrivé, ainsi que la position où l'analyse a démarré (`S.tapeStart` : l'overlay n'apparaît qu'à partir de là en relecture) (`record`, copie prise AVANT traitement, position du propos `vt` ajoutée aux points ; « qui parle » réduit au locuteur). La bande est sauvegardée avec le récap et exportée par le bouton ⏵ du récap (`exportSession`, format `source-session` v1). `publish_session.py` la nettoie (champs affichés seulement, positions recalées de `--offset` pour un direct) et la range dans `site/sessions/`. La page `site/relecture.html` charge, après un clic, le lecteur YouTube (youtube-nocookie.com) et **le vrai `content.js`** (copie `site/overlay/`, synchronisée par `publish_session.py`, vérifiée par `tests/test_replay.py`) avec un faux `chrome.runtime` (`site/relecture.js`), puis réinjecte chaque message quand la lecture atteint sa position : cartes, délais, badge et récap sont ceux du direct, sans GPU ni backend. Trois points d'appui dans `content.js`, sans effet sur l'extension : `window.__fctVideo` (objet vidéo exposé par la page, invisible depuis le monde isolé de l'extension), `backlog: true` sur `talking_points` (points déjà passés après un saut en avant : au récap, sans carte) et un `vt` déjà connu gardé par `addPoint`. Revenir en arrière ne rejoue rien (sauf le badge) ; « Recommencer » repart de zéro. Une session sans arrêt enregistré est close à la fin de la vidéo.

**Générer une session sans regarder la vidéo** (`generate_session.py <lien YouTube>`) : l'audio est téléchargé (yt-dlp, qui exige `yt-dlp-ejs` et un moteur JS — Node.js ou Deno — pour les défis de YouTube), découpé comme l'extension (chunks de 10 s toutes les 8,5 s, sondes de 2,5 s) et envoyé aux **vrais handlers** Socket.IO via le client de test (`async_handlers=False` : un chunk est traité avant l'envoi du suivant). Intervenants détectés par `/analyze_video` comme dans la popup. Le minutage est celui du direct grâce à une **horloge de la vidéo par greenlet** (`VideoClock`) : chaque morceau « arrive » à sa position, les temps d'attente sont sautés et seuls les vrais temps de calcul s'écoulent ; une tâche de fond (analyse, fact-check, identification) hérite de l'heure de sa lanceuse (`eio.start_background_task` enveloppé), et `routes.time.time()` lit cette horloge. Les attentes qui n'existeraient pas en direct ne comptent pas : limites de débit de Mistral, et espacement des recherches web (`SEARCH_GAP_S`, sinon Brave fait suspendre SearxNG). Les événements sont enregistrés au format du bouton ⏵ (`to_message` reproduit ce que l'offscreen transmet à l'overlay). Mesuré : 2 min 30 de débat traitées en 39 s, cartes 14 à 30 s après le propos, verdicts 4 à 7 s après la carte — comme en direct. `VOICES_DIR` permet d'essayer sans toucher à la banque de voix.

**Reconnexion au backend** (`session_reset`) : `S.epoch` est incrémenté ; un `speaker_map` ne renomme que les points de l'epoch courante, et les points anciens restés anonymes deviennent « Locuteur non identifié ».

---

## 11. Toutes les constantes numériques du pipeline

| Constante | Valeur | Fichier |
|---|---|---|
| `CHUNK_MS` / `OVERLAP_MS` | 10000 / 1500 | offscreen.js |
| `PROBE_MS` | 2500 | offscreen.js |
| `CHUNK_OVERLAP_S` | 1.5 | server/config.py |
| `FLUSH_INTERVAL` / `MIN_WORDS` / `MAX_BUFFER_WORDS` | 22s / 30 / 55 | server/config.py |
| `MIN_WORDS_ON_PAUSE` / `MIN_WORDS_ON_STOP` / `FINISH_TIMEOUT_S` | 12 / 8 / 45s | server/config.py |
| `DIARIZATION_THRESHOLD` / `MIN_NEW_SPEAKER_SEC` / `MAX_SPEAKERS` | 0.34 / 2.0s / 12 | server/config.py |
| `PROBE_MATCH_T` | 0.28 | server/config.py |
| `VOICE_MATCH_THRESHOLD` / `VOICE_MATCH_MARGIN` / `VOICE_MATCH_SOLO_BONUS` | 0.45 / 0.08 / 0.10 | server/config.py |
| `VOICE_ENROLL_MIN_SEGMENTS` / `VOICE_ENROLL_MIN_VOTES` | 8 / 3 | server/config.py |
| dédup serveur (recouvrement) | 0.45 | server/dedup.py |
| dédup extension (recouvrement) | 0.6 | content.js |
| `CACHE_TTL_DAYS` / `CACHE_MIN_CONF` / `CACHE_SIM_THRESHOLD` | 30j / 60 / 0.75 | server/config.py |
| `UNSOURCED_MAX_CONF` | 50 | server/sources.py |
| `MISTRAL_MAX_RETRIES` / `MISTRAL_RETRY_BASE_S` | 3 / 2.0s (×2^n) | server/config.py |
| `HOLD_FACT_MS` / `HOLD_FACT_BUSY_MS` / `FC_WAIT_MS` / `MAX_CARD_AGE_MS` | 13000 / 8000 / 30000 / 150000 | content.js |
| `MAX_QUEUE` / `DUPE_MEMORY` / `PROBE_FRESH_MS` | 8 / 6 / 6000 | content.js |
| `CHECKWORTHY_MIN` | 6 (sur 10) | server/config.py |
| `MAX_HOTWORDS_CHARS` / `MAX_LEARNED` | 450 / 20 | server/vocabulary.py |
| `FACTCHECK_FEEDS_REFRESH_S` / `AN_REFRESH_S` | 1h / 7j | server/config.py |
| cache Eurostat / `MAX_PER_CLAIM` | 24h / 3 | server/indicators.py |

---

## 12. Tests

Aucun GPU, aucune clé ni aucun réseau nécessaires — chaque fichier se lance seul (`python tests/<fichier>.py`) ou via `python -m pytest tests` :

| Fichier | Couvre |
|---|---|
| `tests/test_claim_matching.py` | comparaison d'affirmations, dédup, cache (cas réels : négation, nombres, pour/contre) |
| `tests/test_sources.py` | domaines, niveaux partisan / fiabilité faible, verdict normalisé, nom de source, plafonds de confiance ; politique publiée sur le site = code |
| `tests/test_network.py` | repli IPv4 : modes forcés sans test réseau, repli seulement si l'IPv6 est cassé |
| `tests/test_replay.py` | overlay du site synchronisé avec l'extension, nettoyage et recalage des sessions publiées, sessions de `site/sessions/` valides |
| `tests/test_transcript.py` | chevauchement entre chunks, transcript annoté |
| `tests/test_voices.py` | comparaison de noms, stockage de la banque de voix |
| `tests/test_vocabulary.py` | mots attendus par Whisper : priorités, noms propres, limite de taille, écho des hotwords |
| `tests/test_points.py` | note de vérifiabilité, validation et datation de la citation exacte, orateur nommé dans sa propre citation |
| `tests/test_known_factchecks.py` | lecture des flux RSS, correspondance affirmation ↔ fact-check publié |
| `tests/test_official_data.py` | décodage JSON-stat Eurostat, déclencheurs d'indicateurs, jamais d'appel Eurostat pendant un fact-check (cache disque), parsing des scrutins et recherche de votes |
| `tests/test_backend_smoke.py` | backend complet avec Whisper/ECAPA/Mistral/recherche/RSS/Eurostat simulés : chunk → points (vague, citation) → verdict (fact-check publié et série Eurostat en preuves) → arrêt propre ; signalement ; origines CORS |

Sous Windows, si la sortie est redirigée, lancer avec `PYTHONIOENCODING=utf-8` (les messages du backend contiennent des emojis).

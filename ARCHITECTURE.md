# SOURCÉ — Architecture technique

Ce document décrit le fonctionnement interne du pipeline SOURCÉ : capture audio, transcription, diarisation, extraction de talking points, fact-checking, et affichage. Il est basé sur une lecture ligne à ligne de `backend.py` (point d'entrée) + du package `server/` qui contient toute la logique, `extension/content.js`, `extension/offscreen.js` et `extension/background.js`.

## Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Capture audio → chunks](#2-capture-audio--chunks-mapping-temporel-exact)
3. [Réception serveur](#3-réception-serveur--verrouillage-par-session-pas-de-blocage-global)
4. [Structure d'un chunk transcrit](#4-ce-que-produit-un-chunk--structure-exacte)
5. [Diarisation](#5-diarisation--le-clustering-incrémental-et-ses-seuils)
6. [Buffer → Mistral (flush)](#6-buffer--mistral--la-logique-de-flush)
7. [Extraction → déduplication → dispatch](#7-extraction-mistral--déduplication--dispatch)
8. [Fact-check](#8-fact-check--cache--recherche--verdict)
9. [Événements socket.io](#9-table-complète-des-événements-socketio)
10. [Machine à états d'affichage](#10-machine-à-états-daffichage-contentjs)
11. [Constantes du pipeline](#11-toutes-les-constantes-numériques-du-pipeline)

---

## 1. Vue d'ensemble

Quatre composants, trois frontières réseau :

```
[Onglet YouTube]  --tabCapture-->  [offscreen.js]  --socket.io-->  [server/routes.py]  --HTTP-->  [Mistral / SearxNG / HAL / OpenAlex]
       ^                                                                 |
       |________________forwardToContent (via background.js)____________|
       |
[content.js : state machine + DOM]
```

- **`extension/background.js`** — service worker MV3, **sans état mémoire** (Chrome le tue après ~30 s d'inactivité) : tout vit dans `chrome.storage.session` ([background.js:7-9](extension/background.js#L7)). Il orchestre : injecter `content.js`, créer le document offscreen, relayer les messages.
- **`extension/offscreen.js`** — le seul endroit où l'audio existe. Un document offscreen a accès à `MediaRecorder`/`AudioContext`, ce qu'un service worker n'a pas.
- **`backend.py`** — point d'entrée : monkey-patch eventlet puis lance `server/routes.py`. Toute la logique vit dans le package `server/` :
  - `server/app.py` — app Flask/SocketIO, chargement des modèles (Whisper, ECAPA), diagnostics de démarrage.
  - `server/config.py` — constantes et variables d'environnement.
  - `server/state.py` — dicts `session_*` (un `sid` socket.io = un débat en cours = un jeu complet de structures).
  - `server/voices.py` — diarisation (`SpeakerTracker`), banque d'empreintes, identification des locuteurs (vote LLM + match acoustique).
  - `server/factcheck.py` — appels Mistral, recherche web/académique/officielle, prompts.
  - `server/cache.py` / `server/dedup.py` / `server/text_utils.py` — cache SQLite des fact-checks, déduplication sémantique, petits utilitaires texte.
  - `server/routes.py` — routes Flask + handlers Socket.IO, la couche d'orchestration qui relie tout ça.
- **`extension/content.js`** — state machine d'affichage, aucune logique métier (tout arrive déjà décidé du backend).

---

## 2. Capture audio → chunks (mapping temporel exact)

`offscreen.js` lance **deux `MediaRecorder` en parallèle** sur le même `mediaStream` :

| Flux | Durée | Chevauchement | Event émis | Usage |
|---|---|---|---|---|
| Transcription | `CHUNK_MS=10000` | `OVERLAP_MS=1500` | `audio_chunk` | Whisper + diarisation complète |
| Sonde locuteur | `PROBE_MS=2500` | aucun | `speaker_probe` | badge "qui parle" temps réel |

Le chevauchement de 1,5 s ([offscreen.js:9](extension/offscreen.js#L9)) n'est pas cosmétique : chaque enregistreur **planifie son successeur au démarrage**, pas dans `onstop` ([offscreen.js:122-124](extension/offscreen.js#L122)) — `setTimeout(startRecorder, CHUNK_MS - OVERLAP_MS)`. Ça garantit qu'un mot à cheval sur la frontière de deux chunks est capturé en entier par au moins l'un des deux (redondance volontaire, dédupliquée plus loin par `history` côté serveur).

Chaque chunk est un blob WebM envoyé en binaire brut (`ArrayBuffer`) via `socket.emit('audio_chunk', ...)` — pas de JSON, pas de base64.

---

## 3. Réception serveur : verrouillage par session, pas de blocage global

`handle_audio_chunk` ([server/routes.py:302](server/routes.py#L302)) :

1. Acquiert `session_chunk_locks[sid]` (un `eventlet.semaphore.Semaphore(1)` par session) — **sérialise les chunks d'UNE session** (le `SpeakerTracker` n'est pas thread-safe) **sans bloquer les autres sessions**.
2. Écrit le blob sur disque (`tempfile`), calcule `chunk_offset = chunk_abs_time - session_start` (position dans le débat) et `chunk_abs_time = time.time()` (horodatage unix absolu).
3. Délègue tout le calcul lourd à `_transcribe_and_diarize` via `eventlet.tpool.execute(...)` ([server/routes.py:321](server/routes.py#L321)) — **thread natif**, pas le greenlet eventlet.

C'est le point le plus important de toute l'architecture : `eventlet.monkey_patch()` ([backend.py:12](backend.py#L12)) ne coopérativise que les I/O réseau, jamais le calcul CPU/GPU. Sans `tpool`, Whisper (GPU) et ECAPA (CPU) gèleraient tout le serveur, y compris la réception des chunks des autres sessions.

---

## 4. Ce que produit un chunk : structure exacte

`_transcribe_and_diarize` ([server/routes.py:180](server/routes.py#L180)) retourne une liste de dicts, un par segment Whisper :

```python
{"text": str, "speaker": "Intervenant A" | "", "start": float, "end": float, "abs_time": float}
```

Pipeline interne par segment :

1. **Whisper** (`large-v3-turbo`, repli `medium` si VRAM insuffisante) — `vad_filter=True`, `no_speech_threshold=0.45`, `compression_ratio_threshold=2.4` (filtre les répétitions hallucinées).
2. **Filtre hallucination** ([server/text_utils.py:71](server/text_utils.py#L71)) — liste noire (`amara.org`, `sous-titres réalisés`…) + heuristique ponctuation (`meaningful/len < 0.2` → rejeté).
3. **Filtre doublon local** — comparaison texte brut à `session_history[sid]` (5 derniers segments, pas sémantique, juste exact-match — les recouvrements de chunk produisent souvent le texte identique mot pour mot).
4. **Diarisation** ([server/voices.py:96](server/voices.py#L96)) — `speaker_label()` découpe l'échantillon audio du segment (`seg.start:seg.end` en samples 16 kHz), encode via ECAPA-TDNN → embedding 192-d → `SpeakerTracker.assign()`.

Chaque segment part immédiatement en `emit("transcript_segment", r)` — **avant** tout traitement Mistral. C'est ce flux qui alimente le badge "qui parle" côté extension (juste `speaker`, le texte est ignoré côté client à ce stade, cf. [content.js:281](extension/content.js#L281)).

---

## 5. Diarisation : le clustering incrémental et ses seuils

`SpeakerTracker` ([server/voices.py:34](server/voices.py#L34)) ne fait aucun apprentissage préalable — c'est du clustering en ligne pur, par similarité cosinus à des centroïdes.

| Paramètre | Valeur | Rôle |
|---|---|---|
| `DIARIZATION_THRESHOLD` | 0.34 | cosinus min pour rattacher un segment à un locuteur existant |
| `MIN_NEW_SPEAKER_SEC` | 2.0 s | un segment plus court **ne peut jamais** créer un nouveau locuteur (anti "locuteur fantôme" sur une interjection) |
| `MAX_SPEAKERS` | 12 | au-delà, rattachement forcé au plus proche |
| `PROBE_MATCH_T` | 0.28 | seuil (plus tolérant) pour les sondes temps réel, et plancher de plausibilité pour rattacher un segment court à un cluster existant (voir plus bas) |

Mapping mental : **chaque locuteur = un vecteur `sums[i]` (somme des embeddings) + un compteur `counts[i]`** ; le centroïde est recalculé à la volée (`sums[i]/counts[i]`), jamais stocké. `assign()` fait un argmax de similarité cosinus sur tous les centroïdes existants (coût O(nb_locuteurs), négligeable puisque ≤ 12).

**Bug corrigé — un segment court ne s'attache plus à un cluster qui ne lui ressemble pas.** Avant correction, un segment sous `MIN_NEW_SPEAKER_SEC` était rattaché au cluster `best` (le moins dissemblable des existants) **quelle que soit la valeur de `best_sim`**, y compris proche de 0 — l'intention (ne pas fragmenter en locuteurs fantômes) était bonne, mais rien n'empêchait un rattachement à un cluster qui n'a en fait rien à voir. Cas réel observé : la toute première réplique courte ("oui") d'un nouvel intervenant (François Copé) était happée par le cluster d'un autre intervenant déjà installé (Sébastien Chenu), simplement parce qu'aucun cluster n'existait encore pour lui — son texte se retrouvait alors étiqueté avec le mauvais nom, et le fact-check qui suit vérifie logiquement le fait sur la mauvaise personne. `assign()` exige désormais aussi `best_sim >= PROBE_MATCH_T` pour ce rattachement ; en dessous, le segment hérite du dernier locuteur actif (`self.last`, inchangé) plutôt que d'un cluster choisi au hasard — sans jamais créer de nouveau cluster pour autant (le segment reste trop court pour ça).

Les labels (`Intervenant A`, `B`…) sont **anonymes par construction**. Deux mécanismes indépendants les associent à un vrai nom, avec des garanties différentes :

**a) Vote LLM** (`identify_speakers`, [server/voices.py:315](server/voices.py#L315)) — toutes les 2 analyses Mistral (`state["flushes"] % 2 == 0`), le backend envoie les 6 derniers extraits annotés à Mistral avec la liste des invités connus. Un nom n'est **confirmé qu'à 2 votes concordants et strictement majoritaires** (`session_map_votes[sid][label][nom] += 1`) — une identification isolée ne peut pas verrouiller une erreur, mais peut être corrigée par les votes suivants.

**b) Empreinte acoustique** (`match_clusters_to_bank`, [server/voices.py:202](server/voices.py#L202)) — compare le centroïde de session à la banque de voix locale (`voices/index.json` + fichiers `.npy`). Seuils : `VOICE_MATCH_THRESHOLD=0.45` **et** marge `VOICE_MATCH_MARGIN=0.08` avec la 2e meilleure correspondance (anti-confusion). Une fois matché, le label est **verrouillé** (`session_voice_locked`) — les votes LLM ne peuvent plus le modifier. C'est prioritaire sur (a) : la voix prime toujours sur l'inférence textuelle.

**Bug corrigé — la banque n'est plus interrogée sans restriction.** La banque accumule des voix sur **tous les débats passés**, tous locuteurs confondus. Avant correction, `match_clusters_to_bank` comparait le centroïde de session à *toute* la banque : un présentateur (ou un invité non déclaré) pouvait hériter du nom de quelqu'un d'un tout autre débat, simplement parce que c'était, de peu, l'empreinte la moins dissemblable de toute la banque (cas réel observé : un présentateur identifié comme « François Ruffin », absent de l'émission). La fonction restreint désormais les candidats aux `guests` déclarés pour la session (`session_contexts[sid]["guests"]`) quand cette liste existe — sans elle, le comportement reste inchangé (tolérant, par compatibilité).

**c) Auto-enrôlement** (`auto_enroll_voices`) — si un locuteur a été confirmé par vote LLM avec ≥ 8 segments (`VOICE_ENROLL_MIN_SEGMENTS`) et fait partie des invités déclarés, son centroïde est sauvegardé en banque. **La banque s'auto-enrichit à chaque débat** : au suivant, la même personne est reconnue acoustiquement dès le premier segment, sans passer par le vote LLM.

Appelée à deux moments : à chaque confirmation réussie dans `identify_speakers` (**pendant** le débat, pas seulement à la fin) et à `disconnect` par sécurité. Idempotente par construction (`name in _voice_bank` bloque un doublon), donc sans risque à rappeler plusieurs fois par session : dès qu'un nom franchit `VOICE_ENROLL_MIN_SEGMENTS`, il est enregistré immédiatement plutôt que perdu si le backend plante avant la fin propre du débat — le seul cas que l'enregistrement à la seule déconnexion ne couvrait pas.

Chaque changement de mapping émet `speaker_map` → l'extension **renomme rétroactivement** toutes les cartes déjà affichées ([content.js:339](extension/content.js#L339)) via le champ `quiLabel` conservé sur chaque point.

---

## 6. Buffer → Mistral : la logique de flush

`session_buffers[sid]` accumule les `(speaker, text)` de chaque chunk. Le flush se déclenche quand **les deux conditions** sont réunies :

```python
word_count >= MIN_WORDS(30) and (elapsed_since_flush >= FLUSH_INTERVAL(22s) or word_count >= MAX_BUFFER_WORDS(55))
```

— soit "assez de matière et 22 s se sont écoulées", soit "buffer déjà dense (55 mots), on n'attend pas les 22 s". Le texte accumulé passe par `build_transcript()` ([server/text_utils.py:22](server/text_utils.py#L22)) qui fusionne les tours de parole consécutifs du même locuteur en un bloc `"Intervenant A: ... \nIntervenant B: ..."` — c'est **ce texte annoté** qui devient le prompt Mistral, pas la transcription brute.

`ts = buf.get("start_abs")` — le timestamp du **premier** mot du buffer, pas du flush. C'est ce nombre qui, des mois plus tard côté extension, permet `claimVideoTime()` ([content.js:510](extension/content.js#L510)) de recalculer la position vidéo :

```js
video.currentTime - (Date.now()/1000 - ts) - 8   // 8s = marge latence chunk + buffer
```

---

## 7. Extraction Mistral → déduplication → dispatch

`call_mistral()` renvoie une liste de `{"type", "texte", "qui"}`. Deux couches de dédup, **jamais une seule** :

1. **Dédup sémantique serveur** (`is_duplicate_indexed`, [server/dedup.py:13](server/dedup.py#L13)) — index inversé mot-clé → indices (`session_dupe_index[sid]`), évite un scan O(n) de tout l'historique de session. Un point est doublon si `overlap / min(len_a, len_b) >= 0.45` sur les mots-clés (≥ 4 lettres ou ≥ 3 chiffres, hors stopwords).
2. **Dédup d'affichage extension** (`isDuplicateOfAny` + `isNearDupeOfShown`, [content.js:117](extension/content.js#L117)) — même principe mais seuil **0.6**, sur deux périmètres différents : `isDuplicateOfAny` compare à **tout** `S.points` (survit à un redémarrage backend où `session_points` repartirait de zéro), `isNearDupeOfShown` compare seulement aux 6 dernières cartes **affichées** (`DUPE_MEMORY=6`) pour éviter qu'un point similaire mais pas identique n'interrompe une carte qu'on vient de montrer.

Chaque point unique reçoit un `id = uuid4().hex[:8]` — **c'est cet id qui relie `talking_points` et `fact_check_result`** à travers tout le pipeline asynchrone. Seuls les points `type == "affirmation"` déclenchent `fact_check_affirmation` en tâche de fond ; les autres types (`argument`, `subjectif`, `remarque`, `question`, `accord`, `désaccord`) vont directement au récap sans jamais passer par le fact-check.

---

## 8. Fact-check : cache → recherche → verdict

`fact_check_affirmation` ([server/factcheck.py:389](server/factcheck.py#L389)) :

```
cache_lookup(claim)  →  hit ?  → emit verdict instantané (pas d'appel réseau)
        │ miss
        ▼
web_search (SearxNG auto-hébergé, 6 résultats) + scholar_search (HAL + OpenAlex, 4 résultats)
+ datagouv_search (API data.gouv.fr, 3 résultats)
        │
        ▼
annotation fiabilité par domaine (_source_tier) : SOURCE OFFICIELLE / PRESSE ÉTABLIE / FIABILITÉ INCONNUE
        │
        ▼
prompt Mistral avec preuves annotées → JSON {verdict, confiance, explication, source, url}
        │
        ▼
validation : url doit être EXACTEMENT une des href retournées par la recherche
             (sinon vidée — anti-hallucination + anti-XSS javascript:)
        │
        ▼
cache_store (si confiance >= 60 et verdict != non_verifiable)
```

**Souveraineté de la recherche web** — `web_search()` ([server/factcheck.py:205](server/factcheck.py#L205)) n'appelle plus un moteur tiers directement : elle interroge une instance **SearxNG auto-hébergée** (`searxng/docker-compose.yml`, `127.0.0.1:8080`), configurée pour ne solliciter que Brave et Mojeek (`searxng/config/settings.yml`) — ni Google ni Bing, et aucun moteur qui leur sous-traite son index. Qwant a été testé et retiré : son scraping est bloqué par CAPTCHA côté SearxNG, une limitation connue du projet, pas un choix de config. `datagouv_search()` interroge en plus, en direct et sans intermédiaire, l'API publique du catalogue `data.gouv.fr` — c'est la seule source du pipeline qui ne dépend d'aucun agrégateur ni moteur de recherche. Si SearxNG est injoignable, `web_search()` retourne `[]` (dégradé, jamais bloquant) et le backend le signale au démarrage.

Le cache (`factcheck_cache.db`, SQLite) est à **deux niveaux** : la table SQL persiste entre redémarrages serveur, `_cache_mem` est une copie en RAM `[(set_mots_clés, résultat)]` rechargée au démarrage pour un matching flou rapide sans requête SQL par claim (`CACHE_SIM_THRESHOLD=0.75`, `CACHE_TTL_DAYS=30`).

**Retry sur 429** (`call_mistral_api`, [server/factcheck.py:151](server/factcheck.py#L151)) — jusqu'à `MISTRAL_MAX_RETRIES=3` tentatives, délai = `Retry-After` du header si fourni, sinon backoff exponentiel `2s, 4s, 8s`. Chaque tentative émet `mistral_rate_limited` au client (`{attempt, max, wait}`) — c'est ce qui alimente le message "Mistral saturé — tentative 2/3 dans 4s" dans la chip ([content.js:243](extension/content.js#L243)), plutôt que de laisser l'UI figée en silence.

---

## 9. Table complète des événements socket.io

| Event | Sens | Payload | Déclenche côté extension |
|---|---|---|---|
| `set_context` | C→S | `{emission, guests, date, description}` | — |
| `start_transcription` | C→S | — | `session_starts[sid] = now` |
| `audio_chunk` | C→S | `ArrayBuffer` (WebM) | pipeline complet §3-7 |
| `speaker_probe` | C→S | `ArrayBuffer` (WebM, 2.5 s) | badge live |
| `transcript_segment` | S→C | `{text, speaker, start, end, abs_time}` | met à jour le badge |
| `speaker_live` | S→C | `{speaker}` | idem (sonde) |
| `talking_points` | S→C | `{points: [...]}` | `addPoint()` par point |
| `fact_check_result` | S→C | `{id, verdict, confiance, explication, source, url}` | `onFactCheck()` → résout la carte si affichée |
| `speaker_map` | S→C | `{map: {label: nom}, enrolled: [nom, ...]}` | renomme rétroactivement ; `enrolled` évite d'afficher "capture en cours" pour un nom déjà en banque |
| `voice_enrolled` | S→C | `{name}` | retire l'indicateur "capture de l'empreinte…" du badge pour ce nom |
| `voice_not_in_bank` | S→C | `{labels: [label, ...]}` | affiche "Locuteur non identifié" tout de suite, sans attendre IDENT_TIMEOUT_MS |
| `mistral_rate_limited` | S→C | `{attempt, max, wait}` | message temporaire sur la chip |

---

## 10. Machine à états d'affichage (content.js)

Un seul objet `S` ([content.js:58](extension/content.js#L58)) porte tout l'état : `points` (Map id→{point, fc}, ordre d'insertion = ordre du récap), `queue` (ids en attente de carte), `current` (la carte affichée), et **`gen`** — un compteur incrémenté à chaque `teardown()` qui invalide tous les `setTimeout` en vol (`later()` vérifie `g === S.gen` avant d'exécuter). Ça évite tout timer fantôme après un arrêt/redémarrage sans recharger la page.

Cycle de vie d'une carte :

```
showCard (spinner)
   → resolveCurrent (verdict, dès que `fc` existe — immédiat si déjà en cache,
     ou à réception de fact_check_result, ou après FC_WAIT_MS=30000 en fallback
     pour ne jamais bloquer la file)
   → hold HOLD_FACT_MS=13000
   → exitCurrent (animation EXIT_MS=560)
   → GAP_MS=220
   → pump() reprend la file suivante
```

`MAX_QUEUE=8` : au-delà, les plus anciennes affirmations en attente sont retirées de la file d'affichage (mais restent dans `S.points`, donc toujours visibles au récap).

---

## 11. Toutes les constantes numériques du pipeline

| Constante | Valeur | Fichier |
|---|---|---|
| `CHUNK_MS` / `OVERLAP_MS` | 10000 / 1500 | offscreen.js |
| `PROBE_MS` | 2500 | offscreen.js |
| `FLUSH_INTERVAL` / `MIN_WORDS` / `MAX_BUFFER_WORDS` | 22s / 30 / 55 | server/config.py |
| `DIARIZATION_THRESHOLD` / `MIN_NEW_SPEAKER_SEC` / `MAX_SPEAKERS` | 0.34 / 2.0s / 12 | server/config.py |
| `PROBE_MATCH_T` | 0.28 | server/config.py |
| `VOICE_MATCH_THRESHOLD` / `VOICE_MATCH_MARGIN` / `VOICE_ENROLL_MIN_SEGMENTS` | 0.45 / 0.08 / 8 | server/config.py |
| dédup serveur (mots-clés) | seuil 0.45 | server/dedup.py |
| dédup extension (affichage) | seuil 0.6 | content.js |
| `CACHE_TTL_DAYS` / `CACHE_MIN_CONF` / `CACHE_SIM_THRESHOLD` | 30j / 60 / 0.75 | server/config.py |
| `MISTRAL_MAX_RETRIES` / `MISTRAL_RETRY_BASE_S` | 3 / 2.0s (×2^n) | server/config.py |
| `HOLD_FACT_MS` / `FC_WAIT_MS` / `MAX_QUEUE` / `DUPE_MEMORY` | 13000 / 30000 / 8 / 6 | content.js |

// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — content script
//
// Workflow : seules les AFFIRMATIONS vérifiables apparaissent en carte
// (spinner → verdict). Tout le reste (questions, opinions, remarques…) va
// silencieusement au récap ; le compteur de la chip pulse à chaque ajout.
//
// Machine à états : un store unique (S), une seule carte affichée à la fois,
// un seul timer de carte actif, et un compteur de génération (S.gen) qui
// invalide tous les timers en vol à chaque teardown.
//
// Cycle de vie de l'overlay (S.phase) : live → stopping (capture coupée) →
// ended (le backend a rendu ses derniers verdicts). L'overlay ne disparaît
// qu'à la fermeture explicite (✕) : le récap reste consultable et
// exportable après l'arrêt.
// ══════════════════════════════════════════════════════════════════════════════

// Verrou anti-double-injection (manifest auto-inject + injection programmatique)
if (window.__fctInjected) throw new Error('[FCT] already loaded');
window.__fctInjected = true;

// ── Config ─────────────────────────────────────────────────────────────────────

const HOLD_FACT_MS      = 13000; // affichage d'une carte affirmation résolue
const HOLD_FACT_BUSY_MS = 8000;  // …raccourci quand d'autres cartes attendent (sinon le retard sur le direct s'accumule)
const MAX_CARD_AGE_MS   = 150000; // une affirmation reçue il y a plus longtemps ne passe plus en carte (reste au récap)
const RESOLVE_DELAY = 400;   // délai avant résolution quand le fact-check est déjà connu
const FC_WAIT_MS    = 30000; // attente max d'un fact-check avant de libérer la carte
const EXIT_MS       = 560;   // durée de l'animation de sortie
const GAP_MS        = 220;   // pause entre deux cartes
const MAX_QUEUE     = 8;     // file d'affichage max (les plus anciennes sautent, restent au récap)
const DUPE_MEMORY   = 6;     // nb de dernières cartes mémorisées pour l'anti-doublon d'affichage

// Badge "qui parle" : les sondes (≈3 s de latence) font foi ; les segments
// Whisper (10-15 s de retard) ne servent que si aucune sonde n'est arrivée
// récemment — sinon le badge repassait en rafale par les locuteurs d'il y a
// 10 s à chaque chunk.
const PROBE_FRESH_MS = 6000;

// Horodatage vidéo : p.ts (backend) = réception du 1er chunk du buffer
// analysé ; le propos a commencé environ SPEECH_LEAD_S avant.
const SPEECH_LEAD_S = 8;
const MAX_SAMPLES = 4 * 3600; // échantillons (1/s) de la position vidéo gardés : 4 h de débat

const RECAP_KEY = 'fctRecap'; // chrome.storage.local : récap de la capture en cours (survit à un F5)

// L'identification (vote LLM à 2 confirmations concordantes, ou match banque
// de voix) peut prendre plusieurs cycles d'analyse — 150s laisse le temps à
// 2-3 tentatives réalistes avant d'afficher un échec plutôt qu'un label
// technique ("Intervenant A") ou une attente indéfinie.
const IDENT_TIMEOUT_MS = 150000;

// Une fois le NOM connu (vote LLM), l'empreinte vocale n'est pas forcément
// encore en banque (il faut assez de segments et de votes côté backend).
// On affiche "capture en cours" jusqu'à voice_enrolled, ou on abandonne
// l'indicateur après ce délai plutôt que de le laisser tourner pour un
// intervenant qui n'a pas assez parlé pour être enrôlé.
const ENROLL_INDICATOR_TIMEOUT_MS = 60000;

// L'enrôlement backend est souvent déjà acquis au moment même où le nom est
// confirmé (le seuil de segments est atteint avant le vote LLM) : sans ce
// plancher, le loader "empreinte vocale…" n'aurait jamais le temps de
// s'afficher avant de se replier sur le succès.
const MIN_ENROLL_DISPLAY_MS = 5000;
// Durée d'affichage du point vert "empreinte enregistrée" avant le repli.
const ENROLL_SUCCESS_HOLD_MS = 700;

const PENDING_ACCENT = 'oklch(0.72 0.025 255)';

const VERDICT_CFG = {
  vrai:               { tag: 'VRAI',         accent: 'oklch(0.74 0.13 145)', footerRight: '✓ confirmé',       footerColor: 'oklch(0.74 0.13 145)' },
  partiellement_vrai: { tag: 'PARTIEL',      accent: 'oklch(0.76 0.14 90)',  footerRight: '≈ nuancé',         footerColor: 'oklch(0.76 0.14 90)' },
  trompeur:           { tag: 'TROMPEUR',     accent: 'oklch(0.78 0.14 75)',  footerRight: '⚠ trompeur',       footerColor: 'oklch(0.78 0.14 75)' },
  faux:               { tag: 'FAUX',         accent: 'oklch(0.62 0.20 25)',  footerRight: '✗ démenti',        footerColor: 'oklch(0.72 0.16 28)' },
  non_verifiable:     { tag: 'NON VÉRIFIÉ',  accent: 'oklch(0.65 0.02 255)', footerRight: '? non vérifiable', footerColor: 'oklch(0.70 0.02 255)' },
};
// États qui ne sont PAS des verdicts : ne jamais les confondre avec un
// « non vérifiable » (conclusion de fond), ni les compter dans les stats
const STATE_CFG = {
  attente:      { tag: 'EN ATTENTE',   accent: PENDING_ACCENT,         footerRight: '⏳ vérification en cours', footerColor: 'oklch(0.72 0.025 255)' },
  indisponible: { tag: 'INDISPONIBLE', accent: 'oklch(0.58 0.03 255)', footerRight: '⚠ erreur technique',     footerColor: 'oklch(0.66 0.03 255)' },
};

function verdictCfg(fc) {
  if (!fc) return null;
  if (fc.pending) return STATE_CFG.attente;
  if (fc.indisponible) return STATE_CFG.indisponible;
  return VERDICT_CFG[fc.verdict] || VERDICT_CFG.non_verifiable;
}

const TYPE_CFG = {
  affirmation: { tag: 'AFFIRMATION', accent: 'oklch(0.65 0.14 240)' },
  subjectif:   { tag: 'OPINION',     accent: 'oklch(0.70 0.13 285)' },
  argument:    { tag: 'ARGUMENT',    accent: 'oklch(0.68 0.12 310)' },
  remarque:    { tag: 'REMARQUE',    accent: 'oklch(0.65 0.05 255)' },
  question:    { tag: 'QUESTION',    accent: 'oklch(0.76 0.15 90)'  },
  accord:      { tag: 'ACCORD',      accent: 'oklch(0.74 0.13 145)' },
  désaccord:   { tag: 'DÉSACCORD',   accent: 'oklch(0.62 0.20 25)'  },
};

// ── State ──────────────────────────────────────────────────────────────────────

const S = {
  active: false,
  phase: 'idle',     // 'live' | 'stopping' | 'ended'
  endReason: '',     // raison de l'arrêt ('user' | 'navigation' | 'tab_closed')
  endText: '',       // libellé de la chip une fois l'analyse terminée
  videoId: null,     // vidéo analysée (les horodatages ne valent que pour elle)
  gen: 0,            // incrémenté à chaque teardown → invalide tous les timers en vol
  epoch: 0,          // incrémenté à chaque nouvelle session backend (reconnexion) : les labels repartent de zéro
  points: new Map(), // id → { point, fc } — ordre d'insertion préservé (récap)
  sigs: new Map(),   // id → signature d'affirmation (cache de claimSig)
  queue: [],         // ids d'affirmations en attente d'affichage
  current: null,     // { id, el } — carte actuellement affichée
  cardTimer: null,   // LE timer de carte (un seul actif à la fois)
  cardPending: null, // { fn, ms } — dernière phase programmée, pour pause/reprise (récap ouvert)
  shownSigs: [],     // signatures des dernières cartes affichées (anti-doublon)
  speakerMap: {},    // "Intervenant A" → nom réel confirmé par le backend
  speakerFirstSeen: {}, // "Intervenant A" → Date.now() du premier affichage de ce label (pour "identification en cours" → "échec")
  bankMissLabels: new Set(), // labels déjà comparés à la banque de voix sans correspondance → "non identifié" tout de suite
  enrolledNames: new Set(), // noms déjà en banque AU MOMENT où leur nom a été révélé — rien à capturer, jamais d'indicateur
  enrollableNames: new Set(), // noms que le backend POURRA enrôler pendant cette session (invités déclarés, pas encore en banque)
  nameFirstSeen: {}, // nom réel → Date.now() du premier affichage (abandonne l'indicateur de capture après ENROLL_INDICATOR_TIMEOUT_MS)
  enrollDisplayed: new Set(), // noms pour qui le cycle capture→succès a déjà été joué (ou jugé inutile) — plus jamais d'indicateur
  pendingEnrollSuccess: new Set(), // voice_enrolled reçu avant que le badge n'ait affiché "capture en cours" pour ce nom — rejoué dès que possible
  enrollShownAt: null, // Date.now() du passage à "capture en cours" du badge actuel (plancher MIN_ENROLL_DISPLAY_MS)
  badgeTimer: null,  // auto-masquage du badge "qui parle" pendant les silences
  lastProbeAt: 0,    // Date.now() de la dernière sonde "qui parle"
  recapOpen: false,
  showAll: true,     // filtre récap : true = tout voir (défaut)
  lastStatus: 'connected', // dernier statut de connexion connu
  flash: null,       // { text, cls } — message temporaire sur la chip (rate limit, avertissement backend)
  flashTimer: null,
  notes: {},         // messages persistants tant que la condition dure : { ad: '…', muted: '…' }
  samples: [],       // [{ w, t, rate, playing }] — position vidéo échantillonnée chaque seconde
  samplerTimer: null,
  lastAd: false,
  saveTimer: null,
};

// Timer lié à la génération courante : ne fait rien si un teardown est passé entre-temps
function later(fn, ms) {
  const g = S.gen;
  return setTimeout(() => { if (g === S.gen) fn(); }, ms);
}

function setCardTimer(fn, ms) {
  clearTimeout(S.cardTimer);
  S.cardPending = { fn, ms }; // pour pause/reprise si le récap s'ouvre pendant ce délai
  S.cardTimer = later(fn, ms);
}

// ── Vidéo ──────────────────────────────────────────────────────────────────────

function mainVideo() {
  return document.querySelector('#movie_player video.html5-main-video')
    || document.querySelector('#movie_player video')
    || document.querySelector('video');
}

function isAdShowing() {
  return Boolean(document.querySelector('#movie_player.ad-showing, #movie_player.ad-interrupting'));
}

function currentVideoId() {
  const v = new URLSearchParams(location.search).get('v');
  if (v) return v;
  const m = location.pathname.match(/^\/live\/([\w-]{6,})/);
  return m ? m[1] : null;
}

// ── Nom affiché d'un locuteur ──────────────────────────────────────────────────
// Un label brut ("Intervenant A"…) ne doit jamais atteindre l'utilisateur : tant
// qu'aucun nom n'est confirmé (vote LLM ou banque de voix), on affiche l'état de
// la recherche plutôt qu'un identifiant technique. Ne modifie jamais les données
// stockées (point.qui reste le label/nom brut) — uniquement la présentation, pour
// ne pas casser le renommage rétroactif de onSpeakerMap.
const RAW_LABEL_RE = /^Intervenant ([A-Z]|\d+)$/;
// Locuteur d'une session backend précédente (avant reconnexion), jamais
// identifié : son label ne correspond plus à personne
const UNKNOWN_SPEAKER = '?';

// { kind: 'name' | 'progress' | 'unknown', text }
function identState(rawLabelOrName) {
  if (!rawLabelOrName) return null;
  if (rawLabelOrName === UNKNOWN_SPEAKER) return { kind: 'unknown', text: 'Locuteur non identifié' };
  const resolved = S.speakerMap[rawLabelOrName] || rawLabelOrName;
  if (!RAW_LABEL_RE.test(resolved)) return { kind: 'name', text: resolved };
  // Empreinte déjà comparée à la banque de voix, sans correspondance : ce
  // n'est pas quelqu'un de déjà connu — "non identifié" tout de suite, sans
  // attendre IDENT_TIMEOUT_MS, même si le vote LLM tourne encore.
  if (S.bankMissLabels.has(resolved)) return { kind: 'unknown', text: 'Locuteur non identifié' };
  if (!S.speakerFirstSeen[resolved]) S.speakerFirstSeen[resolved] = Date.now();
  const elapsed = Date.now() - S.speakerFirstSeen[resolved];
  return elapsed < IDENT_TIMEOUT_MS && S.phase === 'live'
    ? { kind: 'progress', text: 'Identification du locuteur…' }
    : { kind: 'unknown', text: 'Locuteur non identifié' };
}

// Texte brut (export) : jamais d'état "en cours" dans un document figé
function exportName(rawLabelOrName) {
  const st = identState(rawLabelOrName);
  if (!st) return '';
  return st.kind === 'name' ? st.text : 'Locuteur non identifié';
}

// HTML (carte, récap, badge) : petite icône cohérente avec le spinner/point de
// verdict des cartes — spinner tant que la recherche tourne, point statique
// une fois qu'on a renoncé.
function identHtml(st) {
  if (!st) return '';
  if (st.kind === 'name') return esc(st.text);
  const iconCls = st.kind === 'progress' ? 'fct-ident-icon--progress' : 'fct-ident-icon--unknown';
  return `<span class="fct-ident fct-ident--${st.kind}"><span class="fct-ident-icon ${iconCls}"></span>${esc(st.text)}</span>`;
}

function displayNameHtml(rawLabelOrName) {
  return identHtml(identState(rawLabelOrName));
}

// Une fois le nom connu, son empreinte vocale n'est pas forcément encore en
// banque (voir ENROLL_INDICATOR_TIMEOUT_MS) — seul le badge "qui parle" a la
// place de le montrer ; la carte/le récap n'affichent que le nom. Uniquement
// pour un nom que le backend pourra réellement enrôler (enrollable) : sinon
// l'indicateur tournait 60 s pour rien.
function needsEnrollIndicator(name) {
  if (S.enrollDisplayed.has(name)) return false;
  if (!S.nameFirstSeen[name]) {
    S.nameFirstSeen[name] = Date.now();
    // Déjà en banque AVANT même qu'on apprenne son nom (match acoustique
    // immédiat, ou personne déjà connue d'une session précédente) : rien à
    // capturer, pas d'indicateur à jouer. À distinguer du cas où le backend
    // enrôle PENDANT cette session (voir onVoiceEnrolled) : ce dernier doit
    // toujours montrer l'animation au moins une fois.
    if (S.enrolledNames.has(name)) {
      S.enrollDisplayed.add(name);
      return false;
    }
  }
  if (!S.enrollableNames.has(name) && !S.pendingEnrollSuccess.has(name)) return false;
  return Date.now() - S.nameFirstSeen[name] < ENROLL_INDICATOR_TIMEOUT_MS;
}

// Petit "pop" d'échelle à chaque changement d'état du badge ; l'anneau
// (withFlash) ne s'ajoute qu'au moment précis où un nom vient d'être
// identifié, pour distinguer ce moment-là d'un simple rafraîchissement.
function pulseBadge(badge, withFlash) {
  badge.classList.remove('fct-badge--pulsing');
  if (withFlash) badge.classList.remove('fct-badge--flash');
  void badge.offsetWidth; // relance l'animation
  badge.classList.add('fct-badge--pulsing');
  if (withFlash) badge.classList.add('fct-badge--flash');
}

// Construit et applique le contenu du badge pour un label/nom donné — point
// d'entrée unique partagé par onSegment (nouveau segment) et onSpeakerMap /
// onVoiceEnrolled (mise à jour d'un badge déjà affiché), pour ne jamais
// dupliquer la logique état → contenu à deux endroits.
function applyBadgeContent(badge, rawLabelOrName) {
  const nameEl = badge.querySelector('.fct-badge-name');
  const st = identState(rawLabelOrName);
  if (!st || !nameEl) return;
  const enrolling = st.kind === 'name' && needsEnrollIndicator(st.text);
  // Le badge n'affiche JAMAIS l'icône spinner/point de identHtml() : trop
  // petit pour une 2e icône animée à côté de l'équaliseur, qui porte déjà
  // l'état (rouge/gris) à lui seul. Texte brut uniquement — cf. plus bas.
  const html = enrolling
    ? `<span class="fct-badge-main">${esc(st.text)}</span><span class="fct-badge-enroll-wrap"><span class="fct-badge-enroll-inner"><span class="fct-ident fct-ident--progress fct-badge-enroll"><span class="fct-ident-icon fct-ident-icon--progress"></span><span class="fct-badge-enroll-text">empreinte vocale…</span></span></span></span>`
    : esc(st.text);

  const becameIdentified = st.kind === 'name' && badge.classList.contains('fct-badge--pending-id');

  // Le badge est trop petit pour une 2e icône animée à côté de l'équaliseur
  // quand on ne fait QUE chercher qui parle : seule sa couleur porte cet état
  // (rouge = voix identifiée, gris = en recherche). Une fois le nom connu, la
  // capture d'empreinte a sa propre ligne — le badge passe en 2 lignes pour
  // l'accueillir.
  const wasEnrolling = badge.classList.contains('fct-badge--enrolling');
  badge.classList.toggle('fct-badge--pending-id', st.kind !== 'name');
  badge.classList.toggle('fct-badge--unknown', st.kind === 'unknown');
  badge.classList.toggle('fct-badge--enrolling', enrolling);
  if (enrolling && !wasEnrolling) {
    S.enrollShownAt = Date.now();
    // voice_enrolled est arrivé avant qu'on ait pu montrer "capture en
    // cours" pour ce nom (course LLM/auto-enrôlement côté backend) : on
    // rejoue la séquence de succès maintenant que le badge l'affiche enfin,
    // plutôt que de sauter directement au nom nu.
    if (S.pendingEnrollSuccess.has(st.text)) {
      S.pendingEnrollSuccess.delete(st.text);
      runEnrollSuccess(badge, st.text);
    }
  }
  if (nameEl.innerHTML === html) return;
  nameEl.innerHTML = html;
  nameEl.classList.remove('fct-risein');
  void nameEl.offsetWidth; // relance l'animation
  nameEl.classList.add('fct-risein');
  pulseBadge(badge, becameIdentified);
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Anti-doublon d'affichage ───────────────────────────────────────────────────
// Deuxième filet après la dédup backend : si une carte dit essentiellement la
// même chose qu'une carte récente, elle ne s'affiche pas (mais reste au récap).
// Même règle que server/text_utils.py (claim_signature / claims_match) : deux
// affirmations ne sont « la même » que si leurs nombres, leur négation et
// leurs mots de sens (hausse, baisse, contre…) sont identiques — sans ça,
// « a voté pour » était jeté comme doublon de « a voté contre ».

const STOPWORDS = new Set([
  'avec', 'aussi', 'alors', 'autre', 'autres', 'bien', 'mais', 'même', 'nous',
  'plus', 'pour', 'puis', 'quand', 'sans', 'sont', 'très', 'tout', 'tous',
  'toute', 'toutes', 'vers', 'vous', 'dans', 'ainsi', 'avoir', 'être', 'faire',
  'dire', 'cette', 'cela', 'comme', 'donc', 'dont', 'elle', 'elles', 'entre',
  'leur', 'leurs', 'celui', 'celle', 'ceux', 'celles', 'depuis', 'quel',
  'quelle', 'quels', 'quelles',
]);
const NEGATION_RE = /(?<![\p{L}\d_])(?:ne|jamais|aucune?|nullement|guère)(?![\p{L}\d_])|(?<![\p{L}\d_])n['’]/iu;
const POLARITY_WORDS = new Set(['plus', 'moins', 'contre', 'davantage']);
const POLARITY_STEMS = [
  'hauss', 'baiss', 'augment', 'diminu', 'rédui', 'réduc', 'recul', 'chut', 'explos',
  'effondr', 'stagn', 'stab', 'progressé', 'progression', 'progresse',
  'doubl', 'tripl', 'quadrupl', 'moitié',
  'supérieur', 'inférieur', 'majorit', 'minorit',
  'favorable', 'défavorable', 'oppos', 'soutien', 'soutenu',
  'gagn', 'perd', 'pert', 'excédent', 'déficit', 'record',
  'interdi', 'autoris', 'obligatoire', 'légal', 'illégal',
];

function normNumber(raw) {
  let s = raw.replace(',', '.');
  if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '');
  return s.replace(/^0+/, '') || '0';
}

function claimSig(text) {
  const low = String(text).toLowerCase();
  const compact = low.replace(/(?<=\d)[\s  .](?=\d{3}(?!\d))/g, '');
  const tokens = low.match(/[a-zàâçéèêëîïôùûüœ]+/g) || [];
  return {
    words: new Set(tokens.filter(t => t.length >= 4 && !STOPWORDS.has(t))),
    numbers: new Set((compact.match(/\d+(?:[.,]\d+)?/g) || []).map(normNumber)),
    negative: NEGATION_RE.test(low),
    polar: new Set(tokens.filter(t => POLARITY_WORDS.has(t) || POLARITY_STEMS.some(s => t.startsWith(s)))),
  };
}

function sameSet(a, b) {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

function claimsMatch(a, b, threshold) {
  if (!sameSet(a.numbers, b.numbers) || a.negative !== b.negative || !sameSet(a.polar, b.polar)) return false;
  if (a.words.size < 3 || b.words.size < 3) return false;
  let overlap = 0;
  for (const w of a.words) if (b.words.has(w)) overlap++;
  return overlap / Math.min(a.words.size, b.words.size) >= threshold;
}

function sigOf(point) {
  let sig = S.sigs.get(point.id);
  if (!sig) S.sigs.set(point.id, sig = claimSig(point.texte));
  return sig;
}

// Filet contre la résurgence de doublons après un redémarrage backend : la
// dédup serveur (session_points) repart de zéro sur une nouvelle session/sid,
// mais S.points survit côté extension — on compare donc tout nouveau point à
// TOUT l'historique déjà reçu, pas seulement aux dernières cartes affichées.
function isDuplicateOfAny(sig) {
  for (const { point } of S.points.values()) {
    if (claimsMatch(sig, sigOf(point), 0.6)) return true;
  }
  return false;
}

function isNearDupeOfShown(sig) {
  return S.shownSigs.some(prev => claimsMatch(sig, prev, 0.6));
}

function rememberShown(sig) {
  S.shownSigs.push(sig);
  if (S.shownSigs.length > DUPE_MEMORY) S.shownSigs.shift();
}

// ── Messages ───────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === 'ping') { sendResponse({ pong: true }); return; }
  try {
    if (msg.action === 'showOverlay')     startOverlay();
    if (msg.action === 'captureEnded')    onCaptureEnded(msg.reason);
    if (msg.type === 'talking_points')    (msg.points || []).forEach(addPoint);
    if (msg.type === 'fact_check_result') onFactCheck(msg);
    if (msg.type === 'connection_status') onStatus(msg);
    if (msg.type === 'speaker_map')       onSpeakerMap(msg.map || {}, msg.enrolled || [], msg.enrollable || []);
    if (msg.type === 'speaker_live')      { S.lastProbeAt = Date.now(); onSegment(msg); }
    if (msg.type === 'transcript_segment' && Date.now() - S.lastProbeAt > PROBE_FRESH_MS) onSegment(msg);
    if (msg.type === 'mistral_rate_limited') onRateLimited(msg);
    if (msg.type === 'server_warning')    flashStatus(msg.message, 'warn', 8000);
    if (msg.type === 'voice_enrolled')    onVoiceEnrolled(msg);
    if (msg.type === 'voice_not_in_bank') onVoiceNotInBank(msg);
    if (msg.type === 'session_reset')     onSessionReset();
    if (msg.type === 'finalizing')        onFinalizing();
    if (msg.type === 'session_done')      onSessionDone(msg);
  } catch (e) {
    // Un message malformé ne doit jamais tuer le pipeline d'affichage
    console.error('[FCT] message handler error:', e);
  }
});

// Page (re)chargée pendant une analyse de CET onglet (F5) : la capture a
// continué — on remet l'overlay et le récap sauvegardé, au lieu de laisser
// la capture tourner sans rien afficher.
chrome.runtime.sendMessage({ action: 'contentReady' })
  .then((r) => { if (r?.captured) startOverlay({ restore: true }); })
  .catch(() => {});

window.addEventListener('pagehide', () => {
  if (S.active) saveRecap();
});

// ── Init / teardown ────────────────────────────────────────────────────────────

function startOverlay({ restore = false } = {}) {
  if (restore && S.active) return;
  if (S.active || document.getElementById('fct-chip')) teardown();
  S.active = true;
  S.phase = 'live';
  S.videoId = currentVideoId();
  injectFonts();
  injectChip();
  injectSlot();
  injectBadge();
  startSampler();
  if (restore) {
    restoreRecap();
    flashStatus('page rechargée — analyse toujours en cours', 'warn', 5000);
  } else {
    clearSavedRecap();
  }
}

function teardown() {
  S.gen++; // tous les timers en vol deviennent des no-ops
  clearTimeout(S.cardTimer);
  clearTimeout(S.flashTimer);
  clearTimeout(S.saveTimer);
  clearInterval(S.samplerTimer);
  Object.assign(S, {
    active: false, phase: 'idle', endReason: '', endText: '', videoId: null,
    cardTimer: null, cardPending: null, points: new Map(), sigs: new Map(), queue: [], current: null,
    shownSigs: [], speakerMap: {}, speakerFirstSeen: {}, bankMissLabels: new Set(),
    enrolledNames: new Set(), enrollableNames: new Set(), nameFirstSeen: {}, enrollDisplayed: new Set(),
    pendingEnrollSuccess: new Set(), enrollShownAt: null, badgeTimer: null, lastProbeAt: 0,
    recapOpen: false, showAll: true, lastStatus: 'connected', flash: null, flashTimer: null, notes: {},
    samples: [], samplerTimer: null, lastAd: false, saveTimer: null,
  });
  ['fct-layer', 'fct-chip', 'fct-card-slot', 'fct-recap', 'fct-speaker-badge'].forEach(id => document.getElementById(id)?.remove());
}

// Polices embarquées dans l'extension (plus d'appel à Google Fonts depuis
// les pages YouTube), déclarées seulement au démarrage d'une analyse. Noms
// préfixés « FCT » : aucun risque de remplacer une police de la page.
function injectFonts() {
  if (document.getElementById('fct-fonts')) return;
  const url = (f) => chrome.runtime.getURL(`fonts/${f}`);
  const face = (family, style, weight, file) =>
    `@font-face{font-family:'${family}';font-style:${style};font-weight:${weight};font-display:swap;src:url('${url(file)}') format('woff2')}`;
  const st = document.createElement('style');
  st.id = 'fct-fonts';
  st.textContent = [
    face('FCT Archivo', 'normal', '400 800', 'archivo-latin.woff2'),
    face('FCT Newsreader', 'normal', '400 500', 'newsreader-latin.woff2'),
    face('FCT Newsreader', 'italic', '400 500', 'newsreader-italic-latin.woff2'),
    face('FCT Plex Mono', 'normal', '400', 'ibm-plex-mono-400-latin.woff2'),
    face('FCT Plex Mono', 'normal', '500', 'ibm-plex-mono-500-latin.woff2'),
  ].join('\n');
  document.head.appendChild(st);
}

// Calque de l'overlay, DANS le lecteur vidéo (#movie_player) : la puce, le
// badge et les cartes sont posés sur la vidéo, suivent le mode cinéma et le
// plein écran, et ne recouvrent plus le masthead ni la colonne de droite.
// Repli sur la page si aucun lecteur n'est trouvé. Rappelé régulièrement :
// si YouTube recrée le lecteur, le calque y est replacé.
function overlayRoot() {
  const player = document.querySelector('#movie_player');
  let layer = document.getElementById('fct-layer');
  if (!layer) {
    layer = document.createElement('div');
    layer.id = 'fct-layer';
  }
  const host = player || document.body;
  if (layer.parentElement !== host) host.appendChild(layer);
  layer.classList.toggle('fct-layer--page', !player);
  return layer;
}

// ── Échantillonnage vidéo (horodatages, pubs, son coupé) ─────────────────────
// Chaque seconde : position de lecture, vitesse, lecture/pause. Permet de
// retrouver la position vidéo d'un instant passé malgré les pauses, les
// sauts et les vitesses ≠ 1× — l'ancienne formule (currentTime − temps
// écoulé − 8 s) dérivait dès qu'on touchait au lecteur, et changeait à
// chaque rendu du récap.

function startSampler() {
  clearInterval(S.samplerTimer);
  S.samplerTimer = setInterval(sampleVideo, 1000);
  sampleVideo();
}

function sampleVideo() {
  if (!S.active) return;
  overlayRoot(); // lecteur recréé par YouTube → y replacer le calque
  const video = mainVideo();
  const ad = isAdShowing();
  if (ad !== S.lastAd) {
    S.lastAd = ad;
    // L'offscreen n'envoie pas l'audio d'une pub au backend
    if (S.phase === 'live') chrome.runtime.sendMessage({ action: 'adState', ad }).catch(() => {});
  }
  setNote('ad', ad && S.phase === 'live' ? 'publicité — analyse en pause' : null);
  if (!video) return;
  if (!ad && S.videoId === currentVideoId()) {
    S.samples.push({ w: Date.now() / 1000, t: video.currentTime, rate: video.playbackRate || 1, playing: !video.paused && !video.ended });
    if (S.samples.length > MAX_SAMPLES) S.samples.splice(0, S.samples.length - MAX_SAMPLES);
  }
  const muted = S.phase === 'live' && !ad && (video.muted || video.volume === 0);
  setNote('muted', muted ? "son coupé — l'analyse n'entend rien" : null);
}

// Position vidéo (s) à l'instant `wall` (horloge unix, s), ou null si inconnue
function videoTimeAt(wall) {
  for (let i = S.samples.length - 1; i >= 0; i--) {
    const s = S.samples[i];
    if (s.w <= wall) return Math.max(0, s.playing ? s.t + (wall - s.w) * s.rate : s.t);
  }
  return null;
}

function canSeek() {
  return S.videoId && S.videoId === currentVideoId();
}

function seekTo(vt) {
  const video = mainVideo();
  if (video && canSeek() && Number.isFinite(vt)) video.currentTime = vt;
}

// ── Chip + statut ──────────────────────────────────────────────────────────────

function injectChip() {
  const chip = document.createElement('div');
  chip.id = 'fct-chip';
  chip.innerHTML = `
    <span class="fct-ping">
      <span class="fct-ping-core"></span>
      <span class="fct-ping-ring"></span>
    </span>
    <span class="fct-chip-brand">SOURC<span class="fct-chip-dot">É</span></span>
    <span class="fct-chip-sub" id="fct-chip-sub" role="status">connexion…</span>
    <button class="fct-chip-recap" id="fct-recap-btn" aria-expanded="false" aria-controls="fct-recap">Récap <span id="fct-count"></span></button>
    <button class="fct-chip-stop" id="fct-stop-btn"></button>
  `;
  chip.querySelector('#fct-recap-btn').addEventListener('click', toggleRecap);
  chip.querySelector('#fct-stop-btn').addEventListener('click', () => {
    if (S.phase === 'live') {
      // Le background répond par captureEnded : l'overlay passe en finalisation.
      // Extension rechargée entre-temps (contexte invalide) : la capture est
      // déjà morte, on termine localement.
      const orphan = () => { onCaptureEnded('user'); onSessionDone({ complete: false }); };
      try { chrome.runtime.sendMessage({ action: 'stopCapture' }).catch(orphan); } catch (_) { orphan(); }
    } else {
      clearSavedRecap();
      teardown();
    }
  });
  overlayRoot().appendChild(chip);
  updateChipControls();
  renderStatus();
}

function updateChipControls() {
  const btn = document.getElementById('fct-stop-btn');
  const chip = document.getElementById('fct-chip');
  if (!btn || !chip) return;
  const live = S.phase === 'live';
  btn.textContent = live ? '■' : '✕';
  btn.title = live ? "Arrêter l'analyse" : 'Fermer SOURCÉ';
  btn.setAttribute('aria-label', btn.title);
  chip.classList.toggle('fct-chip--ended', S.phase === 'ended');
}

const STATUS_CFG = {
  connected:     { cls: '',      label: 'analyse en direct' },
  reconnecting:  { cls: 'warn',  label: 'reconnexion…' },
  backend_down:  { cls: 'error', label: 'backend injoignable' },
  capture_error: { cls: 'error', label: 'erreur de capture' },
  unauthorized:  { cls: 'error', label: 'jeton invalide — vérifie la popup' },
};

const END_LABELS = {
  user:       'finalisation des derniers verdicts…',
  navigation: 'arrêtée : vidéo changée — finalisation…',
  tab_closed: 'arrêtée',
};

// Priorité d'affichage : message temporaire > condition persistante (pub,
// son coupé) > état de la session / de la connexion
function renderStatus() {
  const chip = document.getElementById('fct-chip');
  const sub = document.getElementById('fct-chip-sub');
  if (!chip || !sub) return;
  let text, cls;
  const note = Object.values(S.notes).find(Boolean);
  if (S.flash) ({ text, cls } = S.flash);
  else if (note) { text = note; cls = 'warn'; }
  else if (S.phase === 'stopping') { text = END_LABELS[S.endReason] || 'arrêt…'; cls = ''; }
  else if (S.phase === 'ended') { text = S.endText || 'analyse terminée'; cls = ''; }
  else ({ label: text, cls } = STATUS_CFG[S.lastStatus] || STATUS_CFG.connected);
  chip.classList.toggle('fct-chip--warn', cls === 'warn');
  chip.classList.toggle('fct-chip--error', cls === 'error');
  sub.textContent = text;
}

function flashStatus(text, cls, ms) {
  if (!S.active || !text) return;
  S.flash = { text, cls };
  clearTimeout(S.flashTimer);
  S.flashTimer = later(() => { S.flash = null; renderStatus(); }, ms);
  renderStatus();
}

function setNote(key, text) {
  if ((S.notes[key] || null) === (text || null)) return;
  S.notes[key] = text || null;
  renderStatus();
}

function onStatus({ status }) {
  S.lastStatus = status;
  if (status === 'capture_error') {
    S.phase = 'ended';
    S.endText = 'erreur de capture';
    updateChipControls();
  }
  renderStatus();
}

// Mistral saturé (429) : le backend retente automatiquement avec un backoff —
// on l'affiche plutôt que de laisser l'utilisateur croire que l'app est figée.
function onRateLimited({ attempt, max, wait }) {
  flashStatus(`Mistral saturé — tentative ${attempt}/${max} dans ${wait}s`, 'warn', wait * 1000 + 1200);
}

function onCaptureEnded(reason) {
  if (!S.active || S.phase !== 'live') return;
  S.phase = 'stopping';
  S.endReason = reason || 'user';
  setNote('ad', null);
  setNote('muted', null);
  document.getElementById('fct-speaker-badge')?.classList.remove('fct-badge--on');
  updateChipControls();
  renderStatus();
}

function onFinalizing() {
  if (S.phase === 'live') onCaptureEnded('user');
}

// Le backend a rendu ses derniers verdicts (ou a abandonné) : les
// affirmations encore sans verdict ne l'auront plus.
function onSessionDone({ complete }) {
  if (!S.active) return;
  S.phase = 'ended';
  let missing = 0;
  for (const entry of S.points.values()) {
    if (entry.point.type === 'affirmation' && (!entry.fc || entry.fc.pending)) {
      entry.fc = { verdict: 'non_verifiable', indisponible: true, source: '', url: '',
                   explication: "Vérification interrompue par l'arrêt de l'analyse." };
      missing++;
    }
  }
  const n = S.points.size;
  S.endText = `analyse terminée · ${n} point${n > 1 ? 's' : ''}`
    + (!complete && missing ? ` · ${missing} sans verdict` : '');
  updateChipControls();
  renderStatus();
  if (S.current) resolveCurrent();
  if (S.recapOpen) renderRecap();
  scheduleSave();
}

function updateCount() {
  const el = document.getElementById('fct-count');
  if (el) el.textContent = S.points.size > 0 ? `(${S.points.size})` : '';
  // Pulse : feedback visuel qu'un point vient d'arriver
  const btn = document.getElementById('fct-recap-btn');
  if (btn) {
    btn.classList.remove('fct-chip-recap--pulse');
    void btn.offsetWidth; // force reflow pour relancer l'animation
    btn.classList.add('fct-chip-recap--pulse');
  }
}

// ── Badge "qui parle" (haut gauche) ────────────────────────────────────────────

function injectBadge() {
  const badge = document.createElement('div');
  badge.id = 'fct-speaker-badge';
  badge.innerHTML = `
    <span class="fct-eq"><span></span><span></span><span></span></span>
    <span class="fct-badge-name"></span>
  `;
  overlayRoot().appendChild(badge);
  return badge;
}

function onSegment({ speaker }) {
  if (!S.active || S.phase !== 'live' || !speaker) return;
  const badge = document.getElementById('fct-speaker-badge') || injectBadge();

  badge.dataset.label = speaker; // label brut, pour le renommage via speaker_map/voice_enrolled
  applyBadgeContent(badge, speaker);
  badge.classList.add('fct-badge--on');

  // Auto-masquage si plus aucune détection n'arrive (silence, pub, fin) —
  // les sondes arrivent toutes les ~2,5 s quand quelqu'un parle
  clearTimeout(S.badgeTimer);
  S.badgeTimer = later(() => badge.classList.remove('fct-badge--on'), 7000);
}

// ── Card slot ──────────────────────────────────────────────────────────────────

function injectSlot() {
  document.getElementById('fct-card-slot')?.remove();
  const slot = document.createElement('div');
  slot.id = 'fct-card-slot';
  // Une nouvelle carte de vérification est un contenu qui mérite d'être
  // annoncé par un lecteur d'écran, sans lui faire perdre le focus courant.
  slot.setAttribute('role', 'status');
  slot.setAttribute('aria-live', 'polite');
  slot.setAttribute('aria-atomic', 'true');
  slot.addEventListener('click', (e) => {
    const btn = e.target.closest('.fct-card-ts');
    if (btn) seekTo(Number(btn.dataset.vt));
  });
  overlayRoot().appendChild(slot);
  return slot;
}

// ── Store ──────────────────────────────────────────────────────────────────────

function addPoint(point) {
  if (!S.active || !point?.id || typeof point.texte !== 'string' || S.points.has(point.id)) return;
  const sig = claimSig(point.texte);
  if (isDuplicateOfAny(sig)) return;
  S.sigs.set(point.id, sig);
  // quiLabel = label diarisation d'origine, conservé pour pouvoir renommer
  // (ou corriger) rétroactivement quand le mapping évolue — valable pour la
  // session backend courante uniquement (quiEpoch)
  point.quiLabel = point.qui_label || point.qui || '';
  point.quiEpoch = S.epoch;
  if (point.qui && S.speakerMap[point.qui]) point.qui = S.speakerMap[point.qui];
  point.receivedAt = Date.now();
  // Position vidéo du propos, figée à la réception (voir videoTimeAt)
  point.vt = Number.isFinite(point.ts) ? videoTimeAt(point.ts - SPEECH_LEAD_S) : null;
  S.points.set(point.id, { point, fc: null });
  updateCount();
  scheduleSave();
  if (S.recapOpen) renderRecap();

  // Seules les affirmations vérifiables méritent une carte ; le reste vit au récap
  if (point.type !== 'affirmation') return;
  if (isNearDupeOfShown(sig)) return;

  if (S.queue.length >= MAX_QUEUE) S.queue.shift();
  S.queue.push(point.id);
  pump();
}

function onSpeakerMap(map, enrolled, enrollable) {
  // Le backend a identifié (ou corrigé) un locuteur : renommer rétroactivement
  // tous les points de cette session backend via leur label d'origine (quiLabel)
  S.speakerMap = { ...S.speakerMap, ...map };
  for (const name of (enrolled || [])) S.enrolledNames.add(name);
  S.enrollableNames = new Set(enrollable || []);
  let changed = false;
  for (const { point } of S.points.values()) {
    const name = point.quiEpoch === S.epoch && point.quiLabel && map[point.quiLabel];
    if (name && point.qui !== name) {
      point.qui = name;
      changed = true;
    }
  }
  if (S.current) {
    const p = S.points.get(S.current.id)?.point;
    const el = S.current.el.querySelector('.fct-speaker');
    // displayNameHtml, jamais p.qui brut : un locuteur encore anonyme aurait
    // affiché « Intervenant B » à la place de « Identification du locuteur… »
    if (p?.qui && el) el.innerHTML = displayNameHtml(p.qui);
  }
  // Badge "qui parle" : renommer immédiatement si son label vient d'être identifié
  const badge = document.getElementById('fct-speaker-badge');
  if (badge?.dataset.label && map[badge.dataset.label]) {
    applyBadgeContent(badge, badge.dataset.label);
  }
  if (changed) scheduleSave();
  if (changed && S.recapOpen) renderRecap();
}

// Reconnexion au backend = nouvelle session : les labels « Intervenant A… »
// repartent de zéro et ne désignent plus forcément les mêmes personnes. Les
// anciens points gardent le nom déjà résolu ; ceux restés anonymes le
// restent (leur label ne correspond plus à rien).
function onSessionReset() {
  if (!S.active) return;
  S.epoch++;
  S.speakerMap = {};
  S.speakerFirstSeen = {};
  S.bankMissLabels = new Set();
  S.enrollableNames = new Set();
  for (const { point } of S.points.values()) {
    if (point.quiEpoch !== S.epoch && RAW_LABEL_RE.test(point.qui || '')) point.qui = UNKNOWN_SPEAKER;
  }
  const badge = document.getElementById('fct-speaker-badge');
  if (badge) {
    delete badge.dataset.label;
    badge.classList.remove('fct-badge--on');
  }
  if (S.recapOpen) renderRecap();
  scheduleSave();
}

// Joue la séquence de succès (icône → point vert "empreinte enregistrée",
// affichée au moins MIN_ENROLL_DISPLAY_MS depuis le début de la capture,
// puis repli). Appelée par onVoiceEnrolled quand le badge montre déjà la
// capture pour ce nom, ou par applyBadgeContent quand une réussite était en
// attente (voir S.pendingEnrollSuccess) au moment où le badge finit par
// afficher ce nom.
function runEnrollSuccess(badge, name) {
  const finish = () => {
    // Le badge a pu changer de locuteur (ou quitter l'état capture) pendant
    // l'attente du plancher MIN_ENROLL_DISPLAY_MS — ne rien faire dans ce cas,
    // applyBadgeContent a déjà repris la main sur son contenu.
    if (!badge.classList.contains('fct-badge--enrolling') || S.speakerMap[badge.dataset.label] !== name) return;
    const icon = badge.querySelector('.fct-badge-enroll .fct-ident-icon');
    const textEl = badge.querySelector('.fct-badge-enroll-text');
    const label = badge.querySelector('.fct-badge-enroll');
    if (!icon || !textEl || !label) {
      applyBadgeContent(badge, badge.dataset.label);
      return;
    }
    icon.classList.add('fct-ident-icon--done');
    label.classList.add('fct-ident--done');
    textEl.textContent = 'empreinte enregistrée';
    pulseBadge(badge);
    later(() => {
      S.enrollDisplayed.add(name);
      applyBadgeContent(badge, badge.dataset.label);
    }, ENROLL_SUCCESS_HOLD_MS);
  };

  // Le seuil d'enrôlement backend est souvent déjà atteint au moment même où
  // le nom est confirmé : sans ce plancher, le loader n'aurait jamais le
  // temps de s'afficher avant de se replier sur le succès.
  const shownFor = Date.now() - (S.enrollShownAt || Date.now());
  const wait = Math.max(0, MIN_ENROLL_DISPLAY_MS - shownFor);
  if (wait > 0) later(finish, wait);
  else finish();
}

// L'empreinte vocale de ce nom vient d'être sauvegardée en banque. Le vote
// LLM (qui révèle le nom) et l'auto-enrôlement (déclenché à chaque chunk
// audio, dans une tâche de fond séparée côté backend) tournent en parallèle
// : voice_enrolled peut donc arriver AVANT que le badge n'ait jamais eu la
// chance d'afficher "capture en cours" pour ce nom. Dans ce cas on mémorise
// juste la réussite (pendingEnrollSuccess) — applyBadgeContent la rejouera
// dès que ce nom apparaîtra enfin sur le badge, au lieu de sauter
// directement au nom nu sans jamais montrer l'animation.
function onVoiceEnrolled({ name }) {
  if (!name || S.enrollDisplayed.has(name)) return;
  const badge = document.getElementById('fct-speaker-badge');
  const showingThisName = badge?.dataset.label
    && S.speakerMap[badge.dataset.label] === name
    && badge.classList.contains('fct-badge--enrolling');
  if (!showingThisName) {
    S.pendingEnrollSuccess.add(name);
    return;
  }
  runEnrollSuccess(badge, name);
}

// Le backend a comparé une ou plusieurs empreintes à la banque de voix sans
// trouver de correspondance : ces locuteurs ne sont pas quelqu'un de déjà
// connu. On peut afficher "Locuteur non identifié" tout de suite plutôt que
// d'attendre IDENT_TIMEOUT_MS — le vote LLM continue en parallèle et
// remplacera cet état dès qu'un nom sera confirmé (via onSpeakerMap).
function onVoiceNotInBank({ labels }) {
  if (!Array.isArray(labels) || !labels.length) return;
  let changed = false;
  for (const label of labels) {
    if (!S.bankMissLabels.has(label)) { S.bankMissLabels.add(label); changed = true; }
  }
  if (!changed) return;
  const badge = document.getElementById('fct-speaker-badge');
  if (badge?.dataset.label && labels.includes(badge.dataset.label)) {
    applyBadgeContent(badge, badge.dataset.label);
  }
}

function onFactCheck(data) {
  const entry = S.points.get(data.id);
  if (entry) {
    entry.fc = {
      verdict: data.verdict,
      explication: data.explication,
      source: data.source || '',
      url: (typeof data.url === 'string' && /^https?:\/\//.test(data.url)) ? data.url : '',
      confiance: Number.isFinite(data.confiance) ? Math.round(data.confiance) : null,
      indisponible: Boolean(data.indisponible),
    };
    scheduleSave();
  }
  if (S.current?.id === data.id) resolveCurrent();
  if (S.recapOpen) renderRecap();
}

// ── Affichage — une seule carte à la fois ──────────────────────────────────────

function pump() {
  // Le récap se superpose visuellement au slot de carte : avancer la file
  // pendant qu'elle est masquée ferait défiler des cartes que personne ne
  // voit. On gèle l'affichage tant que le récap est ouvert.
  if (!S.active || S.recapOpen) return;
  // Carte détruite par le DOM de YouTube (navigation SPA) → libérer le slot
  if (S.current && !S.current.el.isConnected) {
    clearTimeout(S.cardTimer);
    S.current = null;
  }
  if (S.current) return;
  for (let id = S.queue.shift(); id !== undefined; id = S.queue.shift()) {
    const entry = S.points.get(id);
    // Trop ancienne pour une carte « en direct » (file restée bloquée) :
    // elle reste au récap, avec son horodatage
    if (!entry || Date.now() - (entry.point.receivedAt || 0) > MAX_CARD_AGE_MS) continue;
    showCard(id, entry);
    return;
  }
}

function tsButton(cls, vt) {
  return vt != null && canSeek()
    ? `<button class="${cls}" data-vt="${Number(vt)}" title="Revoir ce passage">▶ ${fmtTime(vt)}</button>`
    : '';
}

function showCard(id, entry) {
  const slot = document.getElementById('fct-card-slot') || injectSlot();
  const { point, fc } = entry;
  rememberShown(sigOf(point));

  const el = document.createElement('div');
  el.className = 'fct-card';
  el.style.setProperty('--accent', PENDING_ACCENT);
  el.innerHTML = `
    <div class="fct-bar"><div class="fct-bar-fill"></div><div class="fct-bar-shimmer"></div></div>
    <div class="fct-inner-glow"></div>
    <div class="fct-card-inner">
      <div class="fct-card-head">
        <span class="fct-brand">
          <span class="fct-brand-diamond">◆</span>SOURC<span class="fct-brand-dot">É</span>
        </span>
        <span class="fct-tag">
          <span class="fct-tag-icon fct-spinner-icon"></span>
          <span class="fct-tag-text">VÉRIFICATION</span>
        </span>
      </div>
      <div class="fct-card-meta">
        ${point.qui ? `<div class="fct-speaker">${displayNameHtml(point.qui)}</div>` : '<div></div>'}
        ${tsButton('fct-card-ts', point.vt)}
      </div>
      <p class="fct-claim">« ${esc(point.texte)} »</p>
      <div class="fct-checking">
        <span class="fct-spinner"></span>
        <span>Recoupement des sources…</span>
      </div>
      <p class="fct-body" style="display:none"></p>
      <div class="fct-footer" style="display:none">
        <span class="fct-source"></span>
        <span class="fct-footer-right"></span>
      </div>
    </div>
  `;

  slot.innerHTML = '';
  slot.appendChild(el);
  S.current = { id, el };
  // Reflow forcé plutôt que requestAnimationFrame : l'état initial est posé,
  // la transition part tout de suite — même dans un onglet où rAF est gelé
  void el.offsetWidth;
  el.classList.add('fct-card--in');

  if (fc) {
    // Résultat déjà connu : résoudre après une courte animation de spinner
    setCardTimer(resolveCurrent, RESOLVE_DELAY);
  } else {
    // En attente du fact-check. Passé FC_WAIT_MS, la carte se libère quand
    // même (elle ne bloque jamais la file) avec un état « en attente » —
    // distinct d'un verdict « non vérifiable » : le vrai verdict remplacera
    // cet état dans le récap dès qu'il arrivera.
    setCardTimer(() => {
      const e = S.points.get(id);
      if (e && !e.fc) {
        e.fc = { verdict: 'non_verifiable', pending: true, source: '', url: '',
                 explication: 'Vérification plus longue que prévu — le verdict apparaîtra dans le récap.' };
      }
      resolveCurrent();
    }, FC_WAIT_MS);
  }
}

function resolveCurrent() {
  if (!S.current) return;
  const { id, el } = S.current;
  const fc = S.points.get(id)?.fc;
  if (!fc) return; // pas encore de résultat — le timer fallback est en place
  const cfg = verdictCfg(fc);

  el.style.setProperty('--accent', cfg.accent);
  const tagIcon = el.querySelector('.fct-tag-icon');
  const tagText = el.querySelector('.fct-tag-text');
  if (tagIcon) tagIcon.className = 'fct-tag-icon fct-dot-icon';
  if (tagText) tagText.textContent = cfg.tag;

  const checking = el.querySelector('.fct-checking');
  const body     = el.querySelector('.fct-body');
  const footer   = el.querySelector('.fct-footer');
  if (checking) checking.style.display = 'none';
  if (body) {
    body.textContent = fc.explication || '';
    body.style.display = 'block';
    body.classList.add('fct-risein');
  }
  if (footer) {
    footer.style.display = 'flex';
    footer.classList.add('fct-risein');
    const srcEl = footer.querySelector('.fct-source');
    if (fc.url) {
      // url validée http(s) dans onFactCheck — source cliquable vers la preuve
      srcEl.innerHTML = `<a href="${esc(fc.url)}" target="_blank" rel="noopener noreferrer">${esc(fc.source || 'source')} ↗</a>`;
    } else {
      srcEl.textContent = fc.pending || fc.indisponible ? '' : (fc.source || 'analyse IA');
    }
    const right = footer.querySelector('.fct-footer-right');
    right.textContent = fc.confiance != null ? `${cfg.footerRight} · ${fc.confiance}%` : cfg.footerRight;
    right.style.color = cfg.footerColor;
  }
  // D'autres cartes attendent : maintien raccourci, sinon le retard sur le
  // direct s'accumule (jusqu'à ~2 min avec une file pleine)
  const hold = S.queue.length >= 2 ? HOLD_FACT_BUSY_MS : HOLD_FACT_MS;
  if (S.recapOpen) {
    // Récap ouvert par-dessus : ne pas lancer le décompte maintenant, la
    // carte sortirait sans avoir été vue — il reprendra à la fermeture
    clearTimeout(S.cardTimer);
    S.cardTimer = null;
    S.cardPending = { fn: exitCurrent, ms: hold };
  } else {
    setCardTimer(exitCurrent, hold);
  }
}

function exitCurrent() {
  if (!S.current) return;
  const { el } = S.current;
  S.current = null;
  el.classList.remove('fct-card--in');
  el.classList.add('fct-card--out');
  // Retirer la carte du DOM après l'animation : une carte invisible qui reste
  // (pointer-events: all) bloquerait les clics sur ce qu'elle recouvre
  later(() => el.remove(), EXIT_MS);
  setCardTimer(pump, EXIT_MS + GAP_MS);
}

function fmtTime(t) {
  t = Math.floor(t);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(s).padStart(2, '0')}`;
}

// ── Sauvegarde du récap (survit à un rechargement de la page) ─────────────────

function scheduleSave() {
  if (!S.active) return;
  clearTimeout(S.saveTimer);
  S.saveTimer = later(saveRecap, 800);
}

function saveRecap() {
  const data = {
    videoId: S.videoId,
    savedAt: Date.now(),
    epoch: S.epoch,
    speakerMap: S.speakerMap,
    points: [...S.points.values()].map(({ point, fc }) => ({ point, fc })),
  };
  try { chrome.storage.local.set({ [RECAP_KEY]: data }).catch(() => {}); } catch (_) {}
}

async function restoreRecap() {
  let data;
  try { data = (await chrome.storage.local.get(RECAP_KEY))[RECAP_KEY]; } catch (_) { return; }
  if (!S.active || !data || data.videoId !== S.videoId || !Array.isArray(data.points)) return;
  for (const { point, fc } of data.points) {
    if (point?.id && !S.points.has(point.id)) S.points.set(point.id, { point, fc });
  }
  S.speakerMap = { ...(data.speakerMap || {}), ...S.speakerMap };
  S.epoch = Math.max(S.epoch, data.epoch || 0);
  updateCount();
  if (S.recapOpen) renderRecap();
}

function clearSavedRecap() {
  try { chrome.storage.local.remove(RECAP_KEY).catch(() => {}); } catch (_) {}
}

// ── Export Markdown ────────────────────────────────────────────────────────────

function exportRecap() {
  const vid = S.videoId || '';
  const link = (p) => (p.vt != null && vid) ? `https://www.youtube.com/watch?v=${vid}&t=${Math.floor(p.vt)}s` : null;
  const title = (document.title || '').replace(/^\(\d+\)\s*/, '').replace(/ - YouTube$/, '').trim();

  const lines = [`# SOURCÉ — Récapitulatif (${new Date().toLocaleDateString('fr-FR')})`, ''];
  if (vid) lines.push(`Vidéo : [${title || vid}](https://www.youtube.com/watch?v=${vid})`, '');
  const affs = [], others = [];
  for (const entry of S.points.values()) {
    (entry.point.type === 'affirmation' ? affs : others).push(entry);
  }

  if (affs.length) {
    lines.push('## Affirmations vérifiées', '');
    for (const { point: p, fc } of affs) {
      const cfg = verdictCfg(fc);
      const verdict = cfg ? cfg.tag + (fc.confiance != null ? ` ${fc.confiance}%` : '') : 'EN ATTENTE';
      let line = `- **[${verdict}]**${p.qui ? ` ${exportName(p.qui)} —` : ''} « ${p.texte} »`;
      if (fc?.explication) line += ` — ${fc.explication}`;
      if (fc?.source && !fc.pending && !fc.indisponible) {
        line += fc.url ? ` *(source : [${fc.source}](${fc.url}))*` : ` *(source : ${fc.source})*`;
      }
      const l = link(p);
      if (l) line += ` — [▶ ${fmtTime(p.vt)}](${l})`;
      lines.push(line);
    }
    lines.push('');
  }
  if (others.length) {
    lines.push('## Autres points', '');
    for (const { point: p } of others) {
      const tag = TYPE_CFG[p.type]?.tag || String(p.type || '?').toUpperCase();
      const l = link(p);
      lines.push(`- **[${tag}]**${p.qui ? ` ${exportName(p.qui)} —` : ''} « ${p.texte} »${l ? ` — [▶ ${fmtTime(p.vt)}](${l})` : ''}`);
    }
  }

  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `source-recap_${new Date().toISOString().slice(0, 10)}${vid ? '_' + vid : ''}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ── Recap panel ────────────────────────────────────────────────────────────────

function toggleRecap() { S.recapOpen ? closeRecap() : openRecap(); }

function openRecap() {
  S.recapOpen = true;
  // Mettre en pause la carte en cours : le récap la recouvre entièrement, la
  // laisser tourner (minuteur + file) ferait sauter des cartes jamais vues.
  if (S.cardTimer) {
    clearTimeout(S.cardTimer);
    S.cardTimer = null;
  }
  document.getElementById('fct-recap-btn')?.classList.add('fct-chip-recap--active');
  document.getElementById('fct-recap-btn')?.setAttribute('aria-expanded', 'true');

  const panel = document.createElement('div');
  panel.id = 'fct-recap';
  panel.setAttribute('role', 'region');
  panel.setAttribute('aria-label', 'Récapitulatif SOURCÉ');
  panel.innerHTML = `
    <div class="fct-recap-header">
      <span class="fct-recap-title">◆ SOURC<span style="color:#e0324f">É</span> — Récapitulatif</span>
      <div style="display:flex;gap:6px">
        <span class="fct-seg" role="group" aria-label="Filtrer le récapitulatif">
          <button type="button" data-filter="all">Tout</button>
          <button type="button" data-filter="aff">Affirmations</button>
        </span>
        <button class="fct-recap-filter" id="fct-recap-export" title="Exporter en Markdown" aria-label="Exporter en Markdown">⬇</button>
        <button class="fct-recap-close" id="fct-recap-close" aria-label="Fermer le récapitulatif">✕</button>
      </div>
    </div>
    <div class="fct-recap-stats" id="fct-recap-stats"></div>
    <div class="fct-recap-body" id="fct-recap-body"></div>
  `;
  document.body.appendChild(panel);
  void panel.offsetWidth; // voir showCard
  panel.classList.add('fct-recap--in');

  panel.querySelector('#fct-recap-close').addEventListener('click', closeRecap);
  panel.querySelector('#fct-recap-export').addEventListener('click', exportRecap);

  // Délégation : les chips "▶ mm:ss" sont recréées à chaque rendu, le listener
  // vit sur le conteneur et survit aux innerHTML
  panel.querySelector('#fct-recap-body').addEventListener('click', (e) => {
    const btn = e.target.closest('.fct-recap-ts');
    if (btn) seekTo(Number(btn.dataset.vt));
  });

  const segButtons = panel.querySelectorAll('.fct-seg button');
  const syncFilter = () => segButtons.forEach(b => b.setAttribute('aria-pressed', String((b.dataset.filter === 'all') === S.showAll)));
  segButtons.forEach(b => b.addEventListener('click', () => {
    S.showAll = b.dataset.filter === 'all';
    syncFilter();
    renderRecap();
  }));
  syncFilter();

  renderRecap();
}

function closeRecap() {
  S.recapOpen = false;
  document.getElementById('fct-recap-btn')?.classList.remove('fct-chip-recap--active');
  document.getElementById('fct-recap-btn')?.setAttribute('aria-expanded', 'false');
  const panel = document.getElementById('fct-recap');
  if (panel) {
    panel.classList.remove('fct-recap--in');
    later(() => panel.remove(), 380);
  }
  // Reprendre la carte interrompue (phase complète, par simplicité) ou, si
  // aucune carte n'était affichée, laisser la file avancer normalement.
  if (S.current && S.cardPending) {
    setCardTimer(S.cardPending.fn, S.cardPending.ms);
  } else {
    pump();
  }
}

function renderStats() {
  const statsEl = document.getElementById('fct-recap-stats');
  if (!statsEl) return;
  const counts = { vrai: 0, partiellement_vrai: 0, trompeur: 0, faux: 0, non_verifiable: 0 };
  let affirmations = 0;
  for (const { point, fc } of S.points.values()) {
    if (point.type === 'affirmation') affirmations++;
    if (fc && !fc.pending && !fc.indisponible) counts[VERDICT_CFG[fc.verdict] ? fc.verdict : 'non_verifiable']++;
  }
  const parts = [`${S.points.size} point${S.points.size > 1 ? 's' : ''}`, `${affirmations} vérifiable${affirmations > 1 ? 's' : ''}`];
  for (const [verdict, n] of Object.entries(counts)) {
    if (n > 0) {
      const cfg = VERDICT_CFG[verdict];
      parts.push(`<span style="color:${cfg.accent}">● ${n} ${cfg.tag.toLowerCase()}</span>`);
    }
  }
  statsEl.innerHTML = parts.join('<span class="fct-recap-stats-sep">·</span>');
}

function renderRecap() {
  renderStats();
  const body = document.getElementById('fct-recap-body');
  if (!body) return;
  const entries = [...S.points.values()];
  const visible = S.showAll ? entries : entries.filter(e => e.point.type === 'affirmation');

  if (visible.length === 0) {
    body.innerHTML = `<div class="fct-recap-empty"><div class="fct-dots"><span></span><span></span><span></span></div><p>En attente des premières analyses…</p></div>`;
    return;
  }

  body.innerHTML = visible.map(({ point: p, fc }) => {
    // Un point malformé ne doit pas faire échouer le rendu des autres
    try {
      const vcfg = p.type === 'affirmation' ? verdictCfg(fc) : null;
      const tcfg = TYPE_CFG[p.type] || { tag: String(p.type || '?').toUpperCase(), accent: PENDING_ACCENT };
      const accent = vcfg ? vcfg.accent : tcfg.accent;
      const srcHtml = fc?.url
        ? `<a class="fct-recap-src" href="${esc(fc.url)}" target="_blank" rel="noopener noreferrer">${esc(fc.source || 'source')} ↗</a>`
        : (fc?.source && !fc.pending && !fc.indisponible ? `<span class="fct-recap-src">${esc(fc.source)}</span>` : '<span></span>');
      const waiting = p.type === 'affirmation' && (!fc || fc.pending);
      return `
      <div class="fct-recap-card" style="--accent:${accent}">
        <div class="fct-recap-bar"></div>
        <div class="fct-recap-card-inner">
          <div class="fct-recap-row">
            <span class="fct-recap-badge">${esc(vcfg && !waiting ? vcfg.tag : tcfg.tag)}</span>
            ${p.qui ? `<span class="fct-recap-speaker">${displayNameHtml(p.qui)}</span>` : ''}
            ${waiting ? '<span class="fct-recap-pending">⏳ vérification…</span>' : ''}
            ${tsButton('fct-recap-ts', p.vt)}
          </div>
          <p class="fct-recap-claim">« ${esc(p.texte)} »</p>
          ${fc?.explication && !waiting ? `<p class="fct-recap-explanation">${esc(fc.explication)}</p>` : ''}
          ${vcfg && !waiting ? `<div class="fct-recap-footer">${srcHtml}<span class="fct-recap-footer-right" style="color:${vcfg.footerColor}">${vcfg.footerRight}${fc?.confiance != null ? ` · ${fc.confiance}%` : ''}</span></div>` : ''}
        </div>
      </div>`;
    } catch (e) {
      console.error('[FCT] renderRecap item error:', e, p);
      return '';
    }
  }).join('');
}

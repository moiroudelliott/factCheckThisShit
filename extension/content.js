// ══════════════════════════════════════════════════════════════════════════════
// vérif.live — content script (v2.1)
//
// Workflow : seules les AFFIRMATIONS vérifiables apparaissent en carte
// (spinner → verdict). Tout le reste (questions, opinions, remarques…) va
// silencieusement au récap ; le compteur de la chip pulse à chaque ajout.
//
// Machine à états : un store unique (S), une seule carte affichée à la fois,
// un seul timer de carte actif, et un compteur de génération (S.gen) qui
// invalide tous les timers en vol à chaque teardown.
// ══════════════════════════════════════════════════════════════════════════════

// Verrou anti-double-injection (manifest auto-inject + injection programmatique)
if (window.__fctInjected) throw new Error('[FCT] already loaded');
window.__fctInjected = true;

// Fonts
(function injectFonts() {
  if (document.getElementById('fct-fonts')) return;
  const l = document.createElement('link');
  l.id = 'fct-fonts'; l.rel = 'stylesheet';
  l.href = 'https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400;1,6..72,500&family=Archivo:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap';
  document.head.appendChild(l);
})();

// ── Config ─────────────────────────────────────────────────────────────────────

const HOLD_FACT_MS  = 13000; // affichage d'une carte affirmation résolue
const RESOLVE_DELAY = 400;   // délai avant résolution quand le fact-check est déjà connu
const FC_WAIT_MS    = 30000; // attente max d'un fact-check avant résolution fallback
const EXIT_MS       = 560;   // durée de l'animation de sortie
const GAP_MS        = 220;   // pause entre deux cartes
const MAX_QUEUE     = 8;     // file d'affichage max (les plus anciennes sautent, restent au récap)
const DUPE_MEMORY   = 6;     // nb de dernières cartes mémorisées pour l'anti-doublon d'affichage

const PENDING_ACCENT = 'oklch(0.72 0.025 255)';

const VERDICT_CFG = {
  vrai:               { tag: 'VRAI',     accent: 'oklch(0.74 0.13 145)', footerRight: '✓ confirmé',  footerColor: 'oklch(0.74 0.13 145)' },
  partiellement_vrai: { tag: 'PARTIEL',  accent: 'oklch(0.76 0.14 90)',  footerRight: '≈ nuancé',    footerColor: 'oklch(0.76 0.14 90)' },
  trompeur:           { tag: 'TROMPEUR', accent: 'oklch(0.78 0.14 75)',  footerRight: '⚠ trompeur',  footerColor: 'oklch(0.78 0.14 75)' },
  faux:               { tag: 'FAUX',     accent: 'oklch(0.62 0.20 25)',  footerRight: '✗ démenti',   footerColor: 'oklch(0.72 0.16 28)' },
};

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
  gen: 0,            // incrémenté à chaque teardown → invalide tous les timers en vol
  points: new Map(), // id → { point, fc } — ordre d'insertion préservé (récap)
  queue: [],         // ids d'affirmations en attente d'affichage
  current: null,     // { id, el } — carte actuellement affichée
  cardTimer: null,   // LE timer de carte (un seul actif à la fois)
  shownWords: [],    // sets de mots-clés des dernières cartes affichées (anti-doublon)
  recapOpen: false,
  showAll: true,     // filtre récap : true = tout voir (défaut)
};

// Timer lié à la génération courante : ne fait rien si un teardown est passé entre-temps
function later(fn, ms) {
  const g = S.gen;
  return setTimeout(() => { if (g === S.gen) fn(); }, ms);
}

function setCardTimer(fn, ms) {
  clearTimeout(S.cardTimer);
  S.cardTimer = later(fn, ms);
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

const STOPWORDS = new Set([
  'avec', 'aussi', 'alors', 'autre', 'autres', 'bien', 'mais', 'même', 'nous',
  'plus', 'pour', 'puis', 'quand', 'sans', 'sont', 'très', 'tout', 'tous',
  'toute', 'toutes', 'vers', 'vous', 'dans', 'ainsi', 'avoir', 'être', 'faire',
  'dire', 'cette', 'cela', 'comme', 'donc', 'dont', 'elle', 'elles', 'entre',
  'leur', 'leurs', 'celui', 'celle', 'ceux', 'celles', 'depuis',
]);

function keyWords(text) {
  const tokens = String(text).toLowerCase().match(/[a-zàâçéèêëîïôùûü]{4,}|\d{3,}/g) || [];
  return new Set(tokens.filter(t => !STOPWORDS.has(t)));
}

function isNearDupeOfShown(text) {
  const words = keyWords(text);
  if (words.size < 3) return false;
  for (const prev of S.shownWords) {
    let overlap = 0;
    for (const w of words) if (prev.has(w)) overlap++;
    if (overlap / Math.min(words.size, prev.size) >= 0.6) return true;
  }
  return false;
}

function rememberShown(text) {
  const words = keyWords(text);
  if (words.size < 3) return;
  S.shownWords.push(words);
  if (S.shownWords.length > DUPE_MEMORY) S.shownWords.shift();
}

// ── Messages ───────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === 'ping') { sendResponse({ pong: true }); return; }
  try {
    if (msg.action === 'showOverlay')     initUI();
    if (msg.action === 'hideOverlay')     teardown();
    if (msg.type === 'talking_points')    (msg.points || []).forEach(addPoint);
    if (msg.type === 'fact_check_result') onFactCheck(msg);
    if (msg.type === 'connection_status') onStatus(msg);
  } catch (e) {
    // Un message malformé ne doit jamais tuer le pipeline d'affichage
    console.error('[FCT] message handler error:', e);
  }
});

// ── Init / teardown ────────────────────────────────────────────────────────────

function initUI() {
  if (S.active) return;
  S.active = true;
  ['fct-chip', 'fct-card-slot', 'fct-recap'].forEach(id => document.getElementById(id)?.remove());
  injectChip();
  injectSlot();
}

function teardown() {
  S.gen++; // tous les timers en vol deviennent des no-ops
  clearTimeout(S.cardTimer);
  S.cardTimer = null;
  S.active = false;
  S.points.clear();
  S.queue = [];
  S.current = null;
  S.shownWords = [];
  S.recapOpen = false;
  S.showAll = true;
  ['fct-chip', 'fct-card-slot', 'fct-recap'].forEach(id => document.getElementById(id)?.remove());
}

// ── Chip + statut de connexion ─────────────────────────────────────────────────

function injectChip() {
  const chip = document.createElement('div');
  chip.id = 'fct-chip';
  chip.innerHTML = `
    <span class="fct-ping">
      <span class="fct-ping-core"></span>
      <span class="fct-ping-ring"></span>
    </span>
    <span class="fct-chip-brand">vérif<span class="fct-chip-dot">.</span>live</span>
    <span class="fct-chip-sub" id="fct-chip-sub">connexion…</span>
    <button class="fct-chip-recap" id="fct-recap-btn">Récap <span id="fct-count"></span></button>
    <button class="fct-chip-stop" id="fct-stop-btn" title="Arrêter la transcription">■</button>
  `;
  chip.querySelector('#fct-recap-btn').addEventListener('click', toggleRecap);
  chip.querySelector('#fct-stop-btn').addEventListener('click', () => {
    try { chrome.runtime.sendMessage({ action: 'stopCapture' }).catch(() => {}); } catch (_) {}
    teardown();
  });
  document.body.appendChild(chip);
}

const STATUS_CFG = {
  connected:     { cls: '',                 label: 'analyse en direct' },
  reconnecting:  { cls: 'fct-chip--warn',   label: 'reconnexion…' },
  backend_down:  { cls: 'fct-chip--error',  label: 'backend injoignable' },
  capture_error: { cls: 'fct-chip--error',  label: 'erreur de capture' },
};

function onStatus({ status }) {
  const chip = document.getElementById('fct-chip');
  const sub  = document.getElementById('fct-chip-sub');
  if (!chip || !sub) return;
  const cfg = STATUS_CFG[status] || STATUS_CFG.connected;
  chip.classList.remove('fct-chip--warn', 'fct-chip--error');
  if (cfg.cls) chip.classList.add(cfg.cls);
  sub.textContent = cfg.label;
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

// ── Card slot ──────────────────────────────────────────────────────────────────

function injectSlot() {
  document.getElementById('fct-card-slot')?.remove();
  const slot = document.createElement('div');
  slot.id = 'fct-card-slot';
  document.body.appendChild(slot);
  return slot;
}

// ── Store ──────────────────────────────────────────────────────────────────────

function addPoint(point) {
  if (!point?.id || typeof point.texte !== 'string' || S.points.has(point.id)) return;
  S.points.set(point.id, { point, fc: null });
  updateCount();
  if (S.recapOpen) renderRecap();

  // Seules les affirmations vérifiables méritent une carte ; le reste vit au récap
  if (point.type !== 'affirmation') return;
  if (isNearDupeOfShown(point.texte)) return;

  if (S.queue.length >= MAX_QUEUE) S.queue.shift();
  S.queue.push(point.id);
  pump();
}

function onFactCheck(data) {
  const entry = S.points.get(data.id);
  if (entry) {
    entry.fc = { verdict: data.verdict, explication: data.explication, source: data.source || '' };
  }
  if (S.current?.id === data.id) resolveCurrent();
  if (S.recapOpen) renderRecap();
}

// ── Affichage — une seule carte à la fois ──────────────────────────────────────

function pump() {
  if (!S.active) return;
  // Carte détruite par le DOM de YouTube (navigation SPA) → libérer le slot
  if (S.current && !S.current.el.isConnected) {
    clearTimeout(S.cardTimer);
    S.current = null;
  }
  if (S.current) return;
  const id = S.queue.shift();
  if (id === undefined) return;
  const entry = S.points.get(id);
  if (!entry) { pump(); return; }
  showCard(id, entry);
}

function showCard(id, entry) {
  const slot = document.getElementById('fct-card-slot') || injectSlot();
  const { point, fc } = entry;
  rememberShown(point.texte);

  const el = document.createElement('div');
  el.className = 'fct-card';
  el.style.setProperty('--accent', PENDING_ACCENT);
  el.innerHTML = `
    <div class="fct-bar"><div class="fct-bar-fill"></div><div class="fct-bar-shimmer"></div></div>
    <div class="fct-inner-glow"></div>
    <div class="fct-card-inner">
      <div class="fct-card-head">
        <span class="fct-brand">
          <span class="fct-brand-diamond">◆</span>vérif<span class="fct-brand-dot">.</span>live
        </span>
        <span class="fct-tag">
          <span class="fct-tag-icon fct-spinner-icon"></span>
          <span class="fct-tag-text">VÉRIFICATION</span>
        </span>
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
  requestAnimationFrame(() => el.classList.add('fct-card--in'));

  if (fc) {
    // Résultat déjà connu : résoudre après une courte animation de spinner
    setCardTimer(resolveCurrent, RESOLVE_DELAY);
  } else {
    // En attente du fact-check. Fallback si le résultat n'arrive jamais :
    // la carte se résout quand même et ne bloque jamais la file.
    setCardTimer(() => {
      const e = S.points.get(id);
      if (e && !e.fc) e.fc = { verdict: 'partiellement_vrai', explication: 'Vérification indisponible.', source: '' };
      resolveCurrent();
    }, FC_WAIT_MS);
  }
}

function resolveCurrent() {
  if (!S.current) return;
  const { id, el } = S.current;
  const fc = S.points.get(id)?.fc;
  if (!fc) return; // pas encore de résultat — le timer fallback est en place
  const cfg = VERDICT_CFG[fc.verdict] || VERDICT_CFG.partiellement_vrai;

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
    footer.querySelector('.fct-source').textContent = fc.source || 'analyse IA';
    const right = footer.querySelector('.fct-footer-right');
    right.textContent = cfg.footerRight;
    right.style.color = cfg.footerColor;
  }
  setCardTimer(exitCurrent, HOLD_FACT_MS);
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

// ── Recap panel ────────────────────────────────────────────────────────────────

function toggleRecap() { S.recapOpen ? closeRecap() : openRecap(); }

function openRecap() {
  S.recapOpen = true;
  document.getElementById('fct-recap-btn')?.classList.add('fct-chip-recap--active');

  const panel = document.createElement('div');
  panel.id = 'fct-recap';
  panel.innerHTML = `
    <div class="fct-recap-header">
      <span class="fct-recap-title">◆ vérif<span style="color:oklch(0.74 0.13 145)">.</span>live — Récapitulatif</span>
      <div style="display:flex;gap:6px">
        <button class="fct-recap-filter" id="fct-recap-filter"></button>
        <button class="fct-recap-close" id="fct-recap-close">✕</button>
      </div>
    </div>
    <div class="fct-recap-stats" id="fct-recap-stats"></div>
    <div class="fct-recap-body" id="fct-recap-body"></div>
  `;
  document.body.appendChild(panel);
  requestAnimationFrame(() => panel.classList.add('fct-recap--in'));

  panel.querySelector('#fct-recap-close').addEventListener('click', closeRecap);

  const filterBtn = panel.querySelector('#fct-recap-filter');
  filterBtn.textContent = S.showAll ? 'Affirmations' : 'Tout voir';
  filterBtn.addEventListener('click', () => {
    S.showAll = !S.showAll;
    filterBtn.textContent = S.showAll ? 'Affirmations' : 'Tout voir';
    renderRecap();
  });

  renderRecap();
}

function closeRecap() {
  S.recapOpen = false;
  document.getElementById('fct-recap-btn')?.classList.remove('fct-chip-recap--active');
  const panel = document.getElementById('fct-recap');
  if (!panel) return;
  panel.classList.remove('fct-recap--in');
  later(() => panel.remove(), 380);
}

function renderStats() {
  const statsEl = document.getElementById('fct-recap-stats');
  if (!statsEl) return;
  const counts = { vrai: 0, partiellement_vrai: 0, trompeur: 0, faux: 0 };
  let affirmations = 0;
  for (const { point, fc } of S.points.values()) {
    if (point.type === 'affirmation') affirmations++;
    if (fc && counts[fc.verdict] !== undefined) counts[fc.verdict]++;
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
      const vcfg = fc ? VERDICT_CFG[fc.verdict] : null;
      const tcfg = TYPE_CFG[p.type] || { tag: String(p.type || '?').toUpperCase(), accent: PENDING_ACCENT };
      const accent = vcfg ? vcfg.accent : tcfg.accent;
      return `
      <div class="fct-recap-card" style="--accent:${accent}">
        <div class="fct-recap-bar"></div>
        <div class="fct-recap-card-inner">
          <div class="fct-recap-row">
            <span class="fct-recap-badge">${esc(p.type === 'affirmation' && vcfg ? vcfg.tag : tcfg.tag)}</span>
            ${p.type === 'affirmation' && !vcfg ? '<span class="fct-recap-pending">⏳ vérification…</span>' : ''}
          </div>
          <p class="fct-recap-claim">« ${esc(p.texte)} »</p>
          ${fc?.explication ? `<p class="fct-recap-explanation">${esc(fc.explication)}</p>` : ''}
          ${vcfg ? `<div class="fct-recap-footer"><span class="fct-recap-footer-right" style="color:${vcfg.footerColor}">${vcfg.footerRight}</span></div>` : ''}
        </div>
      </div>`;
    } catch (e) {
      console.error('[FCT] renderRecap item error:', e, p);
      return '';
    }
  }).join('');
}

// Verrou anti-double-injection (manifest auto-inject + programmatic)
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

const CARD_HOLD_MS      = 13000; // affichage d'une carte affirmation résolue
const CARD_HOLD_TALK_MS = 6500;  // affichage d'une carte non-affirmation (opinion, remarque…)
const CARD_EXIT_MS      = 560;
const CARD_GAP_MS       = 220;
const FC_WAIT_MS        = 30000; // attente max d'un fact-check avant résolution fallback
const MAX_QUEUE         = 8;

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

let allPoints    = {};  // id → point
let allFactChecks = {}; // id → { verdict, explication }
let queue        = [];  // items waiting: { point, fc }
let showing      = null; // { el, point, timers: [] }
let exitTimer    = null; // setTimeout handle for tryNext after card exit
let recapOpen    = false;
let active       = false;
let showAll      = true; // filtre récap : true = tout voir (défaut), false = affirmations seules

// ── Messages ───────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === 'ping') { sendResponse({ pong: true }); return; }
  try {
    if (msg.action === 'showOverlay')     initUI();
    if (msg.action === 'hideOverlay')     teardown();
    if (msg.type === 'talking_points')    (msg.points || []).forEach(enqueue);
    if (msg.type === 'fact_check_result') onFactCheck(msg);
  } catch (e) {
    // Un message malformé ne doit jamais tuer le pipeline d'affichage
    console.error('[FCT] message handler error:', e);
  }
});

// ── Init / teardown ────────────────────────────────────────────────────────────

function initUI() {
  if (active) return; // déjà initialisé dans cette session
  active = true;
  // Nettoyer les éléments orphelins d'une session précédente
  ['fct-chip', 'fct-card-slot', 'fct-recap'].forEach(id => document.getElementById(id)?.remove());
  injectChip();
  injectCardSlot();
}

function teardown() {
  active = false;
  if (exitTimer) { clearTimeout(exitTimer); exitTimer = null; }
  if (showing) { showing.timers.forEach(clearTimeout); showing = null; }
  ['fct-chip', 'fct-card-slot', 'fct-recap'].forEach(id => document.getElementById(id)?.remove());
  allPoints = {}; allFactChecks = {}; queue = []; recapOpen = false; showAll = true;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Status chip ────────────────────────────────────────────────────────────────

function injectChip() {
  const chip = document.createElement('div');
  chip.id = 'fct-chip';
  chip.innerHTML = `
    <span class="fct-ping">
      <span class="fct-ping-core"></span>
      <span class="fct-ping-ring"></span>
    </span>
    <span class="fct-chip-brand">vérif<span class="fct-chip-dot">.</span>live</span>
    <span class="fct-chip-sub">analyse en direct</span>
    <button class="fct-chip-recap" id="fct-recap-btn">Récap <span id="fct-count"></span></button>
    <button class="fct-chip-stop" id="fct-stop-btn" title="Arrêter la transcription">■</button>
  `;
  chip.querySelector('#fct-recap-btn').addEventListener('click', toggleRecap);
  chip.querySelector('#fct-stop-btn').addEventListener('click', () => {
    try { chrome.runtime.sendMessage({ action: 'stopCapture' }); } catch (_) {}
    teardown();
  });
  document.body.appendChild(chip);
}

function updateCount() {
  const el = document.getElementById('fct-count');
  if (!el) return;
  const n = Object.keys(allPoints).length;
  el.textContent = n > 0 ? `(${n})` : '';
}

// ── Card slot ──────────────────────────────────────────────────────────────────

function injectCardSlot() {
  if (document.getElementById('fct-card-slot')) return;
  const slot = document.createElement('div');
  slot.id = 'fct-card-slot';
  document.body.appendChild(slot);
}

// ── Queue management ───────────────────────────────────────────────────────────

function enqueue(point) {
  allPoints[point.id] = point;
  updateCount();
  if (queue.length >= MAX_QUEUE) queue.shift(); // drop oldest if backed up
  queue.push({ point, fc: null });
  tryNext();
}

function onFactCheck(data) {
  allFactChecks[data.id] = { verdict: data.verdict, explication: data.explication, source: data.source };
  // Resolve if currently showing this card
  if (showing?.point?.id === data.id) {
    resolveCard(showing.el, data.verdict, data.explication, data.source);
  } else {
    // Patch queue item so it shows resolved immediately
    const item = queue.find(q => q.point.id === data.id);
    if (item) item.fc = data;
  }
  if (recapOpen) renderRecap();
}

function tryNext() {
  // Si la carte affichée a été retirée du DOM (navigation SPA YouTube), libérer le slot
  if (showing && !showing.el.isConnected) {
    showing.timers.forEach(clearTimeout);
    showing = null;
  }
  if (showing || queue.length === 0 || !active) return;
  const item = queue.shift();
  showCard(item.point, item.fc);
}

// ── Card lifecycle ─────────────────────────────────────────────────────────────

function showCard(point, fc) {
  const slot = document.getElementById('fct-card-slot');
  if (!slot) return;

  const isAffirmation = point.type === 'affirmation';
  const typeCfg = TYPE_CFG[point.type] || { tag: String(point.type || '?').toUpperCase(), accent: PENDING_ACCENT };

  // Build card element
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
        <span class="fct-tag" id="fct-tag-pill">
          <span class="fct-tag-icon fct-spinner-icon"></span>
          <span class="fct-tag-text">${isAffirmation ? 'VÉRIFICATION' : typeCfg.tag}</span>
        </span>
      </div>
      <p class="fct-claim">« ${esc(point.texte)} »</p>
      <div class="fct-checking" id="fct-checking">
        <span class="fct-spinner"></span>
        <span>Recoupement des sources…</span>
      </div>
      <p class="fct-body" id="fct-body" style="display:none"></p>
      <div class="fct-footer" id="fct-footer" style="display:none">
        <span class="fct-source" id="fct-source"></span>
        <span class="fct-footer-right" id="fct-footer-right"></span>
      </div>
    </div>
  `;

  slot.innerHTML = '';
  slot.appendChild(el);

  const timers = [];
  showing = { el, point, timers };

  // Trigger enter animation
  requestAnimationFrame(() => el.classList.add('fct-card--in'));

  if (!isAffirmation) {
    // Non-affirmation: skip checking, show type directly
    el.style.setProperty('--accent', typeCfg.accent);
    el.querySelector('#fct-checking').style.display = 'none';
    scheduleExit(timers, CARD_HOLD_TALK_MS);
  } else if (fc) {
    // Already have fact-check result: resolve quickly
    timers.push(setTimeout(() => resolveCard(el, fc.verdict, fc.explication, fc.source), 400));
  } else {
    // Waiting for fact-check: show spinner, will resolve via onFactCheck().
    // Fallback: si le résultat n'arrive jamais, résoudre quand même pour ne pas
    // bloquer la file (showing resterait occupé pour toujours sinon).
    timers.push(setTimeout(() => {
      resolveCard(el, 'partiellement_vrai', 'Vérification indisponible.', '');
    }, FC_WAIT_MS));
  }
}

function resolveCard(el, verdict, explication, source) {
  if (!el || !el.parentNode) return;
  const cfg = VERDICT_CFG[verdict] || VERDICT_CFG.partiellement_vrai;

  // Transition accent color
  el.style.setProperty('--accent', cfg.accent);

  // Update tag pill
  const tagIcon = el.querySelector('.fct-tag-icon');
  const tagText = el.querySelector('.fct-tag-text');
  if (tagIcon) { tagIcon.className = 'fct-tag-icon fct-dot-icon'; }
  if (tagText) tagText.textContent = cfg.tag;

  // Hide spinner, show body + footer
  const checking = el.querySelector('#fct-checking');
  const body = el.querySelector('#fct-body');
  const footer = el.querySelector('#fct-footer');
  const footerRight = el.querySelector('#fct-footer-right');
  const sourceEl = el.querySelector('#fct-source');

  if (checking) checking.style.display = 'none';
  if (body) { body.textContent = explication; body.style.display = 'block'; body.classList.add('fct-risein'); }
  if (footer) { footer.style.display = 'flex'; footer.classList.add('fct-risein'); }
  if (footerRight) { footerRight.textContent = cfg.footerRight; footerRight.style.color = cfg.footerColor; }
  if (sourceEl) sourceEl.textContent = source || 'Mistral AI';

  // Schedule exit
  if (showing?.el === el) {
    scheduleExit(showing.timers, CARD_HOLD_MS);
  }
}

function scheduleExit(timers, delay) {
  timers.push(setTimeout(() => exitCard(), delay));
}

function exitCard() {
  if (!showing) return;
  const { el, timers } = showing;
  timers.forEach(clearTimeout);
  el.classList.remove('fct-card--in');
  el.classList.add('fct-card--out');
  showing = null;
  exitTimer = setTimeout(tryNext, CARD_EXIT_MS + CARD_GAP_MS);
}

// ── Recap panel ────────────────────────────────────────────────────────────────

function toggleRecap() { recapOpen ? closeRecap() : openRecap(); }

function openRecap() {
  recapOpen = true;
  document.getElementById('fct-recap-btn')?.classList.add('fct-chip-recap--active');

  const panel = document.createElement('div');
  panel.id = 'fct-recap';
  panel.innerHTML = `
    <div class="fct-recap-header">
      <span class="fct-recap-title">◆ vérif<span style="color:oklch(0.74 0.13 145)">.</span>live — Récapitulatif</span>
      <div style="display:flex;gap:6px">
        <button class="fct-recap-filter" id="fct-recap-filter">Tout voir</button>
        <button class="fct-recap-close" id="fct-recap-close">✕</button>
      </div>
    </div>
    <div class="fct-recap-body" id="fct-recap-body"></div>
  `;
  document.body.appendChild(panel);
  requestAnimationFrame(() => panel.classList.add('fct-recap--in'));

  document.getElementById('fct-recap-close').addEventListener('click', closeRecap);

  const filterBtn = document.getElementById('fct-recap-filter');
  filterBtn.textContent = showAll ? 'Affirmations' : 'Tout voir';
  filterBtn.addEventListener('click', () => {
    showAll = !showAll;
    filterBtn.textContent = showAll ? 'Affirmations' : 'Tout voir';
    renderRecap();
  });

  renderRecap();
}

function closeRecap() {
  recapOpen = false;
  document.getElementById('fct-recap-btn')?.classList.remove('fct-chip-recap--active');
  const panel = document.getElementById('fct-recap');
  if (!panel) return;
  panel.classList.remove('fct-recap--in');
  setTimeout(() => panel.remove(), 380);
}

function renderRecap() {
  const body = document.getElementById('fct-recap-body');
  if (!body) return;
  const pts = Object.values(allPoints);
  const visible = showAll ? pts : pts.filter(p => p.type === 'affirmation');

  if (visible.length === 0) {
    body.innerHTML = `<div class="fct-recap-empty"><div class="fct-dots"><span></span><span></span><span></span></div><p>En attente des premières analyses…</p></div>`;
    return;
  }

  body.innerHTML = visible.map(p => {
    // Un point malformé ne doit pas faire échouer le rendu de tous les autres
    try {
      const fc = allFactChecks[p.id];
      const vcfg = fc ? VERDICT_CFG[fc.verdict] : null;
      const tcfg = TYPE_CFG[p.type] || { tag: String(p.type || '?').toUpperCase(), accent: PENDING_ACCENT };
      const accent = vcfg ? vcfg.accent : tcfg.accent;
      return `
      <div class="fct-recap-card" style="--accent:${accent}">
        <div class="fct-recap-bar"></div>
        <div class="fct-recap-card-inner">
          <div class="fct-recap-row">
            <span class="fct-recap-badge">${p.type === 'affirmation' && vcfg ? vcfg.tag : tcfg.tag}</span>
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

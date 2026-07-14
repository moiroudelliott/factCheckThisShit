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
  vrai:               { tag: 'VRAI',         accent: 'oklch(0.74 0.13 145)', footerRight: '✓ confirmé',       footerColor: 'oklch(0.74 0.13 145)' },
  partiellement_vrai: { tag: 'PARTIEL',      accent: 'oklch(0.76 0.14 90)',  footerRight: '≈ nuancé',         footerColor: 'oklch(0.76 0.14 90)' },
  trompeur:           { tag: 'TROMPEUR',     accent: 'oklch(0.78 0.14 75)',  footerRight: '⚠ trompeur',       footerColor: 'oklch(0.78 0.14 75)' },
  faux:               { tag: 'FAUX',         accent: 'oklch(0.62 0.20 25)',  footerRight: '✗ démenti',        footerColor: 'oklch(0.72 0.16 28)' },
  non_verifiable:     { tag: 'NON VÉRIFIÉ',  accent: 'oklch(0.65 0.02 255)', footerRight: '? non vérifiable', footerColor: 'oklch(0.70 0.02 255)' },
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
  speakerMap: {},    // "Intervenant A" → nom réel confirmé par le backend
  badgeTimer: null,  // auto-masquage du badge "qui parle" pendant les silences
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
    if (msg.type === 'speaker_map')       onSpeakerMap(msg.map || {});
    if (msg.type === 'transcript_segment') onSegment(msg);
    if (msg.type === 'speaker_live')      onSegment(msg);
  } catch (e) {
    // Un message malformé ne doit jamais tuer le pipeline d'affichage
    console.error('[FCT] message handler error:', e);
  }
});

// ── Init / teardown ────────────────────────────────────────────────────────────

function initUI() {
  if (S.active) return;
  S.active = true;
  ['fct-chip', 'fct-card-slot', 'fct-recap', 'fct-speaker-badge'].forEach(id => document.getElementById(id)?.remove());
  injectChip();
  injectSlot();
  injectBadge();
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
  S.speakerMap = {};
  S.badgeTimer = null;
  S.recapOpen = false;
  S.showAll = true;
  ['fct-chip', 'fct-card-slot', 'fct-recap', 'fct-speaker-badge'].forEach(id => document.getElementById(id)?.remove());
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

// ── Badge "qui parle" (haut gauche) ────────────────────────────────────────────

function injectBadge() {
  const badge = document.createElement('div');
  badge.id = 'fct-speaker-badge';
  badge.innerHTML = `
    <span class="fct-eq"><span></span><span></span><span></span></span>
    <span class="fct-badge-name"></span>
  `;
  document.body.appendChild(badge);
  return badge;
}

function onSegment({ speaker }) {
  if (!S.active || !speaker) return;
  const badge = document.getElementById('fct-speaker-badge') || injectBadge();
  const nameEl = badge.querySelector('.fct-badge-name');
  const name = S.speakerMap[speaker] || speaker;

  badge.dataset.label = speaker; // label brut, pour le renommage via speaker_map
  if (nameEl.textContent !== name) {
    nameEl.textContent = name;
    nameEl.classList.remove('fct-risein');
    void nameEl.offsetWidth; // relance l'animation
    nameEl.classList.add('fct-risein');
  }
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
  document.body.appendChild(slot);
  return slot;
}

// ── Store ──────────────────────────────────────────────────────────────────────

function addPoint(point) {
  if (!point?.id || typeof point.texte !== 'string' || S.points.has(point.id)) return;
  // quiLabel = label diarisation d'origine, conservé pour pouvoir renommer
  // (ou corriger) rétroactivement quand le mapping évolue
  point.quiLabel = point.qui_label || point.qui || '';
  if (point.qui && S.speakerMap[point.qui]) point.qui = S.speakerMap[point.qui];
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

function onSpeakerMap(map) {
  // Le backend a identifié (ou corrigé) un locuteur : renommer rétroactivement
  // tous les points déjà reçus via leur label d'origine (quiLabel)
  S.speakerMap = { ...S.speakerMap, ...map };
  let changed = false;
  for (const { point } of S.points.values()) {
    const name = point.quiLabel && map[point.quiLabel];
    if (name && point.qui !== name) {
      point.qui = name;
      changed = true;
    }
  }
  if (S.current) {
    const p = S.points.get(S.current.id)?.point;
    const el = S.current.el.querySelector('.fct-speaker');
    if (p?.qui && el) el.textContent = p.qui;
  }
  // Badge "qui parle" : renommer immédiatement si son label vient d'être identifié
  const badge = document.getElementById('fct-speaker-badge');
  if (badge?.dataset.label && map[badge.dataset.label]) {
    badge.querySelector('.fct-badge-name').textContent = map[badge.dataset.label];
  }
  if (changed && S.recapOpen) renderRecap();
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
    };
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
      ${point.qui ? `<div class="fct-speaker">${esc(point.qui)}</div>` : ''}
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
      if (e && !e.fc) e.fc = { verdict: 'non_verifiable', explication: 'Vérification indisponible.', source: '', url: '' };
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
    const srcEl = footer.querySelector('.fct-source');
    if (fc.url) {
      // url validée http(s) dans onFactCheck — source cliquable vers la preuve
      srcEl.innerHTML = `<a href="${esc(fc.url)}" target="_blank" rel="noopener noreferrer">${esc(fc.source || 'source')} ↗</a>`;
    } else {
      srcEl.textContent = fc.source || 'analyse IA';
    }
    const right = footer.querySelector('.fct-footer-right');
    right.textContent = fc.confiance != null ? `${cfg.footerRight} · ${fc.confiance}%` : cfg.footerRight;
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

// ── Timestamps vidéo ───────────────────────────────────────────────────────────
// p.ts = horodatage unix (backend) du moment approximatif où le propos a été
// tenu. Position vidéo = position actuelle − temps écoulé depuis le propos,
// moins ~8 s de marge (latence chunk + buffer). Dérive si la vidéo est mise en
// pause entre-temps — acceptable pour retrouver un passage dans un débat.

function claimVideoTime(ts) {
  const video = document.querySelector('video');
  if (!video || !ts) return null;
  const t = video.currentTime - (Date.now() / 1000 - ts) - 8;
  return t >= 0 ? t : null;
}

function fmtTime(t) {
  t = Math.floor(t);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(s).padStart(2, '0')}`;
}

function seekTo(ts) {
  const video = document.querySelector('video');
  const t = claimVideoTime(ts);
  if (video && t != null) video.currentTime = t;
}

// ── Export Markdown ────────────────────────────────────────────────────────────

function exportRecap() {
  const vid = new URLSearchParams(location.search).get('v') || '';
  const link = (ts) => {
    const t = claimVideoTime(ts);
    return (t != null && vid) ? `https://www.youtube.com/watch?v=${vid}&t=${Math.floor(t)}s` : null;
  };

  const lines = [`# vérif.live — Récapitulatif (${new Date().toLocaleDateString('fr-FR')})`, ''];
  const affs = [], others = [];
  for (const entry of S.points.values()) {
    (entry.point.type === 'affirmation' ? affs : others).push(entry);
  }

  if (affs.length) {
    lines.push('## Affirmations vérifiées', '');
    for (const { point: p, fc } of affs) {
      const verdict = fc
        ? (VERDICT_CFG[fc.verdict]?.tag || fc.verdict) + (fc.confiance != null ? ` ${fc.confiance}%` : '')
        : 'EN ATTENTE';
      let line = `- **[${verdict}]**${p.qui ? ` ${p.qui} —` : ''} « ${p.texte} »`;
      if (fc?.explication) line += ` — ${fc.explication}`;
      if (fc?.source) line += fc.url ? ` *(source : [${fc.source}](${fc.url}))*` : ` *(source : ${fc.source})*`;
      const l = link(p.ts);
      if (l) line += ` — [▶ voir](${l})`;
      lines.push(line);
    }
    lines.push('');
  }
  if (others.length) {
    lines.push('## Autres points', '');
    for (const { point: p } of others) {
      const tag = TYPE_CFG[p.type]?.tag || String(p.type || '?').toUpperCase();
      const l = link(p.ts);
      lines.push(`- **[${tag}]**${p.qui ? ` ${p.qui} —` : ''} « ${p.texte} »${l ? ` — [▶ voir](${l})` : ''}`);
    }
  }

  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `verif-live_${new Date().toISOString().slice(0, 10)}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
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
        <button class="fct-recap-filter" id="fct-recap-export" title="Exporter en Markdown">⬇</button>
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
  panel.querySelector('#fct-recap-export').addEventListener('click', exportRecap);

  // Délégation : les chips "▶ mm:ss" sont recréées à chaque rendu, le listener
  // vit sur le conteneur et survit aux innerHTML
  panel.querySelector('#fct-recap-body').addEventListener('click', (e) => {
    const btn = e.target.closest('.fct-recap-ts');
    if (btn) seekTo(Number(btn.dataset.ts));
  });

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
  const counts = { vrai: 0, partiellement_vrai: 0, trompeur: 0, faux: 0, non_verifiable: 0 };
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
      const vt = claimVideoTime(p.ts);
      const tsChip = vt != null ? `<button class="fct-recap-ts" data-ts="${Number(p.ts)}" title="Revoir ce passage">▶ ${fmtTime(vt)}</button>` : '';
      const srcHtml = fc?.url
        ? `<a class="fct-recap-src" href="${esc(fc.url)}" target="_blank" rel="noopener noreferrer">${esc(fc.source || 'source')} ↗</a>`
        : (fc?.source ? `<span class="fct-recap-src">${esc(fc.source)}</span>` : '<span></span>');
      return `
      <div class="fct-recap-card" style="--accent:${accent}">
        <div class="fct-recap-bar"></div>
        <div class="fct-recap-card-inner">
          <div class="fct-recap-row">
            <span class="fct-recap-badge">${esc(p.type === 'affirmation' && vcfg ? vcfg.tag : tcfg.tag)}</span>
            ${p.qui ? `<span class="fct-recap-speaker">${esc(p.qui)}</span>` : ''}
            ${p.type === 'affirmation' && !vcfg ? '<span class="fct-recap-pending">⏳ vérification…</span>' : ''}
            ${tsChip}
          </div>
          <p class="fct-recap-claim">« ${esc(p.texte)} »</p>
          ${fc?.explication ? `<p class="fct-recap-explanation">${esc(fc.explication)}</p>` : ''}
          ${vcfg ? `<div class="fct-recap-footer">${srcHtml}<span class="fct-recap-footer-right" style="color:${vcfg.footerColor}">${vcfg.footerRight}${fc?.confiance != null ? ` · ${fc.confiance}%` : ''}</span></div>` : ''}
        </div>
      </div>`;
    } catch (e) {
      console.error('[FCT] renderRecap item error:', e, p);
      return '';
    }
  }).join('');
}

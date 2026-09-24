// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — relecture d'une session enregistrée
//
// Rejoue, par-dessus le lecteur YouTube intégré, les messages que le backend
// a envoyés pendant une analyse en direct, chacun à la position vidéo où il
// est arrivé (enregistrés par l'extension, publiés par publish_session.py).
// L'affichage est celui de l'extension elle-même : overlay/content.js,
// copie synchronisée de extension/content.js, reçoit les messages par un
// faux chrome.runtime. Rien n'est recalculé : ni serveur, ni GPU.
//
// Chargé AVANT overlay/content.js (le faux chrome.* doit exister quand il
// s'exécute) ; la page démarre au DOMContentLoaded, une fois content.js prêt.
// ══════════════════════════════════════════════════════════════════════════════

(function () {
  'use strict';

  // ── Faux chrome.* : ce que content.js attend de l'extension ──
  const listeners = [];
  window.chrome = {
    runtime: {
      onMessage: { addListener: (fn) => listeners.push(fn) },
      sendMessage: async (m) => (m && m.action === 'contentReady' ? { captured: false } : { ok: false }),
      getURL: (path) => path, // fonts/… → site/fonts/
    },
    storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } },
  };
  // Copie à chaque envoi : content.js modifie les points qu'il reçoit, et une
  // relecture recommencée doit repartir de la session d'origine
  const send = (msg) => {
    const copy = JSON.parse(JSON.stringify(msg));
    listeners.forEach((fn) => fn(copy, {}, () => {}));
  };

  const SPEAKER_TYPES = new Set(['speaker_live', 'transcript_segment']);
  const BACKLOG_S = 20;   // après un saut en avant, un point passé depuis plus longtemps va au récap sans carte
  const JUMP_S = 3;       // écart entre deux relevés au-delà duquel on considère un saut
  const TICK_MS = 250;
  const PLAYER_TIMEOUT_MS = 10000; // lecteur jamais prêt au-delà : bloqué, on le dit
  const ID_RE = /^[\w-]{11}$/;

  const $ = (id) => document.getElementById(id);
  let session = null;     // { video, started, events }
  let events = [];        // messages hors « qui parle », triés par position
  let speakers = [];      // messages « qui parle », triés par position
  let claims = [];        // [{ id, vt, texte, verdict, at }] — pour la frise
  let player = null;
  let idx = 0, spkIdx = 0, lastCur = 0, ended = false, overlayOn = false, tickTimer = null;
  let recordedEnd = false; // la session contient l'arrêt réel de l'analyse

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    $('restart').addEventListener('click', restart);
    $('fullscreen').addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else $('stage').requestFullscreen?.().catch(() => {});
    });
    renderLegend();
    // Fichier ouvert directement (double-clic) : le navigateur interdit de
    // lire les sessions, et le lecteur YouTube refuse l'origine « file »
    if (location.protocol === 'file:') {
      setFacade('Ouvre cette page via un serveur local', false,
        'Dans le dossier du projet : python -m http.server 4173 --directory site, puis http://localhost:4173/relecture.html');
      return;
    }
    let index;
    try {
      index = await fetch('sessions/index.json', { cache: 'no-cache' }).then((r) => r.json());
    } catch (_) {
      index = { sessions: [] };
    }
    const list = (index.sessions || []).filter((s) => ID_RE.test(s.id || ''));
    if (!list.length) {
      setFacade('Aucune relecture publiée pour le moment.', false);
      return;
    }
    const wanted = new URLSearchParams(location.search).get('s');
    const current = list.find((s) => s.id === wanted) || list[list.length - 1];
    renderPicker(list, current);
    try {
      // no-cache : une session régénérée sous le même identifiant remplace l'ancienne
      session = await fetch(`sessions/${current.id}.json`, { cache: 'no-cache' }).then((r) => r.json());
    } catch (_) {
      setFacade('Impossible de charger cette relecture.', false);
      return;
    }
    prepare(session);
    $('session-title').textContent = session.video.title || current.title || '';
    $('session-meta').textContent = describe(current);
    setFacade('Lancer la relecture', true);
  }

  function describe(s) {
    const parts = [];
    if (s.duration) parts.push(`${Math.round(s.duration / 60)} min`);
    if (s.affirmations) parts.push(`${s.affirmations} affirmation${s.affirmations > 1 ? 's' : ''} vérifiable${s.affirmations > 1 ? 's' : ''}`);
    if (s.date) parts.push(`analysée le ${new Date(s.date).toLocaleDateString('fr-FR')}`);
    return parts.join(' · ');
  }

  function renderPicker(list, current) {
    if (list.length < 2) return;
    const sel = $('picker');
    for (const s of list) {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.title || s.id;
      opt.selected = s === current;
      sel.appendChild(opt);
    }
    sel.hidden = false;
    sel.addEventListener('change', () => { location.search = `?s=${encodeURIComponent(sel.value)}`; });
  }

  function prepare(s) {
    const all = (s.events || []).filter((e) => Number.isFinite(e.t) && e.m && (e.m.type || e.m.action));
    events = all.filter((e) => !SPEAKER_TYPES.has(e.m.type));
    speakers = all.filter((e) => SPEAKER_TYPES.has(e.m.type));
    const byId = new Map();
    for (const e of events) {
      if (e.m.type === 'talking_points') {
        for (const p of e.m.points || []) {
          if (p.type === 'affirmation' && Number.isFinite(p.vt) && !byId.has(p.id)) {
            byId.set(p.id, { id: p.id, vt: p.vt, texte: p.texte, verdict: null, at: Infinity });
          }
        }
      } else if (e.m.type === 'fact_check_result' && byId.has(e.m.id) && !e.m.indisponible) {
        Object.assign(byId.get(e.m.id), { verdict: e.m.verdict, at: e.t });
      }
    }
    claims = [...byId.values()];
    recordedEnd = events.some((e) => e.m.type === 'session_done');
  }

  // ── Lecteur YouTube : chargé seulement au clic (aucun service tiers avant) ──

  const DEFAULT_NOTE = "Le lecteur YouTube (youtube-nocookie.com) n'est chargé qu'à ce moment-là.";

  function setFacade(text, clickable, note = clickable ? DEFAULT_NOTE : '', onClick = loadPlayer) {
    const btn = $('facade');
    btn.hidden = false;
    btn.querySelector('.facade-label').textContent = text;
    btn.disabled = !clickable;
    btn.querySelector('.facade-note').textContent = note;
    btn.querySelector('.facade-note').hidden = !note;
    if (clickable) btn.addEventListener('click', onClick, { once: true });
  }

  // Le lecteur ne se charge pas : dire pourquoi plutôt que « Chargement… » sans fin
  function playerFailed(reason) {
    if (reason === 'embed') {
      setFacade("Cette vidéo ne peut pas être lue ici", false,
        "La chaîne n'autorise pas la lecture intégrée. La relecture reste possible sur YouTube, sans les cartes.");
      return;
    }
    setFacade('Le lecteur YouTube ne répond pas — cliquer pour réessayer', true,
      "Un bloqueur de publicités ou de traqueurs (uBlock, Ghostery, Brave…) bloque peut-être YouTube : "
      + 'autorise youtube.com et youtube-nocookie.com pour cette page.', () => location.reload());
  }

  function loadPlayer() {
    $('facade').querySelector('.facade-label').textContent = 'Chargement du lecteur…';
    let ready = false;
    const timer = setTimeout(() => { if (!ready) playerFailed('timeout'); }, PLAYER_TIMEOUT_MS);
    window.onYouTubeIframeAPIReady = () => {
      player = new YT.Player('player', {
        host: 'https://www.youtube-nocookie.com',
        videoId: session.video.youtube,
        // origin : sans elle, avec l'hôte « nocookie », certains navigateurs
        // ne signalent jamais que le lecteur est prêt
        playerVars: { rel: 0, playsinline: 1, fs: 0, modestbranding: 1, autoplay: 1, origin: location.origin },
        events: {
          onReady: (e) => { ready = true; clearTimeout(timer); onReady(e); },
          onStateChange,
          // 101 / 150 : lecture intégrée refusée par la chaîne
          onError: (e) => { if (e.data === 101 || e.data === 150) { clearTimeout(timer); playerFailed('embed'); } },
        },
      });
    };
    const script = document.createElement('script');
    script.src = 'https://www.youtube.com/iframe_api';
    script.onerror = () => { clearTimeout(timer); playerFailed('script'); };
    document.head.appendChild(script);
  }

  function onReady() {
    $('facade').hidden = true;
    $('controls').hidden = false;
    // La vidéo est dans une iframe : content.js lit sa position par cet objet
    window.__fctVideo = {
      get currentTime() { return player.getCurrentTime() || 0; },
      set currentTime(t) { player.seekTo(t, true); },
      get playbackRate() { return player.getPlaybackRate() || 1; },
      get paused() { return player.getPlayerState() !== YT.PlayerState.PLAYING; },
      get ended() { return player.getPlayerState() === YT.PlayerState.ENDED; },
      muted: false,
      volume: 1,
      get duration() { return player.getDuration() || 0; },
      get seekable() { const d = player.getDuration() || 0; return { length: 1, start: () => 0, end: () => d }; },
    };
    start();
    player.playVideo();
  }

  function onStateChange(e) {
    if (e.data === YT.PlayerState.ENDED && !ended) {
      tick();
      ended = true;
      // Analyse menée jusqu'au bout de la vidéo sans arrêt enregistré : la
      // clore comme l'aurait fait le backend
      if (!recordedEnd && overlayOn) {
        send({ type: 'finalizing' });
        send({ type: 'session_done', complete: true });
      }
    }
  }

  // ── Relecture ──

  function start() {
    idx = 0;
    spkIdx = 0;
    lastCur = 0;
    ended = false;
    shown = -1;
    overlayOn = false;
    // L'overlay n'existe qu'à partir du moment où l'analyse a été lancée
    if (typeof teardown === 'function') teardown();
    clearInterval(tickTimer);
    tickTimer = setInterval(tick, TICK_MS);
    renderTimeline(0);
  }

  function restart() {
    start();
    player.seekTo(0, true);
    player.playVideo();
  }

  function firstAfter(list, t) {
    let lo = 0, hi = list.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (list[mid].t <= t) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  function tick() {
    if (!player || typeof player.getCurrentTime !== 'function') return;
    const cur = player.getCurrentTime() || 0;
    // Avant la position où l'analyse a démarré : pas encore d'overlay
    if (!overlayOn) {
      if (cur + 0.05 < (session.started || 0)) {
        lastCur = cur;
        renderTimeline(cur);
        return;
      }
      send({ action: 'showOverlay' });
      send({ type: 'connection_status', status: 'connected' });
      overlayOn = true;
    }
    const jumped = cur < lastCur - 0.5 || cur > lastCur + JUMP_S;
    // « Qui parle » : seulement en lecture continue (y compris en revoyant un
    // passage) — après un saut, on reprend à la nouvelle position
    if (jumped) {
      spkIdx = firstAfter(speakers, cur);
    } else {
      while (spkIdx < speakers.length && speakers[spkIdx].t <= cur) send(speakers[spkIdx++].m);
    }
    // Le reste une seule fois, dans l'ordre : revenir en arrière ne rejoue
    // rien, sauter en avant range au récap ce qui est passé depuis longtemps
    if (!ended) {
      while (idx < events.length && events[idx].t <= cur) {
        const e = events[idx++];
        send(e.m.type === 'talking_points' && cur - e.t > BACKLOG_S ? { ...e.m, backlog: true } : e.m);
      }
    }
    lastCur = cur;
    renderTimeline(cur);
  }

  // ── Frise : un repère par affirmation, révélé quand son verdict est tombé ──

  let shown = -1;

  function renderTimeline(cur) {
    const duration = (player && player.getDuration && player.getDuration()) || (events.length ? events[events.length - 1].t : 0);
    const bar = $('timeline');
    if (!duration) return;
    $('playhead').style.left = `${Math.min(100, (cur / duration) * 100)}%`;
    const reached = idx ? events[idx - 1].t : -1;
    const visible = claims.filter((c) => c.at <= reached);
    if (visible.length === shown) return;
    shown = visible.length;
    bar.querySelectorAll('.marker').forEach((m) => m.remove());
    for (const c of visible) {
      const cfg = (typeof VERDICT_CFG !== 'undefined' && VERDICT_CFG[c.verdict]) || null;
      const m = document.createElement('button');
      m.className = 'marker';
      m.style.left = `${Math.min(100, (c.vt / duration) * 100)}%`;
      m.style.background = cfg ? cfg.accent : '#888';
      m.title = `${cfg ? cfg.tag : ''} — « ${c.texte} »`;
      m.setAttribute('aria-label', `Revoir : ${m.title}`);
      m.addEventListener('click', () => player.seekTo(Math.max(0, c.vt - 2), true));
      bar.appendChild(m);
    }
    const total = claims.length;
    $('progress').textContent = total ? `${shown} verdict${shown > 1 ? 's' : ''} sur ${total} affirmation${total > 1 ? 's' : ''}` : '';
  }

  function renderLegend() {
    if (typeof VERDICT_CFG === 'undefined') return;
    const labels = { vrai: 'vrai', partiellement_vrai: 'partiellement vrai', trompeur: 'trompeur', faux: 'faux', non_verifiable: 'non vérifiable' };
    $('legend').innerHTML = Object.entries(VERDICT_CFG)
      .map(([k, c]) => `<span><i style="background:${c.accent}"></i>${labels[k] || k}</span>`)
      .join('');
  }
})();

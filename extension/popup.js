// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — popup
// À l'ouverture : lit l'état réel de capture (storage.session via le SW) puis
// health-check le backend. Toute erreur est affichée, jamais silencieuse.
// Les champs saisis (et les intervenants détectés) sont gardés par vidéo :
// la popup se ferme au moindre clic ailleurs, et chaque réouverture
// relançait un appel Mistral payant pour les redétecter.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';
const DRAFT_KEY = 'popupDraft'; // chrome.storage.session : { [videoId]: { emission, guests, detected } }

const $ = (id) => document.getElementById(id);

let videoMeta = null;  // { title, channel, desc, publishDate } — détecté sur la vidéo en cours
let videoId = null;
let capturing = false;

init();

// Identifiant de ce qu'on analyse (même règle que background.js) : la vidéo
// YouTube, sinon la page (origine + chemin) ; null si rien d'analysable
function videoIdFromUrl(url) {
  try {
    const u = new URL(url);
    if (/(^|\.)youtube\.com$/.test(u.hostname)) {
      if (u.pathname === '/watch') return u.searchParams.get('v');
      const m = u.pathname.match(/^\/live\/([\w-]{6,})/);
      return m ? m[1] : null;
    }
    return /^https?:$/.test(u.protocol) ? u.origin + u.pathname : null;
  } catch (_) {
    return null;
  }
}

async function loadDraft() {
  const all = (await chrome.storage.session.get(DRAFT_KEY).catch(() => ({})))[DRAFT_KEY] || {};
  return videoId ? all[videoId] || null : null;
}

async function saveDraft(extra = {}) {
  if (!videoId) return;
  const all = (await chrome.storage.session.get(DRAFT_KEY).catch(() => ({})))[DRAFT_KEY] || {};
  all[videoId] = { ...(all[videoId] || {}), emission: $('emission').value, guests: $('guests').value, ...extra };
  // Quelques vidéos récentes suffisent
  const keys = Object.keys(all);
  for (const k of keys.slice(0, Math.max(0, keys.length - 10))) delete all[k];
  chrome.storage.session.set({ [DRAFT_KEY]: all }).catch(() => {});
}

async function init() {
  const { fctToken } = await chrome.storage.local.get('fctToken').catch(() => ({}));
  if (fctToken) {
    $('token').value = fctToken;
    toggleAdvanced(true);
  }
  $('advanced-toggle').addEventListener('click', () => toggleAdvanced($('advanced-panel').style.display === 'none'));
  $('token').addEventListener('change', () => {
    chrome.storage.local.set({ fctToken: $('token').value.trim() });
  });
  $('emission').addEventListener('input', () => saveDraft());
  $('guests').addEventListener('input', () => saveDraft());
  $('retry').addEventListener('click', () => { $('warn').style.display = 'none'; prepare(); });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  videoId = videoIdFromUrl(tab?.url || '');

  const state = await chrome.runtime.sendMessage({ action: 'getStatus' }).catch(() => null);
  setCapturing(Boolean(state?.capturing));
  if (state?.capturing) {
    // Analyse en cours sur un AUTRE onglet : le dire (le bouton Arrêter
    // arrête celle-là), et proposer d'y aller
    if (state.tabId && state.tabId !== tab?.id) {
      setStatus('en direct — autre onglet');
      $('other-tab').style.display = 'block';
      $('goto-tab').addEventListener('click', async () => {
        const t = await chrome.tabs.update(state.tabId, { active: true }).catch(() => null);
        if (t?.windowId) chrome.windows.update(t.windowId, { focused: true }).catch(() => {});
        window.close();
      });
    }
    return;
  }

  // Champs déjà saisis pour cette vidéo (popup rouverte)
  const draft = await loadDraft();
  if (draft) {
    $('emission').value = draft.emission || '';
    $('guests').value = draft.guests || '';
  }

  // Détection de la vidéo en cours : préremplit l'émission, la description
  // partira au backend pour que Mistral identifie le contexte/les intervenants
  if (videoId) {
    videoMeta = await detectVideo(tab.id);
    if (videoMeta?.title && !$('emission').value) {
      $('emission').value = videoMeta.channel
        ? `${videoMeta.title} — ${videoMeta.channel}`
        : videoMeta.title;
      saveDraft();
    }
  }
  prepare(draft);
}

// Health-check du backend puis détection automatique des intervenants —
// rejouable via « réessaie » si le backend était éteint
async function prepare(draft = null) {
  setStatus('connexion au backend…');
  const ok = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(2500) })
    .then(r => r.ok)
    .catch(() => false);
  if (capturing) return;
  if (!ok) {
    $('warn').style.display = 'block';
    $('btn-start').disabled = true;
    setStatus('backend éteint');
    return;
  }
  $('btn-start').disabled = false;
  setStatus('inactif');

  // Détection automatique des intervenants (Mistral via le backend) — une
  // seule fois par vidéo, pas à chaque ouverture de la popup
  if (videoMeta?.title && !$('guests').value.trim() && !draft?.detected) {
    setStatus('détection des invités…');
    try {
      const token = $('token').value.trim();
      const r = await fetch(`${BACKEND_URL}/analyze_video`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'X-Backend-Token': token } : {}),
        },
        body: JSON.stringify({
          title: videoMeta.title,
          channel: videoMeta.channel,
          description: videoMeta.desc,
          publishDate: videoMeta.publishDate,
        }),
        signal: AbortSignal.timeout(15000),
      });
      const d = await r.json();
      // Analyse déjà démarrée entre-temps : ne pas toucher au formulaire
      if (!capturing && Array.isArray(d.guests) && d.guests.length && !$('guests').value.trim()) {
        $('guests').value = d.guests.join('\n');
      }
      saveDraft({ detected: true });
    } catch (e) {
      console.warn('[FCT] analyze_video:', e);
    }
    // Ne jamais écraser « en direct » si l'analyse a démarré pendant la détection
    if (!capturing) setStatus('inactif');
  }
}

async function detectVideo(tabId) {
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN', // accès aux variables de la page (ytInitialPlayerResponse)
      func: () => {
        // Autres sites (replay, direct d'une chaîne) : balises Open Graph et
        // meta standard, présentes sur la quasi-totalité des pages vidéo
        if (!/(^|\.)youtube\.com$/.test(location.hostname)) {
          const meta = (...names) => {
            for (const n of names) {
              const v = document.querySelector(`meta[property="${n}"], meta[name="${n}"]`)?.content;
              if (v && v.trim()) return v.trim();
            }
            return '';
          };
          const date = meta('article:published_time', 'video:release_date', 'og:updated_time', 'date');
          return {
            title: meta('og:title', 'twitter:title') || document.title.trim(),
            channel: meta('og:site_name', 'application-name') || location.hostname.replace(/^www\./, ''),
            desc: meta('og:description', 'description', 'twitter:description').slice(0, 1200),
            publishDate: /^\d{4}-\d{2}-\d{2}/.test(date) ? date.slice(0, 10) : '',
          };
        }
        const params = new URLSearchParams(location.search);
        const vid = params.get('v') || (location.pathname.match(/^\/live\/([\w-]{6,})/) || [])[1];
        const pr = window.ytInitialPlayerResponse;
        const d = pr?.videoDetails;
        const mf = pr?.microformat?.playerMicroformatRenderer;
        // ytInitialPlayerResponse peut être périmé après une navigation SPA :
        // on ne s'y fie que si son videoId correspond à l'URL courante
        if (d && d.videoId === vid) {
          return {
            title: d.title || '',
            channel: d.author || '',
            desc: (d.shortDescription || '').slice(0, 1200),
            publishDate: String(mf?.publishDate || mf?.uploadDate || '').slice(0, 10),
          };
        }
        // Repli : re-télécharger la page de la vidéo (même origine, rapide) et
        // en extraire les métadonnées fraîches — le DOM SPA est incomplet
        const domFallback = () => ({
          title: (document.querySelector('h1.ytd-watch-metadata')?.textContent
                  || document.title.replace(/ - YouTube$/, '')).trim(),
          channel: (document.querySelector('ytd-channel-name a')?.textContent || '').trim(),
          desc: '',
          publishDate: '',
        });
        const unesc = (s) => { try { return JSON.parse('"' + s + '"'); } catch (_) { return s; } };
        return fetch(location.href, { credentials: 'same-origin' })
          .then(r => r.text())
          .then(html => {
            const t = html.match(/"videoDetails":\{"videoId":"[^"]+","title":"((?:[^"\\]|\\.)*)"/);
            const a = html.match(/"author":"((?:[^"\\]|\\.)*)"/);
            const de = html.match(/"shortDescription":"((?:[^"\\]|\\.)*)"/);
            const p = html.match(/"publishDate":"(\d{4}-\d{2}-\d{2})/);
            const dom = domFallback();
            return {
              title: t ? unesc(t[1]) : dom.title,
              channel: a ? unesc(a[1]) : dom.channel,
              desc: de ? unesc(de[1]).slice(0, 1200) : '',
              publishDate: p ? p[1] : '',
            };
          })
          .catch(domFallback);
      },
    });
    return res?.result || null;
  } catch (e) {
    console.warn('[FCT] detectVideo:', e);
    return null;
  }
}

$('btn-start').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (!videoIdFromUrl(tab?.url || '')) {
    setStatus('ouvre la page de la vidéo');
    return;
  }

  $('btn-start').disabled = true;
  setStatus('démarrage…');

  const res = await chrome.runtime.sendMessage({
    action: 'startCapture',
    tabId: tab.id,
    emission: $('emission').value.trim(),
    guests: $('guests').value.trim(),
    description: videoMeta?.desc || '',
    videoDate: videoMeta?.publishDate || '',
    token: $('token').value.trim(),
  }).catch(e => ({ ok: false, error: e.message }));

  $('btn-start').disabled = false;
  if (res?.ok) {
    setCapturing(true);
  } else {
    setStatus('erreur au démarrage');
    console.error('[FCT] startCapture:', res?.error);
  }
});

$('btn-stop').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ action: 'stopCapture' }).catch(() => {});
  setCapturing(false);
  $('other-tab').style.display = 'none';
});

function toggleAdvanced(open) {
  $('advanced-panel').style.display = open ? 'block' : 'none';
  $('advanced-toggle').textContent = open ? 'Avancé ▴' : 'Avancé ▾';
  $('advanced-toggle').setAttribute('aria-expanded', String(open));
}

function setCapturing(on) {
  capturing = on;
  $('btn-start').style.display = on ? 'none' : 'block';
  $('btn-stop').style.display  = on ? 'block' : 'none';
  $('briefing-section').style.display = on ? 'none' : 'block';
  $('fct-ping').classList.toggle('active', on);
  $('status-text').classList.toggle('active', on);
  setStatus(on ? 'en direct' : 'inactif');
}

function setStatus(text) {
  $('status-text').textContent = text;
  $('status-text').title = text;
}

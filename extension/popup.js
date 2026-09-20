// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — popup (v2)
// À l'ouverture : lit l'état réel de capture (storage.session via le SW) puis
// health-check le backend. Toute erreur est affichée, jamais silencieuse.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';

const $ = (id) => document.getElementById(id);

let videoMeta = null; // { title, channel, desc } — détecté sur la vidéo en cours

init();

async function init() {
  const { fctToken } = await chrome.storage.local.get('fctToken').catch(() => ({}));
  if (fctToken) {
    $('token').value = fctToken;
    $('advanced-panel').style.display = 'block';
    $('advanced-toggle').textContent = 'Avancé ▴';
  }
  $('advanced-toggle').addEventListener('click', () => {
    const open = $('advanced-panel').style.display !== 'none';
    $('advanced-panel').style.display = open ? 'none' : 'block';
    $('advanced-toggle').textContent = open ? 'Avancé ▾' : 'Avancé ▴';
  });
  $('token').addEventListener('change', () => {
    chrome.storage.local.set({ fctToken: $('token').value.trim() });
  });

  const state = await chrome.runtime.sendMessage({ action: 'getStatus' }).catch(() => null);
  setCapturing(Boolean(state?.capturing));
  if (state?.capturing) return;

  // Détection de la vidéo en cours : préremplit l'émission, la description
  // partira au backend pour que Mistral identifie le contexte/les intervenants
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.url?.includes('youtube.com/watch')) {
    videoMeta = await detectVideo(tab.id);
    if (videoMeta?.title && !$('emission').value) {
      $('emission').value = videoMeta.channel
        ? `${videoMeta.title} — ${videoMeta.channel}`
        : videoMeta.title;
    }
  }

  // Health-check : désactive le bouton Start si le backend est éteint
  const ok = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(2500) })
    .then(r => r.ok)
    .catch(() => false);
  if (!ok) {
    $('warn').style.display = 'block';
    $('btn-start').disabled = true;
    setStatus('backend éteint');
    return;
  }

  // Détection automatique des intervenants (Mistral via le backend)
  if (videoMeta?.title && !$('guests').value.trim()) {
    setStatus('détection des intervenants…');
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
        signal: AbortSignal.timeout(10000),
      });
      const d = await r.json();
      if (Array.isArray(d.guests) && d.guests.length && !$('guests').value.trim()) {
        $('guests').value = d.guests.join('\n');
      }
    } catch (e) {
      console.warn('[FCT] analyze_video:', e);
    }
    setStatus('inactif');
  }
}

async function detectVideo(tabId) {
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN', // accès aux variables de la page (ytInitialPlayerResponse)
      func: () => {
        const vid = new URLSearchParams(location.search).get('v');
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

  if (!tab?.url?.includes('youtube.com/watch')) {
    setStatus('ouvre une vidéo YouTube');
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
});

function setCapturing(on) {
  $('btn-start').style.display = on ? 'none' : 'block';
  $('btn-stop').style.display  = on ? 'block' : 'none';
  $('briefing-section').style.display = on ? 'none' : 'block';
  $('fct-ping').classList.toggle('active', on);
  $('status-text').classList.toggle('active', on);
  setStatus(on ? 'en direct' : 'inactif');
}

function setStatus(text) {
  $('status-text').textContent = text;
}

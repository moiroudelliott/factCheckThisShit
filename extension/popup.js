// ══════════════════════════════════════════════════════════════════════════════
// vérif.live — popup (v2)
// À l'ouverture : lit l'état réel de capture (storage.session via le SW) puis
// health-check le backend. Toute erreur est affichée, jamais silencieuse.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';

const $ = (id) => document.getElementById(id);

init();

async function init() {
  const state = await chrome.runtime.sendMessage({ action: 'getStatus' }).catch(() => null);
  setCapturing(Boolean(state?.capturing));
  if (state?.capturing) return;

  // Health-check : désactive le bouton Start si le backend est éteint
  const ok = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(2500) })
    .then(r => r.ok)
    .catch(() => false);
  if (!ok) {
    $('warn').style.display = 'block';
    $('btn-start').disabled = true;
    setStatus('backend éteint');
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

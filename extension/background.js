// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — service worker
// AUCUN état en mémoire : Chrome tue le worker après ~30 s d'inactivité, donc
// tout vit dans chrome.storage.session. Chaque handler relit l'état.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';

function getState() {
  return chrome.storage.session.get({ tabId: null, capturing: false, videoId: null });
}

// Identifiant de ce qu'on analyse : la vidéo YouTube (watch?v=…, /live/…),
// sinon la page (origine + chemin) — ou null (page YouTube sans vidéo, URL
// inconnue). Même règle que currentVideoId() du content script.
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

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg.action) {
        case 'startCapture':
          await handleStart(msg);
          sendResponse({ ok: true });
          break;

        case 'stopCapture':
          await handleStop('user');
          sendResponse({ ok: true });
          break;

        case 'getStatus':
          sendResponse(await getState());
          break;

        // Content script (re)chargé : l'onglet est-il celui qu'on analyse ?
        // Après un F5 pendant l'analyse, la capture continue — l'overlay doit
        // revenir au lieu de laisser la capture tourner sans rien afficher.
        case 'contentReady': {
          const { tabId, capturing } = await getState();
          sendResponse({ captured: Boolean(capturing && sender.tab && sender.tab.id === tabId) });
          break;
        }

        // Pub YouTube en cours (détectée par le content script) → l'offscreen
        // n'envoie pas l'audio de la pub au backend
        case 'adState':
          chrome.runtime.sendMessage({ action: 'setAdState', ad: Boolean(msg.ad) }).catch(() => {});
          break;

        // Bouton ⚑ d'une carte : le content script (origine de la page) ne peut
        // pas appeler le backend, qui n'accepte que l'origine de l'extension
        case 'reportVerdict': {
          const { fctToken } = await chrome.storage.local.get('fctToken');
          const r = await fetch(`${BACKEND_URL}/report_verdict`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(fctToken ? { 'X-Backend-Token': fctToken } : {}) },
            body: JSON.stringify(msg.report || {}),
            signal: AbortSignal.timeout(8000),
          }).catch(() => null);
          sendResponse({ ok: Boolean(r?.ok) });
          break;
        }

        // getUserMedia a échoué dans l'offscreen : ne pas rester « en direct »
        case 'captureFailed':
          await chrome.storage.session.set({ tabId: null, capturing: false, videoId: null });
          await closeOffscreen();
          break;

        // L'offscreen a fini sa dernière analyse (session_done) — on peut le
        // fermer, sauf si une nouvelle capture a démarré entre-temps
        case 'offscreenDone':
          if (!(await getState()).capturing) await closeOffscreen();
          break;

        case 'forwardToContent': {
          // offscreen → content : le tabId voyage dans le message, avec le
          // storage en secours (survit aux redémarrages du worker)
          const tid = msg.tabId ?? (await getState()).tabId;
          if (tid) chrome.tabs.sendMessage(tid, msg.payload).catch(() => {});
          break;
        }
      }
    } catch (e) {
      console.error('[FCT] background error:', e);
      try { sendResponse({ ok: false, error: e.message }); } catch (_) {}
    }
  })();
  return true; // sendResponse asynchrone
});

// Arrêt automatique si l'onglet capturé est fermé
chrome.tabs.onRemoved.addListener(async (closedTabId) => {
  const { tabId, capturing } = await getState();
  if (capturing && closedTabId === tabId) handleStop('tab_closed');
});

// Arrêt automatique si l'onglet capturé change de vidéo (navigation SPA de
// YouTube, autre page du site) ou quitte le site : l'analyse est liée à UNE
// vidéo (invités, date, horodatages), et l'audio d'un autre site n'a pas à
// partir au backend. Un rechargement de la même vidéo (F5) ne coupe rien —
// sauf sur un site hors manifest, où la permission (activeTab) tombe au
// rechargement.
chrome.tabs.onUpdated.addListener(async (tid, info, tab) => {
  if (!info.url && info.status !== 'loading') return;
  const { tabId, capturing, videoId } = await getState();
  if (!capturing || tid !== tabId) return;
  // Sans permission sur la nouvelle page, tab.url est absent : on arrête
  const vid = videoIdFromUrl(info.url || tab.url || '');
  if (vid && (!videoId || vid === videoId)) return;
  handleStop('navigation');
});

async function handleStart({ tabId, emission, guests, description, videoDate, token }) {
  try {
    const tab = await chrome.tabs.get(tabId);
    await chrome.storage.session.set({ tabId, capturing: true, videoId: videoIdFromUrl(tab.url || '') });

    // Injecter le content script si absent (ping/pong)
    const alive = await chrome.tabs.sendMessage(tabId, { action: 'ping' })
      .then(r => r?.pong === true)
      .catch(() => false);

    if (!alive) {
      // Réinitialiser le verrou avant réinjection (extension rechargée sans refresh page)
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => { window.__fctInjected = false; },
      }).catch(() => {});
      await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
      await chrome.scripting.insertCSS({ target: { tabId }, files: ['overlay.css'] }).catch(() => {});
    }

    await chrome.tabs.sendMessage(tabId, { action: 'showOverlay' }).catch(() => {});

    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });

    // Toujours interroger hasDocument() — jamais de flag en mémoire. Un
    // document encore ouvert (session précédente qui finit sa dernière
    // analyse) est réutilisé : l'offscreen gère plusieurs sessions.
    if (!(await chrome.offscreen.hasDocument())) {
      await chrome.offscreen.createDocument({
        url: chrome.runtime.getURL('offscreen.html'),
        reasons: [chrome.offscreen.Reason.USER_MEDIA],
        justification: "Capture audio de l'onglet de la vidéo pour transcription Whisper",
      });
    }

    // tabId inclus pour que l'offscreen le propage dans chaque forwardToContent
    chrome.runtime.sendMessage({ action: 'doCapture', streamId, emission, guests, description, videoDate, token, tabId });
  } catch (e) {
    await chrome.storage.session.set({ tabId: null, capturing: false, videoId: null });
    throw e; // remonte au listener → réponse {ok:false} vers la popup
  }
}

// reason : 'user' | 'navigation' | 'tab_closed' — affiché par le content script
async function handleStop(reason) {
  const { tabId, capturing } = await getState();
  await chrome.storage.session.set({ tabId: null, capturing: false, videoId: null });
  if (!capturing) return;
  // L'offscreen arrête l'audio tout de suite, puis laisse le backend analyser
  // la fin du débat avant de se déconnecter (il ferme le document ensuite,
  // via offscreenDone). L'overlay passe en « finalisation » au lieu de
  // disparaître : le récap reste consultable et exportable.
  chrome.runtime.sendMessage({ action: 'doStop' }).catch(() => {});
  if (tabId) chrome.tabs.sendMessage(tabId, { action: 'captureEnded', reason }).catch(() => {});
}

async function closeOffscreen() {
  const has = await chrome.offscreen.hasDocument().catch(() => false);
  if (has) await chrome.offscreen.closeDocument().catch(() => {});
}

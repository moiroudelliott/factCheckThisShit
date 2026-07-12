// ══════════════════════════════════════════════════════════════════════════════
// vérif.live — service worker (v2)
// AUCUN état en mémoire : Chrome tue le worker après ~30 s d'inactivité, donc
// tout vit dans chrome.storage.session. Chaque handler relit l'état.
// ══════════════════════════════════════════════════════════════════════════════

function getState() {
  return chrome.storage.session.get({ tabId: null, capturing: false });
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      switch (msg.action) {
        case 'startCapture':
          await handleStart(msg);
          sendResponse({ ok: true });
          break;

        case 'stopCapture':
          await handleStop();
          sendResponse({ ok: true });
          break;

        case 'getStatus':
          sendResponse(await getState());
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
  if (capturing && closedTabId === tabId) handleStop();
});

async function handleStart({ tabId, emission, guests, description, videoDate }) {
  try {
    await chrome.storage.session.set({ tabId, capturing: true });

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

    // Toujours interroger hasDocument() — jamais de flag en mémoire
    if (!(await chrome.offscreen.hasDocument())) {
      await chrome.offscreen.createDocument({
        url: chrome.runtime.getURL('offscreen.html'),
        reasons: [chrome.offscreen.Reason.USER_MEDIA],
        justification: "Capture audio de l'onglet YouTube pour transcription Whisper",
      });
    }

    // tabId inclus pour que l'offscreen le propage dans chaque forwardToContent
    chrome.runtime.sendMessage({ action: 'doCapture', streamId, emission, guests, description, videoDate, tabId });
  } catch (e) {
    await chrome.storage.session.set({ tabId: null, capturing: false });
    throw e; // remonte au listener → réponse {ok:false} vers la popup
  }
}

async function handleStop() {
  const { tabId } = await getState();
  chrome.runtime.sendMessage({ action: 'doStop' }).catch(() => {});
  if (tabId) chrome.tabs.sendMessage(tabId, { action: 'hideOverlay' }).catch(() => {});
  await chrome.storage.session.set({ tabId: null, capturing: false });

  // Fermer le document offscreen : évite un double AudioContext au prochain démarrage
  const has = await chrome.offscreen.hasDocument().catch(() => false);
  if (has) await chrome.offscreen.closeDocument().catch(() => {});
}

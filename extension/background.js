let activeTabId = null;

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.action === 'startCapture') handleStart(msg);
  if (msg.action === 'stopCapture')  handleStop();
  // Relaye offscreen → content script
  // Récupère activeTabId depuis le message si le SW a redémarré (activeTabId perdu)
  if (msg.action === 'forwardToContent') {
    const tid = activeTabId ?? msg.tabId;
    if (tid) {
      if (!activeTabId) activeTabId = tid;
      chrome.tabs.sendMessage(tid, msg.payload).catch(() => {});
    }
  }
});

async function handleStart({ tabId, emission, guests }) {
  activeTabId = tabId;
  chrome.storage.session.set({ activeTabId: tabId, isCapturing: true });

  try {
    // 1. Vérifier si le content script est déjà actif (ping/pong)
    const isAlive = await chrome.tabs.sendMessage(tabId, { action: 'ping' })
      .then(r => r?.pong === true)
      .catch(() => false);

    if (!isAlive) {
      // Réinitialiser le verrou avant réinjection (cas d'extension rechargée sans refresh page)
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => { window.__fctInjected = false; },
      }).catch(() => {});
      await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] }).catch(() => {});
      await chrome.scripting.insertCSS({ target: { tabId }, files: ['overlay.css'] }).catch(() => {});
    }

    // 2. Afficher l'overlay (catch: listener ne renvoie pas de réponse, ce qui est normal)
    await chrome.tabs.sendMessage(tabId, { action: 'showOverlay' }).catch(() => {});

    // 3. Obtenir l'ID du flux audio de l'onglet
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });

    // 4. Créer le document offscreen si nécessaire (toujours vérifier, jamais de flag en mémoire)
    const has = await chrome.offscreen.hasDocument();
    if (!has) {
      await chrome.offscreen.createDocument({
        url: chrome.runtime.getURL('offscreen.html'),
        reasons: [chrome.offscreen.Reason.USER_MEDIA],
        justification: "Capture audio de l'onglet YouTube pour transcription Whisper",
      });
    }

    // tabId inclus pour que offscreen.js puisse le propager dans forwardToContent
    chrome.runtime.sendMessage({ action: 'doCapture', streamId, emission, guests, tabId });

  } catch (e) {
    console.error('[FCT] handleStart error:', e.message, e);
    activeTabId = null;
    chrome.storage.session.set({ activeTabId: null, isCapturing: false });
  }
}

async function handleStop() {
  chrome.runtime.sendMessage({ action: 'doStop' }).catch(() => {});
  if (activeTabId) {
    chrome.tabs.sendMessage(activeTabId, { action: 'hideOverlay' }).catch(() => {});
  }
  activeTabId = null;
  chrome.storage.session.set({ activeTabId: null, isCapturing: false });

  // Fermer le document offscreen pour éviter un double AudioContext au prochain démarrage
  const has = await chrome.offscreen.hasDocument().catch(() => false);
  if (has) {
    await chrome.offscreen.closeDocument().catch(() => {});
  }
}

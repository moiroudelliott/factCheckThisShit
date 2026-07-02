let capturing = false;

// Synchroniser l'état au chargement de la popup (le SW peut avoir redémarré)
chrome.storage.session.get(['isCapturing'], (result) => {
  if (result.isCapturing) setCapturing(true);
});

document.getElementById('btn-start').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (!tab?.url?.includes('youtube.com/watch')) {
    setStatus('⚠ Ouvre une vidéo YouTube d\'abord', false);
    return;
  }

  const emission = document.getElementById('emission').value.trim();
  const guests   = document.getElementById('guests').value.trim();

  chrome.runtime.sendMessage({ action: 'startCapture', tabId: tab.id, emission, guests });
  setCapturing(true);
});

document.getElementById('btn-stop').addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'stopCapture' });
  setCapturing(false);
});

function setCapturing(on) {
  capturing = on;
  document.getElementById('btn-start').style.display = on ? 'none' : 'block';
  document.getElementById('btn-stop').style.display  = on ? 'block' : 'none';
  document.getElementById('briefing-section').style.display = on ? 'none' : 'block';
  document.getElementById('fct-ping').classList.toggle('active', on);
  document.getElementById('status-text').classList.toggle('active', on);
  setStatus(on ? 'en direct' : 'inactif');
}

function setStatus(text) {
  document.getElementById('status-text').textContent = text;
}

// ══════════════════════════════════════════════════════════════════════════════
// vérif.live — document offscreen (v2)
// Capture l'audio de l'onglet, le découpe en chunks WebM, streame au backend
// via socket.io, et remonte résultats + état de connexion au content script.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';
const CHUNK_MS = 7000;

let socket = null;
let recorder = null;
let mediaStream = null;
let audioCtx = null;
let isCapturing = false;
let tabId = null;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'doCapture') {
    tabId = msg.tabId;
    start(msg);
  }
  if (msg.action === 'doStop') stop();
});

function forward(payload) {
  chrome.runtime.sendMessage({ action: 'forwardToContent', tabId, payload }).catch(() => {});
}

function report(status, detail = '') {
  forward({ type: 'connection_status', status, detail });
}

async function start({ streamId, emission, guests }) {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId,
        },
      },
      video: false,
    });
  } catch (err) {
    console.error('[FCT] getUserMedia failed:', err);
    report('capture_error', err.message);
    return;
  }

  // Restituer le son dans les haut-parleurs (tabCapture coupe la sortie de l'onglet)
  audioCtx = new AudioContext();
  if (audioCtx.state === 'suspended') await audioCtx.resume().catch(() => {});
  audioCtx.createMediaStreamSource(mediaStream).connect(audioCtx.destination);

  socket = io(BACKEND_URL, { transports: ['websocket'] });

  // 'connect' se déclenche aussi à chaque reconnexion : on renvoie le contexte
  // car le backend a créé une nouvelle session (nouveau sid)
  socket.on('connect', () => {
    report('connected');
    if (emission || guests) {
      socket.emit('set_context', { emission: emission || '', guests: guests || '' });
    }
    socket.emit('start_transcription');
    if (!isCapturing) {
      isCapturing = true;
      recordChunk();
    }
  });

  socket.on('connect_error', () => report('backend_down'));
  socket.on('disconnect', () => { if (isCapturing) report('reconnecting'); });

  socket.on('talking_points', (d) => {
    forward({ type: 'talking_points', points: d.points });
  });

  socket.on('fact_check_result', (d) => {
    forward({
      type: 'fact_check_result',
      id: d.id,
      verdict: d.verdict,
      explication: d.explication,
      source: d.source || '',
    });
  });
}

function recordChunk() {
  if (!isCapturing || !mediaStream) return;

  const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
  const chunks = [];

  recorder = new MediaRecorder(mediaStream, {
    ...(mimeType ? { mimeType } : {}),
    audioBitsPerSecond: 128000,
  });

  recorder.ondataavailable = (e) => { if (e.data?.size > 0) chunks.push(e.data); };

  // La boucle DOIT redémarrer quoi qu'il arrive : toute erreur ici est
  // absorbée, sinon la transcription s'arrête silencieusement pour toujours
  recorder.onstop = async () => {
    try {
      if (chunks.length && socket?.connected) {
        const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
        const buffer = await blob.arrayBuffer();
        socket.emit('audio_chunk', buffer);
      }
    } catch (err) {
      console.error('[FCT] recorder.onstop error:', err);
    }
    if (isCapturing) recordChunk();
  };

  recorder.onerror = (e) => {
    console.error('[FCT] MediaRecorder error:', e.error);
    if (isCapturing) setTimeout(recordChunk, 500);
  };

  recorder.start();
  setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, CHUNK_MS);
}

function stop() {
  isCapturing = false;
  if (recorder?.state === 'recording') recorder.stop();
  mediaStream?.getTracks().forEach(t => t.stop());
  mediaStream = null;
  audioCtx?.close().catch(() => {});
  audioCtx = null;
  socket?.disconnect();
  socket = null;
}

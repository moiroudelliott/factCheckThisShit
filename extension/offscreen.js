// ══════════════════════════════════════════════════════════════════════════════
// vérif.live — document offscreen (v2)
// Capture l'audio de l'onglet, le découpe en chunks WebM, streame au backend
// via socket.io, et remonte résultats + état de connexion au content script.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';
const CHUNK_MS   = 10000; // plus long = plus de contexte pour Whisper, moins de phrases coupées
const OVERLAP_MS = 1500;  // chevauchement entre chunks : ne perd pas les mots coupés à la frontière

let socket = null;
let mediaStream = null;
let audioCtx = null;
let isCapturing = false;
let tabId = null;
let nextTimer = null;
const activeRecorders = new Set();

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

async function start({ streamId, emission, guests, description, videoDate }) {
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
    if (emission || guests || description || videoDate) {
      socket.emit('set_context', {
        emission: emission || '',
        guests: guests || '',
        description: description || '',
        date: videoDate || '',
      });
    }
    socket.emit('start_transcription');
    if (!isCapturing) {
      isCapturing = true;
      startRecorder();
    }
  });

  socket.on('connect_error', () => report('backend_down'));
  socket.on('disconnect', () => { if (isCapturing) report('reconnecting'); });

  socket.on('talking_points', (d) => {
    forward({ type: 'talking_points', points: d.points });
  });

  socket.on('speaker_map', (d) => {
    forward({ type: 'speaker_map', map: d.map || {} });
  });

  socket.on('fact_check_result', (d) => {
    forward({
      type: 'fact_check_result',
      id: d.id,
      verdict: d.verdict,
      explication: d.explication,
      source: d.source || '',
      url: d.url || '',
      confiance: d.confiance,
    });
  });
}

// Chaque enregistreur planifie son successeur AU DÉMARRAGE (pas dans onstop) :
// une erreur d'un enregistreur ne peut donc jamais casser la chaîne. Le
// successeur démarre OVERLAP_MS avant la fin du courant → les deux se
// chevauchent et aucun mot n'est perdu à la frontière des chunks.
function startRecorder() {
  if (!isCapturing || !mediaStream) return;

  const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
  const chunks = [];
  let rec;
  try {
    rec = new MediaRecorder(mediaStream, {
      ...(mimeType ? { mimeType } : {}),
      audioBitsPerSecond: 128000,
    });
  } catch (e) {
    console.error('[FCT] MediaRecorder create failed:', e);
    nextTimer = setTimeout(startRecorder, 1000);
    return;
  }
  activeRecorders.add(rec);

  rec.ondataavailable = (e) => { if (e.data?.size > 0) chunks.push(e.data); };

  rec.onstop = async () => {
    activeRecorders.delete(rec);
    try {
      if (chunks.length && socket?.connected) {
        const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
        socket.emit('audio_chunk', await blob.arrayBuffer());
      }
    } catch (err) {
      console.error('[FCT] recorder.onstop error:', err);
    }
  };

  rec.onerror = (e) => console.error('[FCT] MediaRecorder error:', e.error);

  rec.start();
  nextTimer = setTimeout(startRecorder, CHUNK_MS - OVERLAP_MS);
  setTimeout(() => { if (rec.state === 'recording') rec.stop(); }, CHUNK_MS);
}

function stop() {
  isCapturing = false;
  clearTimeout(nextTimer);
  activeRecorders.forEach(r => { if (r.state === 'recording') r.stop(); });
  activeRecorders.clear();
  mediaStream?.getTracks().forEach(t => t.stop());
  mediaStream = null;
  audioCtx?.close().catch(() => {});
  audioCtx = null;
  socket?.disconnect();
  socket = null;
}

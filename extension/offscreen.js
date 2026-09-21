// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — document offscreen (v2)
// Capture l'audio de l'onglet, le découpe en chunks WebM, streame au backend
// via socket.io, et remonte résultats + état de connexion au content script.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';
const CHUNK_MS   = 10000; // plus long = plus de contexte pour Whisper, moins de phrases coupées
const OVERLAP_MS = 1500;  // chevauchement entre chunks : ne perd pas les mots coupés à la frontière
const PROBE_MS   = 2500;  // sondes "qui parle" : courtes = badge réactif (~3 s de latence)

let socket = null;
let mediaStream = null;
let audioCtx = null;
let isCapturing = false;
let tabId = null;
let nextTimer = null;
let probeTimer = null;
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

async function start({ streamId, emission, guests, description, videoDate, token }) {
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

  // auth.token : ignoré par le backend si BACKEND_TOKEN n'est pas défini côté
  // serveur ; sinon la connexion est refusée sans jeton valide (voir backend.py)
  socket = io(BACKEND_URL, { transports: ['websocket'], auth: { token: token || '' } });

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
      startProbe();
    }
  });

  socket.on('speaker_live', (d) => {
    forward({ type: 'speaker_live', speaker: d.speaker });
  });

  socket.on('connect_error', (err) => {
    report(err?.message === 'unauthorized' ? 'unauthorized' : 'backend_down');
  });
  socket.on('disconnect', () => { if (isCapturing) report('reconnecting'); });

  socket.on('mistral_rate_limited', (d) => {
    forward({ type: 'mistral_rate_limited', attempt: d.attempt, max: d.max, wait: d.wait });
  });

  socket.on('talking_points', (d) => {
    forward({ type: 'talking_points', points: d.points });
  });

  // Badge "qui parle" : seul le locuteur nous intéresse ici
  socket.on('transcript_segment', (d) => {
    if (d.speaker) forward({ type: 'transcript_segment', speaker: d.speaker });
  });

  socket.on('speaker_map', (d) => {
    forward({ type: 'speaker_map', map: d.map || {}, enrolled: d.enrolled || [] });
  });

  // Empreinte vocale sauvegardée en banque pour ce nom — l'extension peut
  // retirer son indicateur "capture de l'empreinte…" (voir content.js)
  socket.on('voice_enrolled', (d) => {
    if (d.name) forward({ type: 'voice_enrolled', name: d.name });
  });

  // Empreinte vocale comparée à la banque et non trouvée : le locuteur n'est
  // pas quelqu'un de déjà connu — l'extension peut passer de "identification
  // en cours" à "non identifié" sans attendre le vote LLM.
  socket.on('voice_not_in_bank', (d) => {
    if (Array.isArray(d.labels) && d.labels.length) forward({ type: 'voice_not_in_bank', labels: d.labels });
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

// Flux parallèle léger : petits blobs de 2,5 s classifiés par empreinte vocale
// seule (pas de transcription) → le badge "qui parle" réagit en ~3 s
function startProbe() {
  if (!isCapturing || !mediaStream) return;

  const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
  const chunks = [];
  let rec;
  try {
    rec = new MediaRecorder(mediaStream, {
      ...(mimeType ? { mimeType } : {}),
      audioBitsPerSecond: 64000,
    });
  } catch (e) {
    probeTimer = setTimeout(startProbe, PROBE_MS);
    return;
  }
  activeRecorders.add(rec);

  rec.ondataavailable = (e) => { if (e.data?.size > 0) chunks.push(e.data); };
  rec.onstop = async () => {
    activeRecorders.delete(rec);
    try {
      if (chunks.length && socket?.connected) {
        const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
        socket.emit('speaker_probe', await blob.arrayBuffer());
      }
    } catch (_) {}
  };
  rec.onerror = () => {};

  rec.start();
  probeTimer = setTimeout(startProbe, PROBE_MS);
  setTimeout(() => { if (rec.state === 'recording') rec.stop(); }, PROBE_MS);
}

function stop() {
  isCapturing = false;
  clearTimeout(nextTimer);
  clearTimeout(probeTimer);
  activeRecorders.forEach(r => { if (r.state === 'recording') r.stop(); });
  activeRecorders.clear();
  mediaStream?.getTracks().forEach(t => t.stop());
  mediaStream = null;
  audioCtx?.close().catch(() => {});
  audioCtx = null;
  socket?.disconnect();
  socket = null;
}

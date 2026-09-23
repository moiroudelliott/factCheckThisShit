// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — document offscreen
// Capture l'audio de l'onglet, le découpe en chunks WebM, streame au backend
// via socket.io, et remonte résultats + état de connexion au content script.
//
// Une « session » = une capture. À l'arrêt, l'audio est coupé tout de suite
// mais la session garde sa connexion le temps que le backend analyse la fin
// du débat (stop_transcription → session_done) : une nouvelle capture peut
// démarrer pendant ce temps, sur sa propre connexion.
// ══════════════════════════════════════════════════════════════════════════════

const BACKEND_URL = 'http://localhost:5000';
const CHUNK_MS   = 10000; // plus long = plus de contexte pour Whisper, moins de phrases coupées
const OVERLAP_MS = 1500;  // chevauchement entre chunks : ne perd pas les mots coupés à la frontière (= CHUNK_OVERLAP_S côté backend)
const PROBE_MS   = 2500;  // sondes "qui parle" : courtes = badge réactif (~3 s de latence)
const FINAL_WAIT_MS = 60000; // arrêt : attente max de session_done (le backend borne lui-même à 45 s)

let current = null; // session en cours de capture (null si aucune)
let adActive = false; // pub YouTube en cours (signalée par le content script)

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'doCapture') {
    if (current) stopSession(current); // ne jamais laisser deux captures en parallèle
    current = startSession(msg);
  }
  if (msg.action === 'doStop' && current) {
    stopSession(current);
    current = null;
  }
  if (msg.action === 'setAdState') {
    adActive = Boolean(msg.ad);
    // Un enregistreur qui a entendu la pub ne doit rien envoyer
    if (adActive && current) current.recorders.forEach(r => { r.tainted = true; });
  }
});

function startSession({ streamId, emission, guests, description, videoDate, token, tabId }) {
  const sess = {
    tabId, socket: null, mediaStream: null, audioCtx: null,
    capturing: false, everConnected: false, nextTimer: null, probeTimer: null,
    recorders: new Set(),
  };
  const forward = (payload) => {
    chrome.runtime.sendMessage({ action: 'forwardToContent', tabId: sess.tabId, payload }).catch(() => {});
  };
  const report = (status, detail = '') => forward({ type: 'connection_status', status, detail });
  sess.forward = forward;

  (async () => {
    try {
      sess.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
        video: false,
      });
    } catch (err) {
      console.error('[FCT] getUserMedia failed:', err);
      report('capture_error', err.message);
      chrome.runtime.sendMessage({ action: 'captureFailed' }).catch(() => {});
      if (current === sess) current = null;
      return;
    }

    // Restituer le son dans les haut-parleurs (tabCapture coupe la sortie de l'onglet)
    sess.audioCtx = new AudioContext();
    if (sess.audioCtx.state === 'suspended') await sess.audioCtx.resume().catch(() => {});
    sess.audioCtx.createMediaStreamSource(sess.mediaStream).connect(sess.audioCtx.destination);

    // auth.token : ignoré par le backend si BACKEND_TOKEN n'est pas défini côté
    // serveur ; sinon la connexion est refusée sans jeton valide (voir backend.py)
    const socket = io(BACKEND_URL, { transports: ['websocket'], auth: { token: token || '' } });
    sess.socket = socket;

    // 'connect' se déclenche aussi à chaque reconnexion : on renvoie le contexte
    // car le backend a créé une nouvelle session (nouveau sid)
    socket.on('connect', () => {
      report('connected');
      // Reconnexion = nouvelle session backend : les labels « Intervenant A… »
      // repartent de zéro et ne désignent plus forcément les mêmes personnes
      if (sess.everConnected) forward({ type: 'session_reset' });
      sess.everConnected = true;
      if (emission || guests || description || videoDate) {
        socket.emit('set_context', {
          emission: emission || '',
          guests: guests || '',
          description: description || '',
          date: videoDate || '',
        });
      }
      socket.emit('start_transcription');
      if (!sess.capturing && sess.mediaStream) {
        sess.capturing = true;
        startRecorder(sess);
        startProbe(sess);
      }
    });

    socket.on('connect_error', (err) => {
      report(err?.message === 'unauthorized' ? 'unauthorized' : 'backend_down');
    });
    socket.on('disconnect', () => { if (sess.capturing) report('reconnecting'); });

    socket.on('speaker_live', (d) => forward({ type: 'speaker_live', speaker: d.speaker }));
    socket.on('mistral_rate_limited', (d) => {
      forward({ type: 'mistral_rate_limited', attempt: d.attempt, max: d.max, wait: d.wait });
    });
    socket.on('server_warning', (d) => { if (d?.message) forward({ type: 'server_warning', message: d.message }); });
    socket.on('talking_points', (d) => forward({ type: 'talking_points', points: d.points }));

    // Badge "qui parle" : seul le locuteur nous intéresse ici
    socket.on('transcript_segment', (d) => {
      if (d.speaker) forward({ type: 'transcript_segment', speaker: d.speaker });
    });

    socket.on('speaker_map', (d) => {
      forward({ type: 'speaker_map', map: d.map || {}, enrolled: d.enrolled || [], enrollable: d.enrollable || [] });
    });

    // Empreinte vocale sauvegardée en banque pour ce nom — l'extension peut
    // retirer son indicateur "capture de l'empreinte…" (voir content.js)
    socket.on('voice_enrolled', (d) => {
      if (d.name) forward({ type: 'voice_enrolled', name: d.name });
    });

    // Empreinte vocale comparée à la banque et non trouvée : l'extension peut
    // passer de "identification en cours" à "non identifié" sans attendre.
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
        indisponible: Boolean(d.indisponible),
      });
    });
  })();

  return sess;
}

// Chaque enregistreur planifie son successeur AU DÉMARRAGE (pas dans onstop) :
// une erreur d'un enregistreur ne peut donc jamais casser la chaîne. Le
// successeur démarre OVERLAP_MS avant la fin du courant → les deux se
// chevauchent et aucun mot n'est perdu à la frontière des chunks.
function startRecorder(sess) {
  if (!sess.capturing || !sess.mediaStream) return;
  const rec = makeRecorder(sess, 'audio_chunk', 128000);
  if (!rec) {
    sess.nextTimer = setTimeout(() => startRecorder(sess), 1000);
    return;
  }
  sess.nextTimer = setTimeout(() => startRecorder(sess), CHUNK_MS - OVERLAP_MS);
  setTimeout(() => { if (rec.state === 'recording') rec.stop(); }, CHUNK_MS);
}

// Flux parallèle léger : petits blobs de 2,5 s classifiés par empreinte vocale
// seule (pas de transcription) → le badge "qui parle" réagit en ~3 s
function startProbe(sess) {
  if (!sess.capturing || !sess.mediaStream) return;
  const rec = makeRecorder(sess, 'speaker_probe', 64000);
  sess.probeTimer = setTimeout(() => startProbe(sess), PROBE_MS);
  if (rec) setTimeout(() => { if (rec.state === 'recording') rec.stop(); }, PROBE_MS);
}

// Enregistreur qui envoie son blob sous `event` à l'arrêt — sauf s'il a
// entendu une pub (tainted). rec.done se résout une fois l'envoi fait :
// l'arrêt propre attend le dernier chunk avant stop_transcription.
function makeRecorder(sess, event, bitrate) {
  const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
  const chunks = [];
  let rec;
  try {
    rec = new MediaRecorder(sess.mediaStream, { ...(mimeType ? { mimeType } : {}), audioBitsPerSecond: bitrate });
  } catch (e) {
    console.error('[FCT] MediaRecorder create failed:', e);
    return null;
  }
  rec.tainted = adActive;
  rec.done = new Promise((resolve) => {
    rec.onstop = async () => {
      sess.recorders.delete(rec);
      try {
        if (chunks.length && !rec.tainted && sess.socket?.connected) {
          const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
          sess.socket.emit(event, await blob.arrayBuffer());
        }
      } catch (err) {
        console.error('[FCT] recorder.onstop error:', err);
      }
      resolve();
    };
  });
  rec.ondataavailable = (e) => { if (e.data?.size > 0) chunks.push(e.data); };
  rec.onerror = (e) => console.error('[FCT] MediaRecorder error:', e.error);
  sess.recorders.add(rec);
  rec.start();
  return rec;
}

async function stopSession(sess) {
  sess.capturing = false;
  clearTimeout(sess.nextTimer);
  clearTimeout(sess.probeTimer);
  // Couper l'audio tout de suite (le son de l'onglet revient à la normale)…
  const flushed = [...sess.recorders].map((r) => {
    if (r.state === 'recording') r.stop();
    return r.done;
  });
  sess.mediaStream?.getTracks().forEach(t => t.stop());
  sess.mediaStream = null;
  sess.audioCtx?.close().catch(() => {});
  sess.audioCtx = null;
  await Promise.race([Promise.all(flushed), new Promise(r => setTimeout(r, 3000))]);

  // …puis laisser le backend analyser la fin du débat avant de se déconnecter
  const socket = sess.socket;
  let complete = false;
  if (socket?.connected) {
    sess.forward({ type: 'finalizing' });
    socket.emit('stop_transcription');
    complete = await new Promise((resolve) => {
      socket.once('session_done', (d) => resolve(Boolean(d?.complete)));
      socket.once('disconnect', () => resolve(false));
      setTimeout(() => resolve(false), FINAL_WAIT_MS);
    });
  }
  socket?.disconnect();
  sess.forward({ type: 'session_done', complete });
  chrome.runtime.sendMessage({ action: 'offscreenDone' }).catch(() => {});
}

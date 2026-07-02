const BACKEND_URL = 'http://localhost:5000';
const CHUNK_MS = 7000;

let socket = null;
let recorder = null;
let mediaStream = null;
let isCapturing = false;
let capturedTabId = null; // conservé même si le SW redémarre

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'doCapture') {
    capturedTabId = msg.tabId;
    startCapture(msg.streamId, msg.emission, msg.guests);
  }
  if (msg.action === 'doStop') stopCapture();
});

async function startCapture(streamId, emission, guests) {
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
    chrome.runtime.sendMessage({
      action: 'forwardToContent',
      tabId: capturedTabId,
      payload: { type: 'capture_error', message: err.message },
    });
    return;
  }

  // Restituer l'audio capturé dans les haut-parleurs (sinon tabCapture coupe le son)
  const audioCtx = new AudioContext();
  // Certains navigateurs suspendent l'AudioContext par autoplay policy — forcer la reprise
  if (audioCtx.state === 'suspended') {
    await audioCtx.resume().catch(() => {});
  }
  const source = audioCtx.createMediaStreamSource(mediaStream);
  source.connect(audioCtx.destination);

  socket = io(BACKEND_URL, { transports: ['websocket'] });

  socket.on('connect', () => {
    if (emission || guests) {
      socket.emit('set_context', { emission: emission || '', guests: guests || '' });
    }
    socket.emit('start_transcription');
    isCapturing = true;
    recordChunk();
  });

  socket.on('talking_points', (data) => {
    chrome.runtime.sendMessage({
      action: 'forwardToContent',
      tabId: capturedTabId,
      payload: { type: 'talking_points', points: data.points },
    });
  });

  socket.on('fact_check_result', (data) => {
    chrome.runtime.sendMessage({
      action: 'forwardToContent',
      tabId: capturedTabId,
      payload: { type: 'fact_check_result', id: data.id, verdict: data.verdict, explication: data.explication, source: data.source || '' },
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

function stopCapture() {
  isCapturing = false;
  if (recorder?.state === 'recording') recorder.stop();
  mediaStream?.getTracks().forEach(t => t.stop());
  socket?.disconnect();
  socket = null;
}

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { io } from 'socket.io-client';
import './WhisperMVP.css';

const SOCKET_URL = 'http://localhost:5000';
const CHUNK_DURATION_MS = 7000;

const TYPE_META = {
  affirmation: { icon: '📊', label: 'Affirmation', cls: 'tp-card--affirmation' },
  subjectif:   { icon: '💭', label: 'Subjectif',   cls: 'tp-card--subjectif' },
  argument:    { icon: '🗣️',  label: 'Argument',    cls: 'tp-card--argument' },
  remarque:    { icon: '💬', label: 'Remarque',    cls: 'tp-card--remarque' },
  question:    { icon: '❓', label: 'Question',    cls: 'tp-card--question' },
  accord:      { icon: '✅', label: 'Accord',      cls: 'tp-card--accord' },
  désaccord:   { icon: '❌', label: 'Désaccord',   cls: 'tp-card--desaccord' },
};

const VERDICT_META = {
  vrai:               { label: '✅ Vrai',               cls: 'verdict--vrai' },
  partiellement_vrai: { label: '⚠️ Partiellement vrai', cls: 'verdict--partiel' },
  trompeur:           { label: '🟠 Trompeur',            cls: 'verdict--trompeur' },
  faux:               { label: '❌ Faux',                cls: 'verdict--faux' },
};

export default function WhisperMVP() {
  const [phase, setPhase] = useState('idle');
  const [segments, setSegments] = useState([]);
  const [talkingPoints, setTalkingPoints] = useState([]);
  const [factChecks, setFactChecks] = useState({});   // { id: { verdict, explication } }
  const [showAllTypes, setShowAllTypes] = useState(false);
  const [deviceError, setDeviceError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [socketStatus, setSocketStatus] = useState('disconnected');
  const [audioDevices, setAudioDevices] = useState([]);
  const [selectedAudioId, setSelectedAudioId] = useState('');
  const [videoDevices, setVideoDevices] = useState([]);
  const [selectedVideoId, setSelectedVideoId] = useState('');
  const [emission, setEmission] = useState('');
  const [guests, setGuests] = useState('');

  const videoRef = useRef(null);
  const socketRef = useRef(null);
  const recorderRef = useRef(null);
  const isLiveRef = useRef(false); // ref pour éviter closure stale dans recordChunk
  const segIdRef = useRef(0);
  const lastSegRef = useRef(null);

  // Énumérer les périphériques audio et vidéo au montage
  useEffect(() => {
    let tempStream = null;
    navigator.mediaDevices.getUserMedia({ audio: true, video: true })
      .then(stream => {
        tempStream = stream;
        return navigator.mediaDevices.enumerateDevices();
      })
      .then(devices => {
        // Libérer le stream de permission immédiatement
        tempStream?.getTracks().forEach(t => t.stop());
        const mics = devices.filter(d => d.kind === 'audioinput');
        const cams = devices.filter(d => d.kind === 'videoinput');
        setAudioDevices(mics);
        if (mics.length > 0) setSelectedAudioId(mics[0].deviceId);
        setVideoDevices(cams);
        if (cams.length > 0) setSelectedVideoId(cams[0].deviceId);
      }).catch(() => {});
  }, []);

  // Auto-scroll vers le dernier élément
  useEffect(() => {
    lastSegRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [talkingPoints]);

  const stopChunking = useCallback(() => {
    isLiveRef.current = false;
    try {
      if (recorderRef.current && recorderRef.current.state !== 'inactive') {
        recorderRef.current.stop();
      }
    } catch (_) {}
  }, []);

  const startChunking = useCallback((audioStream, socket) => {
    const mimeType = MediaRecorder.isTypeSupported('audio/webm')
      ? 'audio/webm'
      : MediaRecorder.isTypeSupported('audio/ogg')
      ? 'audio/ogg'
      : '';

    function recordChunk() {
      if (!isLiveRef.current) return;

      const recorder = new MediaRecorder(audioStream, {
        ...(mimeType ? { mimeType } : {}),
        audioBitsPerSecond: 128000,
      });
      recorderRef.current = recorder;
      const chunks = [];

      recorder.ondataavailable = (e) => {
        if (e.data?.size > 0) chunks.push(e.data);
      };

      recorder.onstop = async () => {
        if (chunks.length > 0) {
          const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
          const buffer = await blob.arrayBuffer();
          if (socket.connected) socket.emit('audio_chunk', buffer);
        }
        if (isLiveRef.current) recordChunk();
      };

      recorder.start();
      setTimeout(() => {
        if (recorder.state === 'recording') recorder.stop();
      }, CHUNK_DURATION_MS);
    }

    recordChunk();
  }, []);

  async function startLive() {
    setDeviceError(null);
    setSegments([]);
    setTalkingPoints([]);
    setFactChecks({});

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: selectedVideoId ? { deviceId: { ideal: selectedVideoId } } : true,
        audio: selectedAudioId ? { deviceId: { ideal: selectedAudioId } } : true,
      });
    } catch (err) {
      setDeviceError(
        err.name === 'NotAllowedError'
          ? "Accès refusé à la caméra/micro. Autorise l'accès dans les paramètres du navigateur."
          : `Erreur: ${err.message}`
      );
      return;
    }

    // Afficher le flux vidéo live
    if (videoRef.current) {
      videoRef.current.srcObject = stream;
      videoRef.current.play().catch(() => {});
    }

    // Connexion WebSocket avec reconnexion automatique
    const socket = io(SOCKET_URL, {
      transports: ['websocket'],
      reconnection: true,
      reconnectionAttempts: 10,
      reconnectionDelay: 1000,
    });
    socketRef.current = socket;

    socket.on('connect_error', (err) => {
      setSocketStatus('error');
      console.error('Connexion échouée:', err.message, '— Le backend Flask est-il démarré ?');
    });

    socket.on('connect', () => {
      setSocketStatus('connected');
      if (emission.trim() || guests.trim()) {
        socket.emit('set_context', { emission, guests });
      }
      socket.emit('start_transcription');
    });

    socket.on('disconnect', () => setSocketStatus('disconnected'));

    socket.on('ready', () => {
      // Lancer le chunking audio uniquement (pas la vidéo)
      const audioStream = new MediaStream(stream.getAudioTracks());
      isLiveRef.current = true;
      startChunking(audioStream, socket);
    });

    socket.on('transcript_segment', (data) => {
      setSegments((prev) => [...prev, { ...data, id: segIdRef.current++ }]);
    });

    socket.on('talking_points', (data) => {
      if (data.points?.length) {
        setTalkingPoints((prev) => [...prev, ...data.points]);
      }
    });

    socket.on('fact_check_result', (data) => {
      setFactChecks((prev) => ({
        ...prev,
        [data.id]: { verdict: data.verdict, explication: data.explication },
      }));
    });

    socket.on('transcription_error', (data) => {
      console.error('Erreur transcription:', data.error);
    });

    setPhase('live');
  }

  function stopLive() {
    stopChunking();

    if (videoRef.current && videoRef.current.srcObject) {
      videoRef.current.srcObject.getTracks().forEach((t) => t.stop());
      videoRef.current.srcObject = null;
    }

    if (socketRef.current) {
      socketRef.current.disconnect();
      socketRef.current = null;
    }

    setPhase('idle');
    setSocketStatus('disconnected');
  }

  function copyAll() {
    const text = talkingPoints.length
      ? talkingPoints.map((p) => `[${TYPE_META[p.type]?.label || p.type}] ${p.texte}`).join('\n')
      : segments.map((s) => s.text).join(' ');
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="app">
      {phase === 'idle' && (
        <div className="idle-screen">
          <div className="logo">
            <span className="logo-check">✓</span>
            <h1>FactCheckThis</h1>
          </div>
          <p className="subtitle">Transcription live en français — 100% local, zéro cloud</p>

          <div className="briefing-form">
            <div className="briefing-title">Briefing (optionnel)</div>
            <input
              type="text"
              className="briefing-input"
              placeholder="Nom de l'émission (ex: BFM Politique, C à vous…)"
              value={emission}
              onChange={e => setEmission(e.target.value)}
            />
            <textarea
              className="briefing-textarea"
              placeholder={"Intervenants, un par ligne :\nMarine Le Pen\nGabriel Attal"}
              value={guests}
              onChange={e => setGuests(e.target.value)}
              rows={3}
            />
          </div>

          {videoDevices.length > 1 && (
            <div className="device-selector">
              <label htmlFor="video-select">Source vidéo</label>
              <select
                id="video-select"
                value={selectedVideoId}
                onChange={e => setSelectedVideoId(e.target.value)}
              >
                {videoDevices.map(d => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || `Caméra ${d.deviceId.slice(0, 8)}`}
                  </option>
                ))}
              </select>
            </div>
          )}

          {audioDevices.length > 0 && (
            <div className="device-selector">
              <label htmlFor="audio-select">Source audio</label>
              <select
                id="audio-select"
                value={selectedAudioId}
                onChange={e => setSelectedAudioId(e.target.value)}
              >
                {audioDevices.map(d => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || `Microphone ${d.deviceId.slice(0, 8)}`}
                  </option>
                ))}
              </select>
            </div>
          )}

          {deviceError && (
            <div className="error-box">{deviceError}</div>
          )}

          <button className="btn-start" onClick={startLive}>
            <span className="btn-dot" />
            Démarrer la transcription
          </button>

          <div className="hint">
            <p>Webcam + micro réels, ou</p>
            <p>OBS Virtual Camera + VB-Cable pour un débat</p>
          </div>
        </div>
      )}

      {phase === 'live' && (
        <div className="live-layout">
          {/* Pane gauche: vidéo */}
          <div className="video-pane">
            <div className="video-wrapper">
              <video ref={videoRef} autoPlay muted playsInline />
              <div className="live-badge">
                <span className="live-dot" />
                EN DIRECT
              </div>
            </div>
            <div className="video-controls">
              <div className={`status-indicator status-${socketStatus}`}>
                {socketStatus === 'connected' ? '● Connecté' : '○ Connexion...'}
              </div>
              <button className="btn-stop" onClick={stopLive}>
                ■ Arrêter
              </button>
            </div>
          </div>

          {/* Pane droite: talking points */}
          <div className="transcript-pane">
            <div className="transcript-header">
              <span className="transcript-title">Analyse en direct</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className={`btn-filter ${showAllTypes ? 'btn-filter--active' : ''}`}
                  onClick={() => setShowAllTypes(v => !v)}
                >
                  {showAllTypes ? 'Affirmations' : 'Tout voir'}
                </button>
                <button
                  className="btn-copy"
                  onClick={copyAll}
                  disabled={talkingPoints.length === 0 && segments.length === 0}
                >
                  {copied ? '✓ Copié' : 'Exporter'}
                </button>
              </div>
            </div>

            <div className="tp-feed">
              {talkingPoints.length === 0 && (
                <div className="tp-empty">
                  <div className="waiting-dots">
                    <span /><span /><span />
                  </div>
                  <p>Analyse en attente…</p>
                  <p className="hint-small">
                    {segments.length === 0
                      ? 'En attente de la parole...'
                      : `${segments.length} segment(s) capturé(s) — analyse dans ~${Math.max(0, 20 - Math.round(segments.length * 7))}s`}
                  </p>
                  {segments.length > 0 && (
                    <p className="tp-last-heard">
                      💬 «{segments[segments.length - 1].text}»
                    </p>
                  )}
                </div>
              )}
              {talkingPoints.length > 0 && (() => {
                const visible = showAllTypes
                  ? talkingPoints
                  : talkingPoints.filter(p => p.type === 'affirmation');
                return visible.map((point, idx) => {
                  const meta = TYPE_META[point.type] ?? { icon: '•', label: point.type, cls: '' };
                  const fc = factChecks[point.id];
                  const vMeta = fc ? VERDICT_META[fc.verdict] : null;
                  return (
                    <div
                      key={point.id ?? idx}
                      className={`tp-card ${meta.cls}`}
                      ref={idx === visible.length - 1 ? lastSegRef : null}
                    >
                      <div className="tp-card-header">
                        <div className="tp-badge">{meta.icon} {meta.label}</div>
                        {point.type === 'affirmation' && (
                          <div className={`verdict-badge ${vMeta ? vMeta.cls : 'verdict--pending'}`}>
                            {vMeta ? vMeta.label : '⏳ Vérification…'}
                          </div>
                        )}
                      </div>
                      <p className="tp-text">{point.texte}</p>
                      {fc?.explication && (
                        <p className="verdict-explication">{fc.explication}</p>
                      )}
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

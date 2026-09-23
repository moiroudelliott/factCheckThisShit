"""État par session (sid Socket.IO) partagé entre routes.py, voices.py et
factcheck.py. Un seul endroit pour créer/nettoyer ces dicts (voir
routes.on_connect / routes.on_disconnect) évite qu'un module oublie
d'initialiser ou de purger l'un d'eux."""

session_history: dict[str, list[str]] = {}
session_starts: dict[str, float] = {}
session_buffers: dict[str, dict] = {}
session_contexts: dict[str, dict] = {}   # { sid: {"emission": str, "guests": [str]} }
session_points: dict[str, list] = {}     # { sid: talking points récents pour le contexte }
session_dupe_index: dict[str, dict] = {}  # { sid: {mot_clé: [indices dans session_points[sid]]} } — évite un scan O(n) à chaque dédup
session_flush_locks: dict[str, object] = {}  # { sid: Semaphore } évite la race condition sur session_points
session_chunk_locks: dict[str, object] = {}  # { sid: Semaphore } sérialise les chunks d'UNE session (le tracker n'est pas thread-safe), sans bloquer les autres sessions
session_speakers: dict[str, object] = {}     # { sid: SpeakerTracker (diarisation) }
session_excerpts: dict[str, list] = {}       # { sid: derniers transcripts annotés (preuves pour l'identification) }
session_speaker_map: dict[str, dict] = {}    # { sid: {"Intervenant A": "Éric Zemmour", …} — mappings confirmés }
session_map_votes: dict[str, dict] = {}      # { sid: {label: {nom: nb_votes}} — une identification = un vote }
session_map_state: dict[str, dict] = {}      # { sid: {"flushes": int, "inflight": bool} }
session_voice_locked: dict[str, set] = {}    # { sid: labels identifiés ACOUSTIQUEMENT — définitifs, les votes LLM ne peuvent pas les changer }
session_bank_miss: dict[str, set] = {}       # { sid: labels déjà comparés à la banque sans correspondance (évite de réémettre voice_not_in_bank en boucle) }
session_pending: dict[str, int] = {}         # { sid: analyses Mistral / fact-checks encore en vol } — l'arrêt propre attend qu'il retombe à 0
session_warned: dict[str, dict] = {}         # { sid: {message: horodatage} } — anti-répétition des server_warning

# LiveTrans architecture

## Realtime pipeline

Chrome remains responsible for the privileged tab capture step. The default
backend keeps one Gemini Live Translate WebSocket session for continuous speech
recognition and Vietnamese translation, then sends only the translated
transcript to the local thainp-tts runtime for speech output.

Pipeline:

tab audio
→ Chrome tabCapture / PCM 16 kHz
→ Gemini 3.5 Live Translate WebSocket
→ thainp-tts local Vietnamese TTS
→ extension panel + speaker output

## Latency and quality levers

- GEMINI_LIVE_MODEL controls the Live Translate model; the default is
  `gemini-3.5-live-translate-preview`.
- Gemini Live returns output transcription and audio. LiveTrans discards the
  Gemini audio and uses only the output transcript, so the selected local
  thainp-tts voice remains the only translated voice.
- The Gemini Live Translate model is the only transcription and translation
  provider in this project. Its generated audio is ignored so local
  thainp-tts remains the only translated voice.
- TTS remains local and is queued separately so a slow translation request does
  not stop audio capture.
- The popup controls local TTS volume from 0–200% and speaking speed from
  0.5x–2x. Speed is sent to the backend and applied with FFmpeg's
  pitch-preserving `atempo` filter; volume remains in the offscreen audio
  player. Both settings are persisted without stopping the active session.
- Gemini Live output is auto-detected at the source-language level and targets
  Vietnamese. As soon as its translated transcript contains a sentence-ending
  mark, that sentence is queued for local TTS without waiting for
  `turn_complete`. Commas are not treated as sentence boundaries.
- If punctuation has not arrived but the unfinished Vietnamese translation
  reaches `TTS_MIN_WORDS` words, LiveTrans queues a fallback chunk of
  `TTS_CHUNK_WORDS` content words (both default to 10) so speech can start
  while the speaker continues. Standalone fillers such as “à”, “ờm”, “ừm”, and
  “hả” do not count toward this threshold.
- The extension does not render a separate partial-preview block. It keeps a
  fixed-height panel with a scrollable transcript, suppresses repeated
  cumulative source text, and only appends translated chunks that are ready for
  the output queue.
- Closing the panel only changes its visibility. Translation and TTS continue
  until the user clicks Dừng; Mở transcript explicitly reopens the panel.
- Standalone conversational fillers such as “à”, “ờm”, “ừm”, and “hả” remain
  visible in the transcript but are skipped by TTS when `TTS_SKIP_FILLERS=true`.

## Privacy and operations

- Capture starts only after the user clicks Bắt đầu.
- Audio is sent to Gemini Live during an active session. Gemini output audio is
  not played; only translated transcript text is sent to local thainp-tts.
- The Gemini API key stays in backend/.env and is never sent to Chrome.
- No meeting audio is stored by default.

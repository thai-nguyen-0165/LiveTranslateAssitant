let captureStream = null;
let captureContext = null;
let captureProcessor = null;
let nextPlaybackTime = 0;
let playbackGain = null;
let playbackGainContext = null;
let playbackVolume = 1;
let originalGain = null;
let originalVolume = 1;
const TARGET_RATE = 16000;

function setPlaybackSettings(message = {}) {
  // Speaking speed is applied by backend atempo to preserve pitch. This
  // player intentionally keeps generated WAVs at their native playback rate.
  const volume = Number(message.volume);
  if (Number.isFinite(volume)) playbackVolume = Math.max(0, Math.min(2, volume / 100));
  if (playbackGain) playbackGain.gain.value = playbackVolume;
  const sourceVolume = Number(message.originalVolume);
  if (Number.isFinite(sourceVolume)) originalVolume = Math.max(0, Math.min(2, sourceVolume / 100));
  if (originalGain) originalGain.gain.value = originalVolume;
}

function getPlaybackDestination(context) {
  if (playbackGainContext !== context) {
    if (playbackGain) {
      try { playbackGain.disconnect(); } catch {}
    }
    playbackGain = context.createGain();
    playbackGain.connect(context.destination);
    playbackGainContext = context;
  }
  playbackGain.gain.value = playbackVolume;
  return playbackGain;
}

// Chrome extension messaging is JSON-serialized in normal MV3 mode. Keep
// audio frames explicit and portable instead of sending ArrayBuffer directly.
function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

function base64ToArrayBuffer(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function downsampleToPcm16(input, inputRate) {
  const ratio = inputRate / TARGET_RATE;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Int16Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < Math.max(start + 1, end); j += 1) sum += input[j] || 0;
    const sample = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    output[i] = sample < 0 ? sample * 32768 : sample * 32767;
  }
  return output;
}

async function startCapture(streamId) {
  await stopCapture();
  captureStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: streamId,
      },
    },
    video: false,
  });
  captureContext = new AudioContext({ latencyHint: "interactive" });
  const source = captureContext.createMediaStreamSource(captureStream);
  // tabCapture mutes the tab's normal output while it is being captured.
  // Route the captured stream back to the speakers so the user hears both
  // the original meeting audio and the translated TTS audio.
  originalGain = captureContext.createGain();
  originalGain.gain.value = originalVolume;
  source.connect(originalGain);
  originalGain.connect(captureContext.destination);
  captureProcessor = captureContext.createScriptProcessor(4096, 1, 1);
  captureProcessor.onaudioprocess = (event) => {
    const pcm = downsampleToPcm16(event.inputBuffer.getChannelData(0), captureContext.sampleRate);
    chrome.runtime.sendMessage({
      type: "PCM_FRAME",
      pcm_b64: arrayBufferToBase64(pcm.buffer),
    }).catch(() => {});
  };
  source.connect(captureProcessor);
  // Keep ScriptProcessor connected so onaudioprocess continues to fire, but
  // do not route a second copy of the original audio to the speakers.
  const captureSink = captureContext.createGain();
  captureSink.gain.value = 0;
  captureProcessor.connect(captureSink);
  captureSink.connect(captureContext.destination);
  await captureContext.resume();
}

async function stopCapture() {
  if (captureProcessor) captureProcessor.disconnect();
  if (captureStream) captureStream.getTracks().forEach((track) => track.stop());
  if (captureContext) await captureContext.close().catch(() => {});
  if (playbackGain) {
    try { playbackGain.disconnect(); } catch {}
  }
  if (originalGain) {
    try { originalGain.disconnect(); } catch {}
  }
  playbackGain = null;
  playbackGainContext = null;
  originalGain = null;
  nextPlaybackTime = 0;
  captureProcessor = null;
  captureStream = null;
  captureContext = null;
}

async function playWav(buffer) {
  const context = captureContext || new AudioContext({ latencyHint: "interactive" });
  const decoded = await context.decodeAudioData(buffer.slice(0));
  const source = context.createBufferSource();
  source.buffer = decoded;
  source.connect(getPlaybackDestination(context));
  const startAt = Math.max(context.currentTime + 0.02, nextPlaybackTime);
  source.start(startAt);
  nextPlaybackTime = startAt + decoded.duration;
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "SET_PLAYBACK_SETTINGS") {
    setPlaybackSettings(message);
  }
  if (message?.type === "START_CAPTURE") {
    startCapture(message.streamId)
      .then(() => chrome.runtime.sendMessage({ type: "CAPTURE_STARTED" }).catch(() => {}))
      .catch((error) => chrome.runtime.sendMessage({
        type: "CAPTURE_ERROR",
        message: `Không lấy được audio tab: ${error.message}`,
      }).catch(() => {}));
  }
  if (message?.type === "STOP_CAPTURE") stopCapture().catch(console.error);
  if (message?.type === "PLAY_AUDIO" && message.wav_b64) {
    playWav(base64ToArrayBuffer(message.wav_b64)).catch((error) => {
      chrome.runtime.sendMessage({
        type: "CAPTURE_ERROR",
        message: `Không phát được giọng dịch: ${error.message}`,
      }).catch(() => {});
    });
  }
});

const $ = (id) => document.getElementById(id);

const DEFAULT_PLAYBACK = { volume: 100, originalVolume: 100, speed: 1 };
const DEFAULT_BACKEND = "http://127.0.0.1:8000";

function normalizeBackendUrl(value) {
  return String(value || DEFAULT_BACKEND).trim().replace(/\/+$/, "") || DEFAULT_BACKEND;
}

function playbackSettings() {
  return {
    volume: Number($('volume').value),
    originalVolume: Number($('original-volume').value),
    speed: Number($('speed').value),
  };
}

function updatePlaybackLabels() {
  $('volume-value').textContent = `${$('volume').value}%`;
  $('original-volume-value').textContent = `${$('original-volume').value}%`;
  $('speed-value').textContent = `${Number($('speed').value).toFixed(2)}x`;
}

function sendPlaybackSettings() {
  updatePlaybackLabels();
  const settings = playbackSettings();
  chrome.storage.local.set({
    ttsVolume: settings.volume,
    originalVolume: settings.originalVolume,
    ttsSpeed: settings.speed,
  });
  chrome.runtime.sendMessage({
    type: 'UPDATE_PLAYBACK_SETTINGS',
    ...settings,
  }).catch(() => { });
}

function setStatus(text, error = false) {
  $("status").textContent = text;
  $("status").style.color = error ? "#ff9a9a" : "#77e0bb";
}

async function loadVoices() {
  const backend = normalizeBackendUrl($("backend").value);
  const response = await fetch(`${backend}/api/voices`);
  if (!response.ok) throw new Error(`Backend HTTP ${response.status}`);
  const payload = await response.json();
  const saved = await chrome.storage.local.get({ voiceId: "" });
  $("voice").replaceChildren(...payload.voices.map((voice) => {
    const option = new Option(voice.name, voice.voice_id);
    option.selected = voice.voice_id === (saved.voiceId || "Ngọc Huyền 2.0");
    return option;
  }));
}

async function loadBackendSetting() {
  const saved = await chrome.storage.local.get({ backendUrl: DEFAULT_BACKEND });
  $("backend").value = normalizeBackendUrl(saved.backendUrl);
}

$("voice").addEventListener("change", () => chrome.storage.local.set({ voiceId: $("voice").value }));
$("backend").addEventListener("change", async () => {
  const backend = normalizeBackendUrl($("backend").value);
  $("backend").value = backend;
  await chrome.storage.local.set({ backendUrl: backend });
  loadVoices().catch((error) => setStatus(`Backend chưa sẵn sàng: ${error.message}`, true));
});
$("volume").addEventListener("input", sendPlaybackSettings);
$("original-volume").addEventListener("input", sendPlaybackSettings);
$("speed").addEventListener("input", sendPlaybackSettings);

$("start").addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !/^https?:/.test(tab.url || "")) throw new Error("Tab hiện tại không hỗ trợ capture.");
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    const response = await chrome.runtime.sendMessage({
      type: "START_SESSION",
      tabId: tab.id,
      streamId,
      voiceId: $("voice").value,
      backendUrl: normalizeBackendUrl($("backend").value),
      ...playbackSettings(),
    });
    if (!response?.ok) throw new Error(response?.error || "Không khởi động được phiên dịch.");
    await chrome.storage.local.set({ voiceId: $("voice").value });
    setStatus("Đang lắng nghe…");
  } catch (error) {
    setStatus(error.message, true);
  }
});

$("show-transcript").addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("Không xác định được tab hiện tại.");
    const response = await chrome.runtime.sendMessage({
      type: "SHOW_TRANSCRIPT",
      tabId: tab.id,
    });
    if (!response?.ok) throw new Error(response?.error || "Chưa bắt đầu tác vụ");
    setStatus("Đã mở transcript trên tab đang dịch.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

$("stop").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "STOP_SESSION" });
  setStatus("Đã dừng.");
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "SESSION_EVENT") return;
  const payload = message.payload || {};
  if (payload.type === "error") setStatus(payload.message, true);
  if (payload.type === "status" && payload.status !== "stopped") {
    setStatus(`LIVE · ${payload.message || "Đang lắng nghe…"}`);
  }
  if (payload.type === "partial") setStatus("LIVE · Đang nhận realtime…");
  if (payload.type === "translation") setStatus(`Đã dịch (${payload.latency_ms} ms)`);
});

async function loadPlaybackSettings() {
  const saved = await chrome.storage.local.get({
    ttsVolume: DEFAULT_PLAYBACK.volume,
    originalVolume: DEFAULT_PLAYBACK.originalVolume,
    ttsSpeed: DEFAULT_PLAYBACK.speed,
  });
  const volume = Number(saved.ttsVolume);
  const originalVolume = Number(saved.originalVolume);
  const speed = Number(saved.ttsSpeed);
  $("volume").value = String(Number.isFinite(volume) ? Math.max(0, Math.min(200, volume)) : DEFAULT_PLAYBACK.volume);
  $("original-volume").value = String(Number.isFinite(originalVolume) ? Math.max(0, Math.min(200, originalVolume)) : DEFAULT_PLAYBACK.originalVolume);
  $("speed").value = String(Number.isFinite(speed) ? Math.max(0.5, Math.min(2, speed)) : DEFAULT_PLAYBACK.speed);
  updatePlaybackLabels();
}

Promise.all([loadPlaybackSettings(), loadBackendSetting()])
  .then(() => loadVoices())
  .catch((error) => setStatus(`Backend chưa sẵn sàng: ${error.message}`, true));

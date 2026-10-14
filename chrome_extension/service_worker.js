let socket = null;
let activeTabId = null;
let session = null;
let offscreenPromise = null;
let playbackSettings = { volume: 100, originalVolume: 100, speed: 1 };
const DEFAULT_BACKEND = "http://127.0.0.1:8000";

function normalizeBackendUrl(value) {
  const backend = String(value || DEFAULT_BACKEND).trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(backend)) {
    throw new Error("Backend URL phải bắt đầu bằng http:// hoặc https://.");
  }
  return backend;
}

function websocketUrl(backend) {
  return `${backend.replace(/^https?:/i, (scheme) => scheme.toLowerCase() === "https:" ? "wss:" : "ws:")}/ws/live`;
}

function normalizePlaybackSettings(message = {}) {
  const volume = Number(message.volume);
  const originalVolume = Number(message.originalVolume);
  const speed = Number(message.speed);
  return {
    volume: Number.isFinite(volume) ? Math.max(0, Math.min(200, volume)) : playbackSettings.volume,
    originalVolume: Number.isFinite(originalVolume)
      ? Math.max(0, Math.min(200, originalVolume))
      : playbackSettings.originalVolume,
    speed: Number.isFinite(speed) ? Math.max(0.5, Math.min(2, speed)) : playbackSettings.speed,
  };
}

function forwardPlaybackSettings() {
  chrome.runtime.sendMessage({
    type: "SET_PLAYBACK_SETTINGS",
    ...playbackSettings,
    // Speed is applied by the TTS backend with pitch preservation. The
    // offscreen player must always play generated WAVs at their native rate.
    speed: 1,
  }).catch(() => { });
}

function pushTtsSpeed() {
  if (socket?.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({
    type: "settings",
    tts_speed: playbackSettings.speed,
  }));
}

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

function sendStatus(payload, targetTabId = activeTabId) {
  chrome.runtime.sendMessage({ type: "SESSION_EVENT", payload }).catch(() => { });
  if (targetTabId !== null) {
    chrome.tabs.sendMessage(targetTabId, { type: "SESSION_EVENT", payload }).catch(() => { });
  }
}

async function ensureOffscreen() {
  if (offscreenPromise) return offscreenPromise;
  offscreenPromise = (async () => {
    if (await chrome.offscreen.hasDocument()) return;
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
      justification: "Thu audio tab và phát audio bản dịch qua loa.",
    });
  })().finally(() => { offscreenPromise = null; });
  return offscreenPromise;
}

async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return true;
  } catch {
    // chrome:// and browser-owned pages cannot receive extension scripts.
    return false;
  }
}

function stopSocket() {
  if (socket) {
    socket.onclose = null;
    socket.close();
  }
  socket = null;
}

async function startSession(message, sendResponse) {
  const previousTabId = activeTabId;
  if (previousTabId !== null && previousTabId !== message.tabId) {
    chrome.runtime.sendMessage({ type: "STOP_CAPTURE" }).catch(() => { });
    sendStatus(
      { type: "status", status: "stopped", message: "Đã chuyển sang tab khác." },
      previousTabId,
    );
  }
  stopSocket();
  activeTabId = message.tabId;
  session = message;
  playbackSettings = normalizePlaybackSettings(message);
  await ensureOffscreen();
  forwardPlaybackSettings();
  await ensureContentScript(activeTabId);

  const params = new URLSearchParams({
    source_lang: "auto",
    voice_id: message.voiceId || "Ngọc Huyền 2.0",
    style: "tu_nhien",
    tts_speed: String(playbackSettings.speed),
  });
  let backendUrl;
  try {
    backendUrl = normalizeBackendUrl(message.backendUrl);
  } catch (error) {
    sendStatus({ type: "error", message: error.message });
    sendResponse({ ok: false, error: error.message });
    return;
  }
  socket = new WebSocket(`${websocketUrl(backendUrl)}?${params}`);
  socket.binaryType = "arraybuffer";
  socket.onopen = () => {
    chrome.runtime.sendMessage({ type: "START_CAPTURE", streamId: message.streamId }).catch(() => { });
    pushTtsSpeed();
    sendStatus({ type: "status", status: "running", message: "Đang lắng nghe tab hiện tại…" });
    sendResponse({ ok: true });
  };
  socket.onmessage = (event) => {
    if (typeof event.data === "string") {
      try {
        const payload = JSON.parse(event.data);
        sendStatus(payload);
      } catch {
        sendStatus({ type: "error", message: "Backend gửi dữ liệu không hợp lệ." });
      }
      return;
    }
    chrome.runtime.sendMessage({
      type: "PLAY_AUDIO",
      wav_b64: arrayBufferToBase64(event.data),
    }).catch(() => { });
  };
  socket.onerror = () => {
    sendStatus({ type: "error", message: "Không kết nối được backend. Hãy chạy server trước." });
    sendResponse({ ok: false, error: "Không kết nối được backend." });
  };
  socket.onclose = () => {
    const closedTabId = activeTabId;
    chrome.runtime.sendMessage({ type: "STOP_CAPTURE" }).catch(() => { });
    sendStatus(
      { type: "status", status: "stopped", message: "Đã dừng phiên dịch." },
      closedTabId,
    );
    socket = null;
    session = null;
    activeTabId = null;
  };
}

async function stopSession() {
  const stoppedTabId = activeTabId;
  stopSocket();
  chrome.runtime.sendMessage({ type: "STOP_CAPTURE" }).catch(() => { });
  sendStatus(
    { type: "status", status: "stopped", message: "Đã dừng phiên dịch." },
    stoppedTabId,
  );
  session = null;
  activeTabId = null;
}

async function showTranscript(tabId, sendResponse) {
  if (activeTabId === null) {
    sendResponse({ ok: false, error: "Chưa bắt đầu tác vụ." });
    return;
  }
  if (tabId !== activeTabId) {
    sendResponse({ ok: false, error: "Phiên dịch đang chạy trên tab khác." });
    return;
  }
  const injected = await ensureContentScript(activeTabId);
  if (!injected) {
    sendResponse({ ok: false, error: "Tab hiện tại không hỗ trợ mở transcript." });
    return;
  }
  sendStatus({ type: "panel", action: "show" }, activeTabId);
  sendResponse({ ok: true });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "UPDATE_PLAYBACK_SETTINGS") {
    playbackSettings = normalizePlaybackSettings(message);
    forwardPlaybackSettings();
    pushTtsSpeed();
    return false;
  }
  if (message?.type === "START_SESSION") {
    startSession(message, sendResponse).catch((error) => {
      sendResponse({ ok: false, error: error.message });
    });
    return true;
  }
  if (message?.type === "STOP_SESSION") {
    stopSession().then(() => sendResponse({ ok: true }));
    return true;
  }
  if (message?.type === "SHOW_TRANSCRIPT") {
    showTranscript(message.tabId, sendResponse).catch((error) => {
      sendResponse({ ok: false, error: error.message });
    });
    return true;
  }
  if (message?.type === "PCM_FRAME") {
    if (socket?.readyState === WebSocket.OPEN && message.pcm_b64) {
      socket.send(base64ToArrayBuffer(message.pcm_b64));
    }
    return false;
  }
  if (message?.type === "CAPTURE_STARTED") {
    sendStatus({ type: "status", status: "capturing", message: "Đã bắt được audio tab; đang chờ câu nói…" });
    return false;
  }
  if (message?.type === "CAPTURE_ERROR") {
    sendStatus({ type: "error", message: message.message });
    return false;
  }
  return false;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId !== activeTabId) return;
  stopSession().catch(() => { });
});

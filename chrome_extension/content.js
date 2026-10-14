(() => {
  if (window.__livetransLoaded) return;
  window.__livetransLoaded = true;

  const panel = document.createElement("section");
  panel.id = "livetrans-panel";
  panel.innerHTML = `
    <div class="livetrans-head">
      <div class="livetrans-brand">
        <span>LiveTrans</span>
        <span class="livetrans-live-badge"><i></i> LIVE</span>
      </div>
      <button title="Ẩn">×</button>
    </div>
    <div class="livetrans-list"></div>
    <div class="livetrans-status">Chưa bắt đầu</div>`;

  const css = document.createElement("style");
  css.textContent = `
    #livetrans-panel { position: fixed; z-index: 2147483647; right: 18px; bottom: 18px; width: 360px; height: min(56vh, 520px); min-height: 180px; max-height: min(72vh, 640px); box-sizing:border-box; overflow: hidden; display:none; flex-direction:column; background: rgba(13,18,28,.96); color: #eef3ff; border: 1px solid #45536b; border-radius: 14px; box-shadow: 0 12px 36px #0008; font: 14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    #livetrans-panel.is-live { border-color: #4dddb0; box-shadow: 0 0 0 1px #4dddb033, 0 12px 36px #0008; }
    .livetrans-head { display:flex; justify-content:space-between; align-items:center; padding:10px 12px; font-weight:700; border-bottom:1px solid #334158; }
    .livetrans-brand { display:flex; align-items:center; gap:8px; }
    .livetrans-live-badge { display:none; align-items:center; gap:4px; padding:2px 6px; border-radius:999px; background:#e24d67; color:#fff; font-size:10px; letter-spacing:.08em; animation:livetrans-glow 1.6s ease-in-out infinite; }
    #livetrans-panel.is-live .livetrans-live-badge { display:inline-flex; }
    .livetrans-live-badge i, .livetrans-live-head i { display:inline-block; width:6px; height:6px; border-radius:50%; background:#fff; box-shadow:0 0 0 0 #fff8; animation:livetrans-pulse 1.2s ease-out infinite; }
    .livetrans-head button { background:none; border:0; color:#aebbd2; font-size:20px; cursor:pointer; }
    .livetrans-status { flex:0 0 auto; padding:8px 12px; color:#9cabc3; font-size:12px; border-top:1px solid #334158; background:#0d121c; }
    .livetrans-list { flex:1 1 auto; min-height:0; height:0; overflow:auto; padding:0 12px 12px; }
    .livetrans-item { border-top:1px solid #2b3547; padding:9px 0; }
    .livetrans-source { color:#9cabc3; font-size:12px; }
    .livetrans-source:empty { display:none; }
    .livetrans-vi { margin-top:3px; color:#fff; font-weight:600; }
    @keyframes livetrans-pulse { 0% { box-shadow:0 0 0 0 #fff9; } 70% { box-shadow:0 0 0 6px #fff0; } 100% { box-shadow:0 0 0 0 #fff0; } }
    @keyframes livetrans-glow { 0%,100% { opacity:.72; } 50% { opacity:1; } }
    @keyframes livetrans-realtime { 0%,100% { border-color:#2dbd91; } 50% { border-color:#73f0c8; box-shadow:0 0 18px #43e0b344; } }
  `;
  document.documentElement.append(css, panel);

  const status = panel.querySelector(".livetrans-status");
  const list = panel.querySelector(".livetrans-list");
  let lastRenderedSource = "";
  let panelDismissed = false;

  const normalizeSource = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const sourceDelta = (sourceText) => {
    const source = normalizeSource(sourceText);
    if (!source) return "";
    const previous = normalizeSource(lastRenderedSource);
    if (!previous) return source;
    if (source === previous || previous.startsWith(source)) return "";
    if (source.startsWith(previous)) return source.slice(previous.length).trim();
    return source;
  };
  const resetTranscript = () => {
    list.replaceChildren();
    lastRenderedSource = "";
  };

  const showPanel = (force = false) => {
    if (panelDismissed && !force) return;
    panel.style.display = "flex";
  };
  const hidePanel = () => {
    panel.style.display = "none";
    panel.classList.remove("is-live");
  };

  panel.querySelector("button").addEventListener("click", () => {
    panelDismissed = true;
    hidePanel();
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== "SESSION_EVENT") return;
    const payload = message.payload || {};

    if (payload.type === "panel" && payload.action === "show") {
      panelDismissed = false;
      showPanel(true);
      panel.classList.add("is-live");
      return;
    }

    if (payload.type === "status") {
      if (payload.status === "stopped") {
        hidePanel();
        return;
      }
      if (payload.status === "running") {
        panelDismissed = false;
        resetTranscript();
      }
      showPanel();
      panel.classList.toggle("is-live", ["running", "capturing", "gemini_live_ready"].includes(payload.status));
      status.textContent = payload.message || payload.status || "";
      return;
    }

    if (payload.type === "error") {
      if (panel.style.display !== "none") status.textContent = `Lỗi: ${payload.message}`;
      return;
    }

    if (payload.type === "partial") {
      showPanel();
      panel.classList.add("is-live");
      status.textContent = "Đang nói...";
      return;
    }

    if (payload.type === "translation") {
      showPanel();
      panel.classList.add("is-live");
      status.textContent = `Độ trễ pipeline: ${payload.latency_ms ?? "—"} ms`;
      const sourceText = payload.source_text || "";
      const translatedText = payload.text_vi || "";
      const last = list.lastElementChild;
      if (last?.dataset.source === sourceText && last?.dataset.translation === translatedText) return;
      const displaySource = sourceDelta(sourceText);
      const item = document.createElement("article");
      item.className = "livetrans-item";
      item.dataset.source = sourceText;
      item.dataset.translation = translatedText;
      item.innerHTML = `<div class="livetrans-source"></div><div class="livetrans-vi"></div>`;
      item.querySelector(".livetrans-source").textContent = displaySource;
      item.querySelector(".livetrans-vi").textContent = translatedText;
      list.append(item);
      lastRenderedSource = sourceText;
      while (list.children.length > 20) list.firstElementChild.remove();
      list.scrollTop = list.scrollHeight;
    }
  });
})();

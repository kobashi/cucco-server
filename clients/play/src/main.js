// Play client entry point: connection glue (same protocol handling contract
// as the reference client), screen routing, and the table screen composed of
// the retained-DOM scene plus overlay layers.

import { CuccoConnection, wsUrlFor } from "../../web-common/connection.js";
import { loadSession, saveSession, clearSession } from "../../web-common/persistence.js";
import { sanitizeWsHost } from "../../web-common/utils.js";
import { createGameState } from "./gameState.js";
import { createTableScene } from "./scene/table.js";
import { renderLobby, renderWaiting } from "./ui/panels.js";
import { renderStatus, renderDock, renderModals, renderLogDrawer } from "./ui/overlays.js";

const screenEl = document.getElementById("screen");

const wsParam = new URLSearchParams(location.search).get("ws");
if (wsParam) {
  localStorage.setItem("cucco_ws_host", sanitizeWsHost(wsParam));
  const url = new URL(location.href);
  url.searchParams.delete("ws");
  history.replaceState(null, "", url);
}

let savedHost = localStorage.getItem("cucco_ws_host") || `${location.hostname || "localhost"}:8765`;
let conn = new CuccoConnection(wsUrlFor(savedHost));

const game = createGameState({
  onChange: () => render(),
  onOp: (op) => {
    // M1: no animation queue yet -- the scene re-syncs via onChange. The op
    // stream is in place for M2's queue to consume.
    if (op.kind === "rejected") actions.resync();
  },
  onToast: showToast,
});
const state = game.state;

// UI-only state (which screen family is showing)
let uiPhase = "name"; // name | lobby | create | join | waiting | table
let connectionStatus = "connecting";

// -- rendering ----------------------------------------------------------------

let sceneRefs = null; // { scene, statusEl, dockEl, modalEl, logEl, headerEl }

function render() {
  // Screen selection mirrors the reference client's routing rules.
  const potRunning = state.table?.dealer_seat != null;
  let target;
  if (state.gameEnded) target = "table"; // game-end modal floats over the final scene
  else if (!state.roomId) target = uiPhase;
  else target = potRunning ? "table" : "waiting";

  if (target !== "table") {
    sceneRefs = null;
    if (target === "waiting") renderWaiting(screenEl, state, actions);
    else renderLobby(screenEl, state, actions, target);
    prependConnBanner();
    return;
  }

  if (!sceneRefs) {
    screenEl.innerHTML = `
      <div class="play-root">
        <header class="play-header">
          <span id="hdr-room"></span><span id="hdr-pot"></span>
        </header>
        <div id="status-holder"></div>
        <div id="scene-holder"></div>
        <div id="dock-holder"></div>
        <div id="log-holder"></div>
        <div id="modal-holder"></div>
      </div>
    `;
    sceneRefs = {
      scene: createTableScene(screenEl.querySelector("#scene-holder")),
      statusEl: screenEl.querySelector("#status-holder"),
      dockEl: screenEl.querySelector("#dock-holder"),
      modalEl: screenEl.querySelector("#modal-holder"),
      logEl: screenEl.querySelector("#log-holder"),
      headerEl: screenEl.querySelector(".play-header"),
    };
  }
  const t = state.table;
  sceneRefs.headerEl.querySelector("#hdr-room").textContent = `卓 ${state.roomId ?? ""}`;
  sceneRefs.headerEl.querySelector("#hdr-pot").textContent = t
    ? `ポット${t.pot_number}・ディール${t.deal_number}`
    : "";
  sceneRefs.scene.sync(state);
  renderStatus(sceneRefs.statusEl, state, game.seatName);
  renderDock(sceneRefs.dockEl, state, actions);
  renderModals(sceneRefs.modalEl, state, actions, game.seatName);
  renderLogDrawer(sceneRefs.logEl, state);
  prependConnBanner();
}

function prependConnBanner() {
  document.querySelector(".conn-banner")?.remove();
  if (connectionStatus === "reconnecting") {
    const banner = document.createElement("div");
    banner.className = "conn-banner";
    banner.textContent = "サーバーとの接続が切れました — 再接続しています…";
    document.body.prepend(banner);
  }
}

function showToast(text) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = text;
  el.classList.add("visible");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.remove("visible"), 4000);
}

// Countdown ticking + prompt expiry (same contract as the reference client:
// the server enforces real deadlines; expired prompts self-dismiss here).
setInterval(() => {
  const now = Date.now();
  let expired = false;
  for (const key of ["dealerReadyPrompt", "turnPrompt", "cuccoWindow", "continuePrompt", "resultPause", "effectWindow"]) {
    if (state[key] && state[key].deadline <= now) {
      state[key] = null;
      expired = true;
    }
  }
  if (expired) {
    render();
    return;
  }
  for (const el of document.querySelectorAll("[data-deadline]")) {
    const remaining = Math.max(0, Math.ceil((Number(el.dataset.deadline) - now) / 1000));
    const text = String(remaining);
    if (el.textContent !== text) el.textContent = text;
  }
}, 250);

// Waiting-room roster poll (join_table replies are unicast; see reference).
setInterval(() => {
  const potRunning = state.table?.dealer_seat != null;
  if (state.roomId && !potRunning && !state.gameEnded) actions.resync();
}, 3000);

// -- actions --------------------------------------------------------------------

function isDeadSessionError(err) {
  return /session_token|no such table/i.test(err?.message ?? "");
}

function persist() {
  saveSession({
    name: state.name,
    playerId: state.playerId,
    sessionToken: state.sessionToken,
    roomId: state.roomId,
    playerType: state.playerType,
    wsHost: savedHost,
  });
}

const actions = {
  setPhase(phase) {
    uiPhase = phase;
    state.error = null;
    render();
  },

  setWsHost(rawHost) {
    const host = sanitizeWsHost(rawHost);
    localStorage.setItem("cucco_ws_host", host);
    savedHost = host;
    conn = new CuccoConnection(wsUrlFor(host));
    wireConnection();
    conn.connect();
    connectionStatus = "connecting";
    render();
  },

  async identify(name, playerType) {
    try {
      await conn.identify(name, playerType);
      state.name = name;
      state.playerId = conn.playerId;
      state.sessionToken = conn.sessionToken;
      state.playerType = playerType;
      state.error = null;
      uiPhase = "lobby";
    } catch (err) {
      state.error = err.message;
    }
    render();
  },

  async createTable(config) {
    try {
      const payload = await conn.createTable(config);
      state.error = null;
      await actions.joinTable(payload.room_id);
    } catch (err) {
      state.error = err.message;
      render();
    }
  },

  async joinTable(roomId) {
    try {
      const snapshot = await conn.joinTable(roomId, null);
      state.roomId = roomId;
      state.error = null;
      game.applySnapshot(snapshot.payload ?? snapshot);
      persist();
    } catch (err) {
      state.error = err.message;
    }
    render();
  },

  async reconnect(saved) {
    conn.playerId = saved.playerId;
    conn.sessionToken = saved.sessionToken;
    try {
      const snapshot = await conn.joinTable(saved.roomId, saved.sessionToken);
      state.name = saved.name;
      state.playerId = saved.playerId;
      state.sessionToken = saved.sessionToken;
      state.roomId = saved.roomId;
      state.playerType = saved.playerType;
      game.applySnapshot(snapshot.payload ?? snapshot);
    } catch (err) {
      if (isDeadSessionError(err)) {
        clearSession();
        state.savedSession = null;
        state.error = `復帰できませんでした: ${err.message}(卓が終了したか、サーバーが再起動された可能性があります)`;
      } else {
        state.error = `再接続に失敗しました: ${err.message} — もう一度お試しください`;
      }
    }
    render();
  },

  forgetSession() {
    clearSession();
    state.savedSession = null;
    uiPhase = "name";
    render();
  },

  resync() {
    if (!state.roomId || !state.sessionToken) return;
    conn
      .joinTable(state.roomId, state.sessionToken)
      .then((snapshot) => {
        game.applySnapshot(snapshot.payload ?? snapshot);
        render();
      })
      .catch((err) => {
        if (isDeadSessionError(err)) {
          clearSession();
          state.savedSession = null;
          state.error = "サーバー側のセッションが失われたため復帰できませんでした。参加し直してください。";
          state.roomId = null;
          uiPhase = "name";
          render();
        } else {
          setTimeout(() => actions.resync(), 3000);
        }
      });
  },

  sendReady() {
    conn.send("ready", {});
    state.readySent = true;
    render();
  },
  sendStartPot: () => conn.send("start_pot", {}),
  sendDealerReady() {
    conn.send("dealer_ready", {});
    state.dealerReadyPrompt = null;
    state.dozoSent = true;
    render();
  },
  sendCambio() {
    conn.send("cambio_declare", {});
    state.turnPrompt = null;
    render();
  },
  sendNoChange() {
    conn.send("no_change_declare", {});
    state.turnPrompt = null;
    render();
  },
  sendCuccoDeclare() {
    conn.send("cucco_declare", {});
    state.cuccoWindow = null;
    render();
  },
  sendCuccoPass() {
    conn.send("cucco_pass", {});
    state.cuccoWindow = null;
    render();
  },
  sendEffectDeclare() {
    conn.send("effect_declare", {});
    state.effectWindow = null;
    render();
  },
  sendEffectPass() {
    conn.send("effect_pass", {});
    state.effectWindow = null;
    render();
  },
  sendContinue(stay) {
    conn.send("continue_declare", { continue: stay });
    state.continuePrompt = null;
    render();
  },
  sendResultAck() {
    conn.send("result_ack", {});
    state.resultPause = null;
    render();
  },

  stayInRoom() {
    state.gameEnded = null;
    state.readySent = false;
    state.lastPotResult = null;
    state.lastDealResult = null;
    state.lastDealOpened = null;
    state.prevDealSummary = null;
    actions.resync();
    render();
  },

  leaveRoom() {
    clearSession();
    state.savedSession = null;
    state.roomId = null;
    state.table = null;
    state.gameEnded = null;
    uiPhase = "lobby";
    render();
  },
};

// -- connection wiring -----------------------------------------------------------

function wireConnection() {
  conn.addEventListener("open", () => {
    connectionStatus = "open";
    if (state.roomId && state.sessionToken) actions.resync();
    render();
  });
  conn.addEventListener("reconnecting", () => {
    connectionStatus = "reconnecting";
    render();
  });
  conn.addEventListener("close", () => {
    if (connectionStatus === "open") {
      connectionStatus = "reconnecting";
      render();
    }
  });
  conn.addEventListener("event", (ev) => {
    if (ev.detail.type === "state_snapshot") {
      // Snapshot handling needs persist() alongside the state update.
      if (!state.gameEnded || ev.detail.payload.game_finished) {
        game.handleEvent(ev.detail.type, ev.detail.payload);
        persist();
      }
      return;
    }
    game.handleEvent(ev.detail.type, ev.detail.payload);
  });
}

// -- boot -------------------------------------------------------------------------

wireConnection();
conn.connect();

const saved = loadSession();
if (saved && saved.sessionToken && saved.roomId) {
  state.savedSession = saved;
}
render();

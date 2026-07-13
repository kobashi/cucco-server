// Play client entry point: connection glue (same protocol handling contract
// as the reference client), screen routing, and the table screen composed of
// the retained-DOM scene plus overlay layers.

import { CuccoConnection, wsUrlFor } from "../../web-common/connection.js";
import { loadSession, saveSession, clearSession } from "../../web-common/persistence.js";
import { sanitizeWsHost } from "../../web-common/utils.js";
import { createGameState } from "./gameState.js";
import { createTableScene, cardHTML } from "./scene/table.js";
import { createQueue, fly, pause } from "./anim/queue.js";
import { banner, shake } from "./anim/effects.js";
import { REFUSAL_LABELS } from "../../web-common/cards.js";
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

const queue = createQueue();

const game = createGameState({
  onChange: () => render(),
  onOp: handleOp,
  onToast: showToast,
});
const state = game.state;

// UI-only state (which screen family is showing)
let uiPhase = "name"; // name | lobby | create | join | waiting | table
let connectionStatus = "connecting";

// -- op -> animation mapping -----------------------------------------------------
//
// Ops arrive AFTER the state has already mutated (state is authoritative);
// what's queued here is purely how the change is shown. While the queue is
// busy, render() leaves the scene alone -- each queued sequence ends with
// its own scene.sync, so slots reveal their new contents only when the
// flight lands. Prompts addressed to me fast-forward everything (the server
// clock doesn't wait for theatrics).

const scene = () => sceneRefs?.scene ?? null;
const syncStep = () => queue.enqueue(async () => sceneRefs?.scene?.sync(state));

function handleOp(op) {
  switch (op.kind) {
    case "rejected":
      actions.resync();
      return;

    case "prompt":
      queue.fastForward();
      return;

    case "rebuild":
      queue.clear();
      return; // onChange render syncs immediately once the queue is empty

    case "deal_started": {
      const seatsInOrder = (state.table?.seats ?? []).filter((s) => s.in_current_pot !== false).map((s) => s.player_id);
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        for (const pid of seatsInOrder) {
          await fly(queue, { fromEl: sc.deckEl(), toEl: sc.slotEl(pid), html: cardHTML(null), duration: 160 });
        }
      });
      syncStep();
      return;
    }

    case "exchange": {
      const { requester, target } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        await Promise.all([
          fly(queue, { fromEl: sc.slotEl(requester), toEl: sc.slotEl(target), html: cardHTML(null), duration: 550 }),
          fly(queue, { fromEl: sc.slotEl(target), toEl: sc.slotEl(requester), html: cardHTML(null), duration: 550 }),
        ]);
      });
      syncStep();
      return;
    }

    case "deck_exchange": {
      const { actor, givenUp } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        await fly(queue, { fromEl: sc.deckEl(), toEl: sc.slotEl(actor), html: cardHTML(null), duration: 450 });
        await fly(queue, { fromEl: sc.slotEl(actor), toEl: sc.discardEl(), html: cardHTML(givenUp), duration: 450 });
      });
      syncStep();
      return;
    }

    case "deck_refused": {
      const { drawn, reason } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        await fly(queue, { fromEl: sc.deckEl(), toEl: sc.discardEl(), html: cardHTML(drawn), duration: 450 });
        await banner(queue, `山札: ${drawn} — ${REFUSAL_LABELS[reason] ?? reason}`, "warn");
      });
      syncStep();
      return;
    }

    case "refused": {
      const { target, reason, revealed } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        await shake(queue, sc.seatEl(target));
        const label = REFUSAL_LABELS[reason] ?? reason;
        await banner(queue, revealed ? `${label}(${revealed})` : label, "warn");
      });
      syncStep();
      return;
    }

    case "cucco_declared": {
      queue.enqueue(async (instant) => {
        if (instant) return;
        await banner(queue, `クク宣言!! — ${game.seatName(op.player)}`, "cucco", 1500);
      });
      syncStep();
      return;
    }

    case "disqualified": {
      const { player, card } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        if (card) {
          await fly(queue, { fromEl: sc.slotEl(player), toEl: sc.discardEl(), html: cardHTML(card), duration: 450 });
        }
        await banner(queue, `${game.seatName(op.player)} 失格`, "danger");
      });
      syncStep();
      return;
    }

    case "reshuffle": {
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        await fly(queue, { fromEl: sc.discardEl(), toEl: sc.deckEl(), html: cardHTML(null), duration: 500 });
      });
      syncStep();
      return;
    }

    case "deal_opened":
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc) return;
        sc.sync(state); // faces are now in the slots
        if (instant) return;
        const faces = sc.root.querySelectorAll(".card-slot .card-face");
        faces.forEach((el, i) => {
          const anim = el.animate(
            [
              { transform: "rotateY(90deg) scale(1.06)", opacity: 0.4 },
              { transform: "rotateY(0deg) scale(1)", opacity: 1 },
            ],
            { duration: 260, delay: i * 60, easing: "ease-out", fill: "backwards" }
          );
          queue._track(anim);
        });
        await pause(queue, 260 + faces.length * 60);
      });
      return;

    case "chips_paid": {
      const { player } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        await fly(queue, { fromEl: sc.seatEl(player), toEl: sc.potEl(), html: '<div class="chip-ghost">🪙</div>', duration: 500 });
      });
      syncStep();
      return;
    }

    case "pot_result": {
      if (op.result === "won") {
        const { winner } = op;
        queue.enqueue(async (instant) => {
          const sc = scene();
          if (!sc || instant) return;
          await fly(queue, { fromEl: sc.potEl(), toEl: sc.seatEl(winner), html: '<div class="chip-ghost">💰</div>', duration: 600 });
        });
      }
      syncStep();
      return;
    }

    default:
      syncStep();
      return;
  }
}

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

  let justCreated = false;
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
    justCreated = true;
  }
  const t = state.table;
  sceneRefs.headerEl.querySelector("#hdr-room").textContent = `卓 ${state.roomId ?? ""}`;
  sceneRefs.headerEl.querySelector("#hdr-pot").textContent = t
    ? `ポット${t.pot_number}・ディール${t.deal_number}`
    : "";
  // While animations are in flight, the scene is owned by the queue (each
  // sequence ends with its own sync); the overlays always track live state.
  if (justCreated || !queue.busy) sceneRefs.scene.sync(state);
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

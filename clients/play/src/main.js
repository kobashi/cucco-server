// Play client entry point: connection glue (same protocol handling contract
// as the reference client), screen routing, and the table screen composed of
// the retained-DOM scene plus overlay layers.

import { CuccoConnection, wsUrlFor } from "../../web-common/connection.js";
import { loadSession, saveSession, clearSession } from "../../web-common/persistence.js";
import { sanitizeWsHost } from "../../web-common/utils.js";
import { createGameState } from "./gameState.js";
import { createTableScene, cardHTML } from "./scene/table.js";
import { createQueue, fly, pause } from "./anim/queue.js";
import { banner, shake, flipReveal, effectMotion, confirmPulse } from "./anim/effects.js";
import { createSound } from "./anim/sound.js";
import { REFUSAL_LABELS, CAUSE_LABELS } from "../../web-common/cards.js";
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
const sound = createSound();

// Effect-activation sounds, keyed by the refusal/deck-draw reason tokens.
const REASON_SOUNDS = {
  house_horse_skip: "skip",
  horse_house_chain: "skip",
  human_refusal: "human",
  human_deck_draw: "human",
  cat_meow: "cat",
  cat_deck_draw: "cat",
  cucco_refusal: "cucco",
};

// How long the result pane will wait for the animation queue before showing
// itself regardless. Comfortably covers a deal's trailing effect + open
// animations, and leaves the bulk of the server's pause for actually reading
// the result.
const RESULT_PANE_GRACE_MS = 2000;

// Pacing for the card-by-card effect beats. Deliberate enough that every
// player can follow who did what: a card flies, is turned face-up, is read,
// then resolves -- one card at a time, like a physical table.
const FLIGHT_MS = 550; // a single card's flight (deck<->seat<->discard)
const REVEAL_HOLD_MS = 750; // how long a turned-up card sits so the table reads it

// Refusal reason -> the on-card motion its effect plays (anim/effects.js).
const REASON_MOTIONS = {
  house_horse_skip: "skip",
  human_refusal: "human",
  cat_meow: "cat",
};

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
      // Don't hard-snap the scene -- speed the pending effect chain up so I
      // still see what just happened before deciding. My action buttons are
      // already live off state, so this never blocks me.
      queue.hurry();
      sound.play("my_turn");
      return;

    case "rebuild":
      queue.clear();
      return; // onChange render syncs immediately once the queue is empty

    // The result pane explains what the animations just showed (the クク
    // reveal, the effect that fired, the open flip), so it waits BEHIND them
    // in the queue rather than covering them. Queued last, it runs once the
    // steps ahead of it have played -- or immediately, if a fast-forward
    // already flushed them.
    case "result_pause": {
      let revealed = false;
      const reveal = () => {
        if (revealed) return;
        revealed = true;
        state.resultPauseReady = true;
        render();
      };
      queue.enqueue(async () => reveal());
      // Safety net: the pane must never miss the server's pause window. The
      // server does not wait for animations, so if the queue is still busy
      // after this grace period, snap the remaining steps and show the pane
      // anyway -- a late pane is bad, a pane the player never sees is worse.
      setTimeout(() => {
        if (revealed) return;
        queue.fastForward();
        requestAnimationFrame(reveal); // let the flushed ghosts clear first
      }, RESULT_PANE_GRACE_MS);
      return;
    }

    case "deal_started": {
      const seatsInOrder = (state.table?.seats ?? []).filter((s) => s.in_current_pot !== false).map((s) => s.player_id);
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        for (const pid of seatsInOrder) {
          sound.play("deal");
          await fly(queue, { fromEl: sc.deckEl(), toEl: sc.slotEl(pid), html: cardHTML(null), duration: 160 });
        }
      });
      syncStep();
      return;
    }

    case "no_change": {
      const { player } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("pass");
        await confirmPulse(queue, sc.slotEl(player));
      });
      syncStep();
      return;
    }

    case "left_pot": {
      const { player } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("leave");
        await banner(queue, `${game.seatName(player)} が離脱`, "warn");
      });
      syncStep();
      return;
    }

    case "exchange": {
      const { requester, target } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("exchange");
        await Promise.all([
          fly(queue, { fromEl: sc.slotEl(requester), toEl: sc.slotEl(target), html: cardHTML(null), duration: FLIGHT_MS }),
          fly(queue, { fromEl: sc.slotEl(target), toEl: sc.slotEl(requester), html: cardHTML(null), duration: FLIGHT_MS }),
        ]);
      });
      syncStep();
      return;
    }

    case "deck_exchange": {
      const { actor, givenUp, newCard } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        // Drawing from the deck is public at a physical table: fly the drawn
        // card face-up to the actor so everyone sees what came off the deck.
        sound.play("deal");
        await fly(queue, { fromEl: sc.deckEl(), toEl: sc.slotEl(actor), html: cardHTML(newCard), duration: FLIGHT_MS });
        sc.sync(state); // the actor's slot now holds the revealed drawn card
        await banner(queue, `${game.seatName(actor)} が山札から ${newCard} を引く`, "info");
        await pause(queue, REVEAL_HOLD_MS);
        // The card given up lands face-up on the discard pile.
        sound.play("flip");
        await fly(queue, { fromEl: sc.slotEl(actor), toEl: sc.discardEl(), html: cardHTML(givenUp), duration: FLIGHT_MS });
      });
      syncStep();
      return;
    }

    case "deck_refused": {
      const { actor, drawn, reason } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        // The drawn card is public and immediately discarded face-up; the
        // disqualification it triggers is narrated by the disqualified op next.
        sound.play("deal");
        await fly(queue, { fromEl: sc.deckEl(), toEl: sc.discardEl(), html: cardHTML(drawn), duration: FLIGHT_MS });
        sound.play(REASON_SOUNDS[reason] ?? "flip");
        await banner(queue, `${game.seatName(actor)} が山札から ${drawn} を引く`, "warn");
        await pause(queue, REVEAL_HOLD_MS);
      });
      syncStep();
      return;
    }

    case "refused": {
      const { target, reason, revealed } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play(REASON_SOUNDS[reason] ?? "skip");
        const motion = REASON_MOTIONS[reason];
        const label = REFUSAL_LABELS[reason] ?? reason;
        if (revealed) {
          // The refusing card's identity became public: flip it up in the
          // target's slot and play the effect's motion so everyone sees it.
          sc.sync(state); // the revealed face is now in the target's slot
          const cardEl = sc.slotEl(target)?.querySelector(".card-face");
          await flipReveal(queue, cardEl);
          if (motion) await effectMotion(queue, cardEl, motion);
          await banner(queue, `${game.seatName(target)}: ${label}(${revealed})`, "warn");
        } else {
          // 馬/家 with reveal off: the card stays hidden, just react.
          await shake(queue, sc.seatEl(target));
          await banner(queue, `${game.seatName(target)}: ${label}`, "warn");
        }
        await pause(queue, REVEAL_HOLD_MS);
      });
      syncStep();
      return;
    }

    case "cucco_declared": {
      const { player } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("cucco");
        sc.sync(state); // the declarer's クク is now revealed in their slot
        const cardEl = sc.slotEl(player)?.querySelector(".card-face");
        await flipReveal(queue, cardEl);
        await effectMotion(queue, cardEl, "cucco");
        await banner(queue, `クク宣言!! — ${game.seatName(player)}`, "cucco", 1500);
        await pause(queue, REVEAL_HOLD_MS);
      });
      syncStep();
      return;
    }

    case "disqualified": {
      const { player, card, cause } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("disqualified");
        const label = CAUSE_LABELS[cause] ?? cause;
        const slot = sc.slotEl(player);
        if (card && slot) {
          // Disclosure is on: turn the offending card face-up in the seat so
          // everyone sees exactly why this player is out, hold, then discard.
          slot.innerHTML = cardHTML(card);
          const cardEl = slot.querySelector(".card-face");
          await flipReveal(queue, cardEl);
          if (card === "道化") await effectMotion(queue, cardEl, "joker");
          await banner(queue, `${game.seatName(player)} 失格 — ${label}`, "danger");
          await pause(queue, REVEAL_HOLD_MS);
          sound.play("deal");
          await fly(queue, { fromEl: slot, toEl: sc.discardEl(), html: cardHTML(card), duration: FLIGHT_MS });
        } else {
          // Disclosure deferred (card hidden): still announce who and why.
          await banner(queue, `${game.seatName(player)} 失格 — ${label}`, "danger");
          await pause(queue, REVEAL_HOLD_MS);
        }
      });
      syncStep();
      return;
    }

    case "reshuffle": {
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("reshuffle");
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
        sound.play("open");
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
        sound.play("chip");
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
          sound.play("pot_win");
          await fly(queue, { fromEl: sc.potEl(), toEl: sc.seatEl(winner), html: '<div class="chip-ghost">💰</div>', duration: 600 });
        });
      }
      syncStep();
      return;
    }

    case "game_ended":
      sound.play("pot_win");
      syncStep();
      return;

    default:
      syncStep();
      return;
  }
}

// -- sound toggle (floats outside #screen so re-renders never remove it) --

function mountSoundToggle() {
  const btn = document.createElement("button");
  btn.id = "sound-toggle";
  btn.type = "button";
  const refresh = () => {
    // Labeled so it reads as a sound control, not a mystery icon.
    btn.textContent = sound.enabled ? "🔊 効果音 ON" : "🔇 効果音 OFF";
    btn.title = sound.enabled ? "効果音: ON(クリックでOFF)" : "効果音: OFF(クリックでON)";
    btn.classList.toggle("off", !sound.enabled);
  };
  btn.addEventListener("click", () => {
    sound.toggle();
    if (sound.enabled) sound.play("chip"); // audible confirmation
    refresh();
  });
  refresh();
  document.body.appendChild(btn);
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
mountSoundToggle();

const saved = loadSession();
if (saved && saved.sessionToken && saved.roomId) {
  state.savedSession = saved;
}
render();

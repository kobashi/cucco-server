import { CuccoConnection, wsUrlFor } from "./connection.js";
import { createStore, pushLog, seatName } from "./state.js";
import { loadSession, saveSession, clearSession } from "./persistence.js";
import { turnOrderFor, advanceTurn } from "./deriveTurn.js";
import { sanitizeWsHost } from "./utils.js";
import * as lobby from "./views/lobby.js";
import * as waitingRoom from "./views/waiting_room.js";
import * as table from "./views/table.js";
import * as result from "./views/result.js";

const appEl = document.getElementById("app");
const { state, notify, subscribe } = createStore();

// `?ws=host[:port]` lets a shared link auto-configure the connection target
// (e.g. a GitHub Pages-hosted client pointing at a cloudflared-tunneled
// server on a different, possibly-random, hostname). Persist it and strip it
// from the URL so a later reload/share doesn't need the param repeated.
const wsParam = new URLSearchParams(location.search).get("ws");
if (wsParam) {
  localStorage.setItem("cucco_ws_host", sanitizeWsHost(wsParam));
  const url = new URL(location.href);
  url.searchParams.delete("ws");
  history.replaceState(null, "", url);
}

let savedHost = localStorage.getItem("cucco_ws_host") || `${location.hostname || "localhost"}:8765`;
let conn = new CuccoConnection(wsUrlFor(savedHost));

// -- rendering -----------------------------------------------------------

function render() {
  appEl.innerHTML = "";
  const screens = { name: lobby, lobby: lobby, create: lobby, join: lobby, waiting: waitingRoom, table, result: table, ended: result };
  const view = screens[state.screen] ?? lobby;
  view.render(appEl, state, actions);
}
subscribe(render);

function update(mutator) {
  mutator();
  notify();
}

// -- countdown ticking (display only; the server enforces the real deadline) --
// Clears prompts whose deadline has passed: the server never notifies a
// unicast-prompt timeout beyond turn_prompt (turn_timeout_consumed) and
// cucco_window (silently drops to the next holder) -- without this, an
// expired modal would keep overlaying whatever comes next.
//
// Countdown digits are updated IN PLACE via [data-deadline] spans rather than
// through notify(): a full innerHTML re-render 4x/second tears down and
// rebuilds every button while the user is trying to click it, which is what
// made the UI feel unresponsive. notify() fires only on an actual state
// change (a prompt expiring).
setInterval(() => {
  const now = Date.now();
  let expired = false;
  for (const key of ["dealerReadyPrompt", "turnPrompt", "cuccoWindow", "continuePrompt"]) {
    if (state[key] && state[key].deadline <= now) {
      state[key] = null;
      expired = true;
    }
  }
  if (expired) {
    notify();
    return;
  }
  for (const el of document.querySelectorAll("[data-deadline]")) {
    const remaining = Math.max(0, Math.ceil((Number(el.dataset.deadline) - now) / 1000));
    const text = String(remaining);
    if (el.textContent !== text) el.textContent = text;
  }
}, 250);

// The server only sends `state_snapshot` to the session that just joined
// (dispatch.py's `_handle_join_table` doesn't broadcast to the rest of the
// table) -- without this, everyone already in the waiting room would never
// see later joiners show up until the first pot's own broadcast snapshot.
// Cheap and idempotent, so a plain poll is simplest.
setInterval(() => {
  if (state.screen === "waiting") actions.resync();
}, 3000);

function showToast(text) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = text;
  el.classList.add("visible");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.remove("visible"), 4000);
}

// -- actions exposed to views ---------------------------------------------

const actions = {
  setWsHost(rawHost) {
    const host = sanitizeWsHost(rawHost);
    localStorage.setItem("cucco_ws_host", host);
    savedHost = host;
    conn = new CuccoConnection(wsUrlFor(host));
    wireConnection();
    conn.connect();
    update(() => (state.connectionStatus = "connecting"));
  },

  async identify(name, playerType) {
    try {
      await conn.identify(name, playerType);
      update(() => {
        state.name = name;
        state.playerId = conn.playerId;
        state.sessionToken = conn.sessionToken;
        state.playerType = playerType;
        state.screen = "lobby";
        state.error = null;
      });
    } catch (err) {
      update(() => (state.error = err.message));
    }
  },

  async createTable(config) {
    try {
      const payload = await conn.createTable(config);
      update(() => (state.error = null));
      await actions.joinTable(payload.room_id);
    } catch (err) {
      update(() => (state.error = err.message));
    }
  },

  async joinTable(roomId) {
    try {
      const snapshot = await conn.joinTable(roomId, null);
      update(() => {
        state.roomId = roomId;
        state.error = null;
        applySnapshot(snapshot);
        persist();
      });
    } catch (err) {
      update(() => (state.error = err.message));
    }
  },

  async reconnect(saved) {
    conn.playerId = saved.playerId;
    conn.sessionToken = saved.sessionToken;
    try {
      const snapshot = await conn.joinTable(saved.roomId, saved.sessionToken);
      update(() => {
        state.name = saved.name;
        state.playerId = saved.playerId;
        state.sessionToken = saved.sessionToken;
        state.roomId = saved.roomId;
        state.playerType = saved.playerType;
        applySnapshot(snapshot);
      });
    } catch (err) {
      clearSession();
      update(() => (state.error = `再接続に失敗しました: ${err.message}`));
    }
  },

  forgetSession() {
    clearSession();
    update(() => {
      state.screen = "name";
    });
  },

  sendReady() {
    conn.send("ready", {});
    update(() => (state.readySent = true));
  },
  sendStartPot() {
    conn.send("start_pot", {});
  },
  sendDealerReady() {
    conn.send("dealer_ready", {});
    update(() => {
      state.dealerReadyPrompt = null;
      state.dozoSent = true;
    });
  },
  sendCambio() {
    conn.send("cambio_declare", {});
    update(() => (state.turnPrompt = null));
  },
  sendNoChange() {
    conn.send("no_change_declare", {});
    update(() => (state.turnPrompt = null));
  },
  sendCuccoDeclare() {
    conn.send("cucco_declare", {});
    update(() => (state.cuccoWindow = null));
  },
  sendCuccoPass() {
    conn.send("cucco_pass", {});
    update(() => (state.cuccoWindow = null));
  },
  sendContinue(stay) {
    conn.send("continue_declare", { continue: stay });
    update(() => (state.continuePrompt = null));
  },

  backToLobby() {
    update(() => {
      state.screen = "lobby";
      state.table = null;
      state.roomId = null;
      state.gameEnded = null;
    });
  },

  resync() {
    if (!state.roomId || !state.sessionToken) return;
    conn.joinTable(state.roomId, state.sessionToken).then((snapshot) => update(() => applySnapshot(snapshot)));
  },
};

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

function applySnapshot(snapshot) {
  state.table = snapshot;
  state.yourHand = snapshot.your_hand;
  state.currentTurnSeat = snapshot.current_turn_seat;
  state.potChips = snapshot.pot_chips ?? 0;
  state.firstActionSeen = (snapshot.declarations_this_deal ?? []).length > 0;
  const potRunning = snapshot.dealer_seat != null;
  if (state.playerType === "spectator") {
    state.screen = potRunning ? "table" : "waiting";
  } else {
    state.screen = potRunning ? "table" : "waiting";
  }
  state.dealerReadyPrompt = null;
  state.turnPrompt = null;
  state.cuccoWindow = null;
  state.continuePrompt = null;
}

function mergeChips(chipsNow) {
  if (!state.table || !chipsNow) return;
  for (const seat of state.table.seats) {
    if (chipsNow[seat.player_id] !== undefined) seat.chips = chipsNow[seat.player_id];
  }
}

// -- wire protocol event handling ------------------------------------------

function wireConnection() {
  conn.addEventListener("open", () => {
    update(() => (state.connectionStatus = "open"));
    // A transport-level reconnect (network blip, not a page reload) gets a
    // brand new server-side session with no `join_table` on it yet -- resend
    // it so we rebind to the existing player_id instead of silently
    // rejecting every subsequent action with "must join_table first".
    if (state.roomId && state.sessionToken) actions.resync();
  });
  conn.addEventListener("reconnecting", () => update(() => (state.connectionStatus = "reconnecting")));
  conn.addEventListener("reconnect_failed", () => update(() => (state.connectionStatus = "disconnected")));
  conn.addEventListener("close", () => {
    if (state.connectionStatus === "open") update(() => (state.connectionStatus = "reconnecting"));
  });

  conn.addEventListener("event", (ev) => handleEvent(ev.detail.type, ev.detail.payload));
}

function handleEvent(type, p) {
  switch (type) {
    case "state_snapshot":
      if (state.gameEnded) return; // the ranking screen is final; don't let a trailing snapshot bounce us off it
      update(() => {
        applySnapshot(p);
        persist();
      });
      return;

    case "action_rejected":
      update(() => {
        state.error = p.reason;
        pushLog(state, `[エラー] ${p.reason}`);
      });
      actions.resync();
      return;

    case "pot_started":
      update(() => {
        if (!state.table) return;
        state.table.dealer_seat = p.dealer_id;
        state.table.pot_number = p.pot_number;
        state.table.deal_number = 0;
        state.table.deck_remaining_count = 44;
        state.table.discard_pile = [];
        state.table.provenance_map = {};
        state.table.declarations_this_deal = [];
        mergeChips(p.chips_now);
        state.potChips = p.pot_chips ?? 0;
        state.lastDealResult = null;
        state.lastPotResult = null;
        state.lastDealOpened = null;
        state.screen = "table";
        pushLog(state, `ポット ${p.pot_number} 開始(親: ${seatName(state, p.dealer_id)}、ポット${state.potChips}枚)`);
      });
      return;

    case "deal_started":
      update(() => {
        if (!state.table) return;
        state.yourHand = p.your_hand;
        state.table.deck_remaining_count = p.deck_remaining_count;
        state.table.declarations_this_deal = [];
        state.table.deal_number = (state.table.deal_number || 0) + 1;
        state.disqualifiedThisDeal = false;
        state.disqualifiedIdsThisDeal = new Set();
        state.requiredChipsByPlayer = {};
        state.pendingContinueIds = new Set();
        state.dozoSent = false;
        state.firstActionSeen = false;
        state._discardPileLenAtDealStart = state.table.discard_pile.length;
        state.lastDealOpened = null;
        state.lastDealResult = null;
        const order = turnOrderFor(state.table, state.disqualifiedIdsThisDeal);
        state.currentTurnSeat = order[0] ?? null;
        pushLog(state, "配布されました");
      });
      return;

    case "dealer_ready":
      update(() => {
        state.dealerReadyPrompt = { timeoutSec: p.timeout_sec, deadline: Date.now() + p.timeout_sec * 1000 };
      });
      return;

    case "turn_prompt":
      update(() => {
        state.turnPrompt = { timeoutSec: p.timeout_sec, deadline: Date.now() + p.timeout_sec * 1000 };
        state.currentTurnSeat = state.playerId;
      });
      return;

    case "cucco_window":
      update(() => {
        state.cuccoWindow = { timeoutSec: p.timeout_sec, deadline: Date.now() + p.timeout_sec * 1000 };
      });
      return;

    case "continue_prompted":
      // Broadcast summary (has required_chips); the unicast continue_prompt
      // that follows for the affected player only carries timeout_sec.
      update(() => {
        state.requiredChipsByPlayer[p.player_id] = p.required_chips;
        state.pendingContinueIds.add(p.player_id);
      });
      return;

    case "continue_prompt":
      update(() => {
        state.continuePrompt = {
          requiredChips: state.requiredChipsByPlayer[state.playerId],
          timeoutSec: p.timeout_sec,
          deadline: Date.now() + p.timeout_sec * 1000,
        };
      });
      return;

    case "no_change_declared":
    case "turn_timeout_consumed":
      update(() => {
        if (!state.table) return;
        state.firstActionSeen = true;
        state.table.declarations_this_deal.push({
          player_id: p.player_id,
          action: "no_change",
          via_timeout: type === "turn_timeout_consumed",
        });
        if (p.player_id === state.playerId) {
          state.turnPrompt = null;
          if (type === "turn_timeout_consumed") showToast("時間切れでノーチェンジになりました");
        }
        pushLog(state, `${seatName(state, p.player_id)}: ノンカンビオ${type === "turn_timeout_consumed" ? "(時間切れ)" : ""}`);
        advanceTurn(state, p.player_id);
      });
      return;

    case "exchange_result":
      update(() => {
        state.firstActionSeen = true;
        handleExchangeResult(p);
      });
      return;

    case "deck_reshuffled":
      update(() => {
        if (!state.table) return;
        state.table.deck_remaining_count = p.remaining_count;
        state.table.discard_pile = [];
        state.table.provenance_map = {};
        showToast("山札が捨て札から再構築されました");
        pushLog(state, "山札を再構築しました");
      });
      return;

    case "cucco_declared":
      update(() => {
        state.firstActionSeen = true;
        state.turnPrompt = null;
        state.cuccoWindow = null;
        pushLog(state, `${seatName(state, p.player_id)} がクク宣言!`);
      });
      return;

    case "player_disqualified":
      update(() => {
        pushLog(state, `${seatName(state, p.player_id)} が失格(${p.cause})`);
        state.disqualifiedIdsThisDeal.add(p.player_id);
        if (p.card && state.table) {
          // Immediate-disclosure setting: this card is already public --
          // reflect it in the live discard pile now rather than waiting for
          // deal_result (which only arrives once the whole deal concludes).
          const originalHolder = state.table.provenance_map?.[p.player_id] ?? null;
          state.table.discard_pile = [
            ...(state.table.discard_pile || []),
            { card: p.card, original_holder: originalHolder, discarded_via: "disqualification" },
          ];
        }
        if (p.player_id === state.playerId) {
          state.disqualifiedThisDeal = true;
          state.yourHand = null;
          state.turnPrompt = null;
          state.cuccoWindow = null;
          state.dealerReadyPrompt = null;
        }
      });
      return;

    case "dealer_changed":
      update(() => {
        if (state.table) state.table.dealer_seat = p.player_id;
      });
      return;

    case "chips_paid":
      update(() => {
        if (!state.table) return;
        const seat = state.table.seats.find((s) => s.player_id === p.player_id);
        if (seat) seat.chips = p.chips_now;
        state.pendingContinueIds.delete(p.player_id); // paying to stay answers the continue prompt
        // Increment only; pot_started/deal_result/state_snapshot carry the
        // absolute pot_chips and re-sync any drift.
        state.potChips += p.amount ?? 0;
        pushLog(state, `${seatName(state, p.player_id)} が ${p.amount} 枚をポットへ(計${state.potChips}枚)`);
      });
      return;

    case "player_left_pot":
      update(() => {
        if (!state.table) return;
        const seat = state.table.seats.find((s) => s.player_id === p.player_id);
        if (seat) seat.in_current_pot = false;
        state.pendingContinueIds.delete(p.player_id); // declining/insolvency also answers it
        pushLog(state, `${seatName(state, p.player_id)} がポットを抜けました(${p.reason})`);
      });
      return;

    case "deal_opened":
      update(() => {
        state.lastDealOpened = p;
        pushLog(state, "オープン!");
      });
      return;

    case "deal_result":
      update(() => {
        state.lastDealResult = p;
        mergeChips(p.chips_now);
        if (p.pot_chips !== undefined) state.potChips = p.pot_chips;
        if (state.table) {
          // Replace (not append to) this deal's slice of the pile with the
          // server's authoritative list -- it supersedes any live guesses
          // made from player_disqualified (immediate-disclosure) and adds
          // any deferred-disclosure cards revealed only now.
          const before = state._discardPileLenAtDealStart ?? state.table.discard_pile.length;
          state.table.discard_pile = [...state.table.discard_pile.slice(0, before), ...p.discarded_cards];
          if (p.next_dealer) state.table.dealer_seat = p.next_dealer;
        }
        state.turnPrompt = null;
        state.cuccoWindow = null;
        state.dealerReadyPrompt = null;
        state.currentTurnSeat = null;
        pushLog(state, `ディール結果: 敗者 ${p.losers.map((id) => seatName(state, id)).join(", ") || "なし"}`);
      });
      return;

    case "pot_result":
      update(() => {
        state.lastPotResult = p;
        mergeChips(p.chips_now);
        if (p.result === "won") state.potChips = 0; // wiped_out keeps the carryover on the table
        pushLog(
          state,
          p.result === "won"
            ? `${seatName(state, p.winner)} が ${p.amount} 枚を獲得!`
            : `このポット(${p.amount}枚)は次のポットへ持ち越しになりました`
        );
      });
      return;

    case "game_ended":
      update(() => {
        state.gameEnded = p;
        state.screen = "ended";
        clearSession();
      });
      return;

    default:
      return; // identified/table_created handled by the request/response
      // helpers in connection.js; evaluation_summary etc. are out of scope.
  }
}

// A single cambio turn can produce several `exchange_result` events in a row
// when the target holds 馬/家 (the request chains to the next player, or to
// the deck). Only the event that actually ends the turn should count as a
// declaration / advance the turn-order guess -- otherwise a long chain would
// log and advance once per hop instead of once for the whole turn (the
// server's own declarations_this_deal only ever records one).
function isTerminalExchange(p) {
  if (p.result === "accepted" || p.result === "deck_exchange_accepted" || p.result === "deck_draw_refused") return true;
  if (p.result === "refused") return p.reason === "human" || p.reason === "cat";
  return false; // refused via 馬/家 -- the request chains onward, not terminal
}

function handleExchangeResult(p) {
  if (!state.table) return;
  const me = state.playerId;
  let turnOwner = null;
  switch (p.result) {
    case "accepted":
      if (p.requester === me || p.target === me) state.yourHand = p.your_new_card;
      pushLog(state, `${seatName(state, p.requester)} が ${seatName(state, p.target)} とカンビオ`);
      turnOwner = p.requester;
      break;
    case "deck_exchange_accepted":
      if (p.actor === me) state.yourHand = p.new_card;
      pushLog(state, `${seatName(state, p.actor)} が山札とカンビオ`);
      turnOwner = p.actor;
      break;
    case "refused":
      pushLog(state, `${seatName(state, p.target)} が拒否(${p.reason}${p.revealed_rank ? ": " + p.revealed_rank : ""})`);
      turnOwner = p.requester;
      break;
    case "deck_draw_refused":
      pushLog(state, `山札が拒否(${p.reason})`);
      turnOwner = p.actor;
      break;
  }
  if (isTerminalExchange(p) && turnOwner) {
    state.table.declarations_this_deal.push({ player_id: turnOwner, action: "cambio", via_timeout: false });
    advanceTurn(state, turnOwner);
  }
  if (turnOwner === me) state.turnPrompt = null;
}

// -- boot -------------------------------------------------------------------

wireConnection();
conn.connect();

const saved = loadSession();
if (saved && saved.sessionToken && saved.roomId) {
  state.screen = "name";
  state.savedSession = saved;
}
render();

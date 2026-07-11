// Central client state: the authoritative "public game state" mirrored from
// the server (docs/protocol/design.md 「公開ゲーム状態」) plus local-only UI
// state (which screen is showing, active modals, log). The server is always
// authoritative -- every field below is either a straight overwrite from a
// received payload, or a value derived purely for display convenience.

export function createStore() {
  const state = {
    screen: "name", // name | lobby | create | join | waiting | table | result | ended
    connectionStatus: "connecting", // connecting | open | reconnecting | disconnected
    error: null,

    // session identity (persisted to localStorage, see persistence.js)
    name: null,
    playerId: null,
    sessionToken: null,
    roomId: null,
    playerType: null, // human | spectator

    // public game state (state_snapshot + incremental updates)
    table: null, // { table_id, mode, seats, spectators, dealer_seat, pot_number, ... }
    currentTurnSeat: null, // best-effort, see deriveTurn.js -- not authoritative
    potChips: 0, // chips currently in the pot (pot_started/deal_result/snapshot absolute + chips_paid increments)
    yourHand: null,
    disqualifiedThisDeal: false,
    disqualifiedIdsThisDeal: new Set(), // every player disqualified so far this deal, for turn-order inference
    disqualifiedInfo: {}, // player_id -> {cause, card|null} this deal, for the deal-summary table
    requiredChipsByPlayer: {}, // player_id -> required_chips, from continue_prompted (broadcast, precedes the unicast continue_prompt)
    pendingContinueIds: new Set(), // players whose 続行/離脱 answer we're waiting on (continue_prompted -> chips_paid/player_left_pot)
    readySent: false, // "準備完了" already clicked (survives the waiting-room resync re-render)
    dozoSent: false, // I am the dealer and already declared どうぞ this deal (my own view only)
    // Whether any turn has resolved this deal. Until then the dealer's どうぞ
    // (and the first player's prompt) are unicast and invisible to
    // bystanders, so the status bar can only say "waiting on the dealer".
    firstActionSeen: false,

    // active prompts (only ever set when *this* session is the addressee)
    dealerReadyPrompt: null, // { timeoutSec, deadline }
    turnPrompt: null, // { timeoutSec, deadline }
    cuccoWindow: null, // { timeoutSec, deadline }
    continuePrompt: null, // { requiredChips, timeoutSec, deadline }

    // last aggregate results, for the result screens
    lastDealOpened: null,
    lastDealResult: null,
    // The previous deal's summary, kept visible through the next deal: the
    // server starts dealing again immediately after deal_result, so without
    // this the result table would flash for milliseconds at most.
    prevDealSummary: null, // {opened, result, disqualifiedInfo, dealNumber}
    lastPotResult: null,
    gameEnded: null,

    // rolling event log for the table screen (exchange results, disqualifications, ...)
    log: [],

    toast: null,
  };

  const listeners = new Set();
  function notify() {
    for (const fn of listeners) fn(state);
  }
  return {
    state,
    notify,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

export function pushLog(state, text) {
  state.log.push({ text, ts: Date.now() });
  if (state.log.length > 200) state.log.shift();
}

export function seatName(state, playerId) {
  if (!playerId) return "?";
  const seat = state.table?.seats?.find((s) => s.player_id === playerId);
  return seat ? seat.name : playerId;
}

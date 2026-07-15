// Authoritative mirror of the public game state, ported from the reference
// client (clients/web/src/main.js handleEvent) -- the protocol semantics are
// identical; only presentation differs. Chips/deck counts are absolute
// overwrites, provenance is tracked incrementally between snapshots, and
// prompts are only ever set when THIS session is the addressee.
//
// Each network event mutates the state synchronously and then reports what
// happened via `emit(op)` -- the play client's scene layer consumes those
// ops (in M2+ they become queued animation steps; in M1 the scene just
// re-syncs on every change).

import { CAUSE_LABELS, REFUSAL_LABELS } from "../../web-common/cards.js";

export function createGameState({ onChange, onOp, onLog, onToast }) {
  const state = {
    // session identity (filled in by main.js)
    name: null,
    playerId: null,
    sessionToken: null,
    roomId: null,
    playerType: null,

    // public game state
    table: null,
    currentTurnSeat: null, // best-effort guess for bystander display
    potChips: 0,
    yourHand: null,
    disqualifiedThisDeal: false,
    disqualifiedIdsThisDeal: new Set(),
    disqualifiedInfo: {},
    // Cards made public MID-deal by an effect firing (猫/人間 always reveal
    // themselves; 馬/家 per horse_house_reveal; クク on declaration). The
    // knowledge follows the CARD, so it swaps along with exchanges and
    // clears when the holder trades with the deck or leaves the deal --
    // exactly what everyone at a physical table would have watched happen.
    revealedCards: {},
    requiredChipsByPlayer: {},
    pendingContinueIds: new Set(),
    readySent: false,
    dozoSent: false,
    firstActionSeen: false,

    // prompts addressed to me (+ the broadcast result pause)
    resultPause: null,
    // The result pane must not cover the effect animations that explain the
    // result (クク宣言・山札交換の特殊札演出…). It's only shown once the
    // presentation queue has drained -- main.js flips this from a queued step.
    resultPauseReady: false,
    effectWindow: null,
    dealerReadyPrompt: null,
    turnPrompt: null,
    cuccoWindow: null,
    continuePrompt: null,

    // aggregates for result views
    lastDealOpened: null,
    lastDealResult: null,
    lastPotResult: null,
    prevDealSummary: null,
    gameEnded: null,

    log: [],
  };

  function log(text) {
    state.log.push({ text, ts: Date.now() });
    if (state.log.length > 200) state.log.shift();
    onLog?.(text);
  }

  function emit(op) {
    onOp?.(op);
  }

  function seatName(pid) {
    if (!pid) return "?";
    return state.table?.seats?.find((s) => s.player_id === pid)?.name ?? pid;
  }

  function mergeChips(chipsNow) {
    if (!state.table || !chipsNow) return;
    for (const seat of state.table.seats) {
      if (chipsNow[seat.player_id] !== undefined) seat.chips = chipsNow[seat.player_id];
    }
  }

  function turnOrder() {
    const seats = (state.table?.seats ?? [])
      .filter((s) => s.in_current_pot !== false && !state.disqualifiedIdsThisDeal.has(s.player_id))
      .map((s) => s.player_id);
    const dealer = state.table?.dealer_seat;
    if (!seats.length || !dealer || !seats.includes(dealer)) return seats;
    const idx = seats.indexOf(dealer);
    return [...seats.slice(idx + 1), ...seats.slice(0, idx + 1)];
  }

  function advanceTurn(resolvedPlayerId) {
    const order = turnOrder();
    if (!order.length) return;
    const idx = order.indexOf(resolvedPlayerId);
    state.currentTurnSeat = idx === -1 || idx + 1 >= order.length ? null : order[idx + 1];
  }

  function stashPrevDealSummary() {
    if (state.lastDealOpened || state.lastDealResult) {
      state.prevDealSummary = {
        opened: state.lastDealOpened,
        result: state.lastDealResult,
        disqualifiedInfo: state.disqualifiedInfo,
        dealNumber: state.table?.deal_number ?? 0,
      };
    }
  }

  function applySnapshot(snapshot) {
    state.table = snapshot;
    state.yourHand = snapshot.your_hand;
    state.currentTurnSeat = snapshot.current_turn_seat;
    state.potChips = snapshot.pot_chips ?? 0;
    state.firstActionSeen = (snapshot.declarations_this_deal ?? []).length > 0;
    // Mid-deal reveal knowledge isn't carried in the snapshot; a reconnect
    // starts from what the snapshot can prove (open hands, discard). Clear
    // any stale reveals so nothing lingers from a prior deal.
    state.revealedCards = {};
    state.dealerReadyPrompt = null;
    state.turnPrompt = null;
    state.cuccoWindow = null;
    state.continuePrompt = null;
    state.effectWindow = null;
    state.resultPause = null;
    emit({ kind: "rebuild" });
  }

  function handleEvent(type, p) {
    switch (type) {
      case "state_snapshot":
        if (state.gameEnded) return;
        if (p.game_finished) {
          state.gameEnded = { ranking: p.final_ranking ?? [] };
          state.table = p;
          emit({ kind: "game_ended" });
          break;
        }
        applySnapshot(p);
        break;

      case "action_rejected":
        log(`[エラー] ${p.reason}`);
        emit({ kind: "rejected", reason: p.reason });
        break;

      case "pot_started": {
        if (!state.table) return;
        stashPrevDealSummary();
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
        state.resultPause = null;
        if (Array.isArray(p.participants) && p.participants.length) {
          const rank = new Map(p.participants.map((pid, i) => [pid, i]));
          state.table.seats.sort(
            (a, b) => (rank.get(a.player_id) ?? p.participants.length) - (rank.get(b.player_id) ?? p.participants.length)
          );
          log(`着席順: ${p.participants.map((pid) => seatName(pid)).join(" → ")}`);
        }
        log(`ポット ${p.pot_number} 開始(親: ${seatName(p.dealer_id)}、ポット${state.potChips}枚)`);
        emit({ kind: "pot_started", dealer: p.dealer_id });
        break;
      }

      case "deal_started": {
        if (!state.table) return;
        stashPrevDealSummary();
        state.yourHand = p.your_hand;
        state.table.deck_remaining_count = p.deck_remaining_count;
        state.table.declarations_this_deal = [];
        state.table.deal_number = (state.table.deal_number || 0) + 1;
        state.disqualifiedThisDeal = false;
        state.disqualifiedIdsThisDeal = new Set();
        state.disqualifiedInfo = {};
        state.revealedCards = {};
        state.requiredChipsByPlayer = {};
        state.pendingContinueIds = new Set();
        state.dozoSent = false;
        state.firstActionSeen = false;
        state.resultPause = null;
        state.effectWindow = null;
        state._discardLenAtDealStart = state.table.discard_pile.length;
        state.lastDealOpened = null;
        state.lastDealResult = null;
        state.table.provenance_map = Object.fromEntries(
          state.table.seats.filter((s) => s.in_current_pot !== false).map((s) => [s.player_id, s.player_id])
        );
        state.currentTurnSeat = turnOrder()[0] ?? null;
        log("配布されました");
        emit({ kind: "deal_started" });
        break;
      }

      case "dealer_ready":
        state.dealerReadyPrompt = { deadline: Date.now() + p.timeout_sec * 1000 };
        emit({ kind: "prompt" });
        break;

      case "turn_prompt":
        state.turnPrompt = { deadline: Date.now() + p.timeout_sec * 1000 };
        state.currentTurnSeat = state.playerId;
        emit({ kind: "prompt" });
        break;

      case "cucco_window":
        state.cuccoWindow = { deadline: Date.now() + p.timeout_sec * 1000 };
        emit({ kind: "prompt" });
        break;

      case "effect_window":
        state.effectWindow = { requester: p.requester, deadline: Date.now() + p.timeout_sec * 1000 };
        emit({ kind: "prompt" });
        break;

      case "result_pause":
        state.resultPause = { deadline: Date.now() + p.timeout_sec * 1000 };
        state.resultPauseReady = false; // main.js reveals it after the queue drains
        emit({ kind: "result_pause" });
        break;

      case "continue_prompted":
        state.requiredChipsByPlayer[p.player_id] = p.required_chips;
        state.pendingContinueIds.add(p.player_id);
        break;

      case "continue_prompt":
        state.continuePrompt = {
          requiredChips: state.requiredChipsByPlayer[state.playerId],
          deadline: Date.now() + p.timeout_sec * 1000,
        };
        emit({ kind: "prompt" });
        break;

      case "no_change_declared":
      case "turn_timeout_consumed": {
        if (!state.table) return;
        state.firstActionSeen = true;
        state.table.declarations_this_deal.push({
          player_id: p.player_id,
          action: "no_change",
          via_timeout: type === "turn_timeout_consumed",
        });
        if (p.player_id === state.playerId) {
          state.turnPrompt = null;
          if (type === "turn_timeout_consumed") onToast?.("時間切れでノーチェンジになりました");
        }
        log(`${seatName(p.player_id)}: ノンカンビオ${type === "turn_timeout_consumed" ? "(時間切れ)" : ""}`);
        advanceTurn(p.player_id);
        emit({ kind: "no_change", player: p.player_id });
        break;
      }

      case "exchange_result":
        state.firstActionSeen = true;
        handleExchange(p);
        break;

      case "deck_reshuffled":
        if (!state.table) return;
        state.table.deck_remaining_count = p.remaining_count;
        state.table.discard_pile = [];
        state.table.provenance_map = {};
        onToast?.("山札が捨て札から再構築されました");
        log("山札を再構築しました");
        emit({ kind: "reshuffle" });
        break;

      case "cucco_declared":
        state.firstActionSeen = true;
        state.turnPrompt = null;
        state.cuccoWindow = null;
        // The declarer's クク is now shown to everyone.
        state.revealedCards[p.player_id] = "クク";
        log(`${seatName(p.player_id)} がクク宣言!`);
        emit({ kind: "cucco_declared", player: p.player_id });
        break;

      case "player_disqualified": {
        log(`${seatName(p.player_id)} が失格(${CAUSE_LABELS[p.cause] ?? p.cause})`);
        state.disqualifiedIdsThisDeal.add(p.player_id);
        state.disqualifiedInfo[p.player_id] = { cause: p.cause, card: p.card ?? null };
        delete state.table?.provenance_map?.[p.player_id];
        delete state.revealedCards[p.player_id];
        if (p.card && state.table) {
          state.table.discard_pile = [
            ...(state.table.discard_pile || []),
            { card: p.card, original_holder: null, discarded_via: "disqualification" },
          ];
        }
        if (p.player_id === state.playerId) {
          state.disqualifiedThisDeal = true;
          state.yourHand = null;
          state.turnPrompt = null;
          state.cuccoWindow = null;
          state.dealerReadyPrompt = null;
          state.effectWindow = null;
        }
        emit({ kind: "disqualified", player: p.player_id, cause: p.cause, card: p.card ?? null });
        break;
      }

      case "dealer_changed":
        if (state.table) state.table.dealer_seat = p.player_id;
        emit({ kind: "dealer_changed", player: p.player_id });
        break;

      case "chips_paid": {
        if (!state.table) return;
        const seat = state.table.seats.find((s) => s.player_id === p.player_id);
        if (seat) seat.chips = p.chips_now;
        state.pendingContinueIds.delete(p.player_id);
        state.potChips += p.amount ?? 0;
        log(`${seatName(p.player_id)} が ${p.amount} 枚をポットへ(計${state.potChips}枚)`);
        emit({ kind: "chips_paid", player: p.player_id, amount: p.amount });
        break;
      }

      case "player_left_pot": {
        if (!state.table) return;
        const seat = state.table.seats.find((s) => s.player_id === p.player_id);
        if (seat) seat.in_current_pot = false;
        state.pendingContinueIds.delete(p.player_id);
        log(`${seatName(p.player_id)} がポットを抜けました(${p.reason})`);
        emit({ kind: "left_pot", player: p.player_id });
        break;
      }

      case "deal_opened":
        state.lastDealOpened = p;
        log("オープン!");
        emit({ kind: "deal_opened", hands: p.hands, losers: p.losers, elevated: p.elevated_joker_holders ?? [] });
        break;

      case "deal_result": {
        state.lastDealResult = p;
        mergeChips(p.chips_now);
        if (p.pot_chips !== undefined) state.potChips = p.pot_chips;
        if (state.table) {
          const before = state._discardLenAtDealStart ?? state.table.discard_pile.length;
          state.table.discard_pile = [...state.table.discard_pile.slice(0, before), ...p.discarded_cards];
          if (p.next_dealer) state.table.dealer_seat = p.next_dealer;
        }
        state.turnPrompt = null;
        state.cuccoWindow = null;
        state.dealerReadyPrompt = null;
        state.currentTurnSeat = null;
        log(`ディール結果: 敗者 ${p.losers.map((id) => seatName(id)).join(", ") || "なし"}`);
        emit({ kind: "deal_result", losers: p.losers, nextDealer: p.next_dealer });
        break;
      }

      case "pot_result":
        state.lastPotResult = p;
        mergeChips(p.chips_now);
        if (p.result === "won") state.potChips = 0;
        log(
          p.result === "won"
            ? `${seatName(p.winner)} が ${p.amount} 枚を獲得!`
            : `このポット(${p.amount}枚)は次のポットへ持ち越しになりました`
        );
        emit({ kind: "pot_result", result: p.result, winner: p.winner, amount: p.amount });
        break;

      case "game_ended":
        state.gameEnded = p;
        state.readySent = false;
        state.resultPause = null;
        emit({ kind: "game_ended" });
        break;

      default:
        return;
    }
    onChange?.(state);
  }

  function handleExchange(p) {
    if (!state.table) return;
    const me = state.playerId;
    const prov = state.table.provenance_map ?? (state.table.provenance_map = {});
    const rev = state.revealedCards;
    const terminal =
      p.result === "accepted" ||
      p.result === "deck_exchange_accepted" ||
      p.result === "deck_draw_refused" ||
      (p.result === "refused" && (p.reason === "human_refusal" || p.reason === "cat_meow"));
    let turnOwner = null;
    switch (p.result) {
      case "accepted":
        if (p.requester === me || p.target === me) state.yourHand = p.your_new_card;
        [prov[p.requester], prov[p.target]] = [prov[p.target] ?? p.target, prov[p.requester] ?? p.requester];
        // Reveal knowledge follows the card that moved.
        {
          const a = rev[p.requester];
          const b = rev[p.target];
          if (b !== undefined) rev[p.requester] = b;
          else delete rev[p.requester];
          if (a !== undefined) rev[p.target] = a;
          else delete rev[p.target];
        }
        log(`${seatName(p.requester)} が ${seatName(p.target)} とカンビオ`);
        turnOwner = p.requester;
        emit({ kind: "exchange", requester: p.requester, target: p.target });
        break;
      case "deck_exchange_accepted":
        if (p.actor === me) state.yourHand = p.new_card;
        prov[p.actor] = null;
        // The deck draw happens in the open, so the actor's new card is
        // public to everyone.
        rev[p.actor] = p.new_card;
        log(`${seatName(p.actor)} が山札とカンビオ(引いた: ${p.new_card} / 出した: ${p.given_up_card})`);
        turnOwner = p.actor;
        emit({ kind: "deck_exchange", actor: p.actor, newCard: p.new_card, givenUp: p.given_up_card });
        break;
      case "refused":
        // The refusing card's identity becomes public: 猫/人間 always
        // (revealed_rank always set by the server), 馬/家 only when the
        // table's horse_house_reveal is on (server sends revealed_rank then).
        if (p.revealed_rank) rev[p.target] = p.revealed_rank;
        log(`${seatName(p.target)} が拒否 — ${REFUSAL_LABELS[p.reason] ?? p.reason}${p.revealed_rank ? `(${p.revealed_rank})` : ""}`);
        turnOwner = p.requester;
        emit({ kind: "refused", requester: p.requester, target: p.target, reason: p.reason, revealed: p.revealed_rank });
        break;
      case "deck_draw_refused":
        log(`山札から ${p.drawn_rank ?? "?"} — ${REFUSAL_LABELS[p.reason] ?? p.reason}`);
        turnOwner = p.actor;
        emit({ kind: "deck_refused", actor: p.actor, drawn: p.drawn_rank, reason: p.reason });
        break;
    }
    if (terminal && turnOwner) {
      state.table.declarations_this_deal.push({ player_id: turnOwner, action: "cambio", via_timeout: false });
      advanceTurn(turnOwner);
    }
    if (turnOwner === me) state.turnPrompt = null;
    if (p.target === me || p.requester === me || p.actor === me) state.effectWindow = null;
  }

  function seatNamePublic(pid) {
    return seatName(pid);
  }

  return { state, handleEvent, applySnapshot, seatName: seatNamePublic };
}

// The table scene: an elliptical felt with seats arranged around it (own
// seat pinned at bottom center), deck/discard/pot in the middle. Retained
// DOM -- built once per roster, then updated in place by sync(state), so
// M2's animation layer can move real elements around instead of fighting
// innerHTML rebuilds.

import { esc } from "../../../web-common/utils.js";
import { RANK_ORDER, CAUSE_LABELS } from "../../../web-common/cards.js";
import { cardInnerHTML } from "./cardArt.js";

// Card markup helpers, shared with the animation layer (flight ghosts use
// the exact same DOM as the slots they fly between).
export function cardHTML(rank, elevated = false) {
  if (rank == null) return `<div class="card card-back"></div>`;
  const special = !/^\d+$/.test(rank);
  return `<div class="card card-face ${special ? "special" : ""}" data-rank="${esc(rank)}">
    ${cardInnerHTML(rank)}${elevated ? '<span class="elevated">↑最強</span>' : ""}
  </div>`;
}

export function createTableScene(root) {
  root.innerHTML = `
    <div class="felt">
      <div class="center">
        <div class="pile-area">
          <div class="deck" id="scene-deck">
            <div class="card card-back"></div>
            <div class="deck-count" id="deck-count"></div>
          </div>
          <div class="pot" id="scene-pot">
            <div class="pot-stack">💰</div>
            <div class="pot-count" id="pot-count"></div>
          </div>
        </div>
        <div class="discard" id="scene-discard"></div>
      </div>
      <div class="seat-layer" id="seat-layer"></div>
    </div>
  `;
  const seatLayer = root.querySelector("#seat-layer");
  const seatEls = new Map(); // player_id -> element

  function seatAngle(index, count) {
    // Own seat sits at the bottom (90° in screen coords where +y is down);
    // everyone else is spread clockwise around the remaining arc.
    return (Math.PI / 2) + (index / count) * Math.PI * 2;
  }

  // On a portrait screen the felt is narrow, so seats that land on the
  // horizontal midline end up level with the centre pile and only a card's
  // width from it -- the deck, the pot and two players all crowded into one
  // line. `+k·sin(2θ)` slides seats away from θ=0 and θ=π (the midline) and
  // toward the top and bottom of the ring, where there is room to spare.
  // Zero on landscape, where the ring is wide enough already.
  const MIDLINE_REPEL = 0.28;

  function repelFromMidline(theta, portrait) {
    return portrait ? theta + MIDLINE_REPEL * Math.sin(2 * theta) : theta;
  }

  function buildSeats(state) {
    seatLayer.innerHTML = "";
    seatEls.clear();
    const seats = state.table?.seats ?? [];
    if (!seats.length) return;
    const myIdx = Math.max(0, seats.findIndex((s) => s.player_id === state.playerId));
    const box = seatLayer.getBoundingClientRect();
    const portrait = box.height > box.width;
    seats.forEach((s, i) => {
      const rel = (i - myIdx + seats.length) % seats.length;
      const theta = repelFromMidline(seatAngle(rel, seats.length), portrait);
      const x = 50 + 41 * Math.cos(theta);
      const y = 50 + (portrait ? 41 : 38) * Math.sin(theta);
      const el = document.createElement("div");
      el.className = "player-seat";
      el.classList.toggle("is-me", s.player_id === state.playerId);
      el.style.left = `${x}%`;
      el.style.top = `${y}%`;
      el.dataset.playerId = s.player_id;
      el.innerHTML = `
        <div class="turn-ring"></div>
        <div class="card-slot"><div class="card card-back"></div></div>
        <div class="name-plate">
          <span class="dealer-mark" hidden>👑</span>
          <span class="p-name">${esc(s.name)}</span>
        </div>
        <div class="chip-count"></div>
        <div class="seat-badges"></div>
      `;
      seatLayer.appendChild(el);
      seatEls.set(s.player_id, el);
    });
  }

  function cardFaceHTML(rank, elevated = false) {
    return cardHTML(rank, elevated);
  }

  function sync(state) {
    const t = state.table;
    if (!t) return;
    const roster = (t.seats ?? []).map((s) => s.player_id).join(",");
    if (roster !== sync._roster) {
      buildSeats(state);
      sync._roster = roster;
    }

    // shownOpened, not lastDealOpened: the authoritative one is set the moment
    // the open event arrives, while the last turn is usually still animating.
    // Reading it here would flip every hand face-up mid-animation -- see the
    // note on shownOpened in gameState.js.
    const opened = state.shownOpened;
    const dealInProgress = t.deal_number > 0 && !opened && !state.lastDealResult;
    // Same rule for the mid-deal reveals and the disqualified seats: read the
    // lagging snapshot the animation layer advances, never the live fields
    // (gameState.js, shownFaces). Null before the first queued step has run --
    // nothing has been animated yet then, so the live values ARE the shown ones.
    const faces = state.shownFaces ?? {
      revealed: state.revealedCards,
      dq: state.disqualifiedIdsThisDeal,
      dqInfo: state.disqualifiedInfo,
    };
    // The deal-out gate: until the last card has landed, seats show a back or
    // nothing -- no open, no reveal, no disqualified face can jump the queue.
    const dealingOut = state.shownDealing;

    for (const s of t.seats ?? []) {
      const el = seatEls.get(s.player_id);
      if (!el) continue;
      el.querySelector(".chip-count").textContent = `${s.chips} 枚`;
      el.querySelector(".dealer-mark").hidden = s.player_id !== t.dealer_seat;
      // shownTurnSeat: 権威状態ではなく「演出が追いついた手番」。詳細は
      // gameState.js の shownTurnSeat のコメントを参照。
      el.classList.toggle("is-turn", s.player_id === state.shownTurnSeat && dealInProgress);
      el.classList.toggle("is-out", s.in_current_pot === false);
      el.classList.toggle("is-disqualified", faces.dq.has(s.player_id));
      el.classList.toggle("is-disconnected", s.connected === false);

      const badges = [];
      if (faces.dq.has(s.player_id)) {
        // 卓の上でも理由まで見えるようにする -- 席を見ただけで「なぜ抜けたか」が
        // 分かるほうが、結果発表を待たずに状況を追える。
        const cause = faces.dqInfo?.[s.player_id]?.cause;
        badges.push(cause ? `失格(${CAUSE_LABELS[cause] ?? cause})` : "失格");
      }
      else if (s.in_current_pot === false) badges.push("脱落中");
      if (s.connected === false) badges.push("切断");
      el.querySelector(".seat-badges").textContent = badges.join("・");

      // Card slot: face-up for my own card, cards an effect made public
      // mid-deal (revealedCards), and everyone at open; face-down otherwise.
      const slot = el.querySelector(".card-slot");
      const openedCard = opened?.hands?.[s.player_id];
      const revealed = faces.revealed?.[s.player_id];
      if (dealingOut) {
        // Mid deal-out: this seat either has its face-down card already or is
        // waiting for it. Nothing else may be drawn here -- not my own hand
        // (the deal step flips it once every seat is served), not a クク some
        // AI has already declared, not a disqualified card. Any state that
        // arrived early simply waits for the sync at the end of the deal step.
        slot.innerHTML = state.shownDealtSeats.has(s.player_id) ? '<div class="card card-back"></div>' : "";
      } else if (openedCard !== undefined) {
        slot.innerHTML = cardFaceHTML(openedCard, opened.elevated_joker_holders?.includes(s.player_id));
      } else if (faces.dq.has(s.player_id)) {
        // Disqualified: the card is taken out of play but STAYS in front of
        // its ex-holder until the deal opens, exactly as at a physical table.
        // Whether it is face-up depends on the disclosure setting -- 遅延公開
        // leaves it there face-DOWN, which is not the same as it being gone.
        const dq = faces.dqInfo?.[s.player_id];
        if (opened || !dq?.onTable) {
          // Collected into the discard at the open, or swept into the deck by
          // a reshuffle (reshuffle_includes_revealed): the seat really is empty.
          slot.innerHTML = "";
        } else {
          slot.innerHTML = dq.card ? cardFaceHTML(dq.card) : '<div class="card card-back"></div>';
        }
      } else if (s.player_id === state.playerId) {
        // shownHand (not yourHand): my slot reveals a new card only at the
        // animation step that earns it, never mid-effect-animation.
        slot.innerHTML = state.shownHand ? cardFaceHTML(state.shownHand) : "";
      } else if (revealed !== undefined) {
        slot.innerHTML = cardFaceHTML(revealed);
      } else {
        slot.innerHTML = dealInProgress || (t.deal_number > 0 && !state.lastDealResult) ? '<div class="card card-back"></div>' : "";
      }
    }

    root.querySelector("#deck-count").textContent = `${t.deck_remaining_count}`;
    root.querySelector("#pot-count").textContent = `${state.potChips}`;

    // Discard: two table-wide styles (state_snapshot's discard_display).
    // "grouped" (default): every discard, grouped by rank. "pile": like a
    // physical pile -- only the most recent card is visible, face-up on top.
    // Display-only; the underlying data is the same either way.
    const pile = t.discard_pile ?? [];
    const discardEl = root.querySelector("#scene-discard");
    if (t.discard_display === "pile") {
      const top = pile[pile.length - 1];
      discardEl.innerHTML = pile.length
        ? `<div class="discard-title">捨て山 ${pile.length}枚</div>
           <div class="discard-pile-top">${cardHTML(top.card)}</div>`
        : "";
    } else {
      const counts = new Map();
      for (const d of pile) counts.set(d.card, (counts.get(d.card) ?? 0) + 1);
      const sorted = [...counts.entries()].sort(([a], [b]) => RANK_ORDER.indexOf(a) - RANK_ORDER.indexOf(b));
      discardEl.innerHTML = sorted.length
        ? `<div class="discard-title">捨て札 ${pile.length}枚</div>` +
          sorted.map(([card, n]) => `<span class="discard-chip">${esc(card)}${n > 1 ? `×${n}` : ""}</span>`).join("")
        : "";
    }
  }

  return {
    sync,
    seatEls,
    root,
    slotEl: (pid) => seatEls.get(pid)?.querySelector(".card-slot") ?? null,
    seatEl: (pid) => seatEls.get(pid) ?? null,
    deckEl: () => root.querySelector("#scene-deck"),
    potEl: () => root.querySelector("#scene-pot"),
    discardEl: () => root.querySelector("#scene-discard") ?? root.querySelector(".center"),
  };
}

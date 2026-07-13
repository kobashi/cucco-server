// The table scene: an elliptical felt with seats arranged around it (own
// seat pinned at bottom center), deck/discard/pot in the middle. Retained
// DOM -- built once per roster, then updated in place by sync(state), so
// M2's animation layer can move real elements around instead of fighting
// innerHTML rebuilds.

import { esc } from "../../../web-common/utils.js";
import { RANK_ORDER } from "../../../web-common/cards.js";

export function createTableScene(root) {
  root.innerHTML = `
    <div class="felt">
      <div class="center">
        <div class="deck" id="scene-deck">
          <div class="card card-back"></div>
          <div class="deck-count" id="deck-count"></div>
        </div>
        <div class="pot" id="scene-pot">
          <div class="pot-stack">💰</div>
          <div class="pot-count" id="pot-count"></div>
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

  function buildSeats(state) {
    seatLayer.innerHTML = "";
    seatEls.clear();
    const seats = state.table?.seats ?? [];
    if (!seats.length) return;
    const myIdx = Math.max(0, seats.findIndex((s) => s.player_id === state.playerId));
    seats.forEach((s, i) => {
      const rel = (i - myIdx + seats.length) % seats.length;
      const theta = seatAngle(rel, seats.length);
      const x = 50 + 41 * Math.cos(theta);
      const y = 50 + 38 * Math.sin(theta);
      const el = document.createElement("div");
      el.className = "player-seat";
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
    const special = !/^\d+$/.test(rank);
    return `<div class="card card-face ${special ? "special" : ""}" data-rank="${esc(rank)}">
      <span>${esc(rank)}</span>${elevated ? '<span class="elevated">↑最強</span>' : ""}
    </div>`;
  }

  function sync(state) {
    const t = state.table;
    if (!t) return;
    const roster = (t.seats ?? []).map((s) => s.player_id).join(",");
    if (roster !== sync._roster) {
      buildSeats(state);
      sync._roster = roster;
    }

    const opened = state.lastDealOpened;
    const dealInProgress = t.deal_number > 0 && !opened && !state.lastDealResult;

    for (const s of t.seats ?? []) {
      const el = seatEls.get(s.player_id);
      if (!el) continue;
      el.querySelector(".chip-count").textContent = `${s.chips} 枚`;
      el.querySelector(".dealer-mark").hidden = s.player_id !== t.dealer_seat;
      el.classList.toggle("is-turn", s.player_id === state.currentTurnSeat && dealInProgress);
      el.classList.toggle("is-out", s.in_current_pot === false);
      el.classList.toggle("is-disqualified", state.disqualifiedIdsThisDeal.has(s.player_id));
      el.classList.toggle("is-disconnected", s.connected === false);

      const badges = [];
      if (state.disqualifiedIdsThisDeal.has(s.player_id)) badges.push("失格");
      else if (s.in_current_pot === false) badges.push("脱落中");
      if (s.connected === false) badges.push("切断");
      el.querySelector(".seat-badges").textContent = badges.join("・");

      // Card slot: my own card face-up; others face-down while playing;
      // everyone face-up at open (from deal_opened.hands).
      const slot = el.querySelector(".card-slot");
      const openedCard = opened?.hands?.[s.player_id];
      const dqCard = state.disqualifiedInfo[s.player_id]?.card;
      if (openedCard !== undefined) {
        slot.innerHTML = cardFaceHTML(openedCard, opened.elevated_joker_holders?.includes(s.player_id));
      } else if (dqCard) {
        slot.innerHTML = cardFaceHTML(dqCard);
      } else if (state.disqualifiedIdsThisDeal.has(s.player_id)) {
        slot.innerHTML = "";
      } else if (s.player_id === state.playerId) {
        slot.innerHTML = state.yourHand ? cardFaceHTML(state.yourHand) : "";
      } else {
        slot.innerHTML = dealInProgress || (t.deal_number > 0 && !state.lastDealResult) ? '<div class="card card-back"></div>' : "";
      }
    }

    root.querySelector("#deck-count").textContent = `${t.deck_remaining_count}`;
    root.querySelector("#pot-count").textContent = `${state.potChips}`;

    // Discard: grouped counts, rank order (matches the reference client).
    const counts = new Map();
    for (const d of t.discard_pile ?? []) counts.set(d.card, (counts.get(d.card) ?? 0) + 1);
    const sorted = [...counts.entries()].sort(([a], [b]) => RANK_ORDER.indexOf(a) - RANK_ORDER.indexOf(b));
    root.querySelector("#scene-discard").innerHTML = sorted.length
      ? `<div class="discard-title">捨て札 ${t.discard_pile.length}枚</div>` +
        sorted.map(([card, n]) => `<span class="discard-chip">${esc(card)}${n > 1 ? `×${n}` : ""}</span>`).join("")
      : "";
  }

  return { sync, seatEls, root };
}

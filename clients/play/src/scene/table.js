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
    // everyone else is spread clockwise around the remaining arc. Evenly --
    // seats used to be pushed away from the horizontal midline so that nobody
    // sat level with the deck, which bunched them at the top and bottom of a
    // portrait screen (the crowded end) to protect a centre pile that is now
    // measured and fitted instead (fitCenter).
    return (Math.PI / 2) + (index / count) * Math.PI * 2;
  }

  // The seat ring, as a fraction of the scene box. Seats hang slightly over the
  // felt's rim on purpose: the name plate reads fine against the rim, and every
  // percent of radius is a percent the middle of the table gets back. Over the
  // SCREEN edge is another matter, so the ring is also clamped to keep whole
  // seats inside the scene -- with nothing pushing seats off the horizontal
  // midline any more, the left and right ones sit at the widest point of the
  // ring, which is exactly where a phone runs out of width.
  const RING_X = 41;
  const RING_Y_PORTRAIT = 41;
  const RING_Y_LANDSCAPE = 38;
  const RING_MIN = 60; // px: below this the ring is not a ring any more
  const EDGE_PAD = 4;
  // A seat's natural footprint (style.css .player-seat width, and its height
  // with card slot + plate + chips + badges). Only used to decide how many fit
  // before they need shrinking -- the real boxes are measured afterwards.
  const SEAT_W = 108;
  const SEAT_H = 132;
  const SEAT_GAP = 8;
  // How small a crowded table may shrink its seats. Below this the names and
  // chip counts stop being readable, so seats are allowed to touch instead.
  const MIN_SEAT_SCALE = 0.62;

  // Ramanujan's approximation -- exact enough to count seats around a ring.
  function ellipsePerimeter(a, b) {
    return Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)));
  }

  // The roster this layout was built for, so a resize can redo the geometry
  // without rebuilding the DOM (which would throw away whatever the animation
  // queue has put in the slots).
  let placed = null; // { order: [player_id...] }

  function placeSeats() {
    if (!placed || !placed.order.length) return;
    const order = placed.order;
    const box = seatLayer.getBoundingClientRect();
    // A scene that cannot be measured (built while its container is collapsed
    // or hidden) still gets a valid ring: the percentages alone do not need a
    // box. Only the size-aware refinements below are skipped -- a resize will
    // redo them the moment there is something to measure. Returning early here
    // instead would leave every seat stacked at the top-left corner.
    const measurable = box.width > 0 && box.height > 0;
    const ringY = box.height > box.width ? RING_Y_PORTRAIT : RING_Y_LANDSCAPE;
    let ringXPct = RING_X;
    let ringYPct = ringY;
    if (measurable) {
      // Shrink the seats (all of them, together) when the ring cannot hold them
      // at full size -- 8 players on a phone otherwise overlap each other before
      // anything else goes wrong. Set on the document root, not the scene, so
      // the flight ghosts on <body> can size themselves to match (style.css).
      const perimeter = ellipsePerimeter((RING_X / 100) * box.width, (ringY / 100) * box.height);
      const scale = Math.max(MIN_SEAT_SCALE, Math.min(1, perimeter / (order.length * (SEAT_W + SEAT_GAP))));
      document.documentElement.style.setProperty("--seat-scale", scale.toFixed(3));
      // Keep whole seats on screen: the ring never reaches closer to an edge
      // than half a (scaled) seat. Without this, removing the old push away
      // from the horizontal midline puts the left and right seats exactly where
      // a phone runs out of width, and their name plates get cut off.
      const rx = Math.max(RING_MIN, Math.min((RING_X / 100) * box.width, box.width / 2 - (SEAT_W * scale) / 2 - EDGE_PAD));
      const ry = Math.max(RING_MIN, Math.min((ringY / 100) * box.height, box.height / 2 - (SEAT_H * scale) / 2 - EDGE_PAD));
      ringXPct = (rx / box.width) * 100;
      ringYPct = (ry / box.height) * 100;
    }
    order.forEach((pid, rel) => {
      const el = seatEls.get(pid);
      if (!el) return;
      const theta = seatAngle(rel, order.length);
      el.style.left = `${50 + ringXPct * Math.cos(theta)}%`;
      el.style.top = `${50 + ringYPct * Math.sin(theta)}%`;
    });
    if (measurable) fitCenter();
  }

  function buildSeats(state) {
    seatLayer.innerHTML = "";
    seatEls.clear();
    const seats = state.table?.seats ?? [];
    placed = null;
    if (!seats.length) return;
    const myIdx = Math.max(0, seats.findIndex((s) => s.player_id === state.playerId));
    seats.forEach((s) => {
      const el = document.createElement("div");
      el.className = "player-seat";
      el.classList.toggle("is-me", s.player_id === state.playerId);
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
    // Own seat first: it goes at the bottom, everyone else clockwise from there.
    placed = { order: seats.map((_, i) => seats[(myIdx + i) % seats.length].player_id) };
    placeSeats();
  }

  // -- the centre pile's room ------------------------------------------------
  //
  // The deck/pot/discard column used to be sized off the viewport (a share of
  // vw with a flat cap). That is a guess about how much room is left, and on a
  // phone with a full table the guess was wrong: the discard grew down into the
  // seats and cards ended up underneath each other. So it is measured instead.
  // The seats are placed first; whatever rectangle is left in the middle, the
  // centre column gets -- as wide and as tall as actually fits.
  const CENTER_MAX_W = 360;
  const CENTER_MAX_H = 320;
  // The floor. Below this the discard stops being able to show anything useful,
  // so it stops shrinking and is allowed to sit close to the seats -- "fit it
  // within a legible range", not "fit it at any cost".
  const CENTER_MIN_W = 132;
  const CENTER_MIN_H = 150;
  const CENTER_GAP = 10; // breathing room between the box and a seat

  function fitCenter() {
    const box = seatLayer.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const cx = box.left + box.width / 2;
    const cy = box.top + box.height / 2;
    const boxes = [...seatEls.values()].map((el) => {
      const r = el.getBoundingClientRect();
      return { dx: Math.abs(r.left + r.width / 2 - cx), dy: Math.abs(r.top + r.height / 2 - cy), w: r.width, h: r.height };
    });
    const maxW = Math.min(CENTER_MAX_W, box.width * 0.9);
    const maxH = Math.min(CENTER_MAX_H, box.height * 0.9);

    // For a given width, how tall can the box be before it reaches a seat?
    // Only seats it overlaps horizontally can limit it -- the ones beside it
    // (now that seats may sit level with the deck) are cleared sideways.
    const heightFor = (w) => {
      let h = maxH;
      for (const s of boxes) {
        if (s.dx >= (w + s.w) / 2 + CENTER_GAP) continue;
        h = Math.min(h, 2 * (s.dy - CENTER_GAP) - s.h);
      }
      return h;
    };

    // Widest is not always best: a narrow-and-tall box can hold more discards
    // than a wide-and-flat one. Sweep the width and keep the largest area.
    let best = null;
    const STEPS = 16;
    for (let i = 0; i <= STEPS; i++) {
      const w = maxW - ((maxW - CENTER_MIN_W) * i) / STEPS;
      const h = Math.min(maxH, heightFor(w));
      if (h < CENTER_MIN_H) continue;
      if (!best || w * h > best.w * best.h) best = { w, h };
    }
    // Nothing clears the seats even at the floor: take the floor anyway.
    if (!best) best = { w: CENTER_MIN_W, h: CENTER_MIN_H };
    root.style.setProperty("--center-w", `${Math.round(best.w)}px`);
    root.style.setProperty("--center-h", `${Math.round(best.h)}px`);
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

  // Turning the phone changes the ring (portrait and landscape use different
  // vertical radii) and how much middle is left over, and both are computed
  // from the measured box -- so both have to be redone. Only the geometry is
  // recomputed, never the markup: a rebuild here would wipe whatever the
  // animation queue has just put in the slots.
  //
  // Two triggers, because neither covers the other's gap. ResizeObserver sees
  // everything that changes the scene's own size -- rotation, the dock growing
  // when the turn buttons appear, a container that only gets its real size a
  // beat after the scene was built (placeSeats falls back to plain percentages
  // until then) -- but its callbacks are frame-driven, so a BACKGROUNDED tab
  // gets none of them, and a phone rotated while the app was in the background
  // would come back laid out for the old orientation. The window events keep
  // firing there. Both funnel into the same debounced pass, so a change that
  // both notice still costs one relayout.
  let pendingRelayout = 0;
  const relayout = () => {
    // main.js drops the whole scene when it leaves the table screen and builds
    // a fresh one on the way back, so an old scene's listeners would otherwise
    // pile up for the life of the page. Detached means retired.
    if (!root.isConnected) {
      window.removeEventListener("resize", relayout);
      window.removeEventListener("orientationchange", relayout);
      sizeWatch.disconnect();
      return;
    }
    // setTimeout rather than requestAnimationFrame, for the same reason: rAF
    // does not run in a background tab either. Measuring works fine there. The
    // delay just collapses a drag-resize into one pass.
    clearTimeout(pendingRelayout);
    pendingRelayout = setTimeout(placeSeats, 120);
  };
  // Repositioning seats never resizes the observed element, so this cannot
  // feed itself.
  const sizeWatch = new ResizeObserver(relayout);
  sizeWatch.observe(seatLayer);
  window.addEventListener("resize", relayout);
  window.addEventListener("orientationchange", relayout);

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

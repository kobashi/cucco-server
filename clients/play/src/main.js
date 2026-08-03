// Play client entry point: connection glue (same protocol handling contract
// as the reference client), screen routing, and the table screen composed of
// the retained-DOM scene plus overlay layers.

import { CuccoConnection, wsUrlFor } from "../../web-common/connection.js";
import { loadSession, saveSession, clearSession } from "../../web-common/persistence.js";
import { sanitizeWsHost, esc, isTypingIn } from "../../web-common/utils.js";
import { tableInviteUrl } from "../../web-common/invite.js";
import { createGameState } from "./gameState.js";
import { createTableScene, cardHTML } from "./scene/table.js";
import { createQueue, fly, pause } from "./anim/queue.js";
import { banner, shake, flipReveal, effectMotion, confirmPulse, setConfirmModeGetter } from "./anim/effects.js";
import { createSound } from "./anim/sound.js";
import { REFUSAL_LABELS, CAUSE_LABELS } from "../../web-common/cards.js";
import { renderLobby, renderWaiting, inviteBlockHTML, wireInviteBlock } from "./ui/panels.js";
import { renderStatus, renderHandInfo, renderDock, renderModals, renderLogDrawer } from "./ui/overlays.js";
import { mountCardReference } from "./ui/cardReference.js";

const screenEl = document.getElementById("screen");

// Invite links (docs/web-client-operations.md「招待リンク」): `?ws=host[:port]`
// carries the game server so a guest never has to configure one, and
// `?room=ID` carries the table to join. Both are consumed once and stripped
// from the URL -- the host is persisted, and the room is a one-shot
// instruction for this visit (a reload should resume the saved session, not
// re-join whatever table the link named).
const params = new URLSearchParams(location.search);
const wsParam = params.get("ws");
const roomParam = params.get("room");
const invitedRoomFromUrl = /^[A-Za-z0-9]{6}$/.test(roomParam ?? "") ? roomParam.toUpperCase() : null;
if (wsParam) localStorage.setItem("cucco_ws_host", sanitizeWsHost(wsParam));
if (wsParam || roomParam) {
  const url = new URL(location.href);
  url.searchParams.delete("ws");
  url.searchParams.delete("room");
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
// How much of the server's pause window to keep back for actually READING the
// result. The pane waits for the presentation queue; this is the point at
// which it stops waiting and snaps whatever is left, so that a slow animation
// chain can never eat the whole window.
//
// It used to be a flat 3.8s from the pause arriving, which was shorter than a
// real tail: a 人間 refusal (flip + effect motion + banner + hold) plus the
// disqualification it causes plus the card-by-card open runs well past 7s, so
// the net fired mid-reveal and the result appeared over cards still turning.
// Measuring back from the deadline instead gives the animation everything the
// server is willing to wait, and adapts if the operator shortens the pause.
const RESULT_PANE_RESERVE_MS = 4000;
// Fallback when the pause carries no usable deadline.
const RESULT_PANE_GRACE_MS = 3800;

// Pacing for the card-by-card effect beats. Deliberate enough that every
// player can follow who did what: a card flies, is turned face-up, is read,
// then resolves -- one card at a time, like a physical table.
const FLIGHT_MS = 550; // a single card's flight (deck<->seat<->discard)
// The open is the payoff of the whole deal, so it is dealt out as a reveal
// rather than a single flash: one card turned at a time around the table,
// then a beat to read the row before the result pane covers it.
// The open takes the SAME wall-clock time at every table size, so the result
// pane always lands with the same amount of its window left. A per-card cost
// that just scaled with the seat count made the pane appear 2.2s into a 15s
// pause at one table and 1.4s into it at another, which read as the countdown
// being inconsistent. Small tables spend the leftover on the closing hold
// rather than dragging each card out.
const OPEN_SEQUENCE_MS = 2200; // turning the whole table over, always
const OPEN_MAX_PER_CARD_MS = 420; // ...but never slower than this per card
const OPEN_HOLD_MS = 600; // everyone face-up, before the result appears
const REVEAL_HOLD_MS = 750; // how long a turned-up card sits so the table reads it
// Hard ceiling on how long the dock may hold the turn buttons for the
// presentation to catch up. Gating purely on queue.busy is not safe: if a
// step ever fails to settle (a background tab can stall Web Animations, so
// `finished` never resolves) the queue stays busy forever and the player
// simply cannot act. Comfortably covers a normal deal-out -- and hurry()
// compresses that to well under half of it -- so in practice the gate lifts
// when the animation ends, and this only ever fires as a backstop.
const TURN_GATE_MAX_MS = 2000;

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
// The table this visit was invited to, if any: the name screen announces it,
// and identify() consumes it by joining that table (cleared either way).
state.invitedRoomId = invitedRoomFromUrl;

// UI-only state (which screen family is showing)
let uiPhase = "name"; // name | lobby | create | join | waiting | table
// A panel render that was held back because the user was typing (see render),
// and which panel is currently on screen -- a redraw of the same panel can
// wait for them to finish typing, a change of panel cannot.
let panelRenderPending = false;
let shownPanel = null;
let heldPanelTimer = 0;

// The held render is released by focusout (below), but never ONLY by it: a
// field can stop being the focused one without this document seeing a focus
// event at all -- the window loses focus, the element is removed by something
// else, the browser restores a background tab. A panel stuck showing stale
// state would be a worse bug than the one being fixed, so it also re-checks on
// its own. While the caret really is still in a field this just re-arms.
function scheduleHeldPanelRender() {
  clearTimeout(heldPanelTimer);
  heldPanelTimer = setTimeout(() => {
    if (!panelRenderPending) return;
    if (isTypingIn(screenEl)) scheduleHeldPanelRender();
    else render();
  }, 800);
}
// Until when the dock may keep the turn buttons disabled waiting on the
// presentation (see TURN_GATE_MAX_MS).
let turnGateUntil = 0;
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
// The dock holds the turn buttons only while the presentation is genuinely
// behind AND the backstop deadline has not passed.
const turnButtonsPending = () => queue.busy && Date.now() < turnGateUntil;
// `turnSeat` is the turn as of the op being animated, captured when the op
// arrived -- never read live inside the step. The queue lags the network, so
// by the time a step runs the authoritative currentTurnSeat has usually moved
// on to the NEXT player; painting that would light the next seat's ring while
// this seat's 猫 effect is still playing.
const syncStep = (turnSeat, faces) =>
  queue.enqueue(async () => {
    if (turnSeat !== undefined) state.shownTurnSeat = turnSeat;
    if (faces) state.shownFaces = faces;
    sceneRefs?.scene?.sync(state);
  });
// What the seats may show AS OF one op, captured the moment that op arrived --
// the reveal map and the disqualified seats, the two remaining things sync()
// used to read live. Same reasoning as `turnSeat` above: a step that read them
// at run time would draw a クク or a 失格 card that belongs to a LATER event.
// disqualifiedInfo entries are copied because `onTable` is flipped in place
// when a reshuffle sweeps the card away -- the snapshot must not follow that.
const captureFaces = (s) => ({
  revealed: { ...s.revealedCards },
  dq: new Set(s.disqualifiedIdsThisDeal),
  dqInfo: Object.fromEntries(Object.entries(s.disqualifiedInfo ?? {}).map(([pid, info]) => [pid, { ...info }])),
});

// The deal-out gate (gameState.js, shownDealing): while it is up the scene
// draws backs and empty seats only. Always paired -- see the `finally` in the
// deal step, and the idle catch-up in render(), which lowers it if a rebuild
// or a cleared queue ever leaves it up.
function beginDealOut() {
  state.shownDealing = true;
  state.shownDealtSeats = new Set();
}
function endDealOut() {
  state.shownDealing = false;
}

// The pending result-pane fast-forward net, if one is armed (see result_pause).
let resultPaneNet = null;
function cancelResultPaneNet() {
  if (resultPaneNet === null) return;
  clearTimeout(resultPaneNet);
  resultPaneNet = null;
}
// The reveal point for MY own card: advance the presentation mirror
// (shownHand) to the authoritative hand, then sync the scene + hand-info so
// my seat and effect line update together -- and only here, so an effect
// animation earlier in the queue finishes first. Enqueue this in place of a
// plain syncStep for any op that can change my hand (exchange / deck draw /
// deal). For others' exchanges yourHand is unchanged, so it's a harmless sync.
// `card` is the hand THIS op reveals, captured when the op was emitted --
// never `state.yourHand` read at step time. The queue lags behind the network:
// while it drains, later events have already advanced yourHand, so a step that
// read it live would show a hand from a future op. That is exactly how the
// outgoing card of a cambio came to display the card being RECEIVED -- the
// previous op's reveal step had already put the new hand in my seat, and the
// exchange step then flew whatever was sitting there.
// Pass `undefined` for ops that don't change my hand: the sync still runs, but
// the shown hand is left alone.
const revealHandStep = (card, turnSeat, faces) =>
  queue.enqueue(async () => {
    if (!sceneRefs) return;
    if (card !== undefined) state.shownHand = card;
    if (turnSeat !== undefined) state.shownTurnSeat = turnSeat;
    if (faces) state.shownFaces = faces;
    sceneRefs.scene.sync(state);
    renderHandInfo(sceneRefs.handInfoEl, state);
  });

// Clear the table before a new deal is dealt out: every card still sitting in
// a seat belongs to the deal that just ended (the open turned them face-up and
// the result pane held them there), so send them to the discard pile first.
// Without this the incoming cards land on top of last deal's hands and read as
// being stacked onto them -- and the slots are emptied here, before the
// flights start, so nothing shows through underneath mid-animation.
async function sweepToDiscard(sc) {
  const slots = [...sc.root.querySelectorAll(".card-slot")].filter((s) => s.firstElementChild);
  if (!slots.length) return;
  const flights = slots.map((slot) => {
    // fly() measures both rects synchronously, so emptying the slot on the
    // very next line keeps the hand-off invisible -- and measures the slot
    // while it still has a card in it (an empty slot can measure zero-width,
    // which fly() treats as "nothing to animate").
    const flight = fly(queue, { fromEl: slot, toEl: sc.discardEl(), html: slot.innerHTML, duration: 320 });
    slot.innerHTML = "";
    return flight;
  });
  sound.play("flip");
  await Promise.all(flights);
}

// The open collects every disqualified player's card into the discard pile.
// Their seats hold a real card until this runs (face-up under 即時公開,
// face-down under 遅延公開), so fly what is actually sitting there.
async function collectDisqualifiedCards(sc, faces) {
  // What is actually SITTING in the seats, i.e. the shown snapshot -- a card
  // the live state has already taken off the table (or put there) is not what
  // the player is looking at.
  const shown = faces?.dqInfo ?? state.disqualifiedInfo ?? {};
  const seats = Object.entries(shown)
    .filter(([, dq]) => dq?.onTable)
    .map(([pid]) => pid);
  if (!seats.length) return;
  const flights = [];
  for (const pid of seats) {
    const slot = sc.slotEl(pid);
    if (!slot || !slot.firstElementChild) continue;
    // Measured before the slot is emptied, same hand-off as sweepToDiscard.
    flights.push(fly(queue, { fromEl: slot, toEl: sc.discardEl(), html: slot.innerHTML, duration: 320 }));
    slot.innerHTML = "";
  }
  if (!flights.length) return;
  sound.play("flip");
  await Promise.all(flights);
}

function handleOp(op) {
  // Snapshot of whose turn it is AS OF THIS OP. handleOp runs synchronously
  // right after the event mutated the state, so this is that op's value; the
  // queued steps below use it instead of reading the live one later.
  const turnSeat = state.currentTurnSeat;
  // Same capture, for the reveals and the disqualified seats (captureFaces).
  const faces = captureFaces(state);
  switch (op.kind) {
    case "rejected":
      actions.resync();
      return;

    case "prompt":
      // 子供の時間 自動続行: a continue prompt only ever appears in the child's
      // time, so if the toggle is on, pay and continue without showing it.
      // (autoContinue is declared below; hoisted `let` initialized by then.)
      if (autoContinue && state.continuePrompt) {
        actions.sendContinue(true);
        showToast("子供の時間: チップを払って自動で続行しました");
        return;
      }
      // Don't hard-snap the scene -- speed the pending effect chain up so I
      // still see what just happened before deciding.
      if (state.turnPrompt) turnGateUntil = Date.now() + TURN_GATE_MAX_MS;
      queue.hurry();
      sound.play("my_turn");
      return;

    case "rebuild":
      queue.clear();
      // A hard scene reset (reconnect / snapshot): there is no animation left
      // to stay behind, so the mirrors take the live values outright.
      state.shownTurnSeat = state.currentTurnSeat;
      state.shownHand = state.yourHand;
      state.shownOpened = state.lastDealOpened;
      state.shownFaces = captureFaces(state);
      // Whatever deal-out was running belonged to the scene we just threw
      // away; leaving the gate closed would hide the rebuilt table.
      endDealOut();
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
      // Skipped in confirm mode: there the human is deliberately gating on
      // clicks, so the reveal must stay behind the confirm cards in the queue
      // rather than jumping the line (drainToLatest at the next deal boundary
      // keeps the backlog bounded if they fall behind).
      if (confirmMode === "off") {
        const deadline = state.resultPause?.deadline;
        const grace = deadline
          ? Math.max(0, deadline - Date.now() - RESULT_PANE_RESERVE_MS)
          : RESULT_PANE_GRACE_MS;
        // Kept so the next deal can cancel it (see cancelResultPaneNet). On an
        // AI-only table the server's result wait is 0s, so this timer fires
        // essentially immediately -- typically AFTER the next deal_started has
        // already called queue.resume() and enqueued its deal-out. Left armed,
        // its fastForward() then set `instant` again and finished the flights
        // mid-deal: cards snapped into place unanimated and the following steps
        // (a クク宣言 an AI had already declared) painted straight onto the
        // table. That is corollary 4 in gameState.js.
        resultPaneNet = setTimeout(() => {
          resultPaneNet = null;
          if (revealed) return;
          queue.fastForward();
          requestAnimationFrame(reveal); // let the flushed ghosts clear first
        }, grace);
      }
      return;
    }

    case "deal_started": {
      const dealtHand = op.yourHand ?? null;
      // Confirm-mode backlog bound: a new deal (incl. a new pot's first deal)
      // is a hard chapter boundary. If the human fell behind on confirm cards
      // -- e.g. they were out of the pot and the AIs raced through the last
      // deal -- drop the stale cards and snap to the deal now on the table
      // rather than making them click through history. clear() empties the
      // queue and dismisses the active card without setting the instant flag,
      // so this deal's own dealing animation (enqueued just below) still plays.
      if (confirmMode !== "off") queue.clear();
      // A new deal is a fresh chapter: play it at full speed even if the tail
      // of the last one was fast-forwarded (see queue.resume) -- and disarm the
      // previous deal's result-pane net, which would otherwise fast-forward
      // THIS deal (corollary 4).
      cancelResultPaneNet();
      queue.resume();
      const seatsInOrder = (state.table?.seats ?? []).filter((s) => s.in_current_pot !== false).map((s) => s.player_id);
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        // The gate is up for the whole step -- the sweep empties every slot,
        // so from here until the last card lands no seat may show a face, no
        // matter what has arrived off the socket meanwhile (gameState.js,
        // shownDealing).
        beginDealOut();
        try {
          await sweepToDiscard(sc);
          for (const pid of seatsInOrder) {
            sound.play("deal");
            await fly(queue, { fromEl: sc.deckEl(), toEl: sc.slotEl(pid), html: cardHTML(null), duration: 160 });
            // The dealt card settles face-down in the seat the moment its own
            // flight lands, so seats fill one by one like a real deal instead of
            // every card popping in at the final sync.
            state.shownDealtSeats.add(pid);
            const slot = sc.slotEl(pid);
            if (slot) slot.innerHTML = cardHTML(null);
          }
        } finally {
          // Whatever happened in there (a fast-forward finishing the flights,
          // a scene rebuild pulling the elements out from under us), the gate
          // must come down -- a stuck gate would hide the whole table.
          endDealOut();
        }
        // Only now, with the whole deal on the table, do I look at my own
        // card: it turns face-up in place rather than having been readable
        // from the moment the event arrived.
        state.shownHand = dealtHand;
        state.shownTurnSeat = turnSeat;
        state.shownFaces = faces;
        sc.sync(state);
        renderHandInfo(sceneRefs.handInfoEl, state);
        await flipReveal(queue, sc.slotEl(state.playerId)?.querySelector(".card-face"));
      });
      revealHandStep(dealtHand, turnSeat, faces); // safety net: also reveals when the step above was skipped
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
      syncStep(turnSeat, faces);
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
      syncStep(turnSeat, faces);
      return;
    }

    case "exchange": {
      const { requester, target, yourNewCard } = op;
      const involvesMe = requester === state.playerId || target === state.playerId;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("exchange");
        const rSlot = sc.slotEl(requester);
        const tSlot = sc.slotEl(target);
        // A cambio swaps two physical cards, so both must visibly LEAVE their
        // seats and cross. Each ghost carries the card that was actually
        // sitting there (face-up if an effect had already revealed it), and
        // the slot is emptied right after fly() has measured it -- otherwise
        // the old cards stay put and the two ghosts read as copies flying
        // over a table where nothing moved.
        // `||`, not `??`: an empty slot yields "" (not nullish), and a ghost
        // with no content measures zero and flies invisibly.
        const rHTML = rSlot?.innerHTML || cardHTML(null);
        const tHTML = tSlot?.innerHTML || cardHTML(null);
        const flights = [
          fly(queue, { fromEl: rSlot, toEl: tSlot, html: rHTML, duration: FLIGHT_MS }),
          fly(queue, { fromEl: tSlot, toEl: rSlot, html: tHTML, duration: FLIGHT_MS }),
        ];
        if (rSlot) rSlot.innerHTML = "";
        if (tSlot) tSlot.innerHTML = "";
        await Promise.all(flights);
        // Refill in THIS step rather than leaning on the one queued below:
        // this step is the only thing that emptied the slots, so it has to be
        // what fills them again. A clear() in between (reconnect/rebuild)
        // drops queued steps, and the two seats would sit visibly empty until
        // the next idle render.
        if (involvesMe) state.shownHand = yourNewCard;
        state.shownTurnSeat = turnSeat;
        state.shownFaces = faces;
        sc.sync(state);
        renderHandInfo(sceneRefs.handInfoEl, state);
      });
      // Only my own exchanges move my hand; someone else's is a plain re-sync.
      revealHandStep(involvesMe ? yourNewCard : undefined, turnSeat, faces);
      // Confirm mode: pause on a card naming what I received, but ONLY when I
      // was the exchange TARGET -- someone else's cambio landed on me, which I
      // didn't initiate and might miss. When I'm the turn player (requester) I
      // chose the cambio and watch my own card flip, so the modal is redundant.
      if (confirmMode === "full" && yourNewCard && target === state.playerId) {
        queue.enqueue(async (instant) => {
          if (instant) return;
          await banner(queue, `交換成立 — あなたの新しい手札: ${yourNewCard}`, "info");
        });
      }
      return;
    }

    case "deck_exchange": {
      const { actor, givenUp, newCard } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        // Two beats, in the order the hands actually move at a table: the card
        // being given up goes to the discard FIRST, and only then does the new
        // one come off the deck. Doing it the other way round read as the new
        // card landing on top of a seat that was still holding the old one.
        const actorSlot = sc.slotEl(actor);
        // 1. The given-up card lands face-up on the discard pile.
        sound.play("flip");
        const discarded = fly(queue, { fromEl: actorSlot, toEl: sc.discardEl(), html: cardHTML(givenUp), duration: FLIGHT_MS });
        if (actorSlot) actorSlot.innerHTML = ""; // measured by fly() already
        await discarded;
        // 2. The draw is public at a physical table, so it travels face-up.
        sound.play("deal");
        await fly(queue, { fromEl: sc.deckEl(), toEl: actorSlot, html: cardHTML(newCard), duration: FLIGHT_MS });
        // It lands face-up, so this IS the reveal point for the actor's
        // (possibly my) new card -- advance shownHand here.
        if (actor === state.playerId) state.shownHand = newCard;
        state.shownTurnSeat = turnSeat;
        state.shownFaces = faces;
        sc.sync(state); // the actor's slot now holds the revealed drawn card
        renderHandInfo(sceneRefs.handInfoEl, state);
        await banner(queue, `${game.seatName(actor)} が山札から ${newCard} を引く`, "info");
        await pause(queue, REVEAL_HOLD_MS);
      });
      syncStep(turnSeat, faces);
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
      syncStep(turnSeat, faces);
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
          state.shownFaces = faces;
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
      syncStep(turnSeat, faces);
      return;
    }

    case "cucco_declared": {
      const { player } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("cucco");
        state.shownFaces = faces;
        sc.sync(state); // the declarer's クク is now revealed in their slot
        const cardEl = sc.slotEl(player)?.querySelector(".card-face");
        await flipReveal(queue, cardEl);
        await effectMotion(queue, cardEl, "cucco");
        // important=true: ends the deal, so it gets a modal even in 最小 mode.
        await banner(queue, `クク宣言!! — ${game.seatName(player)}`, "cucco", 1500, true);
        await pause(queue, REVEAL_HOLD_MS);
      });
      syncStep(turnSeat, faces);
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
          // everyone sees exactly why this player is out. It STAYS there for
          // the rest of the deal (marked 失格) and is collected with everyone
          // else's at the open -- the server holds it out of the discard pile
          // until then too, so nothing flies to the discard here.
          slot.innerHTML = cardHTML(card);
          const cardEl = slot.querySelector(".card-face");
          await flipReveal(queue, cardEl);
          if (card === "道化") await effectMotion(queue, cardEl, "joker");
          // important=true: a player leaving the deal is worth a modal in 最小.
          await banner(queue, `${game.seatName(player)} 失格 — ${label}`, "danger", 1100, true);
          await pause(queue, REVEAL_HOLD_MS);
        } else {
          // Disclosure deferred (card hidden): still announce who and why.
          await banner(queue, `${game.seatName(player)} 失格 — ${label}`, "danger", 1100, true);
          await pause(queue, REVEAL_HOLD_MS);
        }
      });
      syncStep(turnSeat, faces);
      return;
    }

    case "reshuffle": {
      const { sweptCards = [] } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        // 山札の再構成に表向きの失格札まで含める設定: those cards are gathered
        // off the table into the discard first, then the whole pile becomes
        // the new deck -- two beats, so it reads as "collect, then rebuild"
        // rather than cards vanishing from seats for no visible reason.
        if (sweptCards.length) {
          sound.play("flip");
          await Promise.all(
            sweptCards.map(({ player, card }) => {
              const slot = sc.slotEl(player);
              const flight = fly(queue, { fromEl: slot, toEl: sc.discardEl(), html: cardHTML(card), duration: FLIGHT_MS });
              if (slot) slot.innerHTML = ""; // measured by fly() already
              return flight;
            })
          );
        }
        sound.play("reshuffle");
        await fly(queue, { fromEl: sc.discardEl(), toEl: sc.deckEl(), html: cardHTML(null), duration: 500 });
      });
      syncStep(turnSeat, faces);
      return;
    }

    case "deal_opened": {
      // Captured now, for the same reason as the hand above: by the time this
      // step runs, a later deal_started may already have reset lastDealOpened.
      const openedSnapshot = state.lastDealOpened;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc) return;
        // The open is when a disqualified player's card finally joins the
        // discard pile (docs/rules/final_rules.md 7.). Fly it there first, so
        // it is seen being collected rather than just vanishing out of the
        // seat when the sync below empties it.
        if (!instant) await collectDisqualifiedCards(sc, faces);
        // THE reveal point for everyone's hand: advance the presentation
        // mirror here and nowhere else, so the table stays face-down until
        // the last turn's animation has finished playing.
        if (instant) {
          // Snapped: no theatre, just land on the end state.
          state.shownOpened = openedSnapshot;
          state.shownFaces = faces;
          sc.sync(state);
          return;
        }
        // Turn the hands face-up ONE AT A TIME, in seat order. shownOpened
        // stays unset until the last one, because sync() reveals every hand
        // at once -- the whole point here is that it doesn't.
        const hands = openedSnapshot?.hands ?? {};
        const elevated = openedSnapshot?.elevated_joker_holders ?? [];
        // Turn order, not seat order: the deal is played from the dealer's
        // left round to the dealer, so the reveal follows the same path and
        // the dealer's card lands last (docs/rules/final_rules.md 「親」).
        const seatIds = (state.table?.seats ?? []).map((s) => s.player_id);
        const dealerIdx = seatIds.indexOf(state.table?.dealer_seat);
        const inTurnOrder =
          dealerIdx === -1
            ? seatIds
            : [...seatIds.slice(dealerIdx + 1), ...seatIds.slice(0, dealerIdx + 1)];
        const order = inTurnOrder.filter((pid) => hands[pid] !== undefined);
        const per = Math.min(OPEN_MAX_PER_CARD_MS, OPEN_SEQUENCE_MS / Math.max(1, order.length));
        const flipMs = Math.round(per * 0.62);
        const gapMs = Math.round(per) - flipMs;
        sound.play("open");
        for (const pid of order) {
          const slot = sc.slotEl(pid);
          if (!slot) continue;
          slot.innerHTML = cardHTML(hands[pid], elevated.includes(pid));
          await flipReveal(queue, slot.querySelector(".card-face"), flipMs);
          await pause(queue, gapMs);
        }
        state.shownOpened = openedSnapshot;
        state.shownFaces = faces;
        sc.sync(state); // reconcile anything the loop didn't touch
        // Pad whatever the reveal did not spend, so the total from the open to
        // the result pane is the same at a 2-seat table and a 15-seat one.
        const spent = Math.round(per) * order.length;
        await pause(queue, Math.max(0, OPEN_SEQUENCE_MS - spent) + OPEN_HOLD_MS);
      });
      return;
    }

    case "chips_paid": {
      const { player } = op;
      queue.enqueue(async (instant) => {
        const sc = scene();
        if (!sc || instant) return;
        sound.play("chip");
        await fly(queue, { fromEl: sc.seatEl(player), toEl: sc.potEl(), html: '<div class="chip-ghost">🪙</div>', duration: 500 });
      });
      syncStep(turnSeat, faces);
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
      syncStep(turnSeat, faces);
      return;
    }

    case "game_ended":
      // Same chapter-boundary drain: don't leave stale confirm cards stacked
      // behind the final ranking modal.
      if (confirmMode !== "off") queue.clear();
      sound.play("pot_win");
      syncStep(turnSeat, faces);
      return;

    default:
      syncStep(turnSeat, faces);
      return;
  }
}

// -- tool cluster (floats outside #screen so re-renders never remove it) --
//
// One fixed top-right group holding every always-available control (card
// reference, sound toggle) so they never collide with the header's pot
// counter and read as a single toolbar. The header reserves space for it.

function mountToolCluster() {
  const cluster = document.createElement("div");
  cluster.id = "tool-cluster";
  document.body.appendChild(cluster);
  return cluster;
}

// The invite link is otherwise only reachable from the waiting room, which a
// table with AI players leaves almost immediately -- once play starts there
// was no way to hand the link to another person short of ending the game.
// A newcomer who joins mid-game sits out the running game and is seated for
// the next one, so the invite is worth having available throughout.
let inviteOpen = false;

function mountInviteButton(cluster) {
  const btn = document.createElement("button");
  btn.id = "invite-toggle";
  btn.type = "button";
  btn.innerHTML = '🔗<span class="tool-label"> 招待</span>';
  btn.title = "この卓の招待リンクを表示(他の人を呼ぶ)";
  btn.addEventListener("click", () => {
    inviteOpen = !inviteOpen;
    renderInviteOverlay();
  });
  cluster.appendChild(btn);
  return btn;
}

// Leaving mid-table. The result screen has always offered 部屋を出る, but that
// is only reachable once a game ENDS -- a spectator who wanted to stop watching
// (or a player who has to go) had no way out but closing the tab, which also
// meant the server only found out via the socket dropping. This sits next to
// the invite button for the whole time we are at a table.
function mountExitButton(cluster) {
  const btn = document.createElement("button");
  btn.id = "exit-table";
  btn.type = "button";
  btn.innerHTML = '🚪<span class="tool-label"> 退出</span>';
  btn.title = "この卓から退出してロビーに戻る";
  btn.addEventListener("click", () => {
    // A seat in a running game is a different matter from a spectator slot:
    // the table plays on without you (auto-ノンチェンジ), so say so plainly.
    const seated = state.playerType !== "spectator" && (state.table?.seats ?? []).some((s) => s.player_id === state.playerId);
    const inGame = seated && state.table?.deal_number > 0 && !state.gameEnded;
    const message = inGame
      ? "対局中です。退出すると席は残りますが、あなたの手番は自動で処理されます。退出しますか?"
      : "この卓から退出してロビーに戻ります。よろしいですか?";
    if (!confirm(message)) return;
    actions.leaveRoom();
  });
  cluster.appendChild(btn);
  return btn;
}

// Only meaningful once we are actually at a table (same rule as the invite
// button, which renderInviteOverlay applies on every render).
function renderExitButton() {
  const btn = document.getElementById("exit-table");
  if (btn) btn.hidden = !state.roomId;
}

function renderInviteOverlay() {
  let holder = document.getElementById("invite-holder");
  if (!holder) {
    holder = document.createElement("div");
    holder.id = "invite-holder";
    document.body.appendChild(holder);
  }
  const btn = document.getElementById("invite-toggle");
  // Only meaningful once we are actually at a table.
  if (btn) btn.hidden = !state.roomId;
  if (!inviteOpen || !state.roomId) {
    holder.innerHTML = "";
    return;
  }
  holder.innerHTML = `
    <div class="modal-overlay"><div class="modal">
      <h2>この卓に招待する</h2>
      <p>プレイルームID: <strong class="room-id">${esc(state.roomId)}</strong></p>
      ${inviteBlockHTML(
        "live-invite",
        "この卓の招待リンク",
        "受け取った人は名前を入れるだけで参加できます。対局中に入った人は、今のゲームが終わってから次のゲームで着席します。",
        (host) => tableInviteUrl(host, state.roomId)
      )}
      <button type="button" id="invite-close" class="secondary">閉じる</button>
    </div></div>`;
  wireInviteBlock(holder, "live-invite", (host) => tableInviteUrl(host, state.roomId));
  holder.querySelector("#invite-close").addEventListener("click", () => {
    inviteOpen = false;
    renderInviteOverlay();
  });
}

// メッセージ確認モード (3-state): "off" | "min" | "full".
//  - full: 進行メッセージが1枚ずつモーダルになり、確認を押すまで進まない
//  - min : 失格・クク宣言などディールを左右する重要イベントだけモーダル。
//          アニメと効果音で分かる交換・スキップ等は自動で流れる
//  - off : すべて自動で流れる
// 自分のプロンプト到着時は既存のfast-forward規則で解除され、サーバーの
// タイムアウトを待たせない。設定はlocalStorageで永続化(旧 "1"/"0" も移行)。
const CONFIRM_MODES = ["off", "min", "full"];
function loadConfirmMode() {
  const v = localStorage.getItem("cucco_confirm_mode");
  if (v === "1") return "full"; // migrate the old boolean
  if (v === "0" || v == null) return "off";
  return CONFIRM_MODES.includes(v) ? v : "off";
}
let confirmMode = loadConfirmMode();
const confirmActive = () => confirmMode !== "off";
setConfirmModeGetter(() => confirmMode);
queue.setConfirmMode(confirmActive);
// Re-render when the presentation catches up, so the turn buttons the dock
// greyed out (see renderDock's `pending`) come back the instant they may be
// used, rather than on the next unrelated state change.
// `sceneRefs` (not uiPhase) is the "we are showing the table" signal: uiPhase
// only tracks the pre-room screens and stays "lobby" for the whole game, so
// gating on it here meant this never fired and the buttons stayed dead.
queue.setOnDrain(() => { if (sceneRefs) render(); });

const CONFIRM_LABELS = {
  off: ['💨', ' 確認 OFF', "メッセージ確認: OFF — すべて自動で流れる(クリックで切替)"],
  min: ['🔔', ' 確認 最小', "メッセージ確認: 最小 — 失格・クク宣言など重要な場面だけ確認(クリックで切替)"],
  full: ['✋', ' 確認 フル', "メッセージ確認: フル — 進行メッセージを1枚ずつ確認(クリックで切替)"],
};

function mountConfirmToggle(cluster) {
  const btn = document.createElement("button");
  btn.id = "confirm-toggle";
  btn.type = "button";
  const refresh = () => {
    const [icon, label, title] = CONFIRM_LABELS[confirmMode];
    btn.innerHTML = `${icon}<span class="tool-label">${label}</span>`;
    btn.title = title;
    btn.classList.toggle("off", confirmMode === "off");
    btn.dataset.mode = confirmMode;
  };
  btn.addEventListener("click", () => {
    confirmMode = CONFIRM_MODES[(CONFIRM_MODES.indexOf(confirmMode) + 1) % CONFIRM_MODES.length];
    localStorage.setItem("cucco_confirm_mode", confirmMode);
    refresh();
  });
  refresh();
  cluster.appendChild(btn);
}

// 子供の時間 自動続行: ON にすると、子供の時間(1〜3ディール目)の敗者に
// 出る「続行しますか?」を、必ずチップを払って続行するよう自動で応答する
// (続行確認はそもそも子供の時間にしか出ないので、常に続行=true でよい)。
let autoContinue = localStorage.getItem("cucco_auto_continue") === "1";

function mountAutoContinueToggle(cluster) {
  const btn = document.createElement("button");
  btn.id = "auto-continue-toggle";
  btn.type = "button";
  const refresh = () => {
    btn.innerHTML = autoContinue
      ? '▶️<span class="tool-label"> 子の時間 自動続行</span>'
      : '⏸️<span class="tool-label"> 子の時間 手動</span>';
    btn.title = autoContinue
      ? "子供の時間: チップを払って自動で続行する(クリックで手動に)"
      : "子供の時間: 続行/離脱を毎回選ぶ(クリックで自動続行に)";
    btn.classList.toggle("off", !autoContinue);
  };
  btn.addEventListener("click", () => {
    autoContinue = !autoContinue;
    localStorage.setItem("cucco_auto_continue", autoContinue ? "1" : "0");
    refresh();
  });
  refresh();
  cluster.appendChild(btn);
}

function mountSoundToggle(cluster) {
  const btn = document.createElement("button");
  btn.id = "sound-toggle";
  btn.type = "button";
  const refresh = () => {
    // Labeled so it reads as a sound control, not a mystery icon; the text
    // part is a .tool-label so narrow screens can keep just the icon.
    btn.innerHTML = sound.enabled
      ? '🔊<span class="tool-label"> 効果音 ON</span>'
      : '🔇<span class="tool-label"> 効果音 OFF</span>';
    btn.title = sound.enabled ? "効果音: ON(クリックでOFF)" : "効果音: OFF(クリックでON)";
    btn.classList.toggle("off", !sound.enabled);
  };
  btn.addEventListener("click", () => {
    sound.toggle();
    if (sound.enabled) sound.play("chip"); // audible confirmation
    refresh();
  });
  refresh();
  cluster.appendChild(btn);
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
    // Hold the rebuild while a field is being filled in. These panels are
    // rebuilt wholesale, and they are re-rendered on a schedule the user has
    // no say in -- the waiting room polls the roster every 3 seconds, which is
    // exactly where the invite block's ドメイン名 field lives. Rebuilding under
    // the caret loses the position and cancels an IME composition outright.
    // The deferred render runs when the field is left (see the focusout hook).
    //
    // Only ever holds back a redraw of the SAME screen. A change of screen --
    // the game starting while a guest is still typing their name -- must not
    // wait for anyone: it is the thing they are waiting to see.
    if (target === shownPanel && isTypingIn(screenEl)) {
      panelRenderPending = true;
      scheduleHeldPanelRender();
      return;
    }
    panelRenderPending = false;
    shownPanel = target;
    sceneRefs = null;
    if (target === "waiting") renderWaiting(screenEl, state, actions);
    else renderLobby(screenEl, state, actions, target);
    prependConnBanner();
    // Also on the way out: this branch returns early, so without it the invite
    // and exit buttons keep whatever visibility they had and stay hidden in the
    // waiting room -- the one screen where handing out the link matters most,
    // and where a watcher waiting on a game that may never start most wants a
    // way out.
    renderInviteOverlay();
    renderExitButton();
    return;
  }

  let justCreated = false;
  if (!sceneRefs) {
    // Progress message + own-card summary float over the felt's empty
    // bottom corners (the ellipse leaves the rectangle's corners unused),
    // flanking my own seat and the dock -- the space they used to take as
    // a full-width row goes back to the felt. On narrow (portrait phone)
    // screens those corners vanish, so CSS folds both holders back into
    // the normal flow between the scene and the dock.
    screenEl.innerHTML = `
      <div class="play-root">
        <header class="play-header">
          <span id="hdr-room"></span><span id="hdr-pot"></span>
        </header>
        <div id="scene-wrap">
          <div id="scene-holder"></div>
          <div id="status-holder"></div>
          <div id="hand-info-holder"></div>
        </div>
        <div id="dock-holder"></div>
        <div id="log-holder"></div>
        <div id="modal-holder"></div>
      </div>
    `;
    sceneRefs = {
      scene: createTableScene(screenEl.querySelector("#scene-holder")),
      statusEl: screenEl.querySelector("#status-holder"),
      handInfoEl: screenEl.querySelector("#hand-info-holder"),
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
  // When idle, everything has been animated, so the presentation mirrors
  // catch up to the authoritative state (safety net for any reveal path --
  // notably a reconnect, where the snapshot arrives already opened and no
  // queued step will ever run to advance them).
  if (justCreated || !queue.busy) {
    // NOT the turn: advancing it here defeated the whole mirror. A deal's
    // events arrive as several messages (an exchange refusal and the
    // disqualification it causes are two), and the queue empties in the gap
    // between them -- this branch then ran with currentTurnSeat already moved
    // on, jumping the ring to the next seat while the 人間 animation was still
    // playing. Every op that moves the turn carries it to a queued step, and a
    // hard resync (the `rebuild` op) sets it outright, so nothing is left
    // stale by leaving it alone here.
    state.shownHand = state.yourHand;
    state.shownOpened = state.lastDealOpened;
    state.shownFaces = captureFaces(state);
    // Idle means the deal step is over (or never ran, as after a reconnect):
    // holding the gate up here would leave the table permanently blank.
    endDealOut();
    if (justCreated) state.shownTurnSeat = state.currentTurnSeat;
    sceneRefs.scene.sync(state);
  }
  renderStatus(sceneRefs.statusEl, state, game.seatName);
  renderHandInfo(sceneRefs.handInfoEl, state);
  renderDock(sceneRefs.dockEl, state, actions, { pending: turnButtonsPending() });
  renderModals(sceneRefs.modalEl, state, actions, game.seatName);
  renderLogDrawer(sceneRefs.logEl, state);
  prependConnBanner();
  renderInviteOverlay();
  renderExitButton();
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
  for (const key of ["dealerReadyPrompt", "turnPrompt", "continuePrompt", "resultPause", "effectWindow"]) {
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
  // Safety net for the dock's pending gate. queue.setOnDrain re-renders the
  // moment the presentation catches up, but if that ever fails to fire the
  // turn buttons would stay disabled with no way back -- the player simply
  // could not act. Re-assert the enabled state here every tick so the worst
  // case is a quarter-second of lag, never a dead dock.
  const dock = sceneRefs?.dockEl;
  if (dock) {
    const pending = turnButtonsPending();
    for (const b of dock.querySelectorAll("#cambio-btn, #no-change-btn")) {
      if (b.disabled !== pending) b.disabled = pending;
    }
    const wait = dock.querySelector(".dock-wait");
    if (wait && !pending) wait.remove();
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
      // Came in through a table invite link: go straight to that table
      // instead of making the guest re-type an ID they were just handed.
      // Consumed once -- a failure leaves them on the lobby with the error.
      if (state.invitedRoomId) {
        const roomId = state.invitedRoomId;
        state.invitedRoomId = null;
        await actions.joinTable(roomId, { invited: true });
        return;
      }
    } catch (err) {
      state.error = err.message;
    }
    render();
  },

  async createTable(config) {
    // `_creatorRole` is UI-only -- strip it before it reaches the wire.
    const { _creatorRole, ...tableConfig } = config;
    try {
      // Role is fixed at identify time, but the choice that matters is made
      // here, on the create form ("am I playing at the table I'm making?").
      // Re-identify when they differ: nothing is joined yet, so this just
      // mints a fresh session -- and it is the only way to become a spectator
      // without sending the creator back to the name screen.
      if (_creatorRole && _creatorRole !== state.playerType) {
        await conn.identify(state.name, _creatorRole);
        state.playerId = conn.playerId;
        state.sessionToken = conn.sessionToken;
        state.playerType = _creatorRole;
      }
      const payload = await conn.createTable(tableConfig);
      state.error = null;
      await actions.joinTable(payload.room_id);
    } catch (err) {
      state.error = err.message;
      render();
    }
  },

  async joinTable(roomId, { invited = false } = {}) {
    try {
      const snapshot = await conn.joinTable(roomId, null);
      state.roomId = roomId;
      state.error = null;
      game.applySnapshot(snapshot.payload ?? snapshot);
      persist();
    } catch (err) {
      // An invite link the guest didn't type is the one case where the raw
      // protocol error explains nothing they can act on -- the usual cause is
      // a table that has since ended.
      state.error = invited
        ? `招待された卓 ${roomId} に参加できませんでした(${err.message})。` +
          "卓がすでに終了している可能性があります。新しい招待リンクをもらうか、下のボタンから卓を作ってください。"
        : err.message;
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
  // クク is fire-and-forget: the standing dock button sends it at any moment
  // and the server applies it at the next safe point (no window, no pass --
  // nothing the table waits on). If one of my own prompts was showing,
  // optimistically clear it; declaring supersedes answering it.
  sendCuccoDeclare() {
    conn.send("cucco_declare", {});
    state.turnPrompt = null;
    state.dealerReadyPrompt = null;
    state.dozoSent = true;
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
    state.shownOpened = null;
    state.prevDealSummary = null;
    actions.resync();
    render();
  },

  leaveRoom() {
    // Tell the server, don't just walk away: the socket stays open (this page
    // goes back to the lobby, it does not close), so without this the table
    // would still count us as watching -- keeping a room alive that nobody is
    // actually at. See connection.js leaveTable.
    if (state.roomId) conn.leaveTable();
    clearSession();
    state.savedSession = null;
    state.roomId = null;
    state.table = null;
    state.gameEnded = null;
    uiPhase = "lobby";
    renderExitButton();
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

// The other half of the "don't rebuild under the caret" rule in render(): once
// the field is left, run whatever render was held back, so the panel is not
// left showing stale content (a roster missing the person who just joined).
// Deferred by a tick because focusout fires BEFORE the next field gets focus --
// checking immediately would see "nobody is typing" while the user is simply
// tabbing from one field to the next, and rebuild the panel out from under
// the field they are moving into.
screenEl.addEventListener("focusout", () => {
  setTimeout(() => {
    if (panelRenderPending && !isTypingIn(screenEl)) render();
  }, 0);
});

wireConnection();
conn.connect();
const toolCluster = mountToolCluster();
mountConfirmToggle(toolCluster);
mountAutoContinueToggle(toolCluster);
mountCardReference(toolCluster);
mountSoundToggle(toolCluster);
mountInviteButton(toolCluster);
mountExitButton(toolCluster);
renderInviteOverlay(); // hides the button until a table is joined
renderExitButton();

const saved = loadSession();
if (saved && saved.sessionToken && saved.roomId) {
  state.savedSession = saved;
}
render();

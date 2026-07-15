// The presentation queue: network events mutate game state instantly (the
// state is always authoritative), but their VISUALS play out here as
// sequential steps. The one hard rule (from the plan): server timeouts don't
// wait for animations. Two escape hatches enforce that:
//   - fastForward(): snap everything to its end state in one tick (used by
//     the result-pane safety net and, via clear(), by snapshot rebuilds).
//   - hurry(): a gentler catch-up -- keep playing the pending steps but at
//     HURRY_RATE speed, so a player about to act still gets a quick, legible
//     recap of the effect chain instead of a hard snap to the end state.
// Every animation is registered with _track(), so both hatches reach it.

const HURRY_RATE = 3; // playback multiplier for the catch-up recap on my prompt
const HURRY_CEILING_MS = 1500; // hard cap: snap whatever is left after this

export function createQueue() {
  const steps = [];
  let running = false;
  let instant = false; // once set, every remaining step runs with zero duration
  let rate = 1; // >1 while catching up; applied to every tracked animation
  let hurryTimer = null;
  const activeAnimations = new Set();

  const busy = () => running || steps.length > 0;

  async function pump() {
    if (running) return;
    running = true;
    while (steps.length) {
      const step = steps.shift();
      try {
        await step(instant);
      } catch (err) {
        console.error("animation step failed", err);
      }
    }
    running = false;
    instant = false;
    rate = 1; // back to real time once the backlog has drained
    clearTimeout(hurryTimer);
  }

  // Finish the current animation(s) immediately and run all queued steps with
  // zero duration. Synchronous from the caller's perspective except for
  // microtasks -- by the next frame the scene shows the end state.
  function fastForward() {
    instant = true;
    for (const a of activeAnimations) a.finish();
  }

  return {
    enqueue(step) {
      steps.push(step);
      pump();
    },
    fastForward,
    // Speed the remaining recap up (rather than skipping it) so the player
    // about to act still sees what happened. A realistic backlog compresses
    // to well under a second, and the action buttons are already live. No-op
    // when nothing is pending, so it never leaves a lingering fast rate. A
    // ceiling snaps whatever remains if an animation stalls (e.g. a throttled
    // background tab never settles its `finished` promise).
    hurry() {
      if (!busy()) return;
      rate = HURRY_RATE;
      for (const a of activeAnimations) {
        try {
          a.playbackRate = HURRY_RATE;
        } catch {
          /* animation already settled */
        }
      }
      clearTimeout(hurryTimer);
      hurryTimer = setTimeout(() => {
        if (busy()) fastForward();
      }, HURRY_CEILING_MS);
    },
    clear() {
      steps.length = 0;
      clearTimeout(hurryTimer);
      for (const a of activeAnimations) a.finish();
    },
    get busy() {
      return busy();
    },
    _track(animation) {
      activeAnimations.add(animation);
      if (rate !== 1) {
        try {
          animation.playbackRate = rate;
        } catch {
          /* animation already settled */
        }
      }
      const drop = () => activeAnimations.delete(animation);
      animation.finished.then(drop, drop);
    },
  };
}

// Fly a floating clone from one rect to another (deck -> seat, seat -> seat,
// seat -> discard...). The clone lives on a fixed-position layer so table
// layout never reflows; the real elements are updated by the sync step that
// follows the flight.
export function fly(queueRef, { fromEl, toEl, html, duration = 450 }) {
  return new Promise((resolve) => {
    if (!fromEl || !toEl) return resolve();
    const from = fromEl.getBoundingClientRect();
    const to = toEl.getBoundingClientRect();
    if (!from.width || !to.width) return resolve();
    const ghost = document.createElement("div");
    ghost.className = "fly-ghost";
    ghost.innerHTML = html;
    document.body.appendChild(ghost);
    const inner = ghost.firstElementChild ?? ghost;
    const w = inner.offsetWidth || 54;
    const h = inner.offsetHeight || 76;
    ghost.style.left = `${from.left + from.width / 2 - w / 2}px`;
    ghost.style.top = `${from.top + from.height / 2 - h / 2}px`;
    const dx = to.left + to.width / 2 - (from.left + from.width / 2);
    const dy = to.top + to.height / 2 - (from.top + from.height / 2);
    const anim = ghost.animate(
      [{ transform: "translate(0,0)" }, { transform: `translate(${dx}px, ${dy}px)` }],
      { duration, easing: "cubic-bezier(0.3, 0.7, 0.4, 1)" }
    );
    queueRef._track(anim);
    const done = () => {
      ghost.remove();
      resolve();
    };
    anim.finished.then(done, done);
  });
}

export function pause(queueRef, ms) {
  return new Promise((resolve) => {
    const anim = document.body.animate([{}, {}], { duration: ms });
    queueRef._track(anim);
    anim.finished.then(resolve, resolve);
  });
}

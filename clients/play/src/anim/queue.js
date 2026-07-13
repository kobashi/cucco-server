// The presentation queue: network events mutate game state instantly (the
// state is always authoritative), but their VISUALS play out here as
// sequential steps. The one hard rule (from the plan): server timeouts don't
// wait for animations, so fastForward() must be able to complete everything
// pending in one tick -- it is called whenever a prompt addressed to ME
// arrives, and on snapshot rebuilds.

export function createQueue() {
  const steps = [];
  let running = false;
  let instant = false; // once set, every remaining step runs with zero duration
  const activeAnimations = new Set();

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
  }

  return {
    enqueue(step) {
      steps.push(step);
      pump();
    },
    // Finish the current animation(s) immediately and run all queued steps
    // with zero duration. Synchronous from the caller's perspective except
    // for microtasks -- by the next frame the scene shows the end state.
    fastForward() {
      instant = true;
      for (const a of activeAnimations) a.finish();
    },
    clear() {
      steps.length = 0;
      for (const a of activeAnimations) a.finish();
    },
    get busy() {
      return running || steps.length > 0;
    },
    _track(animation) {
      activeAnimations.add(animation);
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

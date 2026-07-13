// One-shot visual effects layered over the table: announcement banners
// (ニャー! / スキップ / クク宣言!! / 失格) and seat shakes. All animations are
// registered with the queue so fastForward() can finish them instantly.

export function banner(queueRef, text, tone = "info", duration = 1100) {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = `fx-banner ${tone}`;
    el.textContent = text;
    document.body.appendChild(el);
    const anim = el.animate(
      [
        { transform: "translate(-50%, -50%) scale(0.6)", opacity: 0 },
        { transform: "translate(-50%, -50%) scale(1.08)", opacity: 1, offset: 0.25 },
        { transform: "translate(-50%, -50%) scale(1)", opacity: 1, offset: 0.75 },
        { transform: "translate(-50%, -50%) scale(1)", opacity: 0 },
      ],
      { duration, easing: "ease-out" }
    );
    queueRef._track(anim);
    const done = () => {
      el.remove();
      resolve();
    };
    anim.finished.then(done, done);
  });
}

export function shake(queueRef, el, duration = 350) {
  return new Promise((resolve) => {
    if (!el) return resolve();
    const anim = el.animate(
      [
        { transform: "translate(-50%, -50%)" },
        { transform: "translate(calc(-50% - 7px), -50%)" },
        { transform: "translate(calc(-50% + 7px), -50%)" },
        { transform: "translate(calc(-50% - 5px), -50%)" },
        { transform: "translate(calc(-50% + 5px), -50%)" },
        { transform: "translate(-50%, -50%)" },
      ],
      { duration, easing: "ease-in-out" }
    );
    queueRef._track(anim);
    anim.finished.then(resolve, resolve);
  });
}

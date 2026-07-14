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

// Flip a face-down card up to reveal it (the slot must already hold the
// face element -- sync it before calling). No-op if there's no card face.
export function flipReveal(queueRef, cardEl, duration = 300) {
  return new Promise((resolve) => {
    if (!cardEl) return resolve();
    const anim = cardEl.animate(
      [
        { transform: "rotateY(90deg) scale(1.05)", opacity: 0.5 },
        { transform: "rotateY(0deg) scale(1)", opacity: 1 },
      ],
      { duration, easing: "ease-out" }
    );
    queueRef._track(anim);
    anim.finished.then(resolve, resolve);
  });
}

// Effect-specific motion played ON the revealed card, keyed to the card that
// fired: 猫 pounces, 人間 slams a firm rejection, 馬/家 sidesteps, クク zooms,
// 道化 tumbles. Each is a self-contained transform burst that returns to rest.
const EFFECT_MOTIONS = {
  cat: [
    { transform: "scale(1)", offset: 0 },
    { transform: "translateY(-24px) scale(1.25) rotate(-8deg)", offset: 0.4 },
    { transform: "translateY(4px) scale(1.1) rotate(4deg)", offset: 0.7 },
    { transform: "scale(1)", offset: 1 },
  ],
  human: [
    { transform: "scale(1)", offset: 0 },
    { transform: "scale(1.45)", offset: 0.25, filter: "brightness(1.5)" },
    { transform: "scale(1.3) rotate(-3deg)", offset: 0.5 },
    { transform: "scale(1.3) rotate(3deg)", offset: 0.75 },
    { transform: "scale(1)", offset: 1 },
  ],
  skip: [
    { transform: "translateX(0)", offset: 0 },
    { transform: "translateX(26px) rotate(10deg)", offset: 0.5 },
    { transform: "translateX(0) rotate(0)", offset: 1 },
  ],
  cucco: [
    { transform: "scale(1)", offset: 0 },
    { transform: "scale(1.6)", offset: 0.5, filter: "drop-shadow(0 0 12px #ffd75e)" },
    { transform: "scale(1)", offset: 1 },
  ],
  joker: [
    { transform: "rotate(0) scale(1)", offset: 0 },
    { transform: "rotate(180deg) scale(1.2)", offset: 0.5 },
    { transform: "rotate(360deg) scale(1)", offset: 1 },
  ],
};

export function effectMotion(queueRef, cardEl, kind, duration = 650) {
  return new Promise((resolve) => {
    const frames = EFFECT_MOTIONS[kind];
    if (!cardEl || !frames) return resolve();
    const anim = cardEl.animate(frames, { duration, easing: "ease-in-out" });
    queueRef._track(anim);
    anim.finished.then(resolve, resolve);
  });
}

// A quiet "kept as-is" confirmation for ノンカンビオ -- a small settle-in
// pulse on the seat, distinct from shake() which reads as a rejection.
export function confirmPulse(queueRef, el, duration = 320) {
  return new Promise((resolve) => {
    if (!el) return resolve();
    const anim = el.animate(
      [
        { transform: "scale(1)", offset: 0 },
        { transform: "scale(1.05)", offset: 0.4, filter: "brightness(1.15)" },
        { transform: "scale(1)", offset: 1 },
      ],
      { duration, easing: "ease-out" }
    );
    queueRef._track(anim);
    anim.finished.then(resolve, resolve);
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

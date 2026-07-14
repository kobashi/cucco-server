// Sound effects, synthesized with the Web Audio API -- no audio files, no
// external assets (consistent with the no-build, no-source-art policy).
// Everything is short (<0.6s) and quiet by design; the master switch
// persists in localStorage and the AudioContext is created lazily on the
// first user gesture (browsers keep it suspended until one anyway).

const STORE_KEY = "cucco_sound";

export function createSound() {
  let ctx = null;
  let enabled = (localStorage.getItem(STORE_KEY) ?? "on") === "on";

  function ensureCtx() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  // Unlock on the first gesture so later event-driven sounds are allowed.
  const unlock = () => {
    if (enabled) ensureCtx();
    window.removeEventListener("pointerdown", unlock);
  };
  window.addEventListener("pointerdown", unlock);

  function tone({ freq = 440, to = null, type = "sine", dur = 0.15, gain = 0.12, at = 0 }) {
    const c = ensureCtx();
    if (!c) return;
    const t0 = c.currentTime + at;
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (to) osc.frequency.exponentialRampToValueAtTime(to, t0 + dur);
    g.gain.setValueAtTime(gain, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(c.destination);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  function swish({ dur = 0.12, gain = 0.08, freq = 1800, at = 0 }) {
    const c = ensureCtx();
    if (!c) return;
    const t0 = c.currentTime + at;
    const len = Math.max(1, Math.floor(c.sampleRate * dur));
    const buf = c.createBuffer(1, len, c.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = c.createBufferSource();
    src.buffer = buf;
    const filter = c.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = freq;
    filter.Q.value = 0.8;
    const g = c.createGain();
    g.gain.setValueAtTime(gain, t0);
    src.connect(filter).connect(g).connect(c.destination);
    src.start(t0);
  }

  const PATTERNS = {
    // -- table mechanics --
    deal: () => swish({ dur: 0.09, gain: 0.06 }),
    exchange: () => {
      swish({ dur: 0.16, gain: 0.08, freq: 1200 });
      swish({ dur: 0.16, gain: 0.08, freq: 2000, at: 0.05 });
    },
    flip: () => tone({ freq: 900, type: "triangle", dur: 0.06, gain: 0.08 }),
    open: () => {
      tone({ freq: 523, dur: 0.2, gain: 0.07 });
      tone({ freq: 659, dur: 0.2, gain: 0.07, at: 0.02 });
      tone({ freq: 784, dur: 0.25, gain: 0.07, at: 0.04 });
    },
    chip: () => {
      tone({ freq: 2100, type: "triangle", dur: 0.07, gain: 0.09 });
      tone({ freq: 2600, type: "triangle", dur: 0.09, gain: 0.06, at: 0.03 });
    },
    pot_win: () => {
      [523, 659, 784, 1046].forEach((f, i) => tone({ freq: f, dur: 0.16, gain: 0.09, at: i * 0.09 }));
    },
    reshuffle: () => swish({ dur: 0.4, gain: 0.09, freq: 900 }),
    my_turn: () => {
      tone({ freq: 880, dur: 0.1, gain: 0.1 });
      tone({ freq: 1175, dur: 0.16, gain: 0.1, at: 0.11 });
    },
    pass: () => tone({ freq: 460, to: 400, type: "sine", dur: 0.1, gain: 0.06 }),
    leave: () => {
      tone({ freq: 500, to: 260, type: "sine", dur: 0.28, gain: 0.08 });
    },

    // -- card effects (distinct per card so the ear learns them) --
    cat: () => {
      // two-note "meow-ish" pitch bends
      tone({ freq: 700, to: 950, type: "sawtooth", dur: 0.16, gain: 0.07 });
      tone({ freq: 950, to: 550, type: "sawtooth", dur: 0.22, gain: 0.07, at: 0.17 });
    },
    human: () => tone({ freq: 180, to: 120, type: "sawtooth", dur: 0.35, gain: 0.11 }),
    skip: () => tone({ freq: 500, to: 1400, type: "triangle", dur: 0.16, gain: 0.09 }),
    cucco: () => {
      [660, 660, 990].forEach((f, i) => tone({ freq: f, type: "square", dur: 0.14, gain: 0.08, at: i * 0.15 }));
    },
    disqualified: () => {
      tone({ freq: 400, dur: 0.18, gain: 0.1 });
      tone({ freq: 300, dur: 0.22, gain: 0.1, at: 0.16 });
      tone({ freq: 220, dur: 0.3, gain: 0.1, at: 0.34 });
    },
  };

  return {
    play(name) {
      if (!enabled) return;
      try {
        PATTERNS[name]?.();
      } catch {
        // audio is never worth breaking the game over
      }
    },
    get enabled() {
      return enabled;
    },
    toggle() {
      enabled = !enabled;
      localStorage.setItem(STORE_KEY, enabled ? "on" : "off");
      if (enabled) ensureCtx();
      return enabled;
    },
  };
}

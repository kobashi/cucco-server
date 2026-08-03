// Player names and room IDs come from other users over the wire -- escape
// before interpolating into innerHTML.
export function esc(str) {
  const div = document.createElement("div");
  div.textContent = String(str ?? "");
  return div.innerHTML;
}

export function secondsLeft(deadline) {
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

// `cucco_ws_host` must be a bare host[:port] -- connection.js's wsUrlFor()
// prepends its own "wss://"/"ws://", so a value that already has a scheme
// (someone pasting the full page URL, e.g. "https://.../?ws=host", into the
// "接続先を変更" field instead of just the host) would otherwise produce a
// broken nested URL like "wss://https://.../?ws=host". Strip any scheme and
// anything from the first "/" onward so a pasted full URL degrades to just
// its host instead of silently failing to connect.
export function sanitizeWsHost(raw) {
  return String(raw ?? "")
    .trim()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//i, "")
    .split(/[/?#]/)[0];
}

// Attributes for a field that takes a MACHINE string -- a host name, a room
// ID -- rather than prose. `type="url"` is the load-bearing one: a Japanese
// IME switches itself to direct alphanumeric input for url/email/password
// fields, the same way it does on a login form, so what is typed is what
// lands in the field. The rest turn off the phone's helpful corrections
// (capitalising the first letter, autocorrecting a domain into a word) and
// the browser's spell-check underline.
//
// Note for the caller: a form containing a `type="url"` field needs
// `novalidate`, because these fields hold a bare host with no scheme, which
// the browser's URL validation rejects. The value is normalised by
// sanitizeWsHost anyway.
export const MACHINE_INPUT_ATTRS =
  'type="url" inputmode="url" autocapitalize="off" autocorrect="off" autocomplete="off" spellcheck="false"';

// What an address field is allowed to contain: host[:port] characters, plus
// the punctuation a pasted URL is made of. The URL parts are kept on purpose
// -- sanitizeWsHost cuts the value down to its host when it is saved, and it
// can only do that if the "://" and the first "/" are still there. Dropping
// them as they were typed turned "https://host/path?x=1" into "https:hostpathx1".
const HOST_CHARS = /[^A-Za-z0-9.:_\-[\]/?#=&%~+]/g;

// Normalise what an IME left behind in an address field.
//
// `type="url"` asks the platform to switch to direct alphanumeric input, and
// Windows IMEs do; **macOS does not**, and no web API can force it -- so the
// field has to cope with text that went through a conversion. NFKC turns the
// full-width letters a Japanese input mode produces (ｈｏｓｔ) into the ASCII
// they stand for, and anything that still cannot appear in a host name is
// dropped rather than left to fail at connection time.
//
// Kana that was never converted has no ASCII to fall back to, so it goes.
// That is the same result as having typed with the IME off, which is what the
// field is asking for.
export function sanitizeHostInput(raw) {
  return String(raw ?? "").normalize("NFKC").replace(HOST_CHARS, "");
}

// Keep an address field ASCII as it is typed, without fighting the IME while
// a conversion is still open. Returns nothing; wires the element in place.
export function keepHostFieldAscii(el) {
  if (!el) return;
  let composing = false;
  const clean = () => {
    if (composing) return; // mid-conversion: let the IME finish first
    const before = el.value;
    const after = sanitizeHostInput(before);
    if (after === before) return;
    // Keep the caret where the user left it, counting only the characters
    // that survived ahead of it.
    const head = sanitizeHostInput(before.slice(0, el.selectionStart ?? before.length)).length;
    el.value = after;
    try {
      el.setSelectionRange(head, head);
    } catch {
      /* not a field that carries a selection */
    }
  };
  el.addEventListener("compositionstart", () => (composing = true));
  el.addEventListener("compositionend", () => {
    composing = false;
    clean();
  });
  el.addEventListener("input", clean);
  el.addEventListener("blur", clean);
}

// Rebuild a panel without throwing away what the user has half-typed into it.
//
// The panels are rebuilt wholesale, and a rebuild resets every field to
// whatever the markup says -- so a connection blip while someone is filling in
// their name empties the box under them. Renders are held back while a field
// has focus (see the clients' render()), but not every rebuild can be held:
// a screen change must go through, and focus can legitimately be elsewhere
// (a stepper button) while a form is half-filled.
//
// So: remember what the user has edited, rebuild, and put it back. Only fields
// the user actually touched are restored -- everything else is state-driven and
// must come from the new markup.
export function preserveFormState(container, rebuild) {
  const active = document.activeElement;
  const focusedId = active && container.contains(active) && active.id ? active.id : null;
  const caret = focusedId && "selectionStart" in active ? [active.selectionStart, active.selectionEnd] : null;
  const edited = [...container.querySelectorAll("[data-user-edited]")]
    .filter((el) => el.id)
    .map((el) => ({ id: el.id, value: el.value, checked: el.checked }));

  rebuild();

  for (const saved of edited) {
    const el = container.querySelector(`#${CSS.escape(saved.id)}`);
    if (!el) continue; // a different screen, or the field is gone: nothing to restore
    el.value = saved.value;
    if (el.type === "checkbox" || el.type === "radio") el.checked = saved.checked;
    el.dataset.userEdited = "1";
  }
  if (!focusedId) return;
  const back = container.querySelector(`#${CSS.escape(focusedId)}`);
  if (!back) return;
  back.focus();
  if (caret && "setSelectionRange" in back) {
    try {
      back.setSelectionRange(caret[0], caret[1]);
    } catch {
      /* the field type does not carry a selection */
    }
  }
}

// Mark fields as touched, so preserveFormState knows what to put back. One
// delegated listener per container, set up once.
export function trackUserEdits(container) {
  const mark = (ev) => {
    const el = ev.target;
    if (el && el.id && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT")) {
      el.dataset.userEdited = "1";
    }
  };
  container.addEventListener("input", mark);
  container.addEventListener("change", mark);
}

// Is the user typing into a field right now?
//
// The panels are rebuilt wholesale (innerHTML = "...") on every render, and
// renders arrive on their own schedule: the waiting room polls the roster
// every 3 seconds, the connection banner flips, an event lands. A rebuild
// under a focused field takes the caret with it and, worse, kills an IME
// composition mid-word -- the field the user was filling in simply fights
// back. Callers use this to hold the rebuild until the field is left.
//
// Radios, checkboxes and read-only fields are excluded: nothing is being
// typed into those, so a rebuild is harmless there and the roster should
// keep updating.
export function isTypingIn(container) {
  const el = document.activeElement;
  if (!el || !container || !container.contains(el)) return false;
  if (el.isContentEditable) return true;
  if (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") return false;
  if (el.readOnly || el.disabled) return false;
  return !["radio", "checkbox", "button", "submit", "reset", "file", "range", "color"].includes(el.type);
}

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

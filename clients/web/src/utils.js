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

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

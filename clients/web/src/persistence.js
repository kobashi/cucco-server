// localStorage persistence for reconnection (docs/human-client-guide.md §1:
// session_token must survive a closed tab, not just an in-memory variable).
const KEY = "cucco_session_v1";

export function saveSession({ name, playerId, sessionToken, roomId, playerType, wsHost }) {
  localStorage.setItem(KEY, JSON.stringify({ name, playerId, sessionToken, roomId, playerType, wsHost }));
}

export function loadSession() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearSession() {
  localStorage.removeItem(KEY);
}

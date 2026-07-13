// Session persistence for reconnection (docs/human-client-guide.md §1).
//
// Two layers, because several tabs of ONE browser are routinely used as
// several different players (demo/testing):
// - sessionStorage is per-tab and survives a reload of that tab -- it is the
//   authoritative copy, so reloading tab A can never restore tab B's player.
//   (A single shared localStorage key did exactly that: every tab overwrote
//   it on each snapshot, and a reload resurrected whichever player wrote
//   last, leaving the tab's real player disconnected until the game died.)
// - localStorage keeps a last-written copy only as a fallback for "closed
//   the tab entirely, come back later" -- best-effort by nature when
//   multiple tabs share the browser.
const KEY = "cucco_session_v1";

export function saveSession({ name, playerId, sessionToken, roomId, playerType, wsHost }) {
  const raw = JSON.stringify({ name, playerId, sessionToken, roomId, playerType, wsHost });
  sessionStorage.setItem(KEY, raw);
  localStorage.setItem(KEY, raw);
}

export function loadSession() {
  for (const store of [sessionStorage, localStorage]) {
    try {
      const raw = store.getItem(KEY);
      if (raw) return JSON.parse(raw);
    } catch {
      // fall through to the next store
    }
  }
  return null;
}

export function clearSession() {
  let own = null;
  try {
    own = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
  } catch {
    own = null;
  }
  sessionStorage.removeItem(KEY);
  // Only clear the shared fallback if it is OUR session -- it may belong to
  // another still-open tab's player.
  try {
    const shared = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (!shared || !own || shared.playerId === own.playerId) localStorage.removeItem(KEY);
  } catch {
    localStorage.removeItem(KEY);
  }
}

// Invite links.
//
// The clients are static pages (GitHub Pages) that talk to a game server on a
// separate, often temporary host (a cloudflared tunnel). Telling a guest "open
// this page, then paste this hostname into 接続先を変更, then type this table
// ID" is three chances to get it wrong, so both pieces travel in the URL
// instead -- `?ws=host[:port]` and `?room=ID`, consumed on load (see the
// clients' entry points).
//
// Two links, because the two situations differ:
//   - server link: introducing the system. Lands on the client chooser with
//     the server preconfigured; the visitor picks a client and makes or joins
//     a table whenever they like.
//   - table link: inviting players to a table you just created. Lands in the
//     same client you are using, on that table.

// ".../play/index.html" and ".../play/" both -> ".../play/"
function clientBaseUrl() {
  return location.origin + location.pathname.replace(/[^/]*$/, "");
}

// ".../play/" -> ".../" (the landing page that offers both clients)
function landingBaseUrl() {
  return clientBaseUrl().replace(/(?:play|web)\/$/, "");
}

export function serverInviteUrl(host) {
  return `${landingBaseUrl()}?ws=${encodeURIComponent(host)}`;
}

export function tableInviteUrl(host, roomId) {
  return `${clientBaseUrl()}?ws=${encodeURIComponent(host)}&room=${encodeURIComponent(roomId)}`;
}

// Copy, reporting whether it worked. navigator.clipboard needs a secure
// context (https / localhost), which both deployments have -- but a denied
// permission or an older browser still has to degrade gracefully, so callers
// pair this with the link shown in a selectable field.
//
// The timeout is not paranoia: writeText() also requires the document to be
// focused, and in a background/unfocused page the promise can simply never
// settle (observed in a headless preview browser). Without the race, the
// button would sit there having reported nothing at all -- worse than saying
// "copy it yourself".
export async function copyText(text, timeoutMs = 1500) {
  try {
    const timedOut = Symbol("timeout");
    const result = await Promise.race([
      navigator.clipboard.writeText(text).then(() => true),
      new Promise((resolve) => setTimeout(() => resolve(timedOut), timeoutMs)),
    ]);
    return result === true;
  } catch {
    return false;
  }
}

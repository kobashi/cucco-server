// WebSocket wrapper for the Cucco protocol (docs/protocol/design.md).
// Mirrors clients/common/ws_client.py: same envelope shape, same
// identify/create_table/join_table handshakes. Adds auto-reconnect, which
// the Python reference client doesn't need (it's not a long-lived human UI).

const PROTOCOL_VERSION = "1.0";
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 4000, 4000]; // fixed short backoff, max 5 tries

export class CuccoConnection extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    this.ws = null;
    this.playerId = null;
    this.sessionToken = null;
    this.roomId = null;
    this._reconnectAttempt = 0;
    this._manualClose = false;
  }

  connect() {
    this._manualClose = false;
    this.ws = new WebSocket(this.url);
    this.ws.addEventListener("open", () => {
      this._reconnectAttempt = 0;
      this.dispatchEvent(new CustomEvent("open"));
    });
    this.ws.addEventListener("message", (msg) => {
      let data;
      try {
        data = JSON.parse(msg.data);
      } catch {
        return;
      }
      const type = data.type;
      const payload = data.payload || {};
      this.dispatchEvent(new CustomEvent("event", { detail: { type, payload } }));
      this.dispatchEvent(new CustomEvent(`event:${type}`, { detail: payload }));
    });
    this.ws.addEventListener("close", () => {
      this.dispatchEvent(new CustomEvent("close"));
      if (!this._manualClose) this._scheduleReconnect();
    });
    this.ws.addEventListener("error", () => {
      // "close" always follows "error" for a WebSocket, so reconnection is
      // scheduled there -- nothing to do here beyond surfacing it for UI.
      this.dispatchEvent(new CustomEvent("transport_error"));
    });
  }

  close() {
    this._manualClose = true;
    this.ws?.close();
  }

  _scheduleReconnect() {
    if (this._reconnectAttempt >= RECONNECT_DELAYS_MS.length) {
      this.dispatchEvent(new CustomEvent("reconnect_failed"));
      return;
    }
    const delay = RECONNECT_DELAYS_MS[this._reconnectAttempt];
    this._reconnectAttempt += 1;
    this.dispatchEvent(new CustomEvent("reconnecting", { detail: { attempt: this._reconnectAttempt, delay } }));
    setTimeout(() => this.connect(), delay);
  }

  send(type, payload = {}) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type,
        table_id: this.roomId,
        protocol_version: PROTOCOL_VERSION,
        payload,
        ts: new Date().toISOString(),
      })
    );
  }

  // -- one-shot request/response helpers (used only during the handshake;
  // in-game actions are fire-and-forget via `send`, driven by prompts) ----

  // Resolves with the next `type` event, or rejects if `action_rejected`
  // arrives first (e.g. join_table for a room_id that doesn't exist), the
  // socket closes before a response, or nothing comes back within 10s (the
  // socket can be stuck CONNECTING, in which case `send` silently no-ops).
  _waitFor(type, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        this.removeEventListener(`event:${type}`, onOk);
        this.removeEventListener("event:action_rejected", onReject);
        this.removeEventListener("close", onClose);
        clearTimeout(timer);
      };
      const onOk = (ev) => {
        cleanup();
        resolve(ev.detail);
      };
      const onReject = (ev) => {
        cleanup();
        reject(new Error(ev.detail.reason || "action_rejected"));
      };
      const onClose = () => {
        cleanup();
        reject(new Error("接続が切断されました"));
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error("サーバーからの応答がタイムアウトしました"));
      }, timeoutMs);
      this.addEventListener(`event:${type}`, onOk);
      this.addEventListener("event:action_rejected", onReject);
      this.addEventListener("close", onClose);
    });
  }

  async identify(name, playerType) {
    const waiter = this._waitFor("identified");
    this.send("identify", { name, player_type: playerType });
    const payload = await waiter;
    this.playerId = payload.player_id;
    this.sessionToken = payload.session_token;
    return payload;
  }

  async createTable(config) {
    const waiter = this._waitFor("table_created");
    this.send("create_table", config);
    const payload = await waiter;
    this.roomId = payload.room_id;
    return payload;
  }

  async joinTable(roomId, sessionToken) {
    const waiter = this._waitFor("state_snapshot");
    const payload = { room_id: roomId };
    if (sessionToken) payload.session_token = sessionToken;
    this.send("join_table", payload);
    this.roomId = roomId;
    return waiter;
  }
}

export function wsUrlFor(host) {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${host}`;
}

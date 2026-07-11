import { esc } from "../utils.js";

export function render(el, state, actions) {
  const t = state.table;
  const isSpectator = state.playerType === "spectator";
  const readyIds = t?.ready_ids ?? [];
  const seats = t?.seats ?? [];
  const readyCount = seats.filter((s) => readyIds.includes(s.player_id)).length;
  const isCreator = !isSpectator && t?.dealer_seat == null && state.playerId === t?.creator_id;
  const startNeeded = Math.max(0, 2 - readyCount);
  el.innerHTML = `
    <div class="panel">
      <h1>待機中</h1>
      <p>プレイルームID: <strong class="room-id">${esc(state.roomId)}</strong>
        <button id="copy-btn" class="secondary">コピー</button></p>
      <h2>参加者</h2>
      <ul class="seat-list">
        ${seats
          .map(
            (s) =>
              `<li>${readyIds.includes(s.player_id) ? '<span class="ready-mark">✅</span>' : ""}${esc(s.name)} ${s.player_type === "ai" ? "(AI)" : ""} ${s.connected ? "" : "(切断中)"}</li>`
          )
          .join("")}
      </ul>
      ${t?.spectators?.length ? `<p class="muted">観戦者: ${t.spectators.length}人</p>` : ""}
      ${
        isSpectator
          ? `<p class="muted">観戦者として参加しています。ゲーム開始をお待ちください。</p>`
          : state.readySent
            ? `<button id="ready-btn" disabled>準備完了ずみ・開始をお待ちください</button><p class="muted">全員が準備完了するとポットが始まります。</p>`
            : `<button id="ready-btn">準備完了</button><p class="muted">全員が準備完了するとポットが始まります。</p>`
      }
      ${
        isCreator
          ? startNeeded > 0
            ? `<button id="start-pot-btn" class="secondary" disabled>卓を開始する</button><p class="muted">あと${startNeeded}人準備完了が必要です</p>`
            : `<button id="start-pot-btn" class="secondary">卓を開始する</button><p class="muted">準備完了した参加者だけで今すぐ開始できます。</p>`
          : ""
      }
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  el.querySelector("#copy-btn").addEventListener("click", () => navigator.clipboard?.writeText(state.roomId));
  // Ready-state lives in `state` (not just the DOM): the 3s waiting-room
  // resync poll re-renders this screen, which would otherwise silently
  // re-enable a button the player already pressed.
  el.querySelector("#ready-btn")?.addEventListener("click", () => actions.sendReady());
  el.querySelector("#start-pot-btn")?.addEventListener("click", () => actions.sendStartPot());
}

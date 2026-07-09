import { esc } from "../utils.js";

export function render(el, state, actions) {
  const t = state.table;
  const isSpectator = state.playerType === "spectator";
  el.innerHTML = `
    <div class="panel">
      <h1>待機中</h1>
      <p>プレイルームID: <strong class="room-id">${esc(state.roomId)}</strong>
        <button id="copy-btn" class="secondary">コピー</button></p>
      <h2>参加者</h2>
      <ul class="seat-list">
        ${(t?.seats ?? [])
          .map((s) => `<li>${esc(s.name)} ${s.player_type === "ai" ? "(AI)" : ""} ${s.connected ? "" : "(切断中)"}</li>`)
          .join("")}
      </ul>
      ${t?.spectators?.length ? `<p class="muted">観戦者: ${t.spectators.length}人</p>` : ""}
      ${
        isSpectator
          ? `<p class="muted">観戦者として参加しています。ゲーム開始をお待ちください。</p>`
          : `<button id="ready-btn">準備完了</button><p class="muted">全員が準備完了するとポットが始まります。</p>`
      }
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  el.querySelector("#copy-btn").addEventListener("click", () => navigator.clipboard?.writeText(state.roomId));
  el.querySelector("#ready-btn")?.addEventListener("click", (e) => {
    actions.sendReady();
    e.target.disabled = true;
    e.target.textContent = "準備完了ずみ・開始をお待ちください";
  });
}

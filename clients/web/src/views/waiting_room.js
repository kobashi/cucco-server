import { esc } from "../../../web-common/utils.js";

export function render(el, state, actions) {
  const t = state.table;
  const isSpectator = state.playerType === "spectator";
  const readyIds = t?.ready_ids ?? [];
  const seats = t?.seats ?? [];
  const creatorId = t?.creator_id;
  const isCreator = !isSpectator && state.playerId === creatorId;
  // The creator never presses 準備完了 -- sending start_pot IS their
  // participation declaration (the server auto-readies them then). So the
  // start gate counts the creator as ready-in-effect.
  const readyCount = seats.filter((s) => readyIds.includes(s.player_id)).length;
  const effectiveReady = readyCount + (isCreator && !readyIds.includes(state.playerId) ? 1 : 0);
  const startNeeded = Math.max(0, 2 - effectiveReady);

  el.innerHTML = `
    <div class="panel">
      <h1>待機中</h1>
      <p>プレイルームID: <strong class="room-id">${esc(state.roomId)}</strong>
        <button id="copy-btn" class="secondary">コピー</button></p>
      <h2>参加者</h2>
      <ul class="seat-list">
        ${seats
          .map((s) => {
            const marks = [];
            if (readyIds.includes(s.player_id)) marks.push('<span class="ready-mark">✅</span>');
            const tags = [];
            if (s.player_id === creatorId) tags.push("(主催)");
            if (s.player_type === "ai") tags.push("(AI)");
            if (!s.connected) tags.push("(切断中)");
            return `<li>${marks.join("")}${esc(s.name)} ${tags.join(" ")}</li>`;
          })
          .join("")}
      </ul>
      ${t?.spectators?.length ? `<p class="muted">観戦者: ${t.spectators.length}人</p>` : ""}
      ${
        isSpectator
          ? `<p class="muted">観戦者として参加しています。ゲーム開始をお待ちください。</p>`
          : isCreator
            ? startNeeded > 0
              ? `<p class="muted">参加者の準備完了を待っています(あと${startNeeded}人必要)。プレイルームIDを共有してください。</p>
                 <button id="start-pot-btn" disabled>ゲームを開始する</button>`
              : `<p class="muted">準備完了した参加者と一緒に開始できます(あなたも自動的に参加します)。</p>
                 <button id="start-pot-btn">ゲームを開始する</button>`
            : state.readySent
              ? `<button id="ready-btn" disabled>準備完了ずみ・開始をお待ちください</button><p class="muted">主催者が開始するとポットが始まります。</p>`
              : `<button id="ready-btn">準備完了</button><p class="muted">準備完了すると、主催者の開始操作でポットが始まります。</p>`
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

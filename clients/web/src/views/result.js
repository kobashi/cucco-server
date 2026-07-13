import { esc } from "../../../web-common/utils.js";
import { seatName } from "../state.js";

export function render(el, state, actions) {
  const g = state.gameEnded;
  el.innerHTML = `
    <div class="panel">
      <h1>ゲーム終了</h1>
      <ol class="ranking">
        ${(g?.ranking ?? [])
          .map(([pid, chips]) => `<li>${esc(seatName(state, pid))} — ${chips} チップ</li>`)
          .join("")}
      </ol>
      <p class="muted">この部屋はそのまま残っています。同じメンバー(途中参加も可)で
        もう一度遊ぶか、部屋を出るか選んでください。チップは新しいゲームでリセットされます。</p>
      <button id="stay-btn">この部屋で続けて遊ぶ</button>
      <button id="leave-btn" class="secondary">部屋を出る</button>
    </div>
  `;
  el.querySelector("#stay-btn").addEventListener("click", () => actions.stayInRoom());
  el.querySelector("#leave-btn").addEventListener("click", () => actions.leaveRoom());
}

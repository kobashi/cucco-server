import { esc } from "../utils.js";
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
      <button id="lobby-btn">ロビーに戻る</button>
    </div>
  `;
  el.querySelector("#lobby-btn").addEventListener("click", () => actions.backToLobby());
}

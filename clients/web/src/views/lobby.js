import { esc } from "../utils.js";

export function render(el, state, actions) {
  if (state.screen === "name") return renderName(el, state, actions);
  if (state.screen === "create") return renderCreate(el, state, actions);
  if (state.screen === "join") return renderJoin(el, state, actions);
  return renderChoice(el, state, actions);
}

function renderName(el, state, actions) {
  el.innerHTML = `
    <div class="panel">
      <h1>Cucco</h1>
      ${state.savedSession ? `
        <div class="callout">
          <p>前回の卓(${esc(state.savedSession.roomId)})の続きがあります。</p>
          <button id="resume-btn">再接続する</button>
          <button id="forget-btn" class="secondary">忘れて新しく始める</button>
        </div>
      ` : ""}
      <form id="name-form">
        <label>名前 <input id="name-input" required maxlength="24" autofocus></label>
        <fieldset>
          <label><input type="radio" name="ptype" value="human" checked> プレイヤーとして参加</label>
          <label><input type="radio" name="ptype" value="spectator"> 観戦者として参加</label>
        </fieldset>
        <button type="submit">つづける</button>
      </form>
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  el.querySelector("#resume-btn")?.addEventListener("click", () => actions.reconnect(state.savedSession));
  el.querySelector("#forget-btn")?.addEventListener("click", () => actions.forgetSession());
  el.querySelector("#name-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = el.querySelector("#name-input").value.trim();
    const ptype = el.querySelector('input[name="ptype"]:checked').value;
    if (name) actions.identify(name, ptype);
  });
}

function renderChoice(el, state, actions) {
  el.innerHTML = `
    <div class="panel">
      <h1>Cucco</h1>
      <p>ようこそ、${esc(state.name)}さん</p>
      <button id="create-btn">卓を作る</button>
      <button id="join-btn">プレイルームIDで参加する</button>
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  el.querySelector("#create-btn").addEventListener("click", () => {
    state.screen = "create";
    state.error = null;
    render(el, state, actions);
  });
  el.querySelector("#join-btn").addEventListener("click", () => {
    state.screen = "join";
    state.error = null;
    render(el, state, actions);
  });
}

function renderCreate(el, state, actions) {
  el.innerHTML = `
    <div class="panel">
      <h1>卓を作る</h1>
      <form id="create-form">
        <label>終了条件
          <select id="end-condition">
            <option value="chips_zero">誰かのチップが0枚で終了</option>
            <option value="round_limit">既定ディール数で終了</option>
          </select>
        </label>
        <label id="round-limit-row" style="display:none">既定ディール数
          <input id="round-limit" type="number" min="1" value="20">
        </label>
        <label>開始チップ枚数 <input id="starting-chips" type="number" min="1" step="1" value="25"></label>
        <label>失格カードの開示
          <select id="disclosure">
            <option value="deferred" selected>ディール終了時にまとめて公開</option>
            <option value="immediate">失格時に即座に公開</option>
          </select>
        </label>
        <label><input type="checkbox" id="horse-house-reveal"> 馬/家どちらの拒否か公開する</label>
        <button type="submit">作成する</button>
        <button type="button" id="back-btn" class="secondary">戻る</button>
      </form>
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  const endConditionEl = el.querySelector("#end-condition");
  const roundLimitRow = el.querySelector("#round-limit-row");
  endConditionEl.addEventListener("change", () => {
    roundLimitRow.style.display = endConditionEl.value === "round_limit" ? "" : "none";
  });
  el.querySelector("#back-btn").addEventListener("click", () => {
    state.screen = "lobby";
    render(el, state, actions);
  });
  el.querySelector("#create-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const endCondition = endConditionEl.value;
    actions.createTable({
      mode: "normal",
      end_condition: endCondition,
      round_limit: endCondition === "round_limit" ? Math.round(Number(el.querySelector("#round-limit").value)) : null,
      starting_chips: Math.round(Number(el.querySelector("#starting-chips").value)),
      disqualified_card_disclosure: el.querySelector("#disclosure").value,
      horse_house_reveal: el.querySelector("#horse-house-reveal").checked,
    });
  });
}

function renderJoin(el, state, actions) {
  el.innerHTML = `
    <div class="panel">
      <h1>卓に参加する</h1>
      <form id="join-form">
        <label>プレイルームID <input id="room-input" required maxlength="6" style="text-transform:uppercase" autofocus></label>
        <button type="submit">参加する</button>
        <button type="button" id="back-btn" class="secondary">戻る</button>
      </form>
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  el.querySelector("#back-btn").addEventListener("click", () => {
    state.screen = "lobby";
    render(el, state, actions);
  });
  el.querySelector("#join-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const roomId = el.querySelector("#room-input").value.trim().toUpperCase();
    if (roomId) actions.joinTable(roomId);
  });
}

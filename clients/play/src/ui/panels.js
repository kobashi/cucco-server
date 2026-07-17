// Lobby / waiting screens, adapted from the reference client's views but
// simplified: same actions contract (identify/createTable/joinTable/
// sendReady/sendStartPot/reconnect/forgetSession/setWsHost).

import { esc } from "../../../web-common/utils.js";

// The name screen's whole panel is torn down and rebuilt (innerHTML = "...")
// on every render() -- including connectionStatus flipping to "open" moments
// after page load, while the user is mid-click/mid-typing in the "接続先を
// 変更" <details>. A plain <details> with no `open` attribute would snap
// back shut right under them, and the host input would lose whatever they'd
// typed. Both survive re-renders here because they live outside the DOM
// this function keeps destroying.
let wsHostOpen = false;
let wsHostDraft = "";

export function renderLobby(el, state, actions, phase) {
  if (phase === "name") return renderName(el, state, actions);
  if (phase === "create") return renderCreate(el, state, actions);
  if (phase === "join") return renderJoin(el, state, actions);
  return renderChoice(el, state, actions);
}

function renderName(el, state, actions) {
  el.innerHTML = `
    <div class="panel">
      <h1>Cucco <span class="sub">プレイ用クライアント</span></h1>
      ${
        state.savedSession
          ? `<div class="callout">
              <p>前回の卓(${esc(state.savedSession.roomId)})の続きがあります。</p>
              <button id="resume-btn">再接続する</button>
              <button id="forget-btn" class="secondary">忘れて新しく始める</button>
            </div>`
          : ""
      }
      <form id="name-form">
        <label>名前 <input id="name-input" required maxlength="24" autofocus></label>
        <fieldset>
          <label><input type="radio" name="ptype" value="human" checked> プレイヤーとして参加</label>
          <label><input type="radio" name="ptype" value="spectator"> 観戦者として参加</label>
        </fieldset>
        <button type="submit">つづける</button>
      </form>
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
      <details class="ws-host-details" ${wsHostOpen ? "open" : ""}>
        <summary>接続先を変更(通常は不要)</summary>
        <p class="muted">現在の接続先: ${esc(localStorage.getItem("cucco_ws_host") || `${location.hostname}:8765`)}</p>
        <form id="ws-host-form">
          <label>ホスト名のみ(URL全体は不可) <input id="ws-host-input" placeholder="ws.example.trycloudflare.com" value="${esc(wsHostDraft)}"></label>
          <button type="submit" class="secondary">接続先を保存</button>
        </form>
      </details>
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
  const wsHostDetails = el.querySelector(".ws-host-details");
  wsHostDetails.addEventListener("toggle", () => (wsHostOpen = wsHostDetails.open));
  const wsHostInput = el.querySelector("#ws-host-input");
  wsHostInput.addEventListener("input", () => (wsHostDraft = wsHostInput.value));
  el.querySelector("#ws-host-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const host = wsHostInput.value.trim();
    if (host) {
      wsHostDraft = "";
      actions.setWsHost(host);
    }
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
  el.querySelector("#create-btn").addEventListener("click", () => actions.setPhase("create"));
  el.querySelector("#join-btn").addEventListener("click", () => actions.setPhase("join"));
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
        <label>特殊札の効果(道化を除く)
          <select id="effect-declaration">
            <option value="auto" selected>自動で発動(標準ルール)</option>
            <option value="declared">宣言式 — 宣言しないと発動せず交換成立</option>
          </select>
        </label>
        <label>結果確認の待機時間(秒。全員が確認すれば短縮)
          <input id="result-pause" type="number" min="0" max="60" step="1" value="15">
        </label>
        <button type="submit">作成する</button>
        <button type="button" id="back-btn" class="secondary">戻る</button>
      </form>
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  const endCondition = el.querySelector("#end-condition");
  endCondition.addEventListener("change", () => {
    el.querySelector("#round-limit-row").style.display = endCondition.value === "round_limit" ? "" : "none";
  });
  el.querySelector("#back-btn").addEventListener("click", () => actions.setPhase("lobby"));
  el.querySelector("#create-form").addEventListener("submit", (e) => {
    e.preventDefault();
    actions.createTable({
      mode: "normal",
      end_condition: endCondition.value,
      round_limit: endCondition.value === "round_limit" ? Math.round(Number(el.querySelector("#round-limit").value)) : null,
      starting_chips: Math.round(Number(el.querySelector("#starting-chips").value)),
      disqualified_card_disclosure: el.querySelector("#disclosure").value,
      horse_house_reveal: el.querySelector("#horse-house-reveal").checked,
      effect_declaration: el.querySelector("#effect-declaration").value,
      result_pause_sec: Math.max(0, Math.min(60, Number(el.querySelector("#result-pause").value) || 0)),
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
  el.querySelector("#back-btn").addEventListener("click", () => actions.setPhase("lobby"));
  el.querySelector("#join-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const roomId = el.querySelector("#room-input").value.trim().toUpperCase();
    if (roomId) actions.joinTable(roomId);
  });
}

export function renderWaiting(el, state, actions) {
  const t = state.table;
  const isSpectator = state.playerType === "spectator";
  const readyIds = t?.ready_ids ?? [];
  const seats = t?.seats ?? [];
  const creatorId = t?.creator_id;
  const isCreator = !isSpectator && state.playerId === creatorId;
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
            const tags = [];
            if (s.player_id === creatorId) tags.push("(主催)");
            if (s.player_type === "ai") tags.push("(AI)");
            if (!s.connected) tags.push("(切断中)");
            return `<li>${readyIds.includes(s.player_id) ? "✅" : ""}${esc(s.name)} ${tags.join(" ")}</li>`;
          })
          .join("")}
      </ul>
      ${t?.spectators?.length ? `<p class="muted">観戦者: ${t.spectators.length}人</p>` : ""}
      ${
        isSpectator
          ? `<p class="muted">観戦者として参加しています。ゲーム開始をお待ちください。</p>`
          : isCreator
            ? startNeeded > 0
              ? `<p class="muted">参加者の準備完了を待っています(あと${startNeeded}人必要)。IDを共有してください。</p>
                 <button id="start-pot-btn" disabled>ゲームを開始する</button>`
              : `<p class="muted">準備完了した参加者と一緒に開始できます(あなたも自動的に参加します)。</p>
                 <button id="start-pot-btn">ゲームを開始する</button>`
            : state.readySent
              ? `<button id="ready-btn" disabled>準備完了ずみ・開始をお待ちください</button>`
              : `<button id="ready-btn">準備完了</button><p class="muted">準備完了すると、主催者の開始操作でポットが始まります。</p>`
      }
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  el.querySelector("#copy-btn").addEventListener("click", () => navigator.clipboard?.writeText(state.roomId));
  el.querySelector("#ready-btn")?.addEventListener("click", () => actions.sendReady());
  el.querySelector("#start-pot-btn")?.addEventListener("click", () => actions.sendStartPot());
}

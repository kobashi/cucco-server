// Lobby / waiting screens, adapted from the reference client's views but
// simplified: same actions contract (identify/createTable/joinTable/
// sendReady/sendStartPot/reconnect/forgetSession/setWsHost).

import { esc, sanitizeWsHost, MACHINE_INPUT_ATTRS } from "../../../web-common/utils.js";
import { serverInviteUrl, tableInviteUrl, copyText } from "../../../web-common/invite.js";

const currentWsHost = () => localStorage.getItem("cucco_ws_host") || `${location.hostname}:8765`;

// The host that goes INTO invite links, kept separate from the host this
// browser connects to (`cucco_ws_host`). The organiser usually runs the server
// on the same machine and connects to localhost, while guests need the public
// tunnel domain -- so the announcement URL cannot just reuse the local
// connection target. Remembered so a new tunnel domain is typed once, not
// once per link.
const INVITE_HOST_KEY = "cucco_invite_host";
const invitePublishHost = () => localStorage.getItem(INVITE_HOST_KEY) || currentWsHost();

// Copy feedback and the invite <details> state live outside the DOM these
// renders keep destroying, for the same reason wsHostOpen does above: the
// waiting screen re-renders on every roster/presence update, which would wipe
// the confirmation a fraction of a second after the organizer clicked コピー.
let inviteStatusFor = null; // which invite block the message belongs to
let inviteStatusText = "";
let inviteDetailsOpen = false;
let inviteStatusTimer = null;

function setInviteStatus(el, id, text) {
  inviteStatusFor = id;
  inviteStatusText = text;
  const node = el.querySelector(`#${id}-status`);
  if (node) node.textContent = text;
  clearTimeout(inviteStatusTimer);
  inviteStatusTimer = setTimeout(() => {
    inviteStatusFor = null;
    inviteStatusText = "";
    const live = document.querySelector(`#${id}-status`);
    if (live) live.textContent = "";
  }, 6000);
}

// An invite link: the server domain to publish (editable, remembered), the
// resulting link in a read-only field -- visible and selectable even where the
// clipboard API is unavailable -- and a copy button that reports what happened.
// `buildUrl(host)` is what makes the block server-invite or table-invite.
export function inviteBlockHTML(id, label, hint, buildUrl) {
  const host = invitePublishHost();
  return `
    <div class="invite-block">
      <label for="${id}-host">ゲームサーバーのドメイン名</label>
      <input id="${id}-host" class="invite-host" value="${esc(host)}" placeholder="xxxx.trycloudflare.com"
             ${MACHINE_INPUT_ATTRS}>
      <p class="muted invite-hint">
        招待リンクに載せる公開ドメイン。外から繋いでもらうトンネルのドメインを入れる
        (この欄はリンク用で、この画面自身の接続先は変わりません)。
      </p>
      <label for="${id}-url">${esc(label)}</label>
      <div class="invite-row">
        <input id="${id}-url" class="invite-url" readonly value="${esc(buildUrl(host))}">
        <button type="button" id="${id}-copy" class="secondary">コピー</button>
      </div>
      <p class="muted invite-hint">${hint}</p>
      <p class="invite-status" id="${id}-status" role="status">${
        inviteStatusFor === id ? esc(inviteStatusText) : ""
      }</p>
    </div>`;
}

export function wireInviteBlock(el, id, buildUrl) {
  const hostField = el.querySelector(`#${id}-host`);
  const urlField = el.querySelector(`#${id}-url`);

  // Rebuild the link as the domain is typed. The raw text stays in the field
  // (sanitising mid-typing would fight the user, e.g. eating a "/" they are
  // still typing past); only the stored and published values are normalised.
  hostField?.addEventListener("input", () => {
    const host = sanitizeWsHost(hostField.value);
    if (host) localStorage.setItem(INVITE_HOST_KEY, host);
    else localStorage.removeItem(INVITE_HOST_KEY); // empty = fall back to the connection host
    urlField.value = buildUrl(host || currentWsHost());
  });

  el.querySelector(`#${id}-copy`)?.addEventListener("click", async () => {
    const ok = await copyText(urlField.value);
    if (!ok) urlField.select(); // fall back to "selected, press ⌘C yourself"
    setInviteStatus(
      el,
      id,
      ok ? "コピーしました" : "コピーできませんでした — 上のリンクを選択してコピーしてください"
    );
  });
}

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
        state.invitedRoomId
          ? `<div class="callout">
              <p>卓 <strong class="room-id">${esc(state.invitedRoomId)}</strong> に招待されています。</p>
              <p class="muted">名前を入れて「つづける」を押すと、そのまま参加します。</p>
            </div>`
          : ""
      }
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
        <form id="ws-host-form" novalidate>
          <label>ホスト名のみ(URL全体は不可) <input id="ws-host-input" placeholder="ws.example.trycloudflare.com" value="${esc(wsHostDraft)}" ${MACHINE_INPUT_ATTRS}></label>
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
      <details class="invite-details" ${inviteDetailsOpen ? "open" : ""}>
        <summary>Cuccoに招待するリンク(システムの紹介用)</summary>
        ${inviteBlockHTML(
          "server-invite",
          "ゲームサーバー付きの招待リンク",
          "このリンクを開いた人は、接続先の設定なしでクライアントを選んで遊べます。" +
            "卓に誘うときは、卓を立てたあとの待機画面に出る<strong>卓の招待リンク</strong>を使ってください。",
          serverInviteUrl
        )}
      </details>
    </div>
  `;
  el.querySelector("#create-btn").addEventListener("click", () => actions.setPhase("create"));
  el.querySelector("#join-btn").addEventListener("click", () => actions.setPhase("join"));
  const details = el.querySelector(".invite-details");
  details.addEventListener("toggle", () => (inviteDetailsOpen = details.open));
  wireInviteBlock(el, "server-invite", serverInviteUrl);
}

// AI seats are set with −/+ steppers rather than number fields: on a phone a
// number spinner means summoning the numeric keyboard and hitting native
// arrows a few pixels tall, and the value is a single digit almost every
// time. The authoritative count still lives in a hidden `.ai-count` input per
// policy, so the create payload below is built exactly as before.
const MAX_AI_TOTAL = 14;
const AI_POLICIES = [
  ["matrix", "matrix(人数×手札で判断)"],
  ["always_change", "always_change(常にチェンジ)"],
  ["always_no_change", "always_no_change(常にノーチェンジ)"],
  ["counting_aggressive", "counting_aggressive(カウンティング・積極型)"],
  ["counting_conservative", "counting_conservative(カウンティング・堅実型)"],
];

function aiStepperHTML([policy, label]) {
  return `
    <div class="ai-row">
      <span class="ai-label">${esc(label)}</span>
      <span class="stepper">
        <button type="button" class="step-btn" data-policy="${policy}" data-step="-1" aria-label="${esc(label)} を1人減らす">−</button>
        <output class="ai-readout" data-policy="${policy}">0</output>
        <button type="button" class="step-btn" data-policy="${policy}" data-step="1" aria-label="${esc(label)} を1人増やす">+</button>
      </span>
      <input class="ai-count" data-policy="${policy}" type="hidden" value="0">
    </div>`;
}

// Wire the steppers: clamp each policy at 0 and the table at MAX_AI_TOTAL,
// and keep the buttons' disabled state honest so the limits are visible
// rather than silently enforced on click.
function wireAiSteppers(el) {
  const fieldset = el.querySelector(".ai-players");
  const countFor = (policy) => fieldset.querySelector(`.ai-count[data-policy="${policy}"]`);
  const total = () => [...fieldset.querySelectorAll(".ai-count")].reduce((n, i) => n + Number(i.value), 0);
  const refresh = () => {
    const sum = total();
    el.querySelector("#ai-total").textContent = `${sum}`;
    for (const btn of fieldset.querySelectorAll(".step-btn")) {
      btn.disabled =
        btn.dataset.step === "-1" ? Number(countFor(btn.dataset.policy).value) <= 0 : sum >= MAX_AI_TOTAL;
    }
  };
  fieldset.addEventListener("click", (e) => {
    const btn = e.target.closest(".step-btn");
    if (!btn || btn.disabled) return;
    const input = countFor(btn.dataset.policy);
    const next = Number(input.value) + Number(btn.dataset.step);
    if (next < 0 || next > MAX_AI_TOTAL) return;
    input.value = `${next}`;
    fieldset.querySelector(`.ai-readout[data-policy="${btn.dataset.policy}"]`).textContent = `${next}`;
    refresh();
  });
  refresh();
}

function renderCreate(el, state, actions) {
  // Kept in `state`, not just the DOM: the AI steppers and the too-few-AI guard
  // both re-render this form, which would otherwise silently drop a 観戦のみ
  // choice back to プレイヤー and create the table with the creator seated.
  const creatorRole = state.creatorRole ?? (state.playerType === "spectator" ? "spectator" : "human");
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
        <label>馬/家による拒否時の札
          <select id="horse-house-reveal">
            <option value="false" selected>公開しない(標準ルール)</option>
            <option value="true">馬か家かを公開する</option>
          </select>
        </label>
        <label>山札の再構成
          <select id="reshuffle-includes-revealed">
            <option value="false" selected>捨て札のみで再構成する(標準ルール)</option>
            <option value="true">場に出ている失格札も混ぜてから再構成する</option>
          </select>
        </label>
        <label>特殊札の効果(道化を除く)
          <select id="effect-declaration">
            <option value="auto" selected>自動で発動(標準ルール)</option>
            <option value="declared">宣言式 — 宣言しないと発動せず交換成立</option>
          </select>
        </label>
        <label>結果確認の待機時間(秒。全員が確認すれば短縮)
          <input id="result-pause" type="number" min="0" max="60" step="1" value="15">
        </label>
        <label>捨て札の表示
          <select id="discard-display">
            <option value="grouped" selected>全て一覧表示(種類ごとにまとめる)</option>
            <option value="pile">捨て山 — 最後の1枚だけ見える</option>
          </select>
        </label>
        <label>この卓での自分
          <select id="creator-role">
            <option value="human" ${creatorRole === "spectator" ? "" : "selected"}>プレイヤーとして参加する</option>
            <option value="spectator" ${creatorRole === "spectator" ? "selected" : ""}>観戦のみ(自分は参加しない)</option>
          </select>
        </label>
        <p id="spectator-note" class="ai-total muted" hidden>
          観戦のみの場合、卓に必要な人数はAIだけで揃える必要があります(2人以上)。
        </p>
        <fieldset class="ai-players">
          <legend>AIプレイヤーを追加(サーバー内蔵、合計${MAX_AI_TOTAL}人まで)</legend>
          ${AI_POLICIES.map(aiStepperHTML).join("")}
          <p class="ai-total muted">追加するAI: <span id="ai-total">0</span> 人</p>
        </fieldset>
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
  const roleSel = el.querySelector("#creator-role");
  const note = el.querySelector("#spectator-note");
  const syncRoleNote = () => {
    state.creatorRole = roleSel.value;
    note.hidden = roleSel.value !== "spectator";
  };
  roleSel.addEventListener("change", syncRoleNote);
  syncRoleNote();
  wireAiSteppers(el);
  el.querySelector("#create-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const aiTotal = [...el.querySelectorAll(".ai-count")].reduce((n, i) => n + Number(i.value), 0);
    if (roleSel.value === "spectator" && aiTotal < 2) {
      // Caught here rather than at the server: a spectator-created table with
      // fewer than 2 AI seats has nobody who can ever play, so it would sit in
      // the waiting room forever with no error to explain why.
      state.error = "観戦のみで卓を作るには、AIプレイヤーを2人以上追加してください。";
      renderLobby(el, state, actions, "create");
      return;
    }
    actions.createTable({
      // 卓での自分の立場。観戦のみなら main.js が identify をやり直してから
      // 卓を作る。初回の開始は作成者の操作を待つ(サーバー側のゲート)ので、
      // 観戦者が作った卓でも人を待つ間がある。
      _creatorRole: el.querySelector("#creator-role").value,
      mode: "normal",
      end_condition: endCondition.value,
      round_limit: endCondition.value === "round_limit" ? Math.round(Number(el.querySelector("#round-limit").value)) : null,
      starting_chips: Math.round(Number(el.querySelector("#starting-chips").value)),
      disqualified_card_disclosure: el.querySelector("#disclosure").value,
      horse_house_reveal: el.querySelector("#horse-house-reveal").value === "true",
      reshuffle_includes_revealed: el.querySelector("#reshuffle-includes-revealed").value === "true",
      effect_declaration: el.querySelector("#effect-declaration").value,
      discard_display: el.querySelector("#discard-display").value,
      result_pause_sec: Math.max(0, Math.min(60, Number(el.querySelector("#result-pause").value) || 0)),
      ai_players: [...el.querySelectorAll(".ai-count")]
        .map((input) => ({ policy: input.dataset.policy, count: Math.round(Number(input.value)) || 0 }))
        .filter((spec) => spec.count > 0),
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
  // 観戦者でも作成者なら開始を握る。観戦作成の卓は参加資格者がAIだけなので、
  // ここでボタンを出さないと「参加者を確認してから始める」ができない。
  const isCreator = state.playerId === creatorId;
  const readyCount = seats.filter((s) => readyIds.includes(s.player_id)).length;
  // 観戦の作成者は自分が卓に着かないので、頭数には数えない。
  const effectiveReady = readyCount + (isCreator && !isSpectator && !readyIds.includes(state.playerId) ? 1 : 0);
  const startNeeded = Math.max(0, 2 - effectiveReady);

  el.innerHTML = `
    <div class="panel">
      <h1>待機中</h1>
      <p>プレイルームID: <strong class="room-id">${esc(state.roomId)}</strong>
        <button id="copy-btn" class="secondary">コピー</button></p>
      ${inviteBlockHTML(
        "table-invite",
        "この卓の招待リンク",
        "同じサーバーの同じ卓に直接入れます。受け取った人は名前を入れるだけで参加できます。",
        (host) => tableInviteUrl(host, state.roomId ?? "")
      )}
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
        isSpectator && !isCreator
          ? `<p class="muted">観戦者として参加しています。ゲーム開始をお待ちください。</p>`
          : isCreator
            ? startNeeded > 0
              ? `<p class="muted">参加者の準備完了を待っています(あと${startNeeded}人必要)。IDを共有してください。</p>
                 <button id="start-pot-btn" disabled>ゲームを開始する</button>`
              : `<p class="muted">${
                   isSpectator
                     ? "準備完了した参加者で開始できます(あなたは観戦のみで、席には着きません)。"
                     : "準備完了した参加者と一緒に開始できます(あなたも自動的に参加します)。"
                 }</p>
                 <button id="start-pot-btn">ゲームを開始する</button>`
            : state.readySent
              ? `<button id="ready-btn" disabled>準備完了ずみ・開始をお待ちください</button>`
              : `<button id="ready-btn">準備完了</button><p class="muted">準備完了すると、主催者の開始操作でポットが始まります。</p>`
      }
      ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    </div>
  `;
  el.querySelector("#copy-btn").addEventListener("click", () => navigator.clipboard?.writeText(state.roomId));
  wireInviteBlock(el, "table-invite", (host) => tableInviteUrl(host, state.roomId ?? ""));
  el.querySelector("#ready-btn")?.addEventListener("click", () => actions.sendReady());
  el.querySelector("#start-pot-btn")?.addEventListener("click", () => actions.sendStartPot());
}

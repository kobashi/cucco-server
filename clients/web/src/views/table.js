import { esc, secondsLeft } from "../utils.js";
import { seatName } from "../state.js";
import { RANK_ORDER } from "../cards.js";

// Countdown digits live in [data-deadline] spans so the ticker in main.js can
// update them in place -- re-rendering the whole screen 4x/second to move a
// number is what made the buttons feel unresponsive.
function countdown(deadline) {
  return `<span data-deadline="${deadline}">${secondsLeft(deadline)}</span>`;
}

export function render(el, state, actions) {
  const t = state.table;
  if (!t) return;
  const isSpectator = state.playerType === "spectator";

  el.innerHTML = `
    <div class="table-screen">
      <header class="table-header">
        <span>卓 ${esc(state.roomId)}</span>
        <span>ポット ${t.pot_number}・ディール ${t.deal_number}</span>
        <span class="pot-chips">💰 ポット ${state.potChips}枚</span>
        <span>残り山札: ${t.deck_remaining_count}枚</span>
      </header>

      ${renderStatusBar(state, isSpectator)}
      ${renderSeats(t, state)}
      ${isSpectator ? "" : renderHand(state)}
      ${renderDiscardPile(t)}
      ${renderDeclarations(t, state)}
      ${renderDealSummary(state)}
      ${renderPotResult(state, isSpectator)}
      ${isSpectator ? "" : renderActionArea(state, actions)}
      ${renderPrevDealSummary(state)}
      ${renderLog(state)}
    </div>
    ${!isSpectator && state.cuccoWindow ? renderCuccoModal(state) : ""}
    ${!isSpectator && state.continuePrompt ? renderContinueModal(state) : ""}
  `;

  el.querySelector("#dealer-ready-btn")?.addEventListener("click", actions.sendDealerReady);
  el.querySelector("#cambio-btn")?.addEventListener("click", actions.sendCambio);
  el.querySelector("#no-change-btn")?.addEventListener("click", actions.sendNoChange);
  el.querySelector("#cucco-declare-btn")?.addEventListener("click", actions.sendCuccoDeclare);
  el.querySelector("#cucco-pass-btn")?.addEventListener("click", actions.sendCuccoPass);
  el.querySelector("#continue-yes-btn")?.addEventListener("click", () => actions.sendContinue(true));
  el.querySelector("#continue-no-btn")?.addEventListener("click", () => actions.sendContinue(false));
}

// One always-visible line answering "who/what are we waiting on right now".
// For bystanders this is best-effort: the dealer's どうぞ, turn prompts and
// cucco windows are unicast (runner.py sends them only to the addressee), so
// the deal phase is reconstructed from the broadcast resolution events.
function statusFor(state, isSpectator) {
  if (!isSpectator) {
    if (state.dealerReadyPrompt) return { text: "あなたが親です — 手札を確認して「どうぞ」を宣言してください", mine: true };
    if (state.turnPrompt) return { text: "あなたの手番です — カンビオ / ノンカンビオを選んでください", mine: true };
    if (state.cuccoWindow) return { text: "クク宣言のチャンス!", mine: true };
    if (state.continuePrompt) return { text: "続行するかどうか選んでください", mine: true };
  }
  const waitingContinue = [...(state.pendingContinueIds ?? [])].filter((id) => id !== state.playerId);
  if (waitingContinue.length) {
    return { text: `${waitingContinue.map((id) => seatName(state, id)).join("、")} さんの続行確認を待っています…`, mine: false };
  }
  if (state.lastPotResult) return { text: "まもなく次のポットが始まります…", mine: false };
  if (state.lastDealResult || state.lastDealOpened) return { text: "まもなく次のディールが始まります…", mine: false };
  const dealer = state.table?.dealer_seat;
  const iAmDealer = dealer === state.playerId;
  if (!state.firstActionSeen && !(iAmDealer && state.dozoSent) && dealer) {
    return { text: `親(${seatName(state, dealer)})の「どうぞ」を待っています…`, mine: false };
  }
  if (state.currentTurnSeat) {
    return { text: `${seatName(state, state.currentTurnSeat)} さんの手番です…`, mine: false };
  }
  return { text: "進行中…", mine: false };
}

function renderStatusBar(state, isSpectator) {
  const { text, mine } = statusFor(state, isSpectator);
  return `<div class="status-bar ${mine ? "status-mine" : ""}">${esc(text)}</div>`;
}

function renderSeats(t, state) {
  return `
    <div class="seats">
      ${t.seats
        .map((s) => {
          const isDealer = s.player_id === t.dealer_seat;
          const isTurn = s.player_id === state.currentTurnSeat;
          const classes = ["seat"];
          if (isDealer) classes.push("dealer");
          if (isTurn) classes.push("current-turn");
          if (!s.in_current_pot) classes.push("out");
          if (!s.connected) classes.push("disconnected");
          return `
            <div class="${classes.join(" ")}">
              <div class="seat-name">${esc(s.name)}${isDealer ? " 👑" : ""}</div>
              <div class="seat-chips">${s.chips} チップ</div>
              ${isTurn ? '<div class="seat-turn-flag">⏳ 手番</div>' : ""}
              ${!s.in_current_pot ? '<div class="seat-flag">脱落中</div>' : ""}
              ${!s.connected ? '<div class="seat-flag">切断中</div>' : ""}
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderHand(state) {
  let content;
  if (state.yourHand) {
    content = `<div class="hand-card">${esc(state.yourHand)}</div>`;
  } else if (state.disqualifiedThisDeal) {
    content = `<p class="muted">このディールは失格しました。観戦のみです。</p>`;
  } else {
    content = `<p class="muted">次のディールを待っています。</p>`;
  }
  return `<section class="your-hand"><h2>あなたの手札</h2>${content}</section>`;
}

function renderDiscardPile(t) {
  if (!t.discard_pile?.length) return `<section><h2>捨て札</h2><p class="muted">まだありません</p></section>`;
  // Original holder is deliberately omitted here (per-card provenance stays
  // in provenance_map for the 猫 effect, not this list) -- grouped counts
  // sorted by rank are what's actually useful for counting cards.
  const counts = new Map();
  for (const d of t.discard_pile) counts.set(d.card, (counts.get(d.card) ?? 0) + 1);
  const sorted = [...counts.entries()].sort(([a], [b]) => RANK_ORDER.indexOf(a) - RANK_ORDER.indexOf(b));
  return `
    <section>
      <h2>捨て札(${t.discard_pile.length}枚)</h2>
      <ul class="discard-list">
        ${sorted.map(([card, count]) => `<li>${esc(card)}${count > 1 ? ` <span class="muted">× ${count}</span>` : ""}</li>`).join("")}
      </ul>
    </section>
  `;
}

function renderDeclarations(t, state) {
  if (!t.declarations_this_deal?.length) return "";
  return `
    <section>
      <h2>宣言履歴</h2>
      <ul class="declaration-list">
        ${t.declarations_this_deal
          .map(
            (d) =>
              `<li>${esc(seatName(state, d.player_id))}: ${
                d.action === "cambio" ? "カンビオ" : d.action === "cucco_declare" ? "クク宣言" : "ノンカンビオ"
              }${d.via_timeout ? "(時間切れ)" : ""}</li>`
          )
          .join("")}
      </ul>
    </section>
  `;
}

const CAUSE_LABELS = {
  received_joker: "道化を受け取った",
  human_refusal: "人間に拒否された",
  human_deck_draw: "山札から人間",
  cat_refusal: "猫の効果",
  cat_deck_draw: "山札から猫の効果",
};

// Per-player summary table shown at deal open/result: card, outcome,
// payment, chips, and whether they stay for the next deal. Replaces the old
// two prose callouts, which made players reconstruct the situation from the
// log.
function renderDealSummary(state) {
  if (state.lastDealOpened || state.lastDealResult) {
    return summaryTable(state, state.lastDealOpened, state.lastDealResult, state.disqualifiedInfo, null);
  }
  return "";
}

// The previous deal's table, shown lower on the screen while the next deal
// is already underway (the server deals again immediately, so this is the
// only chance to actually read the result).
function renderPrevDealSummary(state) {
  if (state.lastDealOpened || state.lastDealResult) return ""; // live one is showing
  const prev = state.prevDealSummary;
  if (!prev) return "";
  return summaryTable(state, prev.opened, prev.result, prev.disqualifiedInfo, prev.dealNumber);
}

function summaryTable(state, opened, result, disqualifiedInfo, prevDealNumber) {
  const t = state.table;
  const losers = new Set([...(opened?.losers ?? []), ...(result?.losers ?? [])]);
  const leftPot = new Set(result?.left_pot ?? []);
  const paid = result?.chips_paid ?? {};

  const rows = t.seats.map((s) => {
    const pid = s.player_id;
    const dq = disqualifiedInfo[pid];
    const openedCard = opened?.hands?.[pid];
    const card = openedCard ?? dq?.card ?? null;
    const elevated = opened?.elevated_joker_holders?.includes(pid);

    let outcome;
    if (dq) outcome = `途中失格(${CAUSE_LABELS[dq.cause] ?? dq.cause})`;
    else if (losers.has(pid)) outcome = "敗者";
    else if (openedCard !== undefined) outcome = "生存";
    else if (!s.in_current_pot) outcome = "ポット外";
    else outcome = "—";

    let next;
    if (!result) next = "";
    else if (leftPot.has(pid)) next = "脱落";
    else if (paid[pid] !== undefined) next = "復帰(継続)";
    else if (!s.in_current_pot) next = "—";
    else next = "続行";

    const cls = [];
    if (dq || losers.has(pid)) cls.push("row-loser");
    if (!s.in_current_pot) cls.push("row-out");
    return `
      <tr class="${cls.join(" ")}">
        <td>${esc(s.name)}${pid === state.playerId ? " (あなた)" : ""}</td>
        <td class="cell-card">${card ? esc(card) + (elevated ? " ↑最強扱い" : "") : '<span class="muted">非公開</span>'}</td>
        <td>${esc(outcome)}</td>
        <td>${paid[pid] !== undefined ? `${paid[pid]}枚` : ""}</td>
        <td>${s.chips}枚</td>
        <td>${esc(next)}</td>
      </tr>`;
  });

  const isPrev = prevDealNumber != null;
  const title = isPrev
    ? `前のディール(ディール${prevDealNumber})の結果`
    : result
      ? "ディール結果"
      : "オープン";
  return `
    <section class="callout ${isPrev ? "prev-summary" : ""}">
      <h2>${esc(title)}</h2>
      <div class="summary-scroll">
        <table class="deal-summary">
          <thead><tr><th>プレイヤー</th><th>カード</th><th>結果</th><th>支払い</th><th>所持チップ</th><th>次ディール</th></tr></thead>
          <tbody>${rows.join("")}</tbody>
        </table>
      </div>
      ${result && !isPrev ? `<p class="muted">ポット: ${result.pot_chips ?? state.potChips}枚</p>` : ""}
    </section>
  `;
}

function renderPotResult(state, isSpectator) {
  const r = state.lastPotResult;
  if (!r) return "";
  // No button here on purpose: after the first pot the server auto-enrolls
  // everyone in the next one (dispatch.py -- `ready` only gates the FIRST
  // pot), so a "next pot" button would be a no-op that looks broken.
  return `
    <section class="callout highlight">
      <h2>ポット結果</h2>
      ${r.result === "won" ? `<p>${esc(seatName(state, r.winner))} が ${r.amount} チップを獲得!</p>` : `<p>このポット(${r.amount}枚)は次のポットへ持ち越しになりました。</p>`}
      <p class="muted">まもなく次のポットが自動的に始まります。</p>
    </section>
  `;
}

function renderActionArea(state, actions) {
  if (state.dealerReadyPrompt) {
    return `
      <section class="action-area urgent">
        <p>あなたが親です。手札を確認してから「どうぞ」を宣言してください。(残り${countdown(state.dealerReadyPrompt.deadline)}秒)</p>
        <button id="dealer-ready-btn">どうぞ</button>
      </section>
    `;
  }
  if (state.turnPrompt) {
    return `
      <section class="action-area urgent">
        <p>あなたの手番です。(残り${countdown(state.turnPrompt.deadline)}秒)</p>
        <button id="cambio-btn">カンビオ(交換する)</button>
        <button id="no-change-btn" class="secondary">ノンカンビオ(交換しない)</button>
      </section>
    `;
  }
  const waitingOn = state.currentTurnSeat ? seatName(state, state.currentTurnSeat) : null;
  return `<section class="action-area"><p class="muted">${waitingOn ? `${esc(waitingOn)}さんの手番です` : "待機中です"}</p></section>`;
}

function renderCuccoModal(state) {
  return `
    <div class="modal-overlay">
      <div class="modal cucco-modal">
        <h2>クク宣言のチャンス!</h2>
        <p>あなたはクク札を持っています。今すぐ宣言してディールを終了させますか?</p>
        <p class="countdown">残り ${countdown(state.cuccoWindow.deadline)} 秒</p>
        <button id="cucco-declare-btn">クク宣言する</button>
        <button id="cucco-pass-btn" class="secondary">今は宣言しない</button>
      </div>
    </div>
  `;
}

function renderContinueModal(state) {
  const c = state.continuePrompt;
  return `
    <div class="modal-overlay">
      <div class="modal">
        <h2>続行しますか?</h2>
        <p>必要チップ: ${c.requiredChips}枚</p>
        <p>あなたの現在のチップ: ${state.table.seats.find((s) => s.player_id === state.playerId)?.chips ?? "?"}枚</p>
        <p class="countdown">残り ${countdown(c.deadline)} 秒</p>
        <button id="continue-yes-btn">続行する</button>
        <button id="continue-no-btn" class="secondary">離脱する</button>
      </div>
    </div>
  `;
}

function renderLog(state) {
  return `
    <section class="log">
      <h2>ログ</h2>
      <ul>${state.log.slice(-30).reverse().map((e) => `<li>${esc(e.text)}</li>`).join("")}</ul>
    </section>
  `;
}

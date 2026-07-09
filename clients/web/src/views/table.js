import { esc, secondsLeft } from "../utils.js";
import { seatName } from "../state.js";

export function render(el, state, actions) {
  const t = state.table;
  if (!t) return;
  const isSpectator = state.playerType === "spectator";

  el.innerHTML = `
    <div class="table-screen">
      <header class="table-header">
        <span>卓 ${esc(state.roomId)}</span>
        <span>ポット ${t.pot_number}・ディール ${t.deal_number}</span>
        <span>残り山札: ${t.deck_remaining_count}枚</span>
      </header>

      ${renderSeats(t, state)}
      ${isSpectator ? "" : renderHand(state)}
      ${renderDiscardPile(t)}
      ${renderDeclarations(t, state)}
      ${renderDealOpened(state)}
      ${renderDealResult(state)}
      ${renderPotResult(state, isSpectator)}
      ${isSpectator ? "" : renderActionArea(state, actions)}
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
  el.querySelector("#next-pot-btn")?.addEventListener("click", (e) => {
    actions.sendReady();
    e.target.disabled = true;
  });
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
  return `
    <section>
      <h2>捨て札</h2>
      <ul class="discard-list">
        ${t.discard_pile
          .map((d) => {
            return `<li>${esc(d.card)}${d.original_holder ? ` <span class="muted">(元の持ち主: ${esc(seatIdToName(t, d.original_holder))})</span>` : ""}</li>`;
          })
          .join("")}
      </ul>
    </section>
  `;
}

function seatIdToName(t, id) {
  return t.seats.find((s) => s.player_id === id)?.name ?? id;
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

function renderDealOpened(state) {
  const o = state.lastDealOpened;
  if (!o) return "";
  return `
    <section class="callout">
      <h2>オープン</h2>
      <ul>
        ${Object.entries(o.hands)
          .map(([pid, card]) => {
            const elevated = o.elevated_joker_holders?.includes(pid);
            return `<li>${esc(seatName(state, pid))}: ${esc(card)}${elevated ? " (最強扱い)" : ""}</li>`;
          })
          .join("")}
      </ul>
    </section>
  `;
}

function renderDealResult(state) {
  const r = state.lastDealResult;
  if (!r) return "";
  return `
    <section class="callout">
      <h2>ディール結果</h2>
      <p>敗者: ${r.losers.length ? r.losers.map((id) => esc(seatName(state, id))).join(", ") : "なし"}</p>
      ${Object.entries(r.chips_paid)
        .map(([pid, amt]) => `<p>${esc(seatName(state, pid))} が ${amt} チップ支払い</p>`)
        .join("")}
      ${r.left_pot.length ? `<p>脱落: ${r.left_pot.map((id) => esc(seatName(state, id))).join(", ")}</p>` : ""}
    </section>
  `;
}

function renderPotResult(state, isSpectator) {
  const r = state.lastPotResult;
  if (!r) return "";
  return `
    <section class="callout highlight">
      <h2>ポット結果</h2>
      ${r.result === "won" ? `<p>${esc(seatName(state, r.winner))} が ${r.amount} チップを獲得!</p>` : `<p>このポットは持ち越しになりました。</p>`}
      ${isSpectator ? "" : `<button id="next-pot-btn">次のポットへ</button>`}
    </section>
  `;
}

function renderActionArea(state, actions) {
  if (state.dealerReadyPrompt) {
    return `
      <section class="action-area urgent">
        <p>あなたが親です。手札を確認してから「どうぞ」を宣言してください。(残り${secondsLeft(state.dealerReadyPrompt.deadline)}秒)</p>
        <button id="dealer-ready-btn">どうぞ</button>
      </section>
    `;
  }
  if (state.turnPrompt) {
    return `
      <section class="action-area urgent">
        <p>あなたの手番です。(残り${secondsLeft(state.turnPrompt.deadline)}秒)</p>
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
        <p class="countdown">残り ${secondsLeft(state.cuccoWindow.deadline)} 秒</p>
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
        <p class="countdown">残り ${secondsLeft(c.deadline)} 秒</p>
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

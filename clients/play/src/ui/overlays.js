// Everything layered on top of the table scene: the status line, the action
// dock at the bottom (my prompts), modals (cucco / effect / continue /
// result pause / game end), and the collapsible log drawer. Re-rendered
// into their own containers on each state change -- cheap, and the scene
// itself is never rebuilt by these.

import { esc, secondsLeft } from "../../../web-common/utils.js";

function countdown(deadline) {
  return `<span data-deadline="${deadline}">${secondsLeft(deadline)}</span>`;
}

const EFFECT_ACTION_LABELS = {
  猫: "「ニャー!」と鳴く(要求者の札の元の持ち主が失格)",
  人間: "拒否する(要求者が失格)",
  馬: "「スキップ」(要求を次へ)",
  家: "「スキップ」(要求を次へ)",
};

export function renderStatus(el, state, seatName) {
  const isSpectator = state.playerType === "spectator";
  let text = "進行中…";
  let mine = false;
  if (!isSpectator && state.dealerReadyPrompt) [text, mine] = ["あなたが親です — 手札を確認して「どうぞ」", true];
  else if (!isSpectator && state.turnPrompt) [text, mine] = ["あなたの手番です", true];
  else if (!isSpectator && state.cuccoWindow) [text, mine] = ["クク宣言のチャンス!", true];
  else if (!isSpectator && state.effectWindow) [text, mine] = ["効果を宣言しますか?", true];
  else if (!isSpectator && state.continuePrompt) [text, mine] = ["続行するか選んでください", true];
  else {
    const waiting = [...(state.pendingContinueIds ?? [])].filter((id) => id !== state.playerId);
    if (waiting.length) text = `${waiting.map(seatName).join("、")} さんの続行確認を待っています…`;
    else if (state.lastPotResult) text = "まもなく次のポットが始まります…";
    else if (state.lastDealResult || state.lastDealOpened) text = "まもなく次のディールが始まります…";
    else {
      const dealer = state.table?.dealer_seat;
      const iAmDealer = dealer === state.playerId;
      if (!state.firstActionSeen && !(iAmDealer && state.dozoSent) && dealer) text = `親(${seatName(dealer)})の「どうぞ」を待っています…`;
      else if (state.currentTurnSeat) text = `${seatName(state.currentTurnSeat)} さんの手番です…`;
    }
  }
  el.innerHTML = `<div class="status-line ${mine ? "mine" : ""}">${esc(text)}</div>`;
}

export function renderDock(el, state, actions) {
  if (state.playerType === "spectator") {
    el.innerHTML = "";
    return;
  }
  let html = "";
  if (state.dealerReadyPrompt) {
    html = `
      <span class="dock-timer">${countdown(state.dealerReadyPrompt.deadline)}秒</span>
      <button id="dealer-ready-btn">どうぞ</button>`;
  } else if (state.turnPrompt) {
    html = `
      <span class="dock-timer">${countdown(state.turnPrompt.deadline)}秒</span>
      <button id="cambio-btn">カンビオ(交換)</button>
      <button id="no-change-btn" class="secondary">ノンカンビオ</button>`;
  }
  el.innerHTML = html;
  el.querySelector("#dealer-ready-btn")?.addEventListener("click", actions.sendDealerReady);
  el.querySelector("#cambio-btn")?.addEventListener("click", actions.sendCambio);
  el.querySelector("#no-change-btn")?.addEventListener("click", actions.sendNoChange);
}

export function renderModals(el, state, actions, seatName) {
  const isSpectator = state.playerType === "spectator";
  let html = "";
  if (!isSpectator && state.cuccoWindow) {
    html = modal(
      "urgent",
      `<h2>クク宣言のチャンス!</h2>
       <p>あなたはクク札を持っています。今すぐ宣言してディールを終了させますか?</p>
       <p class="countdown">残り ${countdown(state.cuccoWindow.deadline)} 秒</p>
       <button id="cucco-declare-btn">クク宣言する</button>
       <button id="cucco-pass-btn" class="secondary">今は宣言しない</button>`
    );
  } else if (!isSpectator && state.effectWindow) {
    const label = EFFECT_ACTION_LABELS[state.yourHand] ?? "効果を宣言する";
    html = modal(
      "urgent",
      `<h2>効果を宣言しますか?</h2>
       <p>${esc(seatName(state.effectWindow.requester))} さんがあなたに交換を要求しています。<br>
          あなたの札: <strong>${esc(state.yourHand ?? "?")}</strong></p>
       <p class="countdown">残り ${countdown(state.effectWindow.deadline)} 秒</p>
       <button id="effect-declare-btn">${esc(label)}</button>
       <button id="effect-pass-btn" class="secondary">宣言しない(交換に応じる)</button>`
    );
  } else if (!isSpectator && state.continuePrompt) {
    const myChips = state.table?.seats?.find((s) => s.player_id === state.playerId)?.chips ?? "?";
    html = modal(
      "",
      `<h2>続行しますか?</h2>
       <p>必要チップ: ${state.continuePrompt.requiredChips}枚 / 現在のチップ: ${myChips}枚</p>
       <p class="countdown">残り ${countdown(state.continuePrompt.deadline)} 秒</p>
       <button id="continue-yes-btn">続行する</button>
       <button id="continue-no-btn" class="secondary">離脱する</button>`
    );
  } else if (state.resultPause && state.resultPauseReady) {
    html = modal(
      "wide result",
      `<h2>${state.lastPotResult ? "ポット結果" : "判定結果"}</h2>
       ${resultSummaryHTML(state, seatName)}
       <p class="countdown">残り ${countdown(state.resultPause.deadline)} 秒</p>
       ${
         isSpectator
           ? '<p class="muted">まもなく進行します。</p>'
           : '<button id="result-ack-btn">確認した(全員そろえば先へ進む)</button>'
       }`
    );
  } else if (state.gameEnded) {
    html = modal(
      "wide",
      `<h2>ゲーム終了</h2>
       <ol class="ranking">
         ${(state.gameEnded.ranking ?? []).map(([pid, chips]) => `<li>${esc(seatName(pid))} — ${chips} チップ</li>`).join("")}
       </ol>
       <p class="muted">この部屋はそのまま残っています。チップは新しいゲームでリセットされます。</p>
       <button id="stay-btn">この部屋で続けて遊ぶ</button>
       <button id="leave-btn" class="secondary">部屋を出る</button>`
    );
  }
  el.innerHTML = html;
  el.querySelector("#cucco-declare-btn")?.addEventListener("click", actions.sendCuccoDeclare);
  el.querySelector("#cucco-pass-btn")?.addEventListener("click", actions.sendCuccoPass);
  el.querySelector("#effect-declare-btn")?.addEventListener("click", actions.sendEffectDeclare);
  el.querySelector("#effect-pass-btn")?.addEventListener("click", actions.sendEffectPass);
  el.querySelector("#continue-yes-btn")?.addEventListener("click", () => actions.sendContinue(true));
  el.querySelector("#continue-no-btn")?.addEventListener("click", () => actions.sendContinue(false));
  el.querySelector("#result-ack-btn")?.addEventListener("click", actions.sendResultAck);
  el.querySelector("#stay-btn")?.addEventListener("click", actions.stayInRoom);
  el.querySelector("#leave-btn")?.addEventListener("click", actions.leaveRoom);
}

function modal(cls, inner) {
  return `<div class="modal-overlay"><div class="modal ${cls}">${inner}</div></div>`;
}

function resultSummaryHTML(state, seatName) {
  const opened = state.lastDealOpened;
  const result = state.lastDealResult;
  const pot = state.lastPotResult;
  const losers = new Set([...(opened?.losers ?? []), ...(result?.losers ?? [])]);
  const paid = result?.chips_paid ?? {};
  const leftPot = new Set(result?.left_pot ?? []);
  const rows = (state.table?.seats ?? [])
    .map((s) => {
      const pid = s.player_id;
      const dq = state.disqualifiedInfo[pid];
      const card = opened?.hands?.[pid] ?? dq?.card ?? null;
      const elevated = opened?.elevated_joker_holders?.includes(pid);
      let outcome = "生存";
      if (dq) outcome = "途中失格";
      else if (losers.has(pid)) outcome = "敗者";
      else if (card == null && s.in_current_pot === false) outcome = "ポット外";
      let next = "";
      if (result) next = leftPot.has(pid) ? "脱落" : paid[pid] !== undefined ? "復帰" : s.in_current_pot === false ? "—" : "続行";
      return `<tr class="${dq || losers.has(pid) ? "loser" : ""}">
        <td>${esc(s.name)}${pid === state.playerId ? "(あなた)" : ""}</td>
        <td class="c">${card ? esc(card) + (elevated ? "↑" : "") : "非公開"}</td>
        <td>${outcome}</td><td>${paid[pid] !== undefined ? paid[pid] + "枚" : ""}</td><td>${s.chips}枚</td><td>${next}</td>
      </tr>`;
    })
    .join("");
  const potLine = pot
    ? `<p>${pot.result === "won" ? `${esc(seatName(pot.winner))} が ${pot.amount} 枚を獲得!` : `ポット(${pot.amount}枚)は持ち越し`}</p>`
    : "";
  return `${potLine}<div class="summary-scroll"><table class="deal-summary">
    <thead><tr><th>プレイヤー</th><th>カード</th><th>結果</th><th>支払い</th><th>チップ</th><th>次</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

export function renderLogDrawer(el, state) {
  const items = state.log.slice(-40).reverse();
  el.innerHTML = `
    <details class="log-drawer">
      <summary>ログ</summary>
      <ul>${items.map((e) => `<li>${esc(e.text)}</li>`).join("")}</ul>
    </details>
  `;
}

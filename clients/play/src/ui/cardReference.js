// Card ability reference: a "？" button in the fixed tool cluster (outside
// #screen, so table re-renders never touch it, same trick as the sound
// toggle) that opens a modal listing every special card's effect + flavor
// line. Purely informational -- no game state, no server interaction.

import { esc } from "../../../web-common/utils.js";
import { CARD_REFERENCE } from "../cardInfo.js";
import { cardHTML } from "../scene/table.js";

// Which face(s) to show as a thumbnail next to each entry. The plain-card
// group entry gets one number sample plus the three named-but-plain ranks.
function thumbRanks(entry) {
  return entry.rank.startsWith("数字札") ? ["7", "桶", "仮面", "獅子"] : [entry.rank];
}

export function mountCardReference(cluster) {
  const btn = document.createElement("button");
  btn.id = "card-reference-btn";
  btn.type = "button";
  btn.title = "カードの効果一覧";
  btn.innerHTML = '？<span class="tool-label"> カード効果</span>';

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay card-reference-overlay";
  overlay.hidden = true;
  overlay.innerHTML = `
    <div class="modal wide card-reference-modal">
      <h2>カードの効果</h2>
      <div class="card-ref-list">
        ${CARD_REFERENCE.map(
          (c) => `
          <div class="card-ref-item">
            <div class="card-ref-head">
              <div class="card-ref-thumbs">${thumbRanks(c).map((r) => cardHTML(r)).join("")}</div>
              <div>
                <div class="card-ref-name">${esc(c.rank)}</div>
                ${c.flavor ? `<div class="card-ref-flavor">${esc(c.flavor)}</div>` : ""}
                <p class="card-ref-effect">${esc(c.effect)}</p>
              </div>
            </div>
          </div>`
        ).join("")}
      </div>
      <button id="card-reference-close-btn" class="secondary">閉じる</button>
    </div>
  `;
  document.body.appendChild(overlay);

  const open = () => (overlay.hidden = false);
  const close = () => (overlay.hidden = true);
  btn.addEventListener("click", open);
  overlay.querySelector("#card-reference-close-btn").addEventListener("click", close);
  // Click on the dim backdrop (not the modal card itself) also closes it.
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  cluster.appendChild(btn);
}

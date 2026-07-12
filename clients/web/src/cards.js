// Card rank names as used on the wire (src/cucco/domain/cards.py `Rank`).
// Names only -- no illustrations/icons, per docs/rules/final_rules.md.
export const RANK_ORDER = [
  "道化", "獅子", "仮面", "桶",
  "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
  "家", "猫", "馬", "人間", "クク",
];

const SPECIAL_RANKS = new Set(["道化", "獅子", "仮面", "桶", "家", "猫", "馬", "人間", "クク"]);

export function isSpecialRank(rank) {
  return SPECIAL_RANKS.has(rank);
}

export function rankLabel(rank) {
  return rank == null ? "?" : rank;
}

// Wire tokens -> human-readable Japanese, for the log and result views.
export const REFUSAL_LABELS = {
  house_horse_skip: "スキップ(馬または家)",
  human_refusal: "人間!",
  cat_meow: "猫「ニャー!」",
  horse_house_chain: "馬/家 — 次のカードへ",
  cucco_refusal: "クク(拒否扱い)",
  human_deck_draw: "人間",
  cat_deck_draw: "猫",
};

export const CAUSE_LABELS = {
  received_joker: "道化を受け取った",
  human_refusal: "人間に拒否された",
  human_deck_draw: "山札から人間",
  cat_refusal: "猫の効果",
  cat_deck_draw: "山札から猫の効果",
};

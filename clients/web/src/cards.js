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

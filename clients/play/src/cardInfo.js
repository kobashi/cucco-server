// Card ability reference data for the in-client help panel (card faces are
// drawn separately in scene/cardArt.js -- original pictograms only, never
// the source decks' art) -- effect summaries condensed
// from docs/rules/final_rules.md「特殊札の効果」, flavor lines from
// docs/rules/flavor_text.md(正式採用: 日本語宣言セリフ). Ordered strongest
// to weakest to match how a player actually looks things up mid-deal.
export const CARD_REFERENCE = [
  {
    rank: "クク",
    flavor: "「私は全てを明らかにする」",
    gist: "いつでも「クク宣言」でディールを終了できます(宣言者は負けません)。",
    effect:
      "最強の札。交換を要求されたら拒否できず、通常のカードと同様に交換に応じる。" +
      "保持者はディール中いつでも「クク宣言」でディールを即終了させられる" +
      "(その時点の手札で最弱者を判定。クク自体が最強のため宣言者は負けない)。" +
      "山札から引かれた場合は交換前のカードのまま(拒否扱い)。",
  },
  {
    rank: "人間",
    flavor: "「私に挑むものは全て滅びるであろう」",
    gist: "交換を拒否すると、要求した人が即座に失格します。",
    effect: "交換を拒否し、要求した人を即座に失格にする。山札から引かれた場合は親が失格する。",
  },
  {
    rank: "馬",
    flavor: "「私は跳ねて飛び越える」",
    gist: "「スキップ」で拒否でき、要求は隣のプレイヤーへ流れます。",
    effect:
      "「スキップ」とだけ宣言して拒否でき、要求はさらに右隣のプレイヤーへ移る。" +
      "山札から引かれた場合も同様に次のカードへ進む。拒否した札が馬か家かを明かすかは卓のルール設定による。",
  },
  {
    rank: "猫",
    flavor: "「私の呪いはさかのぼる」(拒否時は「ニャー」)",
    gist: "「ニャー」で拒否すると、交換相手の札の元の持ち主が失格します。",
    effect:
      "「ニャー」と鳴いて拒否し、要求者が今持っているカードを最初に配られたプレイヤー(元の持ち主)を" +
      "即座に失格にする。元の持ち主が既にそのディールで失格済みの場合、効果は不発。",
  },
  {
    rank: "家",
    flavor: "「あなたは私を通り過ぎる馬と同様です」",
    gist: "馬と同じく「スキップ」で拒否でき、要求は隣のプレイヤーへ流れます。",
    effect: "馬と同じく「スキップ」で拒否し、要求をさらに右隣のプレイヤーへ渡す。山札からの場合も馬と同様。",
  },
  {
    rank: "数字札(0〜12)・桶・仮面・獅子",
    flavor: null,
    gist: "特殊効果はありません。交換にはそのまま応じます。",
    effect:
      "特殊効果なし。交換を要求されたら必ず応じる。数字が大きいほど強く、桶・仮面・獅子は" +
      "道化に次いで弱い(道化 < 獅子 < 仮面 < 桶 < 0〜12)。",
  },
  {
    rank: "道化",
    flavor: "「私は最も弱く、最も強い」",
    gist: "受け取ると即失格。持っているだけなら安全です。",
    effect:
      "通常は最弱の札で、交換に応じなければならない。交換で道化を受け取ったプレイヤーは即座に失格になる。" +
      "山札から引かれた場合は例外的に最強(ククより上)の札として扱われる。",
  },
];

// ranks文字列(手札の実際の値)-> CARD_REFERENCEの各エントリ。「数字札(0〜12)・
// 桶・仮面・獅子」はグループ表示なので、該当する個別ランクをここで展開する。
const PLAIN_GROUP = CARD_REFERENCE.find((c) => c.rank.startsWith("数字札"));
const PLAIN_RANKS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "桶", "仮面", "獅子"];
const BY_RANK = new Map(CARD_REFERENCE.filter((c) => c !== PLAIN_GROUP).map((c) => [c.rank, c]));
for (const rank of PLAIN_RANKS) BY_RANK.set(rank, PLAIN_GROUP);

// 自分の手札(現在のランク)に対応する効果情報を返す。未知のランク・nullは
// null(呼び出し側で「表示しない」を選べるように、フォールバックは作らない)。
export function cardEffectFor(rank) {
  return BY_RANK.get(rank) ?? null;
}

// Card ability reference data for the in-client help panel. Text only (no
// illustrations/icons, per project policy) -- effect summaries condensed
// from docs/rules/final_rules.md「特殊札の効果」, flavor lines from
// docs/rules/flavor_text.md(正式採用: 日本語宣言セリフ). Ordered strongest
// to weakest to match how a player actually looks things up mid-deal.
export const CARD_REFERENCE = [
  {
    rank: "クク",
    flavor: "「私は全てを明らかにする」",
    effect:
      "最強の札。交換を要求されたら拒否できず、通常のカードと同様に交換に応じる。" +
      "保持者はディール中いつでも「クク宣言」でディールを即終了させられる" +
      "(その時点の手札で最弱者を判定。クク自体が最強のため宣言者は負けない)。" +
      "山札から引かれた場合は交換前のカードのまま(拒否扱い)。",
  },
  {
    rank: "人間",
    flavor: "「私に挑むものは全て滅びるであろう」",
    effect: "交換を拒否し、要求した人を即座に失格にする。山札から引かれた場合は親が失格する。",
  },
  {
    rank: "馬",
    flavor: "「私は跳ねて飛び越える」",
    effect:
      "「スキップ」とだけ宣言して拒否でき、要求はさらに右隣のプレイヤーへ移る。" +
      "山札から引かれた場合も同様に次のカードへ進む。拒否した札が馬か家かを明かすかは卓のルール設定による。",
  },
  {
    rank: "猫",
    flavor: "「私の呪いはさかのぼる」(拒否時は「ニャー」)",
    effect:
      "「ニャー」と鳴いて拒否し、要求者が今持っているカードを最初に配られたプレイヤー(元の持ち主)を" +
      "即座に失格にする。元の持ち主が既にそのディールで失格済みの場合、効果は不発。",
  },
  {
    rank: "家",
    flavor: "「あなたは私を通り過ぎる馬と同様です」",
    effect: "馬と同じく「スキップ」で拒否し、要求をさらに右隣のプレイヤーへ渡す。山札からの場合も馬と同様。",
  },
  {
    rank: "数字札(0〜12)・桶・仮面・獅子",
    flavor: null,
    effect:
      "特殊効果なし。交換を要求されたら必ず応じる。数字が大きいほど強く、桶・仮面・獅子は" +
      "道化に次いで弱い(道化 < 獅子 < 仮面 < 桶 < 0〜12)。",
  },
  {
    rank: "道化",
    flavor: "「私は最も弱く、最も強い」",
    effect:
      "通常は最弱の札で、交換に応じなければならない。交換で道化を受け取ったプレイヤーは即座に失格になる。" +
      "山札から引かれた場合は例外的に最強(ククより上)の札として扱われる。",
  },
];

"""チップ首位が弱くなった瞬間にクク宣言する(サンプル: 狙い撃ち)

> [!NOTE]
> この形は内蔵の `counting_sniper` に入っている(条件は少し緩めてある)。
> **ただし勝率は上がらなかった**(確認走行7,000ゲームで +0.39pt、SE 0.40pt、
> 有意差なし)。ここに残してあるのは「強い土台を継承して一点だけ変える」書き方
> の見本としてで、このまま使えば強くなるという意味ではない。
> 測った中身は docs/ai-policy-reference.md §3。

クク宣言はディールを即終了させるだけで、誰かを直接倒すわけではない。
**凍結した瞬間の最弱者が敗者になる**ので、宣言は「いつ凍結するか」を選ぶ
武器として使う。この見本は「チップ首位が交換の対象にされて成立した直後」を
狙う。要求した側は自分の札を弱いと判断して動いているので、対象にされた席が
受け取った札は弱い可能性が高い。

待てる窓がどこにあるかは席で決まる(docs/ai-policy-reference.md
「手番の順と交換相手」)。自分を狙えるのは**直前の1席だけ**なので:

- 自分より前に未行動の席が残っているあいだは危険。クク は交換を拒否できない
  ので、そこで温存すると取られる
- **自分の手番を過ぎれば誰も自分を狙えない。**ここが安全に様子を見られる窓

交換判断は内蔵の `counting_conservative` をそのまま使い、宣言のタイミングだけ
書き換えている。自分のAIを作るときもこの形(強い土台を継承して一点だけ変える)
が速い。
"""

from cucco.ai.policies import CountingConservative


class SniperPolicy(CountingConservative):
    """チップ首位が弱くなった瞬間にクク宣言する(サンプル)"""

    name = "example_cucco_sniper"

    def _worth_waiting(self, ctx) -> bool:
        """宣言を我慢して狙う価値があるか。"""
        leader = ctx.chip_leader
        if leader is None or leader == ctx.my_id:
            # 同点首位(狙い先が決まらない)か、自分が首位。待つ理由がない。
            return False
        if ctx.is_child_time:
            # 子供の時間(1〜3ディール目)の敗者はチップを払って残るだけなので、
            # 首位を削る効果が小さい。大人の時間なら敗者はポットから脱落する。
            return False
        # 首位がまだ弱くなっていないあいだは待つ。
        return not ctx.was_taken_from(leader)

    def declare_cucco_eagerly(self, ctx) -> bool:
        """クク を持った瞬間(配られた直後・交換で受け取った直後)に呼ばれる。"""
        if ctx.unacted_before_me():
            # 自分より前に未行動の席がある = 取られる危険がある。待てない。
            return True
        return not self._worth_waiting(ctx)

    def decide_cucco_declare_ctx(self, ctx) -> bool:
        """自分の手番と、親の「どうぞ」で呼ばれる。

        ここで宣言すれば以降の席が動く前に凍結できる。宣言しなければ手番は
        ノーチェンジで流れ、**以降は誰にも奪われない**まま狙う側に回れる。
        """
        return not self._worth_waiting(ctx)

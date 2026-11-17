"""弱い数字だけ交換する(サンプル)

このファイルをコピーして `policies/` に置き、`name` と中身を書き換えれば
自分のAIになります。クラスの1行目の説明が、卓を作る画面に出ます。
"""

from cucco.ai.policies import BasePolicy


class ExamplePolicy(BasePolicy):
    """弱い数字だけ交換する(サンプル)"""

    # 卓を作る画面に出る名前。英小文字・数字・_ で2〜32文字。
    # 他の人と重ならない名前にしてください(成績もこの名前で残ります)。
    name = "example_weak_change"

    def decide_change(self, own_rank: str, alive_count: int) -> bool:
        """自分の手番。True で交換(カンビオ)、False で交換しない。

        own_rank:    自分の手札。"0"〜"12" か、"道化" "人間" "馬" "猫" "家"
                     "クク" "仮面" などの特殊札(いずれも文字列)
        alive_count: そのディールに残っている人数(自分を含む)
        """
        if own_rank in ("道化", "獅子", "仮面", "桶"):
            return True               # 0 より弱い特殊札。持っていると負ける
        if not own_rank.isdigit():
            return False              # 人間・馬・猫・家・クク は強いので持つ
        return int(own_rank) <= 4     # 4以下なら交換

    # ククを「持った瞬間」に宣言したいなら、これを足す。足さないと、宣言するのは
    # 自分の手番が来たときだけで、それまでに直前の席に取られることがある
    # (docs/ai-policy-reference.md §1「クク宣言は2つのメソッドに分かれる」)。
    #
    # def declare_cucco_eagerly(self, ctx) -> bool:
    #     return True

# AI方策リファレンス

書いている最中に引くための一覧。**作り方・置き方の手順**は
[ai-player-guide.md](ai-player-guide.md)、**ルール本体**は
[rules/final_rules.md](rules/final_rules.md)、**強いAIの考え方**は
[ai-advanced-policies.md](ai-advanced-policies.md) を参照。

正はコードにある: [`src/cucco/ai/policies.py`](../src/cucco/ai/policies.py)(基底クラス)、
[`src/cucco/ai/context.py`](../src/cucco/ai/context.py)(判断材料)。

## 1. 実装するメソッド

`BasePolicy` を継承し、必要なものだけ上書きする。**すべて同期関数**で、
**即座に返す**こと(サーバーの中で動くので、待つと卓全体が止まる)。

| メソッド | 呼ばれる場面 | 返す | 未実装時 |
|---|---|---|---|
| `decide_change(own_rank, alive_count)` | 自分の手番 | `True`=交換(カンビオ) / `False`=しない | **これか `decide_change_ctx` のどちらかは必須** |
| `decide_cucco_declare(own_rank, alive_count)` | クク札を持っている自分の手番・「どうぞ」 | `True`=クク宣言 | 常に `True` |
| `decide_continue(chips, required_chips)` | 子供の時間に敗者になった | `True`=払って続行 | 常に `True` |
| `decide_change_ctx(ctx)` | 上と同じ場面(**こちらが優先**) | 同上 | `decide_change` に委譲 |
| `decide_cucco_declare_ctx(ctx)` | 同上 | 同上 | `decide_cucco_declare` に委譲 |
| `decide_continue_ctx(ctx)` | 同上 | 同上 | `decide_continue` に委譲 |
| `declare_cucco_eagerly(ctx)` | クク札を**持った瞬間**(手番を待たずに宣言するか) | `True`=即宣言 | `False` |

`_ctx` 版を実装すると、下の `PolicyContext` すべてが使える。両方実装した場合は
`_ctx` 版が呼ばれる。

> [!NOTE]
> **例外を投げても対局は止まらない。** その判断だけ既定値(交換しない / 続行する /
> クク宣言する)で進み、サーバーのログに理由が残る。ただし黙って弱くなるので、
> ログは確認すること。

## 2. 判断材料(`PolicyContext`)

| フィールド | 型 | 中身 |
|---|---|---|
| `own_rank` | `str` | 自分の手札(`"0"`〜`"12"` か特殊札名) |
| `alive_count` | `int` | そのディールに残っている人数(自分を含む) |
| `deal_number` | `int` | そのポット内のディール番号(1始まり)。**1〜3が子供の時間** |
| `pot_chips` | `int` | 場に積まれているチップ |
| `my_chips` | `int` | 自分の持ちチップ |
| `is_dealer` | `bool` | 自分が親か |
| `unseen_counts` | `dict[str, int]` | **まだ見えていない札**の枚数(札名→枚数)。自分の手札は除いてある |
| `known_held` | `dict[str, str]` | 公開されて分かっている他家の手札(player_id→札名)。分かっている分だけ |
| `turn_actions_this_deal` | `int` | そのディールでこれまでに行われた手番宣言の数 |
| `required_chips` | `int` | 続行に必要なチップ(`decide_continue_ctx` のときだけ意味がある) |

補助:

| | 返す |
|---|---|
| `ctx.is_child_time` | 子供の時間(1〜3ディール目)か |
| `ctx.unseen_total` | 見えていない札の合計枚数 |
| `ctx.unseen_weaker_than_own()` | そのうち**自分より弱い**札の枚数 |
| `strength_of(rank)` | 札の強さ(数が大きいほど強い)。`from cucco.ai.context import strength_of` |

### 見えないもの

- **他人の手札**(公開されたものを除く。`known_held` がその全部)
- **山札の並び**(次に何が来るかは誰にも分からない)
- 他の方策の内部状態、他の卓の状況

## 3. 札の早見表

強い順。**同じ札が2枚ずつ、全44枚**。

| 札 | 強さ | 持っているとき | 交換を要求されたとき |
|---|---|---|---|
| クク | 最強 | いつでも「クク宣言」でディールを即終了できる(宣言者は負けない) | 拒否できない。普通に交換に応じる |
| 人間 | 2番目 | — | 拒否でき、**要求した人が失格** |
| 馬 | 3番目 | — | 「スキップ」で拒否。要求は隣へ流れる |
| 猫 | 4番目 | — | 「ニャー」で拒否。要求者の札の**元の持ち主が失格** |
| 家 | 5番目 | — | 「スキップ」で拒否。要求は隣へ流れる |
| 12 〜 0 | 数の順 | — | 拒否できない |
| 桶 | 0より下 | — | 拒否できない |
| 仮面 | さらに下 | — | 拒否できない |
| 獅子 | さらに下 | — | 拒否できない |
| 道化 | **最弱** | 持っているだけなら安全 | 拒否できない。**交換で受け取った側が即失格**(渡した側は無傷)。山札から引いた場合だけは例外的に最強(ククより上)になる |

細かい条件(山札から引いた場合、道化の昇格、猫の不発、開示設定)は
[rules/final_rules.md](rules/final_rules.md)「特殊札の効果」を参照。

## 4. 書き方の型

```python
from cucco.ai.policies import BasePolicy


class MyPolicy(BasePolicy):
    """一行の説明(卓を作る画面に出る)"""

    name = "zemi_yourname"

    def decide_change_ctx(self, ctx) -> bool:
        # 自分より弱い札が、見えていない札の何割あるか
        weaker = ctx.unseen_weaker_than_own()
        share = weaker / ctx.unseen_total if ctx.unseen_total else 0
        # 弱い札が少ない = 自分が最弱になりやすい -> 交換する
        return share < 0.35

    def decide_change(self, own_rank, alive_count):
        return False   # ctx 版を使う場合の保険(呼ばれない)
```

**状態を持ちたい場合**は `__init__` を書いてよい。方策のインスタンスは
**1ゲームにつき1つ**作られる(ディールをまたいで持ち越せるが、ゲームが変わると
リセットされる)。

## 5. 手元で測る

サーバーを起動しておき、外部クライアントとして自分の方策を走らせる。
**`--policy-dir` で `policies/` が読み込まれる**ので、自分の方策名がそのまま使える。

```bash
# 1) サーバー(別ターミナル)
python -m cucco.server.app

# 2) 評価モードで卓を作る(200ゲームを自動連続)
python -m clients.mock_ai.mock_ai --name Mine --create \
    --mode evaluation --game-count 200 --policy zemi_yourname

# 3) 表示された room_id に対戦相手を参加させる(人数分くり返す)
#    参加する側にも --mode evaluation を付ける(下記の注意)
python -m clients.mock_ai.mock_ai --name Rival --room <room_id> \
    --mode evaluation --policy matrix
```

全員が揃うと自動で走り、終わると成績が配信される。あとから見るには:

```bash
python -m cucco.tools.stats --policies      # 方策別の通算成績
python -m cucco.tools.stats --evaluations   # 評価モードの実行一覧
```

> [!WARNING]
> **参加する側にも `--mode evaluation` を付ける。** 付け忘れると、そのクライアントは
> 1ゲーム目が終わった時点で卓を離れてしまい、残った人数が足りずに評価が途中で
> 打ち切られる(`games_played` が指定より少ない結果になる)。

> [!IMPORTANT]
> **1回の結果で判断しない。** 人数と相手構成で優劣は桁違いに変わる。何ゲーム必要か、
> どう比べるかは [ai-advanced-policies.md](ai-advanced-policies.md)「検証の作法」に
> 実測付きで書いてある。

## 6. つまずきやすいところ

| 症状 | 原因 |
|---|---|
| 卓を作る画面に自分の方策が出ない | サーバーを再起動していない / 読み込みに失敗(サーバーのログを見る) |
| `--policy` で「unknown policy」 | クライアント側のカレントディレクトリに `policies/` がない(`--policy-dir` で指定) |
| 弱いはずなのに勝率が変わらない | `decide_change_ctx` と `decide_change` の両方を実装していて、`_ctx` 側だけが効いている |
| たまに変な手を打つ | 例外が出て既定値に倒れている(サーバーのログに `policy ... failed` が出る) |
| 卓が固まる | 判断の中で時間のかかる処理をしている。方策は即座に返すこと |
| 評価が指定より少ないゲーム数で終わる | 参加側の `--mode evaluation` を付け忘れている(1ゲームで抜けてしまう) |

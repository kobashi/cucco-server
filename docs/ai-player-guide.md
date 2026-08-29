# ゼミ生AIの作り方と、サーバーへの追加のしかた

自分のAIをCuccoの卓に座らせるための手順書。**サーバーのコードには一切手を
入れない**。ファイルを1つ置くか、自分のプログラムを外から繋ぐかのどちらか。

関連: **[方策リファレンス](ai-policy-reference.md)**(書いている最中に引く一覧) /
[ルール](rules/final_rules.md) / [AIの考え方](ai-advanced-policies.md) /
[プロトコル](protocol/design.md) / [外部クライアントの作り方](ai-client-guide.md)

## 1. 2つの道

| | 方策ファイル | 外部クライアント |
|---|---|---|
| 置くもの | `.py` を1つ | 自分のプログラム(言語は自由) |
| 動く場所 | サーバーの中 | 自分のPC(サーバーに接続) |
| 手間 | **クラスを1つ書くだけ** | 通信の実装が要る([ガイド](ai-client-guide.md)) |
| できること | 手札・人数・チップ・場に出た札から判断 | 同上(受け取るイベントは全部見られる) |
| 向いている人 | まずAIの中身を考えたい | 通信も含めて作りたい / Python以外で書きたい |
| 卓への座らせ方 | 卓を作る画面でAIとして選ぶ | 自分でサーバーに接続して卓に入る |

**迷ったら方策ファイル**。この文書はそちらを中心に説明する。

## 2. 方策ファイルの書き方

### 最小の形

見本が [`docs/examples/policy_example.py`](examples/policy_example.py) にある。
コピーして名前と中身を変えるのが早い。

```python
"""弱い数字だけ交換する(サンプル)"""

from cucco.ai.policies import BasePolicy


class MyPolicy(BasePolicy):
    """弱い数字だけ交換する"""       # ← この1行が卓を作る画面に出る

    name = "zemi_yamada"             # ← 卓で選ぶときの名前

    def decide_change(self, own_rank: str, alive_count: int) -> bool:
        if not own_rank.isdigit():
            return False
        return int(own_rank) <= 4
```

### 決めること

| メソッド | いつ呼ばれるか | 返すもの | 省略したときの動き |
|---|---|---|---|
| `decide_change(own_rank, alive_count)` | 自分の手番 | `True`=交換(カンビオ) | **必須** |
| `decide_cucco_declare(own_rank, alive_count)` | クク札を持っている手番 | `True`=クク宣言 | 常に宣言する |
| `decide_continue(chips, required_chips)` | 子供の時間に敗者になった | `True`=払って続行 | 常に払って続行 |

`own_rank` は `"0"`〜`"12"` の数字か、`"道化" "人間" "馬" "猫" "家" "クク" "仮面"`
などの特殊札(いずれも文字列)。`alive_count` はそのディールに残っている人数
(自分を含む)。

### 名前の決まり

- 英小文字・数字・`_` で **2〜32文字**
- **他の人と重ならない名前**にする(`zemi_yamada` のように接頭辞を付けると安全)。
  成績はこの名前で記録されるので、重なると混ざる
- 既にある名前(`matrix` など)は使えない。使うと**そのファイルだけ読み込まれない**

### 場に出た札まで見て判断する

数えながら打つAIを書くなら、`decide_change_ctx(ctx)` を実装する。`ctx` から
見えるもの(自分の手札・残り人数・チップ・捨て札・公開された札・未確認の札の
枚数)は [`src/cucco/ai/context.py`](../src/cucco/ai/context.py) と
[ai-advanced-policies.md](ai-advanced-policies.md) を参照。

```python
class MyCountingPolicy(BasePolicy):
    """未確認の札から勝てる見込みを数える"""

    name = "zemi_yamada_counting"

    def decide_change_ctx(self, ctx) -> bool:
        unseen = ctx.unseen_counts()          # まだ見えていない札の枚数
        return False                          # ここに自分の判断を書く

    def decide_change(self, own_rank, alive_count):
        return False                          # ctx 版を使う場合も残しておく
```

## 3. 置いて動かす

1. ファイルを **`policies/`** に置く(サーバーのフォルダ直下)
2. **サーバーを再起動する** — 読み込みは起動時だけ
3. 起動ログで確認する

```bash
tail -20 ~/cucco-server.log | grep policy
```

```
INFO:cucco.ai.plugins:loaded policy zemi_yamada from yamada.py     ← 成功
WARNING:cucco.ai.plugins:skipped yamada.py: 読み込み中にエラー: ...  ← 失敗
```

4. ブラウザで卓を作ると、**AIの一覧に自分の名前が並んでいる**

> [!IMPORTANT]
> **壊れたファイルがあってもサーバーは起動する。** 読めなかったものは警告を出して
> 飛ばし、残りで動く。自分のAIが一覧に出ないときは、まずこのログを見ること。

## 4. 強さを測る

対局させて成績を見る。人を集めなくても、AI同士で回せる。

```bash
# 卓を作る画面で「観戦のみ」+ 自分のAIと相手のAIを数人 → 開始
# 何ゲームか回してから:
python -m cucco.tools.stats --policies
```

`--policies` は方策別の成績(勝率・平均順位・失格率)を出す。**方策名で集計する**
ので、名前を変えると別物として記録される。

まとまった数を回すなら評価モード(卓の作成時に選ぶ)を使う。測り方の作法
(何ゲーム必要か、相手構成で結果がどう変わるか)は
[ai-advanced-policies.md](ai-advanced-policies.md)「検証の作法」を参照。

## 5. やってはいけないこと

方策はサーバーの中で動く。**他の人の対局と同じプロセス**にいることを忘れない。

| | なぜ |
|---|---|
| 時間のかかる処理(重い計算・長いループ) | サーバー全体が止まる。判断は一瞬で返す |
| `while True` などの無限ループ | 卓が固まり、再起動するまで戻らない |
| ネットワークやファイルへのアクセス | 同上(待ちが発生する)。判断に必要な情報は引数で渡される |
| 他の方策や卓の状態を書き換える | 他人の対局を壊す |
| 例外を投げる | 落ちても**その判断だけ**は既定値(交換しない/続行する)で進み、ログに残る。ただし放置しない |

## 6. 提出のしかた

- ファイル名は `姓_なにか.py`(例: `yamada_counting.py`)
- `name` は `zemi_姓` で始める
- 1ファイルに複数の方策を書いてもよい(クラスを並べる)
- 提出物は `policies/` に置かれる。**このフォルダはGitの管理外**なので、
  リポジトリには入らない

## 7. 外部クライアントとして作る場合

Python以外で書きたい、通信も自分で作りたい場合はこちら。作り方は
[ai-client-guide.md](ai-client-guide.md)、動く実装は
[`clients/mock_ai/`](../clients/mock_ai/) にある。卓には自分で接続して入る
(`identify` → `join_table` → `ready`)。**対局中の卓には後から入れない**ので、
ゲームが始まる前に入っておくこと。

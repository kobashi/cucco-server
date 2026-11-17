# AIを作る人の入口

自分のAIを Cucco の卓に座らせたい人が、**どの文書を・どの順で読めばよいか**を
まとめた案内。Coding Agent(Claude Code など)に実装を頼むときに渡す文書の
組もここにある。

## 1. 最短の道

1. [`examples/policy_example.py`](examples/policy_example.py) をコピーして、`name` と中身を書き換える
2. `policies/` に置いてサーバーを再起動する
3. 卓を作る画面のAI一覧に自分の名前が出る

手順の詳細は [ai-player-guide.md](ai-player-guide.md)。**サーバーのコードには
手を入れない。**

## 2. 目的から文書を引く

| 知りたいこと | 読む文書 | 何が分かるか |
|---|---|---|
| まず動かしたい | [ai-player-guide.md](ai-player-guide.md) | 方策ファイルの最小の形、名前の決まり、置き方、起動ログの見方、やってはいけないこと |
| メソッドと判断材料の一覧 | [ai-policy-reference.md](ai-policy-reference.md) §1・§2 | どのメソッドがいつ呼ばれるか、`ctx` から何が見えるか |
| **クク宣言のタイミング** | [ai-policy-reference.md](ai-policy-reference.md) §1「クク宣言は2つのメソッドに分かれる」 | 手番前・手番・手番後のどこで何が呼ばれるか。**手番前に宣言するには `declare_cucco_eagerly` が要る** |
| 手番の順と交換相手 | [ai-policy-reference.md](ai-policy-reference.md) §3 | 誰と交換するか、誰に狙われるか、親の山札交換の違い |
| 札の強さと効果 | [ai-policy-reference.md](ai-policy-reference.md) §4 | 強い順の早見表。**道化・獅子・仮面・桶は 0 より弱い** |
| ルールの正確な条件 | [rules/final_rules.md](rules/final_rules.md) | 失格・拒否連鎖・クク宣言の優先順位など、細かい条件の正 |
| 強さを測りたい | [ai-policy-reference.md](ai-policy-reference.md) §6 | サーバーなしで速く測る `tools/fast_eval.py`、読み方、何ゲーム必要か |
| うまく動かない | [ai-policy-reference.md](ai-policy-reference.md) §7 | 一覧に出ない、変な手を打つ、卓が固まる、などの原因 |
| Python 以外で書きたい | [ai-client-guide.md](ai-client-guide.md) と [protocol/design.md](protocol/design.md) | 外からサーバーに接続するAIの作り方、メッセージの仕様 |

## 3. Coding Agent に渡す文書

次の組を渡せば、方策ファイルを実装できる。上から順に重要。

| 渡すもの | 役割 |
|---|---|
| [ai-player-guide.md](ai-player-guide.md) | 何を作るか(ファイルの形・置き方・禁止事項) |
| [ai-policy-reference.md](ai-policy-reference.md) | 使えるメソッドと判断材料のすべて |
| [rules/final_rules.md](rules/final_rules.md) | ルールの正 |
| [`examples/policy_example.py`](examples/policy_example.py) | 最小の見本 |
| [`examples/policy_sniper.py`](examples/policy_sniper.py) | 内蔵方策を継承して一点だけ変える見本 |
| [`../src/cucco/ai/policies.py`](../src/cucco/ai/policies.py) | 基底クラスと内蔵方策の実装(**文書と食い違ったらこちらが正**) |
| [`../src/cucco/ai/context.py`](../src/cucco/ai/context.py) | 判断材料(`PolicyContext`・`LossModel`)の実装 |
| [`../tools/fast_eval.py`](../tools/fast_eval.py) | 強さを測る道具(冒頭に使い方) |

リポジトリごと開いているなら、次のように頼めば足りる。

```text
docs/ai-start-here.md を読み、そこに挙がっている文書に従って、
Cucco のAI方策を policies/zemi_<名前>.py に実装してください。

- 方針: (例) 最下位を避けることを最優先にする
- サーバーのコード(src/)は変更しない
- 書いたら tools/fast_eval.py で内蔵の matrix と比べ、結果を報告する
```

実装させたあとで確かめること:

- **`fast_eval.py` で内蔵の `matrix` と比べる。** `matrix` に大きく負けるなら、
  どこかに取り違えがある(よくあるのは下の2つ)
- **0 より弱い特殊札を持ち続けていないか。** 「数字でない札は持つ」と書くと、
  道化・獅子・仮面・桶まで持ってしまう
- **ククを手番前に宣言できているか。** `decide_cucco_declare` だけでは、自分の
  手番が来るまで宣言しない

## 4. 強くするヒントはどこにあるか

| 文書 | 何が分かるか |
|---|---|
| [rules/strategy_hints.md](rules/strategy_hints.md) | 判断のときに考えることの一覧(残り人数、前の席の動き、ラウンド数など)。最初の発想の出発点 |
| [rules/play_summary_granpere.md](rules/play_summary_granpere.md) | 製品付属の判断表。人数ごとの「これ以下なら交換」の目安 |
| [ai-advanced-policies.md](ai-advanced-policies.md) | 強いAIの設計案(数え上げ・推論・シミュレーション・チップ期待値・強化学習)と、考えるべき6つの観点。**検証の作法**(何ゲーム回すか、相手で結果がどう変わるか)もここ |
| [ai-policy-reference.md](ai-policy-reference.md) §2 の `ctx.loss_model` など | 内蔵の最強方策が使っている部品。「誰かが自分より弱い確率」「持った場合/交換した場合に負ける確率」をそのまま呼べる |
| [ai-policy-reference.md](ai-policy-reference.md) §3「狙い撃ち」 | クク宣言で誰を負けさせるかを選ぶ考え方と、**試したが勝率に出なかった**という実測 |
| [`../CHANGELOG.md`](../CHANGELOG.md) の「未リリース」と v1.7.0 | **何が効いて何が効かなかったか**の実測記録。相手の癖を学ぶ、天井の測定(あとどれだけ伸びるか)、効かなかったものの一覧 |
| [`../tools/decision_map/README.md`](../tools/decision_map/README.md) | 「どの判断で勝率を落としているか」を測る道具。当て推量で直す前に使う |
| [`../src/cucco/ai/policies.py`](../src/cucco/ai/policies.py) の `CountingWin` | 内蔵で最強の方策の実装。**先に自分で考えてから読み比べる**のがよい |

### いま分かっている強さの目安

4人卓・内蔵AIの混成卓での勝率(偶然なら 25%)。

| 方策 | 勝率の目安 | 中身 |
|---|---|---|
| `matrix` | 20% 前後 | 人数と札だけの判断表。**まず超える相手** |
| `counting_win` | 30% 前後 | 他席の動きから札を推論する。内蔵で最強 |

数字は相手の構成と人数で大きく動く。自分で測るときは同じ卓・同じシードで
比べること([ai-policy-reference.md](ai-policy-reference.md) §6)。

## 5. 文書の地図(AI関係のみ)

```
docs/
  ai-start-here.md          ← この文書(入口)
  ai-player-guide.md        手順書: 作る・置く・測る・提出する
  ai-policy-reference.md    リファレンス: メソッド・判断材料・手番順・札・測り方
  ai-advanced-policies.md   教材: 強いAIの設計案と検証の作法
  ai-client-guide.md        外部クライアントとして作る場合
  examples/                 方策ファイルの見本
  rules/final_rules.md      ルールの正
  rules/strategy_hints.md   考えることの一覧
  rules/play_summary_granpere.md   製品付属の判断表
  protocol/design.md        通信の仕様(外部クライアント向け)
src/cucco/ai/               基底クラス・内蔵方策・判断材料の実装
tools/fast_eval.py          強さを速く測る
tools/decision_map/         どの判断で損をしているかを測る
policies/                   自分の方策を置く場所(Git 管理外)
```

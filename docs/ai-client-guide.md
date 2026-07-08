# AIプレイヤー実装ガイド

ゼミ生がCucco対戦サーバーに接続するAIプレイヤーをPythonで実装するためのガイド。プロトコルの正式な定義は`docs/protocol/design.md`、ゲームルールは`docs/rules/final_rules.md`を参照。このガイドは、実装者が最初に読む「how-to」としてそれらを実装視点で要約・補足するもの。現状の仕様をベースとしており、実装を進める中で要件不足が判明した場合は随時ルール文書・プロトコル文書を修正する。

## 1. 接続からポット開始まで

1. WebSocketでサーバーに接続する
2. `identify`を送信する(`{name, player_type: "ai"}`)。サーバーから`identified`(セッショントークン入り)が返る。**このトークンは保存しておくこと**(切断時の再接続に必須)
3. 卓に参加する
   - 自分で卓を立てる場合: `create_table`を送信し、`table_created`でプレイルームIDを受け取る
   - 既存の卓に参加する場合: 他の参加者から共有されたプレイルームIDを使って`join_table`(`{room_id}`)を送信する
4. `state_snapshot`で現在の卓の状況を受け取る
5. `ready`を送信して次のポットへの参加(参加費チップ1枚)を表明する
6. `pot_started`でポット開始を確認する

再接続時は、`join_table`に保存済みの`session_token`を含めて送信すれば、`state_snapshot`(自分の現在の手札`your_hand`を含む)を受け取ってそのまま復帰できる。

## 2. AIが応答すべき場面(判断ポイント)

AIクライアントは基本的に「サーバーからの通知を待ち、必要な場面でアクションを返す」受動的なループになる。判断が必要になるのは以下の場面のみ。

| サーバーからの通知 | 状況 | 送るべきアクション |
|---|---|---|
| `pot_result`(または卓参加直後の`state_snapshot`) | 次のポットが始まる前 | `ready`(参加費チップ1枚を払って次のポットへの参加を表明) |
| `deal_started` | 自分が親の場合、配布直後 | `dealer_ready`(「どうぞ」宣言) |
| `turn_prompt` | 自分の手番が来た | `cambio_declare`(交換を要求する) または `no_change_declare`(しない) |
| `cucco_window` | クク札を保持している(かつ失格していない) | `cucco_declare`(ディールを終了させる) または `cucco_pass`(見送る) |
| `continue_prompt` | 子供の時間(1〜3ディール目)で敗者になった | `continue_declare`(`{continue: true/false}`) |

**`ready`はポットごとに毎回送り直す必要がある**。最初の参加時だけでなく、`pot_result`を受け取るたびに次のポットへの`ready`を送らないと、タイムアウトでそのポットに参加しない(観戦扱い)になってしまう。ただし評価モード(`mode: "evaluation"`)では、1回の`ready`で`game_count`回分のゲームが自動連続実行されるため、この限りではない。

上記以外の通知(`exchange_result`, `player_disqualified`, `deal_opened`, `deal_result`, `pot_result`, `game_ended`など)は状態把握のためのイベントであり、応答アクションは不要。`pot_result`はそのポットの決着(勝者確定または持ち越し)、`game_ended`はゲーム全体の終了(チップ数による最終順位)を表す。

**重要**: `cucco_window`は、クク札を保持していない場合は届かない。保持している場合は自分の手番かどうかに関わらず毎回(1つのアトミックな処理が完了するたび)届くため、**必ず`cucco_declare`か`cucco_pass`のどちらかを即座に返すこと**。応答しない(パスもしない)とタイムアウト(AI用: デフォルト2秒)まで卓全体の進行が止まってしまう。特に評価モード(高速連続対局)ではこの応答速度が重要になる。

## 3. 状態管理

AIクライアントは基本的にステートレスに実装できない。以下の情報をサーバーから受け取ったイベントを元に保持し、判断に使う。

- **自分の現在の手札**: `your_hand`(`state_snapshot`)、`deal_started`、`exchange_result`(自分が関係者の場合)から更新する
- **自分のチップ枚数・全員のチップ枚数**: 常に絶対値で送られてくる(`pot_started`, `deal_result`, `pot_result`)。差分計算は不要、受け取った値でそのまま上書きしてよい
- **残り山札枚数**: `deck_remaining_count`(カウンティングに使用。`deck_reshuffled`でリセットされる)
- **捨て札とカードの来歴**: `discard_pile`(内容と経緯)、`provenance_map`(現在の所持者→最初の持ち主)。特に「猫」の効果(要求者が現在持っているカードの最初の持ち主が失格する)を予測するために、`provenance_map`を追跡しておくと有利
- **そのディール中の宣言履歴**: `declarations_this_deal`(誰がいつカンビオ/ノンカンビオ/クク宣言をしたか)。ただし`cucco_pass`は公開情報に含まれない(誰がククを持っているか推測されないようにするため)
- **親の位置・現在の手番**: `dealer_seat`, `current_turn_seat`

戦略設計の参考として、`docs/rules/strategy_hints.md`(考慮すべき観点)と`docs/rules/play_summary_granpere.md`(カードごとのチェンジ判断の目安)も参照するとよい。

## 4. 典型的な1ディールのメッセージフロー(例)

4人卓、自分が2番手のプレイヤーである場合の例。

```
サーバー→全員: deal_started        (自分の手札を含む、残り山札枚数を含む)
サーバー→親:   cucco_window        (親がクク保持なら、他のプレイヤーより先に確認)
親→サーバー:   cucco_pass          (または cucco_declare でここでディール終了)
サーバー→(親以外のクク保持者): cucco_window (親の確認の後、ディール開始直後の分)
(該当プレイヤー)→サーバー: cucco_pass または cucco_declare
親→サーバー:   dealer_ready        (「どうぞ」)
サーバー→全員: turn_prompt         (1番手の手番)
1番手→サーバー: cambio_declare または no_change_declare
サーバー→全員: exchange_result     (交換結果、または no_change_declared)
サーバー→(自分がクク保持なら): cucco_window
自分→サーバー: cucco_pass または cucco_declare
サーバー→自分: turn_prompt         (自分の手番)
自分→サーバー: cambio_declare または no_change_declare
サーバー→全員: exchange_result
... (残りのプレイヤーも同様に繰り返す) ...
サーバー→親:   turn_prompt         (親の最終手番、山札交換)
親→サーバー:   cambio_declare または no_change_declare
サーバー→全員: exchange_result     (山札からの交換。特殊札なら連鎖・失格イベントを伴う)
サーバー→全員: deal_opened         (全員の手札を公開)
サーバー→全員: deal_result         (敗者・支払いチップ・現在チップ数・次の親)
(子供の時間の敗者がいれば) サーバー→敗者: continue_prompt
敗者→サーバー: continue_declare
```

途中で誰かが「人間」「猫」「道化」により失格した場合は`player_disqualified`が割り込む。その場合、失格したプレイヤーは以後の`turn_prompt`/`cucco_window`の対象から外れる(ルールの詳細は`docs/rules/final_rules.md`「途中失格者はそのディールから完全に除外される」を参照)。

## 5. エラー・異常系への対応

- `action_rejected`を受け取った場合: 現在の状態から見て不正な操作を送った(手番でないのに宣言した等)。自分の内部状態がサーバーとズレている可能性が高い。**`state_snapshot`を明示的に要求するアクションは存在しない**ので、保存しておいた`session_token`を使って`join_table`を送り直すこと(再接続と同じ扱いになり、`state_snapshot`が返ってくるので、そこから状態を立て直す)。合わせてログを確認しロジックを見直す
- タイムアウトで自動処理された場合(`turn_timeout_consumed`): 応答が間に合わなかったことを意味する。AI用タイムアウト(デフォルト10秒、`cucco_window`は2秒)に収まるよう、応答ロジックの処理時間に注意する
- 切断からの再接続: 手番中に切断してもゲームからは脱落しない(ノーチェンジ扱いで進む)。再接続後は`state_snapshot`の`your_hand`で現在の手札を確認してから判断を再開すること。`your_hand`が`null`の場合は、まだそのディールでカードが配られていない(ディールとディールの間)か、そのディール中に既に失格して手札を失っている状態を意味する

## 6. 実装上の注意

- `protocol_version`をエンベロープに含めること。バージョン不一致はサーバー側で検出され`action_rejected`になる
- 交換相手は常に「自分の右隣のプレイヤー」に固定されているため、`cambio_declare`にターゲット指定は不要
- 馬・家の開示方法など、ゲーム開始時に決まる設定(`create_table`のpayload、`docs/protocol/design.md`参照)はディール中に変化しないため、卓参加時に一度確認しておけばよい
- 評価モード(`mode: "evaluation"`)で動作確認・自己対戦によるテストが可能。高速連続対局になるため、判断ロジックの応答速度・例外処理を評価モードでまず検証することを推奨する

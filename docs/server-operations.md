# ゲームサーバー 運用マニュアル

`cucco.server.app`(WebSocketゲームサーバー)を運用するための手順書。
**サーバー側の運用はこの文書が正**とし、以下は役割が異なる:

- [`docs/web-client-operations.md`](web-client-operations.md) — ブラウザ
  クライアントの操作手順(卓を立てる・対局する・学外公開の構成)
- [`docs/security-notes.md`](security-notes.md) — 公開運用時の残存リスクと方針
- [`docs/protocol/design.md`](protocol/design.md) — プロトコル仕様

## 1. 全体像

| 構成要素 | 実体 | 更新方法 |
|---|---|---|
| ゲームサーバー | `python -m cucco.server.app`(このマシンで常駐) | **手動で再起動が必要** |
| ブラウザクライアント | GitHub Pages(`clients/`をActionsが自動公開) | mainにpushすれば自動反映 |
| 対局データ | `data/`(SQLite+行動ログ) | サーバーが自動書き込み |

> [!IMPORTANT]
> クライアントはpushで自動更新されるが、**サーバーは再起動するまで古いコードの
> まま動き続ける**。サーバー側の修正(AIの挙動・管理機能・GC・プロトコル)を
> 反映するには §7 の手順で再起動すること。

## 2. セットアップ

Python 3.11+ が必要。初回のみ:

```bash
cd ~/cucco-server          # リポジトリの場所
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest                     # 全テストが通ることを確認
```

## 3. 起動

### 3.1 基本

```bash
source .venv/bin/activate
python -m cucco.server.app
```

`ws://0.0.0.0:8765` で待ち受ける。フォアグラウンド起動なので、そのターミナル
を閉じるとサーバーも止まる(常駐させる場合は §3.4)。

### 3.2 起動オプション

| オプション | 既定値 | 説明 |
|---|---|---|
| `--host` | `0.0.0.0` | 待ち受けアドレス。`127.0.0.1`にするとLAN直アクセスを塞ぎ、トンネル経由のみに絞れる |
| `--port` | `8765` | ゲームポート(対局に必須) |
| `--data-dir` | `data` | 成績DBと行動ログの保存先 |
| `--admin-port` | `8766` | 管理リスナー。**アドレスは127.0.0.1固定**。`0`で無効化 |
| `--admin-token` | 毎回自動生成 | 管理トークン。省略すると起動ログにのみ出力される |
| `--gc-interval` | `60`(秒) | 放置卓の自動掃除の間隔。`0`で無効化 |

### 3.3 推奨する起動コマンド(仮運用)

管理トークンを固定し、ログをファイルに残す形:

```bash
source .venv/bin/activate
python -m cucco.server.app --admin-token 好きな文字列 >> ~/cucco-server.log 2>&1
```

管理CLIを使わないなら `--admin-port 0` を付けて管理ポートごと閉じてよい。

### 3.4 常駐させる

ターミナルを閉じても動かし続ける場合:

```bash
cd ~/cucco-server && source .venv/bin/activate
nohup python -m cucco.server.app --admin-token 好きな文字列 >> ~/cucco-server.log 2>&1 &
```

`tmux`/`screen` のセッション内で起動しておくと、後から画面に戻れて便利。
macOSの`launchd`による自動起動はこのリポジトリでは未設定(必要になったら
plistを別途用意する)。

## 4. ログ

**サーバーはログファイルを自分では作らない。** ログ(`logging.basicConfig`)は
**標準エラー出力**に出るだけなので、残したい場合は §3.3 のようにリダイレクト
する。ローテーションも無いので、長期運用ではログファイルの肥大に注意。

起動時に出る主なログ:

```
INFO:cucco.server.admin:admin listener on ws://127.0.0.1:8766 (local only ...)
INFO:cucco.server:admin token (this run only): 3f2a...      ← --admin-token 未指定時のみ
INFO:cucco.server:table GC sweeping every 60s
INFO:cucco.server:cucco-server listening on ws://0.0.0.0:8765
```

運用中に出る主なログ: `GC removed abandoned table <卓ID>`、
`admin aborted table <卓ID>`、`received SIGTERM, shutting down`、
例外時の `TableRunner crashed for table <卓ID>` + トレースバック。

> [!NOTE]
> `--admin-token` を指定せずログも残していないと、管理CLIのトークンが分から
> なくなる。仮運用ではトークンを固定するのが楽。

## 5. 停止

`SIGTERM`(通常の`kill`)または`SIGINT`(Ctrl-C)で安全に停止する。どちらも
リスナーを閉じ、GCタスクを止め、接続中のクライアントにWebSocketの
going-awayフレームを送る(クライアントは「再接続中」表示になり、再起動後は
`session_token`で復帰できる)。

```bash
pkill -f cucco.server.app          # -f は必須
# または: kill $(pgrep -f cucco.server.app)
```

- **`pkill` に `-f` を付けないと効かない**。付けないとプロセス名(`python…`)
  としか照合せず、引数の`cucco.server.app`を見ないため何もマッチしない
- `kill -9`(SIGKILL)は不要。終了処理をスキップするだけ
- **記録済みの成績は失われない**(対局終了時にSQLiteへcommit済み)。失われる
  のは進行中の対局のみ。可能なら参加者に一声かけてから止める

停止確認:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN   # 何も出なければ停止済み
```

## 6. 稼働中の管理

### 6.1 卓の確認・強制終了(管理CLI)

管理リスナーは`127.0.0.1`のみで待ち受ける。**同じマシン上で**実行する:

```bash
python -m cucco.tools.admin --token <トークン> list           # 全卓の一覧
python -m cucco.tools.admin --token <トークン> status <卓ID>  # 卓の詳細(手札は含まれない)
python -m cucco.tools.admin --token <トークン> abort <卓ID>   # 進行中ゲームを強制終了して卓を閉じる
python -m cucco.tools.admin --token <トークン> remove <卓ID>  # ゲームの走っていない卓を削除
```

`abort`すると参加者には通常の`game_ended`(現在チップによる順位)が届き、
内蔵AIのタスクも停止する。

> [!WARNING]
> 管理ポートは**絶対にCloudflare Tunnelに載せない**。公開するのはゲームポート
> (8765)のみ(`docs/security-notes.md`「管理リスナー」)。

### 6.2 放置卓の自動掃除(GC)

サーバーは既定で60秒ごとに放置卓を自動削除する。手動のabort/removeは即時
掃除として併用できる。

| 対象 | しきい値 |
|---|---|
| 実クライアント(人間・観戦者・外部AI)が誰も接続していない卓 | 10分 |
| クラッシュ/終了して放置された卓 | 5分(無操作) |

判定が「無操作時間」ではなく「実クライアント不在の継続時間」なのは、**内蔵AI
だけで連戦し続ける卓は常時ブロードキャストしていて無操作時間が増えない**ため。
観戦者がタブを閉じたAI卓もこれで掃除される。

### 6.3 成績の確認

読み取り専用なので稼働中に実行してよい:

```bash
python -m cucco.tools.stats                  # プレイヤー別の通算成績
python -m cucco.tools.stats --policies       # 内蔵AI方策別の成績
python -m cucco.tools.stats --player 名前     # 個人の直近ゲーム
python -m cucco.tools.stats --recent 5       # 直近5ゲームの結果
python -m cucco.tools.stats --evaluations    # 評価モードの実行一覧
```

## 7. バージョンアップ(サーバー再起動)

```bash
cd ~/cucco-server
pkill -f cucco.server.app                 # 1. 止める(対局が無い時間帯に)
git pull                                  # 2. 最新を取得
source .venv/bin/activate
uv pip install -e ".[dev]"                # 3. 依存が変わっていれば更新
pytest -q                                 # 4. テストが通ることを確認
nohup python -m cucco.server.app --admin-token 好きな文字列 >> ~/cucco-server.log 2>&1 &
```

DBのスキーマ変更は起動時に自動マイグレーションされる(既存の`results.db`を
そのまま使える)。

## 8. データの管理

| パス | 内容 |
|---|---|
| `data/results.db` | SQLite。ゲームごとの成績(参加者・順位・チップ・内蔵AIの方策名)と評価モードの集計 |
| `data/action_logs/*.jsonl` | ゲームごとの行動ログ+シャッフルシード(決定論的リプレイ用) |

- `data/`は`.gitignore`済み。**表示名にゼミ生の実名が含まれるため、成績の
  出力やDBを公開リポジトリにコミットしないこと**
- 行動ログは1ゲーム1ファイルで増え続け、自動削除もローテーションも無い
  (現在4000ファイル超)。ディスクを圧迫する前に、古いものを別の場所へ
  退避するか削除する運用を検討する
- バックアップはサーバー停止中に`data/`をコピーするのが最も確実

## 9. 主なタイムアウト・上限(参考)

| 項目 | 値 | 変更方法 |
|---|---|---|
| 手番タイムアウト | 人間30秒 / AI10秒 | 卓作成時(`create_table`)に卓ごと指定 |
| 効果宣言ウィンドウ | 人間10秒 / AI2秒 | 同上 |
| 結果確認の待機 | 既定0秒(クライアントのフォームでは15秒) | 同上 |
| ゲーム開始の安全弁 | 最初の`ready`から10分で自動開始 | コード定数 |
| 再接続の猶予 | 60秒(接続2人未満で待つ時間) | コード定数 |
| 卓の人数上限 | プレイヤー15人 / 観戦者30人 | コード定数 |

## 10. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| `pkill`でサーバーが止まらない | `-f`を付けていない。`pkill -f cucco.server.app` |
| 起動時に`Address already in use` | 旧プロセスが残っている。`lsof -nP -iTCP:8765 -sTCP:LISTEN`でPIDを確認して停止 |
| 管理トークンが分からない | 起動ログを確認。残していなければ`--admin-token`を指定して再起動 |
| 管理CLIが繋がらない | 別マシンから実行していないか(127.0.0.1限定)。`--admin-port 0`で無効化していないか |
| 卓が消えずに残る | 通常はGCが10分で掃除する。急ぐなら管理CLIの`abort`/`remove` |
| クライアントが接続できない | ①サーバー稼働中か ②`--host 127.0.0.1`にしていないか ③クライアントの接続先(`?ws=host:port`または「接続先を変更」)が正しいか ④トンネルURLが変わっていないか |
| AIの新しい挙動が反映されない | サーバーを再起動していない(§7)。クライアント側の変更はPagesで自動反映される |
| 特定の卓だけ進まない | 管理CLIの`status`で接続状況を確認し、必要なら`abort` |
| ログにトレースバック(`TableRunner crashed`) | その卓は停止する。卓IDを控えて`abort`し、再現手順が分かれば報告 |

## 11. 運用チェックリスト

**対局セッションの前**
- [ ] 最新版で起動しているか(§7)
- [ ] 起動ログに`listening`が出ているか / 管理トークンを控えたか
- [ ] トンネル公開する場合、URLを参加者に共有したか(`docs/web-client-operations.md` §5)

**セッション中**
- [ ] 進行が止まった卓がないか(`admin list`の無操作時間)

**セッションの後**
- [ ] 成績を確認(`cucco.tools.stats`)。**出力はリポジトリにコミットしない**
- [ ] 長期停止するなら安全に停止(§5)、`data/`のバックアップ

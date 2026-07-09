# Cucco ブラウザクライアント

人間プレイヤー向けのブラウザUI。ビルドツールなし、素のHTML/CSS/ES Modules。
プロトコルは`docs/protocol/design.md`・`docs/human-client-guide.md`を参照。

## 起動方法

1. サーバーを起動する(リポジトリルートで):
   ```bash
   source .venv/bin/activate
   python -m cucco.server.app
   ```
2. 別ターミナルで、このディレクトリを静的ファイルサーバーとして配信する
   (ES Modulesは`file://`から直接読み込めないため必須):
   ```bash
   cd clients/web
   python -m http.server 8000
   ```
3. ブラウザで `http://localhost:8000` を開く

デフォルトの接続先は `ws://<ページを開いたホスト名>:8765` (サーバーの既定ポート)。
別ホスト/ポートのサーバーに繋ぐ場合(クライアントとサーバーを別ドメインで
ホストする構成など)は、`?ws=host[:port]` クエリパラメータ付きのURLを開く
(初回だけでよく、`localStorage`に保存されてURLからは自動的に取り除かれる)か、
名前入力画面下部の「接続先を変更」から設定する。学外公開しての試験運用
(Cloudflare Pages + trycloudflare.com)の具体的な手順は
`docs/web-client-operations.md`を参照。

## 動作確認

同一サーバーに対して人間タブ2〜3枚+`clients/mock_ai`のAIクライアントを混ぜて
卓を作成・参加させ、1ポット分プレイして確認する。

```bash
python -m clients.mock_ai.mock_ai --name Bot --room <プレイルームID> --policy matrix
```

## 既知の制約(初期実装のスコープ外)

- 成績・統計の閲覧UI、評価モード(`mode: "evaluation"`)専用の閲覧ダッシュボードは
  未実装(観戦は通常の観戦ビューが動作する程度)
- 「現在の手番」表示は、宣言イベント(`no_change_declared`/`exchange_result`等)
  から推測したベストエフォートの表示(`src/deriveTurn.js`参照)。サーバーは
  自分の手番かどうかを`turn_prompt`で直接個別通知するため、入力操作自体は
  この推測に依存しない
- ページをリロードして再接続した直後、`your_hand`が`null`の場合に「まだ配られて
  いない」か「そのディールで失格した」かの区別は、リロード前からの継続セッション
  でのみ正確に働く(`player_disqualified`受信をローカルに保持しているため)。
  リロードをまたいだ場合は「次のディールを待っています」表示にフォールバックする

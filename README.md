# cucco-server

伝統的なトリックテイキング/賭博カードゲーム「Cucco」(グランペール版、通称
Cambio)のネット対戦サーバー。ゲーム研究ゼミ内での運用を想定し、人間・AI
プレイヤーの両方が同じWebSocketプロトコルで対局できる。

## 特徴

- **ルールエンジン**: 22種×2枚(44枚)のカード、特殊札(道化・人間・馬・猫・家・
  クク)の連鎖・失格・拒否ロジックを含む完全なドメイン層。I/Oを一切含まない
  純粋な同期コードで、テストが容易([`docs/rules/final_rules.md`](docs/rules/final_rules.md))
- **WebSocket/JSONプロトコル**: 人間・AIどちらのクライアントも同一プロトコルで
  接続([`docs/protocol/design.md`](docs/protocol/design.md))
- **AI専用高速評価モード**: `game_count`回のゲームを座席ローテーションしながら
  自動連続実行し、勝率・平均順位・失格率などの集計結果を配信
- **永続化と成績確認**: ゲーム終了ごとの成績をSQLiteに記録(内蔵AIは方策名
  付き)。決定論的リプレイ用にシャッフルシード+行動ログをJSON Linesで保存。
  `python -m cucco.tools.stats`で通算成績(名前別・方策別)・直近ゲーム・
  評価モードの実行一覧を表形式で確認できる
- **卓ごとの細かいルール設定**: 終了条件、特殊札ごとの失格カード開示タイミング、
  馬/家の開示可否などを`create_table`時に卓単位で選択可能
- **サーバー内蔵AIプレイヤー**: 卓作成時に`ai_players`で方策と人数を指定する
  だけで、別プロセスなしにAI対戦相手を同席させられる(AI vs AI、AI vs 人間)。
  方策実装は外部Mock AIクライアントと共通(`src/cucco/ai/`)。基本3方策に加え、
  捨て札・公開札のカウンティングで判断する強化版2種
  (`counting_aggressive`積極型 / `counting_conservative`堅実型)を同梱
- **サンプルクライアント**: すぐ動かせるMock AI(3方策)と対話式Stubクライアント
  ([`clients/`](clients/))
- **人間向けブラウザクライアント(2種)**: ビルドツール不要の素のHTML/CSS/
  ES Modules。卓の作成・参加者募集から対局・観戦・連戦まで一通り動作する。
  プロトコル全機能の最小実装リファレンス([`clients/web/`](clients/web/))と、
  卓を囲むレイアウト+カード/チップのアニメーション・特殊札の発動演出・
  効果音(Web Audio API合成、ON/OFF切替可)・カード効果一覧パネルで
  実際のゲームプレイに近づけたプレイ用クライアント
  ([`clients/play/`](clients/play/))の2つを同一サーバー・同一卓で
  相互に利用できる
- **卓ルールの拡張選択肢**: 結果確認の待機(全員確認でスキップ可能)、
  特殊札(道化を除く)の効果をクク同様に能動的な宣言制にする「宣言式」
  ルール、着席順・最初の親のランダム化、卓作成者の切断時の主催者引き継ぎ
  など、実運用で見えた要望に対応

## 状態

ドメイン層・プロトコル層・サーバー層・永続化層・評価モード・サンプル
クライアント・人間向けブラウザクライアント2種(リファレンス+効果音・
カード演出付きプレイ用)まで実装済み、テスト314件全てパス。複数回のレビュー(Fable5)を経て主要な
不具合は修正済み。GitHub Pages + Cloudflare Tunnelでの学外公開運用まで
実績あり。成績確認はCLI(`cucco.tools.stats`)で可能。サーバー管理機能
(卓の一覧・停止卓の中止)は今後の課題。

## 公開クライアント(運用例)

ブラウザクライアント(リファレンス+プレイ用)はGitHub Pagesで公開している。

**https://kobashi.github.io/cucco-server/**

> [!NOTE]
> これは**クライアント(静的ファイル)のみ**のホスティングであり、対局に必要な
> WebSocketゲームサーバー(`cucco.server.app`)は含まれない。実際に対局するには、
> 上記URLとは別に**各自でサーバーを用意し起動する必要がある**(下記クイック
> スタート、または`docs/web-client-operations.md`の学外公開手順を参照)。
> サーバー起動後、クライアント画面の「接続先を変更」または`?ws=host:port`
> パラメータで接続先を指定する。

## クイックスタート

```bash
# Python 3.11+ が必要。uv (https://docs.astral.sh/uv/) を使う場合:
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# 標準のvenv/pipでも可:
# python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"

# テスト実行
pytest

# サーバー起動 (ws://0.0.0.0:8765)
python -m cucco.server.app
```

別ターミナルでMock AIを2体対戦させる例:

```bash
source .venv/bin/activate
python -m clients.mock_ai.mock_ai --name Alice --create --policy matrix
# 表示された room_id を使って2体目を参加させる
python -m clients.mock_ai.mock_ai --name Bob --room <room_id> --policy always_change
```

対話式に1人で動作確認したい場合は`clients/stub/stub_client.py`を使う
(`docs/ai-client-guide.md` §4のメッセージフロー例と突き合わせて確認できる)。

## プロジェクト構成

```
src/cucco/
  domain/       # ルールエンジン(純粋・同期・I/Oなし)
  ai/           # AI方策+ボット頭脳(内蔵ボットとMock AIクライアントで共用)
  protocol/     # ワイヤーフォーマット(エンベロープ・アクション・イベント変換)
  server/       # asyncio/WebSocketネットワーキング
  persistence/  # SQLite成績記録 + JSON Lines行動ログ + 集計クエリ層
  tools/        # 運用者向けCLI(成績確認 python -m cucco.tools.stats)
  evaluation/   # AI専用高速評価モード(game_countループ・座席ローテーション)
clients/
  common/       # Pythonクライアント共通のWebSocketラッパー
  mock_ai/      # 自動対局クライアント(方策プラガブル)
  stub/         # 対話式ターミナルクライアント
  index.html    # ブラウザクライアント選択のランディングページ
  web-common/   # ブラウザクライアント共有モジュール(プロトコル層)
  web/          # 人間向けブラウザクライアント(最小実装リファレンス)
  play/         # プレイ体験重視のリッチクライアント(アニメーション対応)
tests/          # unit/ + integration/(実WebSocket経由の結合テスト)
docs/           # 要求仕様・ルール・プロトコル設計・実装ガイド類
```

## ドキュメント

- [`docs/requirements.md`](docs/requirements.md) — 要求仕様書(全体像はここから)
- [`docs/rules/final_rules.md`](docs/rules/final_rules.md) — 確定したゲームルール
- [`docs/protocol/design.md`](docs/protocol/design.md) — 通信プロトコル設計
- [`docs/protocol/decisions.md`](docs/protocol/decisions.md) — プロトコル設計の決定事項・変更履歴
- [`docs/ai-client-guide.md`](docs/ai-client-guide.md) — AIプレイヤー実装ガイド
- [`docs/ai-advanced-policies.md`](docs/ai-advanced-policies.md) — 上位AI実装案集(教材)
- [`docs/human-client-guide.md`](docs/human-client-guide.md) — 人間向けUI実装ガイド
- [`docs/web-client-operations.md`](docs/web-client-operations.md) — ブラウザクライアント操作手順書
- [`docs/security-notes.md`](docs/security-notes.md) — セキュリティ運用メモ(公開運用時の残存リスク)

## 開発方針

- 言語: Python 3.11+
- 通信: WebSocket / JSON
- 公開: Git管理、GitHubで公開
- 運用想定: 開発当初はiMac上でローカル運用、安定後にCloudflare Tunnel経由で限定公開

## 変更履歴

[CHANGELOG.md](CHANGELOG.md)

## ライセンス

[MIT License](LICENSE)

# cucco-server

カードゲーム「Cucco」のネット対戦サーバー。

## 概要

- ゲーム研究ゼミ内での運用を想定したCucco対戦サーバー
- プレイヤーは人間・AIの両方に対応(AIは確定した通信プロトコルに従って対局する)
- 開発当初はiMac上でローカル運用、安定後にCloudflare経由で限定公開

## 状態

要求仕様・ゲームルール・通信プロトコル設計が確定。実装はこれから。

## ドキュメント

- [`docs/requirements.md`](docs/requirements.md) — 要求仕様書(全体像はここから)
- [`docs/rules/final_rules.md`](docs/rules/final_rules.md) — 確定したゲームルール
- [`docs/protocol/design.md`](docs/protocol/design.md) — 通信プロトコル設計
- [`docs/ai-client-guide.md`](docs/ai-client-guide.md) — AIプレイヤー実装ガイド

## 開発方針

- 言語: Python
- 通信: WebSocket / JSON
- 公開: Git管理、GitHubで公開

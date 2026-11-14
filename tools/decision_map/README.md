# 判断の地図: どこで勝率・順位を落としているかを測る

方策の判断を**1つだけ反転**し、そのポットの決着まで打たせて、最終1位の確率と
期待順位がどう動くかを測る道具。`counting_win` / `counting_rank` の数字は
ここから出した(docs/ai-policy-reference.md §6)。

研究用のスクリプトで、サーバーからは使われない。`fit.py` だけ numpy が要る。

```bash
cd tools/decision_map
# 1) ポットの切れ目のチップ状況と最終順位を集める(相手は基準の4卓を巡回)
python collect.py 120000 vdata.pkl counting_sniper
# 2) 価値関数を当てはめる: チップ状況 -> 最終1位の確率 / 期待順位
uv run --with numpy python fit.py vdata.pkl v.json
# 3) 判断を反転して差を測る(10万ゲームで約20分、80万判断)
python rollout.py 100000 dv.pkl v.json counting_sniper
# 4) 表にする
python report.py dv.pkl
```

## 読み方

- 値はすべて **(する − しない)**。W は勝率(pt)、R は期待順位(正なら上位)
- 「しきい値のずれ」は、交換のしきい値に足すべき量。0 に近ければ今の数字で合っている
- 価値関数は「以後は全員が今の方策で打つ」前提の近似。出力の calibration の行で
  予測と実際が合っているかを必ず見る

## 守ること

- **地図は候補を作る道具で、採用の根拠ではない。**作った候補は
  `tools/fast_eval.py --panel` で、**新しいシード**を使って確かめる
- 基準方策や相手を変えたら、1) から作り直す(価値関数も地図もその卓のもの)
- 重い走行は1本ずつ。並べると遅くなるだけでなく、他の評価走行を落とす

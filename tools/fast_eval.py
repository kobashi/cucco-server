"""方策の強さを**対比較**で速く測る。サーバーは要らない。

評価モードで勝率 0.3pt の差を見るには 20万ゲーム級が必要で、1走行7,000
ゲーム・15分では測れなかった(SE 0.5pt 前後)。ここでは2つを変えている。

1. **速さ** — `cucco.evaluation.fast` で、同じ対局を JSON も asyncio も通さず
   に回し、さらにコア数で並列化する
2. **対比較** — 同じシードで2回まわす。1回目は基準の方策、2回目は**同じ席**に
   候補の方策を座らせ、残りの席は同じ方策のままにする。札の配られ方が同じ
   なので、候補が基準と違う判断をしないゲームは結果が完全に一致し、差は0に
   なる。見るのは勝率そのものではなく**ゲームごとの差の平均**で、判断の違いが
   少ない候補ほど標準誤差が小さくなる

使い方:
    python tools/fast_eval.py --candidate counting_sniper            # 相手は基準×3
    python tools/fast_eval.py --candidate zemi_x --baseline matrix --opponents matrix,matrix,counting_conservative
    python tools/fast_eval.py --candidate counting_sniper --games 100000
    python tools/fast_eval.py --candidate counting_sniper --panel    # 基準の4卓を相手に測って平均
    python tools/fast_eval.py --verify 200                           # 本物の評価モードと突き合わせる

読み方: 「差」の行だけを根拠にすること。±の後ろは標準誤差で、z が 3 を
超えなければ「差は検出できない」と読む。**候補を並べて一番良いものを選んだ
数字は根拠にならない**(docs/ai-policy-reference.md §6)— 選んだ1つを、
`--seed` を変えて取り直すこと。

`--verify` は同じシードで本物の評価モード(内蔵ボット、プロセス内)と高速版を
回し、着席順と最終順位がゲームごとに一致するかを数える。進行(`TableRunner`)
や頭脳を変えたあとは、これを通してから数字を信じること。
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import itertools
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from cucco.ai.plugins import load_policy_dir
from cucco.ai.policies import POLICIES
from cucco.domain.config import GameConfig
from cucco.evaluation.fast import lineup, play_game

TARGET = "P0"  # 候補/基準を座らせる席。lineup() の先頭


def _config(chips: int) -> GameConfig:
    return GameConfig(mode="evaluation", game_count=1, starting_chips=chips)


_loaded_dirs: set[str] = set()


def _load_policies(policy_dir: str | None) -> None:
    # プロセスごとに1回だけ。並列の子プロセスは束ごとにここを通るので、
    # 2回目を読むと「その名前は既に使われている」で全部はじかれる。
    if policy_dir and policy_dir not in _loaded_dirs:
        _loaded_dirs.add(policy_dir)
        report = load_policy_dir(Path(policy_dir))
        for failure in report.failures:
            print(f"警告: {failure.source.name} を読めなかった: {failure.reason}", file=sys.stderr)


def _worker(args) -> list[tuple[int, int, int, int, bool]]:
    """シードの束を回し、(基準の順位, 基準のチップ, 候補の順位, 候補のチップ,
    全席の結果が一致したか) を返す。`candidate` が None なら基準だけ回す。"""
    baseline, candidate, opponents, chips, seeds, policy_dir = args
    _load_policies(policy_dir)
    config = _config(chips)
    base_ids, base_policy = lineup([baseline, *opponents])
    out = []
    for seed in seeds:
        b = play_game(base_ids, base_policy, config, seed)
        if candidate is None:
            out.append((b.rank_of(TARGET), b.chips_of(TARGET), 0, 0, True))
            continue
        cand_ids, cand_policy = lineup([candidate, *opponents])
        c = play_game(cand_ids, cand_policy, config, seed)
        out.append((b.rank_of(TARGET), b.chips_of(TARGET), c.rank_of(TARGET), c.chips_of(TARGET), b.ranking == c.ranking))
    return out


def _mean_se(values: list[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, float("nan")
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var / n)


def _fmt(mean: float, se: float, scale: float = 1.0, unit: str = "") -> str:
    z = mean / se if se and not math.isnan(se) and se > 0 else float("nan")
    return f"{mean * scale:+.3f}{unit} ± {se * scale:.3f}  (z={z:+.2f})"


def _opponents(args) -> list[str]:
    opponents = args.opponents.split(",") if args.opponents else [args.baseline] * 3
    for name in [args.baseline, args.candidate, *opponents]:
        if name is not None and name not in POLICIES:
            sys.exit(f"方策 {name!r} が無い。使えるもの: {sorted(POLICIES)}")
    return opponents


# 強さの基準にする相手の卓。相手を1種類に固定すると「その相手にだけ効く」
# 変更を強化と取り違える(conservative×3 相手に +0.8pt だった変更が、混成卓と
# matrix×3 相手では 0 だった)。`--panel` はこの4卓を同じ数ずつ回して平均する。
PANEL: list[tuple[str, list[str]]] = [
    ("conservative×3", ["counting_conservative"] * 3),
    ("混成", ["matrix", "counting_aggressive", "counting_conservative"]),
    ("matrix×3", ["matrix"] * 3),
    ("aggressive×3", ["counting_aggressive"] * 3),
]


def _measure(args, opponents: list[str], seeds: list[int]) -> tuple[list[tuple[int, int, int, int, bool]], float]:
    jobs = max(1, args.jobs)
    chunk = max(1, min(500, math.ceil(len(seeds) / (jobs * 4))))
    chunks = [seeds[i : i + chunk] for i in range(0, len(seeds), chunk)]
    tasks = [(args.baseline, args.candidate, opponents, args.chips, c, args.policy_dir) for c in chunks]

    started = time.time()
    rows: list[tuple[int, int, int, int, bool]] = []
    if jobs == 1:
        for task in tasks:
            rows += _worker(task)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for part in pool.map(_worker, tasks):
                rows += part
    return rows, time.time() - started


def _diffs(rows) -> tuple[list[float], list[float]]:
    """ゲームごとの (勝ちの差, 順位の差)。どちらも正なら候補が良い。"""
    d_win = [(1.0 if r[2] == 1 else 0.0) - (1.0 if r[0] == 1 else 0.0) for r in rows]
    d_rank = [float(r[0] - r[2]) for r in rows]
    return d_win, d_rank


def run_panel(args) -> None:
    if not args.candidate:
        sys.exit("--panel には --candidate が要る")
    _opponents(args)
    print(f"[{args.baseline} → {args.candidate}]  相手の卓ごとに {args.games:,} シード  /  初期チップ {args.chips}")
    print(f"\n{'相手の卓':<16}{'勝率差(pt)':>22}{'平均順位差':>26}")
    all_win: list[float] = []
    all_rank: list[float] = []
    for i, (label, opponents) in enumerate(PANEL):
        seeds = list(range(args.seed + i * args.games, args.seed + (i + 1) * args.games))
        rows, _ = _measure(args, opponents, seeds)
        d_win, d_rank = _diffs(rows)
        all_win += d_win
        all_rank += d_rank
        print(f"{label:<16}{_fmt(*_mean_se(d_win), scale=100):>26}{_fmt(*_mean_se(d_rank)):>28}", flush=True)
    print(f"{'4卓の平均':<16}{_fmt(*_mean_se(all_win), scale=100):>26}{_fmt(*_mean_se(all_rank)):>28}")
    print("\n差は 候補 − 基準(同じシード・同じ席)。平均順位差は正なら候補が上位。")


def run_paired(args) -> None:
    opponents = _opponents(args)
    seeds = list(range(args.seed, args.seed + args.games))
    jobs = max(1, args.jobs)
    rows, elapsed = _measure(args, opponents, seeds)

    n = len(rows)
    played = n * (2 if args.candidate else 1)
    print(f"卓: [{args.baseline} → {args.candidate or '(差し替えなし)'}] 対 {', '.join(opponents)}  /  初期チップ {args.chips}")
    print(f"{n:,} シード(対局 {played:,})  {elapsed:.1f}秒  {played / elapsed:,.0f} ゲーム/秒  ({jobs} 並列)")

    b_win, b_se = _mean_se([1.0 if r[0] == 1 else 0.0 for r in rows])
    b_rank, _ = _mean_se([float(r[0]) for r in rows])
    print(f"\n基準 {args.baseline}: 勝率 {b_win * 100:.2f}% ± {b_se * 100:.2f}  平均順位 {b_rank:.3f}")
    if not args.candidate:
        return
    c_win, c_se = _mean_se([1.0 if r[2] == 1 else 0.0 for r in rows])
    c_rank, _ = _mean_se([float(r[2]) for r in rows])
    print(f"候補 {args.candidate}: 勝率 {c_win * 100:.2f}% ± {c_se * 100:.2f}  平均順位 {c_rank:.3f}")

    d_win, d_rank = _diffs(rows)
    d_chips = [float(r[3] - r[1]) for r in rows]
    same = sum(1 for r in rows if r[4]) / n
    unpaired_se = math.sqrt(b_se**2 + c_se**2)
    paired_se = _mean_se(d_win)[1]
    print("\n差(候補 − 基準、同じシード同士):")
    print(f"  勝率      {_fmt(*_mean_se(d_win), scale=100, unit='pt')}")
    print(f"  平均順位  {_fmt(*_mean_se(d_rank))}   正なら候補が上位")
    print(f"  最終チップ {_fmt(*_mean_se(d_chips))}")
    print(f"\n結果が全席一致したゲーム: {same * 100:.1f}%")
    if paired_se > 0:
        print(
            f"対比較の効き: 勝率差の SE {paired_se * 100:.3f}pt "
            f"(対にしない場合 {unpaired_se * 100:.3f}pt、ゲーム数換算で {(unpaired_se / paired_se) ** 2:.1f}倍)"
        )


# -- 本物の評価モードとの突き合わせ -------------------------------------------


async def _real_run(policies: list[str], chips: int, seeds: list[int]):
    """内蔵ボットの評価モードをプロセス内で回し、ゲームごとの着席順と順位を拾う。"""
    import cucco.evaluation.runner as runner_mod
    import cucco.server.dispatch as dispatch_mod
    from cucco.evaluation.runner import EvaluationRunner
    from cucco.protocol.envelope import build_envelope
    from cucco.server.dispatch import ConnectionHandler
    from cucco.server.registry import TableRegistry

    games = []
    captured = {}

    class RecordingGame(runner_mod.Game):
        def __init__(self, seats, config, rng):
            super().__init__(seats, config, rng)
            games.append(self)

    class RecordingRunner(EvaluationRunner):
        def __init__(self, table, participants, **kwargs):
            captured["participants"] = list(participants)
            captured["table"] = table
            super().__init__(table, participants, **kwargs)

    seed_iter = iter(seeds)
    originals = (runner_mod.Game, dispatch_mod.EvaluationRunner)
    runner_mod.Game = RecordingGame
    dispatch_mod.EvaluationRunner = functools.partial(RecordingRunner, seed_source=lambda: next(seed_iter))
    try:

        class Sink:
            summary = None

            async def send(self, message: str) -> None:
                data = json.loads(message)
                if data["type"] == "evaluation_summary":
                    self.summary = data["payload"]

        registry, sink = TableRegistry(), Sink()
        handler = ConnectionHandler(sink, registry)
        await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
        counts = [(p, len(list(g))) for p, g in itertools.groupby(policies)]
        await handler.handle_message(
            build_envelope(
                "create_table",
                {
                    "mode": "evaluation",
                    "game_count": len(seeds),
                    "starting_chips": chips,
                    "ai_players": [{"policy": p, "count": n} for p, n in counts],
                },
            )
        )
        room_id = next(iter(registry._tables))
        await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
        while sink.summary is None:
            await asyncio.sleep(0.02)
    finally:
        runner_mod.Game, dispatch_mod.EvaluationRunner = originals

    table = captured["table"]
    policy_of = {pid: table.get(pid).ai_policy for pid in captured["participants"]}
    return captured["participants"], policy_of, games


def run_verify(args) -> None:
    opponents = _opponents(args)
    policies = [args.candidate or args.baseline, *opponents]
    seeds = list(range(args.seed, args.seed + args.verify))
    config = _config(args.chips)

    started = time.time()
    participants, policy_of, games = asyncio.run(_real_run(policies, args.chips, seeds))
    real_sec = time.time() - started

    started = time.time()
    mismatches = []
    for seed, real in zip(seeds, games):
        fast = play_game(participants, policy_of, config, seed)
        if tuple(real.seats) != fast.seats or tuple(real.final_ranking) != fast.ranking or real.round_count != fast.deals:
            mismatches.append((seed, real.round_count, fast.deals))
    fast_sec = time.time() - started

    n = len(seeds)
    print(f"卓: {', '.join(policies)}  /  初期チップ {args.chips}  /  {n} ゲーム")
    print(f"評価モード {real_sec:.1f}秒({n / real_sec:.1f} ゲーム/秒)  高速版 {fast_sec:.1f}秒({n / fast_sec:.1f} ゲーム/秒)")
    print(f"着席順・最終順位・ディール数が一致: {n - len(mismatches)} / {n}")
    for seed, real_deals, fast_deals in mismatches[:10]:
        print(f"  不一致 seed={seed}: ディール数 評価モード {real_deals} / 高速版 {fast_deals}")
    if mismatches:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--candidate", help="測りたい方策(省略すると基準だけを回す)")
    parser.add_argument("--baseline", default="counting_conservative", help="比べる相手。同じ席に座らせる")
    parser.add_argument("--opponents", help="残りの席の方策をカンマ区切りで(既定: 基準×3)")
    parser.add_argument("--games", type=int, default=20000, help="シードの数。対比較では対局はこの2倍")
    parser.add_argument("--seed", type=int, default=1, help="最初のシード。取り直すときは変える")
    parser.add_argument("--chips", type=int, default=25, help="初期チップ")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--policy-dir", default="policies", help="追加の方策を読む場所")
    parser.add_argument("--panel", action="store_true", help="基準の4卓を相手に測って平均する(--opponents は無視)")
    parser.add_argument("--verify", type=int, metavar="N", help="N ゲームを本物の評価モードと突き合わせる")
    args = parser.parse_args()

    _load_policies(args.policy_dir)
    if args.verify:
        run_verify(args)
    elif args.panel:
        run_panel(args)
    else:
        run_paired(args)


if __name__ == "__main__":
    main()

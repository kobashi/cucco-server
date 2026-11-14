"""高速評価器(cucco.evaluation.fast)は、評価モードと同じ対局を再現する。

高速版は `TableRunner` の進行を同期で書き直したもの。片方だけ直すと、
速いが別のゲームを測る道具になる — それを数字からは見分けられないので、
同じシードで本物と突き合わせて、ずれたらここで落とす。
"""

import asyncio
import importlib.util
from pathlib import Path

from cucco.domain.config import GameConfig
from cucco.evaluation.fast import lineup, play_game

_TOOL = Path(__file__).resolve().parents[3] / "tools" / "fast_eval.py"
_spec = importlib.util.spec_from_file_location("fast_eval_tool", _TOOL)
fast_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fast_eval)

# クク宣言の3通り(プロンプトでだけ宣言・持った瞬間に宣言・待つ)が1卓に揃う並び。
POLICIES = ["counting_sniper", "matrix", "counting_conservative", "always_change"]
CHIPS = 5  # 短いゲームで数を稼ぐ
CONFIG = GameConfig(mode="evaluation", game_count=1, starting_chips=CHIPS)


def test_same_seed_reproduces_the_evaluation_mode_game():
    seeds = list(range(4242, 4242 + 60))
    participants, policy_of, games = asyncio.run(fast_eval._real_run(POLICIES, CHIPS, seeds))
    assert len(games) == len(seeds)
    for seed, real in zip(seeds, games):
        fast = play_game(participants, policy_of, CONFIG, seed)
        assert fast.seats == tuple(real.seats), seed
        assert fast.ranking == tuple(real.final_ranking), seed
        assert fast.deals == real.round_count, seed


def test_swapping_in_the_same_policy_changes_nothing():
    """対比較の前提: 席を「差し替えた」先が同じ方策なら、結果は完全に同じ。"""
    ids, policy_of = lineup(POLICIES)
    for seed in range(20):
        assert play_game(ids, policy_of, CONFIG, seed) == play_game(ids, dict(policy_of), CONFIG, seed)

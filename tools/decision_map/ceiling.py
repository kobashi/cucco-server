"""天井の測定: 見えないはずの情報を見せた「カンニングAI」が、counting_win にどれだけ勝つか。

usage: ceiling.py N n_games MODE [seed0] [相手(カンマ区切り、N-1 席ぶん)] [方策を読むディレクトリ]

MODE(どれも交換の判断だけを差し替える。継続とクク宣言は counting_win のまま):
  hands   全員の手札が見える
  acted   もう動いた席の手札だけ見える     … 動きからの推論が完璧だった場合の上限
  behind  まだ動いていない席の手札だけ見える … 手がかりのない部分。運
  model   手札は見えないが、各席の「この札なら持つ」の基準を正確に知っている
                                           … 相手の打ち方を完璧に学んだ場合の上限
  deal    持つ/替えるの両方を最後まで打たせて、このディールで負けないほうを選ぶ
          (山札の並びと後ろの席の動きまで見えている。届かない上限)

hands / acted / behind / model は、決め方(負ける確率の差が CHANGE_MARGIN 以上なら
交換)を変えずに、入力の情報だけを差し替える。相手は既定で混成卓(lab.panel(N)[0])。
相手を指定すれば、打ち方の違う卓での「適応の伸びしろ」(model)が測れる。
"""

import dataclasses
import math
import sys
from concurrent.futures import ProcessPoolExecutor

from cucco.ai import policies as pol
from cucco.ai.context import strength_of
from cucco.evaluation.fast import lineup, play_game
from lab import CFG, panel

N = int(sys.argv[1])
MODE = sys.argv[3]
SEED0 = int(sys.argv[4]) if len(sys.argv) > 4 else 97_000_000
assert MODE in ("hands", "acted", "behind", "model", "deal"), MODE
ME = "P0"
if len(sys.argv) > 6:
    from pathlib import Path

    from cucco.ai.plugins import load_policy_dir

    load_policy_dir(Path(sys.argv[6]))
OPP = sys.argv[5].split(",") if len(sys.argv) > 5 else panel(N)[0]
assert len(OPP) == N - 1, OPP
ALWAYS = {r.value for r in pol.ALWAYS_CHANGE_RANKS}
RANKS = list(pol.FULL_DECK_COUNTS)
ST = {"game": None, "keep": {}, "stat": [0, 0], "inner": False, "script": None, "idx": 0, "fired": False, "out": None, "hist": None, "seed": None}


class Stop(Exception):
    pass


def wrap(base_name):
    """相手の方策。model のときだけ、判断のたびに「どの札なら持ったか」を書き残す。"""
    Base = pol.POLICIES[base_name]

    class Wrapped(Base):
        name = "w_" + base_name

        def decide_change_ctx(self, ctx):
            if MODE == "model":
                ST["keep"][ctx.my_id] = frozenset(
                    r for r in RANKS if not Base.decide_change_ctx(self, dataclasses.replace(ctx, own_rank=r))
                )
            return super().decide_change_ctx(ctx)

    pol.POLICIES[Wrapped.name] = Wrapped
    return Wrapped.name


WOPP = [wrap(p) for p in OPP]


class Cheat(pol.CountingWin):
    name = "cheat__"

    def decide_change_ctx(self, ctx):
        base = super().decide_change_ctx(ctx)
        if MODE == "deal":
            return self._by_outcome(ctx, base)
        if MODE == "model":
            choice = self._with_keep_sets(ctx, base)
        else:
            deal = ST["game"].current_pot.current_deal
            seen = dict(ctx.known_held)
            for pid, card in deal.hands.items():
                if pid == ctx.my_id or pid in deal.disqualified:
                    continue
                acted = pid in ctx.acted_this_deal
                if MODE == "hands" or (MODE == "acted") == acted:
                    seen[pid] = card.value
            choice = super().decide_change_ctx(dataclasses.replace(ctx, known_held=seen))
        ST["stat"][0] += 1
        ST["stat"][1] += choice != base
        return choice

    def _with_keep_sets(self, ctx, base):
        """各席の札の分布を、その席の本当の基準(持つ札の集合)で絞って見積もる。"""
        own = ctx.own_rank
        if own in pol.NEVER_CHANGE_RANKS or own not in pol.FULL_DECK_COUNTS or own in ALWAYS or ctx.unseen_total <= 0:
            return base
        total = ctx.unseen_total
        cards = sorted((strength_of(r), r, n) for r, n in ctx.unseen_counts.items() if n > 0)
        roles = ctx.seat_roles()
        known = {p: strength_of(ctx.known_held[p]) for p, r in roles.items() if r == "known"}
        dists = {}
        for pid, role in roles.items():
            if role == "known":
                continue
            keep = ST["keep"].get(pid)
            if role == "kept" and keep is not None:
                weights = [(st, n if r in keep else 0.0) for st, r, n in cards]
            elif role == "stuck" and keep is not None:
                weights = [(st, 0.0 if r in keep else n) for st, r, n in cards]
            else:
                weights = [(st, n) for st, r, n in cards]
            mass = sum(w for _, w in weights)
            if mass <= 0:
                weights = [(st, n) for st, r, n in cards]
                mass = sum(w for _, w in weights)
            dists[pid] = (weights, mass)

        def none_weaker(t, skip=None):
            if any(k < t for p, k in known.items() if p != skip):
                return 0.0
            out = 1.0
            for pid, (weights, mass) in dists.items():
                if pid != skip:
                    out *= 1.0 - sum(w for st, w in weights if st < t) / mass
            return out

        mine = strength_of(own)
        target = ctx.exchange_target
        if target is None:
            change = sum(n / total * none_weaker(st) for st, r, n in cards)
        elif target in known:
            change = 0.0 if known[target] >= mine else none_weaker(known[target], skip=target)
        else:
            change = sum(n / total * none_weaker(st, skip=target) for st, r, n in cards if st < mine)
        return none_weaker(mine) - change > self.CHANGE_MARGIN

    def _by_outcome(self, ctx, base):
        """同じシードを打ち直して、持つ/替えるの結果を両方見てから選ぶ。"""
        if ST["inner"]:
            i = ST["idx"]
            ST["idx"] += 1
            if i < len(ST["script"]):
                ST["fired"] = ST["fired"] or i == len(ST["script"]) - 1
                return ST["script"][i]
            return base
        choice = base
        if ctx.own_rank in pol.FULL_DECK_COUNTS and ctx.own_rank not in pol.NEVER_CHANGE_RANKS:
            keep, change = _replay(ST["hist"] + [False]), _replay(ST["hist"] + [True])
            if keep is not None and change is not None and keep != change:
                choice = change > keep
            ST["stat"][0] += 1
            ST["stat"][1] += choice != base
        ST["hist"].append(choice)
        return choice


pol.POLICIES["cheat__"] = Cheat
IDS, POLOF = lineup(["cheat__", *WOPP])
_, POLOF_BASE = lineup(["counting_win", *WOPP])


def _replay(script):
    """交換の判断を script のとおりに打たせ、最後の判断のディールで負けなければ 1。"""
    saved = (ST["script"], ST["idx"], ST["fired"], ST["out"])
    ST.update(inner=True, script=script, idx=0, fired=False, out=None)

    def observe(game, type_, payload):
        if ST["fired"] and type_ == "deal_result":
            ST["out"] = 0.0 if ME in payload["losers"] else 1.0
            raise Stop()

    try:
        play_game(IDS, POLOF, CFG, ST["seed"], observer=observe)
    except Stop:
        pass
    out = ST["out"]
    ST.update(inner=False, script=saved[0], idx=saved[1], fired=saved[2], out=saved[3])
    return out


def work(seeds):
    rows = []
    ST["stat"] = [0, 0]

    def observe(game, type_, payload):
        ST["game"] = game
        if type_ == "deal_started":
            ST["keep"].clear()

    for seed in seeds:
        base = [p for p, _ in play_game(IDS, POLOF_BASE, CFG, seed, observer=observe).ranking]
        ST.update(seed=seed, hist=[], inner=False)
        cheat = [p for p, _ in play_game(IDS, POLOF, CFG, seed, observer=None if MODE == "deal" else observe).ranking]
        rows.append((base[0] == ME, base.index(ME) + 1, cheat[0] == ME, cheat.index(ME) + 1))
    return rows, ST["stat"]


def _mean_se(xs):
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs)) / len(xs)


if __name__ == "__main__":
    n = int(sys.argv[2])
    seeds = list(range(SEED0, SEED0 + n))
    rows, stat = [], [0, 0]
    with ProcessPoolExecutor(8) as ex:
        for r, s in ex.map(work, [seeds[i : i + 20] for i in range(0, n, 20)]):
            rows += r
            stat = [stat[0] + s[0], stat[1] + s[1]]
    win, se = _mean_se([r[2] - r[0] for r in rows])
    rank, ser = _mean_se([r[1] - r[3] for r in rows])
    print(
        f"{N}人 {MODE:6s} {len(rows)}ゲーム  counting_win {100 * sum(r[0] for r in rows) / len(rows):.2f}%"
        f"  カンニング {100 * sum(r[2] for r in rows) / len(rows):.2f}%  差 {100 * win:+.2f}pt ±{100 * se:.2f}"
        f"  順位差 {rank:+.3f} ±{ser:.3f}  判断を変えた割合 {100 * stat[1] / max(stat[0], 1):.1f}%"
    )

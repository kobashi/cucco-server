"""usage: calibrate.py N n_games  — 推論つき見積もり p_someone_weaker を実際の手札と突き合わせる。"""
import math, sys, collections
from concurrent.futures import ProcessPoolExecutor
from cucco.ai import policies as pol
from cucco.ai.context import strength_of
from cucco.evaluation.fast import lineup, play_game
from lab import CFG
NP = int(sys.argv[1]); ST = {"game": None, "rows": None}
SPECIAL = set(pol.NEVER_CHANGE_RANKS) | {r.value for r in pol.ALWAYS_CHANGE_RANKS}
LEAKS = (0.0, 0.15, 0.3, 0.5)
def cut(ctx):
    tot = ctx.unseen_total
    danger = ((ctx.unseen_counts.get("人間", 0) + ctx.unseen_counts.get("猫", 0)) / tot) if tot else 0
    return 0.62 - 0.047 * ctx.alive_count - 0.5 * danger - (0.05 if ctx.is_child_time else 0)
class Probe(pol.CountingWin):
    name = "probe__"
    def decide_change_ctx(self, ctx):
        d = super().decide_change_ctx(ctx)
        own = ctx.own_rank
        if own in pol.FULL_DECK_COUNTS and own not in SPECIAL:
            deal = ST["game"].current_pot.current_deal; me = ctx.my_id; mine = strength_of(own)
            st = {p: strength_of(deal.hands[p].value) for p in deal.order if p not in deal.disqualified and p != me}
            roles = ctx.seat_roles(); c = cut(ctx)
            rc = collections.Counter(); rw = collections.Counter()
            for p, r in roles.items():
                if p in st: rc[r] += 1; rw[r] += st[p] < mine
            U = ctx.unseen_total
            ST["rows"].append((len(st) + 1, U and ctx.unseen_weaker_than_own() / U, [ctx.p_someone_weaker(c, l) for l in LEAKS],
                               any(v < mine for v in st.values()), dict(rc), dict(rw), d, bool(ctx.is_child_time)))
        return d
pol.POLICIES["probe__"] = Probe
CYC = ["counting_win", "matrix", "counting_aggressive", "counting_conservative"]
IDS, POLOF = lineup(["probe__", *[CYC[i % 4] for i in range(NP - 1)]])
def work(seeds):
    ST["rows"] = []
    def obs(game, t, p): ST["game"] = game
    for s in seeds: play_game(IDS, POLOF, CFG, s, observer=obs)
    return ST["rows"]
if __name__ == "__main__":
    n = int(sys.argv[2]); seeds = list(range(91_000_000, 91_000_000 + n)); rows = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(work, [seeds[i:i + 50] for i in range(0, n, 50)]): rows += r
    N = len(rows); tr = sum(r[3] for r in rows) / N
    print(f"\n#### {NP}人卓  判断 {N}  実際に誰かが弱い {tr:.3f}")
    for j, l in enumerate(LEAKS):
        bias = sum(r[2][j] for r in rows) / N - tr
        # 帯ごとの |ずれ| の加重平均
        g = collections.defaultdict(lambda: [0, 0.0, 0])
        for r in rows:
            a = g[min(int(r[2][j] * 10), 9)]; a[0] += 1; a[1] += r[2][j]; a[2] += r[3]
        ece = sum(abs(a[1] - a[2]) for a in g.values()) / N
        line = " ".join(f"{a[1]/a[0]:.2f}/{a[2]/a[0]:.2f}" for k, a in sorted(g.items()))
        print(f"  leak={l:.2f}  平均のずれ {bias:+.3f}  帯別のずれ {ece:.3f}   帯(見積/実際): {line}")
    print("  役割ごとの「その席が自分より弱い」率:")
    for role in ("kept", "stuck", "moved", "unacted", "known"):
        c = sum(r[4].get(role, 0) for r in rows); w = sum(r[5].get(role, 0) for r in rows)
        if c: print(f"    {role:8s} 席数 {c:7d}  実際 {w/c:.3f}")

"""分岐ロールアウト(パネル版): P0 の判断を1つ反転し、そのポットの決着まで打たせて
勝率と期待順位の差を測る。 usage: rollout.py n out.pkl v2.json [BASE] [seed0]"""
import pickle, random, sys, time
from concurrent.futures import ProcessPoolExecutor
from cucco.ai import policies as pol
from cucco.evaluation.fast import lineup, play_game
from lab import CFG, PANEL, POLIDX, Value

VAL = Value(sys.argv[3])
BASE = sys.argv[4] if len(sys.argv) > 4 else "counting_sniper"
SEED0 = int(sys.argv[5]) if len(sys.argv) > 5 else 20_000_000
BaseCls = pol.POLICIES[BASE]
ST = {"log": None, "force": None, "n": {"change": 0, "continue": 0}, "seg": 0, "fired": False}

class Probe(BaseCls):
    name = "probe__"
    def _hit(self, kind, base, feats):
        k = ST["n"][kind]; ST["n"][kind] += 1
        if ST["log"] is not None: ST["log"].append((kind, k, ST["seg"], base, feats))
        f = ST["force"]
        if f is not None and f[0] == kind and f[1] == k:
            ST["fired"] = True; return not base
        return base
    def decide_change_ctx(self, ctx):
        base = super().decide_change_ctx(ctx)
        feats = None
        if ST["log"] is not None:
            tot = ctx.unseen_total; own = ctx.own_rank
            num = own in pol.FULL_DECK_COUNTS and own not in pol.NEVER_CHANGE_RANKS and own not in {r.value for r in pol.ALWAYS_CHANGE_RANKS}
            pw = ctx.unseen_weaker_than_own() / tot if tot and own in pol.FULL_DECK_COUNTS else -1
            cut = bool(num and tot > 0 and 0 < pw and not any(pol.strength_of(r) < pol.strength_of(own) for r in ctx.known_held.values()))
            danger = ((ctx.unseen_counts.get("人間", 0) + ctx.unseen_counts.get("猫", 0)) / tot) if tot else 0
            cutoff = self.cutoff_base - self.cutoff_slope * ctx.alive_count - self.danger_weight * danger - (self.child_time_extra if ctx.is_child_time else 0)
            pos = len(ctx.acted_this_deal) + len(ctx.disqualified & set(ctx.turn_order))
            cfg = "p0" if pos == 0 else ("x" if ctx.exchanges_this_deal else f"k{min(pos, 3)}")
            others = [v for p, v in ctx.chips.items() if p != ctx.my_id]
            feats = dict(rank=own, alive=ctx.alive_count, deal=min(ctx.deal_number, 4), child=ctx.is_child_time,
                         deck=ctx.exchange_target is None, pos=pos, cfg=cfg, cut=cut, chips=ctx.my_chips, pot=ctx.pot_chips,
                         orig=ctx.own_card_original, pw=pw, margin=pw - cutoff, nex=len(ctx.exchanges_this_deal),
                         minother=min(others) if others else -1, ahead=sum(1 for v in others if v > ctx.my_chips),
                         lead=(ctx.my_chips - max(others)) if others else 0, li=ST["li"])
        return self._hit("change", base, feats)
    def decide_continue_ctx(self, ctx):
        base = super().decide_continue_ctx(ctx)
        feats = None
        if ST["log"] is not None:
            others = [v for p, v in ctx.chips.items() if p != ctx.my_id]
            feats = dict(req=ctx.required_chips, chips=ctx.my_chips, pot=ctx.pot_chips, alive=ctx.alive_count,
                         minother=min(others) if others else -1, ahead=sum(1 for v in others if v > ctx.my_chips),
                         lead=(ctx.my_chips - max(others)) if others else 0, li=ST["li"])
        return self._hit("continue", base, feats)
pol.POLICIES["probe__"] = Probe
class Stop(Exception): pass
LINEUPS = [lineup(["probe__", *opp]) for opp in PANEL]
POLS = [[0] + [POLIDX[p] for p in opp] for opp in PANEL]

def value_at(game, li):
    chips = game.chips; ids = LINEUPS[li][0]
    if any(c <= 0 for c in chips.values()):
        order = [p for p, _ in sorted(chips.items(), key=lambda kv: kv[1], reverse=True)]
        return (1.0 if order[0] == "P0" else 0.0), float(order.index("P0") + 1)
    cs = [chips[i] for i in ids]
    return VAL.pwin(cs, POLS[li]), VAL.erank(cs, POLS[li])

def run(seed, force=None, log=None):
    li = seed % len(PANEL)
    ST.update(log=log, force=force, seg=0, fired=False, li=li); ST["n"] = {"change": 0, "continue": 0}
    vals = []
    def obs(game, t, p):
        if t == "pot_result" and p["result"] == "won":
            vals.append(value_at(game, li)); ST["seg"] += 1
            if force is not None and ST["fired"]: raise Stop()
    try: play_game(*LINEUPS[li], CFG, seed, observer=obs)
    except Stop: pass
    return vals

def work(args):
    seeds, per = args
    rng = random.Random(seeds[0]); rows = []
    for seed in seeds:
        log = []; base_vals = run(seed, log=log)
        for kind, k, seg, base, feats in rng.sample(log, min(per, len(log))):
            vals = run(seed, force=(kind, k))
            if not ST["fired"]: continue
            dw = vals[-1][0] - base_vals[seg][0]; dr = base_vals[seg][1] - vals[-1][1]   # 正 = 反転が良い
            if base: dw, dr = -dw, -dr                                                    # (する − しない)
            rows.append((kind, base, dw, dr, feats))
    return rows

if __name__ == "__main__":
    n, per = int(sys.argv[1]), 8
    t = time.time(); seeds = list(range(SEED0, SEED0 + n))
    chunks = [(seeds[i:i + 100], per) for i in range(0, n, 100)]
    rows = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(work, chunks): rows += r
    pickle.dump(rows, open(sys.argv[2], "wb"))
    print(len(rows), "samples", round(time.time() - t), "s")

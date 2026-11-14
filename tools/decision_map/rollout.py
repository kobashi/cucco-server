"""usage: rollout.py N n_games out.pkl v.json BASE [seed0]"""
import pickle, random, sys, time
from concurrent.futures import ProcessPoolExecutor
from cucco.ai import policies as pol
from cucco.evaluation.fast import lineup, play_game
from lab import CFG, panel, POLIDX, Value
N = int(sys.argv[1]); VAL = Value(sys.argv[4]); BASE = sys.argv[5]; SEED0 = int(sys.argv[6]) if len(sys.argv) > 6 else 20_000_000
PAN = panel(N); BaseCls = pol.POLICIES[BASE]
ST = {"log": None, "force": None, "n": {"change": 0, "continue": 0}, "seg": 0, "fired": False, "li": 0}
ALWAYS = {r.value for r in pol.ALWAYS_CHANGE_RANKS}
def kcut(ctx):
    tot = ctx.unseen_total
    danger = ((ctx.unseen_counts.get("人間", 0) + ctx.unseen_counts.get("猫", 0)) / tot) if tot else 0
    return 0.62 - 0.047 * ctx.alive_count - 0.5 * danger - (0.05 if ctx.is_child_time else 0), danger
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
        base = super().decide_change_ctx(ctx); feats = None
        if ST["log"] is not None:
            own = ctx.own_rank; tot = ctx.unseen_total
            num = own in pol.FULL_DECK_COUNTS and own not in pol.NEVER_CHANGE_RANKS
            feats = dict(num=False, rank=own, n=N, li=ST["li"])
            if num and tot > 0:
                c, danger = kcut(ctx); lm = ctx.loss_model(c); roles = list(ctx.seat_roles().values())
                others = [v for p, v in ctx.chips.items() if p != ctx.my_id]
                feats = dict(num=True, rank=own, n=N, li=ST["li"], alive=ctx.alive_count, child=bool(ctx.is_child_time), deal=min(ctx.deal_number, 5),
                             deck=ctx.exchange_target is None, pw=ctx.unseen_weaker_than_own() / tot, danger=danger, kc=c,
                             lk=lm.lose_if_keep(), lc=lm.lose_if_change(), flk=lm.final_lose_if_keep(), flc=lm.final_lose_if_change(), always=own in ALWAYS, nk=roles.count("kept"), ns=roles.count("stuck"),
                             nmv=roles.count("moved"), nu=roles.count("unacted"), nkn=roles.count("known"),
                             tknown=lm.target_role == "known", flowed=ctx.was_taken_from(ctx.my_id), orig=ctx.own_card_original,
                             chips=ctx.my_chips, pot=ctx.pot_chips, ahead=sum(1 for v in others if v > ctx.my_chips), nopp=len(others))
        return self._hit("change", base, feats)
    def decide_continue_ctx(self, ctx):
        base = super().decide_continue_ctx(ctx); feats = None
        if ST["log"] is not None:
            others = [v for p, v in ctx.chips.items() if p != ctx.my_id]
            feats = dict(n=N, li=ST["li"], req=ctx.required_chips, chips=ctx.my_chips, pot=ctx.pot_chips, alive=ctx.alive_count,
                         minother=min(others) if others else -1, ahead=sum(1 for v in others if v > ctx.my_chips),
                         lead=(ctx.my_chips - max(others)) if others else 0, nopp=len(others),
                         avg=sum(others) / len(others) if others else 0)
        return self._hit("continue", base, feats)
pol.POLICIES["probe__"] = Probe
class Stop(Exception): pass
LINEUPS = [lineup(["probe__", *opp]) for opp in PAN]; POLS = [[0] + [POLIDX[p] for p in opp] for opp in PAN]
def value_at(game, li):
    chips = game.chips; ids = LINEUPS[li][0]
    if any(c <= 0 for c in chips.values()):
        order = [p for p, _ in sorted(chips.items(), key=lambda kv: kv[1], reverse=True)]
        return (1.0 if order[0] == "P0" else 0.0), float(order.index("P0") + 1)
    cs = [chips[i] for i in ids]; return VAL.pwin(cs, POLS[li]), VAL.erank(cs, POLS[li])
def run(seed, force=None, log=None):
    li = seed % len(PAN); ST.update(log=log, force=force, seg=0, fired=False, li=li); ST["n"] = {"change": 0, "continue": 0}; vals = []
    def obs(game, t, p):
        if t == "pot_result" and p["result"] == "won":
            vals.append(value_at(game, li)); ST["seg"] += 1
            if force is not None and ST["fired"]: raise Stop()
    try: play_game(*LINEUPS[li], CFG, seed, observer=obs)
    except Stop: pass
    return vals
def work(args):
    seeds, per = args; rng = random.Random(seeds[0]); rows = []
    for seed in seeds:
        log = []; base_vals = run(seed, log=log)
        cand = [x for x in log if x[0] == "continue" or x[4]["num"]]
        for kind, k, seg, base, feats in rng.sample(cand, min(per, len(cand))):
            vals = run(seed, force=(kind, k))
            if not ST["fired"]: continue
            dw = vals[-1][0] - base_vals[seg][0]; dr = base_vals[seg][1] - vals[-1][1]
            if base: dw, dr = -dw, -dr
            rows.append((kind, base, dw, dr, feats))
    return rows
if __name__ == "__main__":
    n = int(sys.argv[2]); t = time.time(); seeds = list(range(SEED0, SEED0 + n)); rows = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(work, [(seeds[i:i + 50], 8) for i in range(0, n, 50)]): rows += r
    pickle.dump(rows, open(sys.argv[3], "wb")); print(N, "players", len(rows), "samples", round(time.time() - t), "s", flush=True)

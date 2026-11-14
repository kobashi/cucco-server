"""クク宣言の判断反転。 usage: rollout_cucco.py N n_games out.pkl v.json [seed0]"""
import pickle, sys, time
from concurrent.futures import ProcessPoolExecutor
from cucco.ai import policies as pol
from cucco.ai.context import strength_of
from cucco.evaluation.fast import lineup, play_game
from lab import CFG, panel, POLIDX, Value
N = int(sys.argv[1]); VAL = Value(sys.argv[4]); SEED0 = int(sys.argv[5]) if len(sys.argv) > 5 else 30_000_000
PAN = panel(N); ST = {"log": None, "force": None, "k": 0, "seg": 0, "fired": False, "li": 0, "game": None}
class Probe(pol.CountingWin):
    name = "probe__"
    def _hit(self, base, ctx, prompt):
        k = ST["k"]; ST["k"] += 1
        if ST["log"] is not None:
            lm = ctx.loss_model(self.opponent_keep_cutoff(ctx) if ctx.unseen_total else 0.4); wp = lm.weakest_probs()
            mine = ctx.chips.get(ctx.my_id, ctx.my_chips); ch = {p: ctx.chips.get(p, 0) for p in wp}
            allo = [v for p, v in ctx.chips.items() if p != ctx.my_id]
            deal = ST["game"].current_pot.current_deal
            st = {p: strength_of(deal.hands[p].value) for p in wp if p in deal.hands}
            truew = min(st, key=st.get) if st else None
            idx = ctx.turn_order.index(ctx.my_id) if ctx.my_id in ctx.turn_order else 0
            top = max(wp, key=wp.get) if wp else None
            feats = dict(n=N, li=ST["li"], prompt=prompt, unsafe=len(ctx.unacted_before_me()), acted=ctx.my_id in ctx.acted_this_deal,
                         after=sum(1 for p in ctx.turn_order[idx + 1:] if p in wp and p not in ctx.acted_this_deal),
                         alive=len(wp) + 1, child=bool(ctx.is_child_time), deal=min(ctx.deal_number, 5), pot=ctx.pot_chips, mine=mine,
                         avg=sum(allo) / len(allo) if allo else 0, ahead=sum(1 for v in allo if v > mine), nopp=len(allo),
                         s_rich=sum(w for p, w in wp.items() if ch[p] >= mine), s_lead=sum(w for p, w in wp.items() if allo and ch[p] == max(allo)),
                         e_chip=sum(w * ch[p] for p, w in wp.items()), prior_rich=(sum(1 for p in wp if ch[p] >= mine) / len(wp)) if wp else 0,
                         prior_chip=(sum(ch.values()) / len(ch)) if ch else 0, maxp=wp[top] if top else 0, top_chip=ch[top] if top else 0,
                         nex=len(ctx.exchanges_this_deal), true_chip=ch.get(truew, 0) if truew else 0, model_hit=(truew == top),
                         ptrue=wp.get(truew, 0) if truew else 0)
            ST["log"].append((k, ST["seg"], base, feats))
        f = ST["force"]
        if f is not None and f == k:
            ST["fired"] = True; return not base
        return base
    def declare_cucco_eagerly(self, ctx): return self._hit(super().declare_cucco_eagerly(ctx), ctx, False)
    def decide_cucco_declare_ctx(self, ctx): return self._hit(super().decide_cucco_declare_ctx(ctx), ctx, True)
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
    li = seed % len(PAN); ST.update(log=log, force=force, seg=0, fired=False, li=li, k=0); vals = []
    def obs(game, t, p):
        ST["game"] = game
        if t == "pot_result" and p["result"] == "won":
            vals.append(value_at(game, li)); ST["seg"] += 1
            if force is not None and ST["fired"]: raise Stop()
    try: play_game(*LINEUPS[li], CFG, seed, observer=obs)
    except Stop: pass
    return vals
def work(seeds):
    rows = []
    for seed in seeds:
        log = []; base_vals = run(seed, log=log)
        for k, seg, base, feats in log[:14]:
            vals = run(seed, force=k)
            if not ST["fired"]: continue
            dw = vals[-1][0] - base_vals[seg][0]; dr = base_vals[seg][1] - vals[-1][1]
            if base: dw, dr = -dw, -dr      # (宣言する − 待つ)
            rows.append((base, dw, dr, feats))
    return rows
if __name__ == "__main__":
    n = int(sys.argv[2]); t = time.time(); seeds = list(range(SEED0, SEED0 + n)); rows = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(work, [seeds[i:i + 50] for i in range(0, n, 50)]): rows += r
    pickle.dump(rows, open(sys.argv[3], "wb")); print(N, "players", len(rows), "samples", round(time.time() - t), "s", flush=True)

"""パネルの各卓で、ポット境界のチップ状況と最終順位を集める。P0 = 基準方策。"""
import pickle, sys, time
from concurrent.futures import ProcessPoolExecutor
from cucco.evaluation.fast import lineup, play_game
from lab import CFG, PANEL
BASE = sys.argv[3] if len(sys.argv) > 3 else "counting_sniper"
def work(seeds):
    out = []
    for seed in seeds:
        li = seed % len(PANEL)
        ids, pol = lineup([BASE, *PANEL[li]])
        states = []
        def obs(game, t, p):
            if t == "pot_result" and p["result"] == "won":
                states.append(tuple(p["chips_now"][i] for i in ids))
        r = play_game(ids, pol, CFG, seed, observer=obs)
        order = [pid for pid, _ in r.ranking]
        rk = tuple(order.index(i) for i in ids)
        for s in states[:-1]:
            out.append((li, s, rk))
    return out
if __name__ == "__main__":
    n = int(sys.argv[1]); t = time.time()
    seeds = list(range(10_000_000, 10_000_000 + n))
    chunks = [seeds[i:i+500] for i in range(0, n, 500)]
    data = []
    with ProcessPoolExecutor(8) as ex:
        for o in ex.map(work, chunks): data += o
    pickle.dump(data, open(sys.argv[2], "wb"))
    print(len(data), "states", round(time.time() - t), "s")

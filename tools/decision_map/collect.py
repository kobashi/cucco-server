"""usage: collect.py N n_games out.pkl BASE"""
import pickle, sys, time
from concurrent.futures import ProcessPoolExecutor
from cucco.evaluation.fast import lineup, play_game
from lab import CFG, panel
N = int(sys.argv[1]); BASE = sys.argv[4]; PAN = panel(N)
def work(seeds):
    out = []
    for seed in seeds:
        li = seed % len(PAN); ids, pol = lineup([BASE, *PAN[li]]); states = []
        def obs(game, t, p):
            if t == "pot_result" and p["result"] == "won": states.append(tuple(p["chips_now"][i] for i in ids))
        r = play_game(ids, pol, CFG, seed, observer=obs)
        order = [pid for pid, _ in r.ranking]; rk = tuple(order.index(i) for i in ids)
        for s in states[:-1]: out.append((li, s, rk))
    return out
if __name__ == "__main__":
    n = int(sys.argv[2]); t = time.time(); seeds = list(range(10_000_000, 10_000_000 + n)); data = []
    with ProcessPoolExecutor(8) as ex:
        for o in ex.map(work, [seeds[i:i + 200] for i in range(0, n, 200)]): data += o
    pickle.dump(data, open(sys.argv[3], "wb")); print(N, "players", len(data), "states", round(time.time() - t), "s", flush=True)

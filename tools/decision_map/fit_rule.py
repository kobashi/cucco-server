"""ルール「交換 ⇔ lk − β·lc > τ」の β, τ を利得最大で探す。 usage: fit_rule.py W|R"""
import pickle, sys, math
import numpy as np
OBJ = 2 if sys.argv[1] == "W" else 3; NS = [2, 3, 4, 6, 8, 10]; SC = 100 if OBJ == 2 else 1
D = "/".join(__file__.split("/")[:-1]); rows = []
for n in NS: rows += [r for r in pickle.load(open(f"{D}/dv{n}.pkl", "rb")) if r[0] == "change" and r[4]["num"]]
F = [r[4] for r in rows]; y = np.array([r[OBJ] for r in rows]) * SC; base = np.array([r[1] for r in rows])
col = lambda k: np.array([float(f[k]) for f in F])
lk, lc, n, alive, child, deck = col("lk"), col("lc"), col("n"), col("alive"), col("child"), col("deck")
idx = np.random.default_rng(2).permutation(len(y)); te = np.zeros(len(y), bool); te[idx[: len(y) // 3]] = True; tr = ~te
def gain(dec, m):
    g = np.where(dec & ~base, y, 0) + np.where(~dec & base, -y, 0); g = g[m]
    return (g.mean(), g.std() / math.sqrt(len(g))) if len(g) else (0, 0)
BET = np.arange(0.4, 2.01, 0.1); TAU = np.arange(-0.2, 0.31, 0.02)
def best(m):
    b = (-9, 0, 0)
    for be in BET:
        s = lk - be * lc
        for ta in TAU:
            g = gain(s > ta, m & tr)[0]
            if g > b[0]: b = (g, be, ta)
    return b
g, be, ta = best(np.ones(len(y), bool)); dec = (lk - be * lc) > ta
print(f"全体: β={be:.1f} τ={ta:+.2f}  train {g:+.3f}  test {gain(dec, te)[0]:+.3f} ±{gain(dec, te)[1]:.3f}   人数別 test:", " ".join(f"{k}:{gain(dec, te & (n == k))[0]:+.3f}" for k in NS))
print("β を全体の値に固定して、区分ごとの最適 τ:")
s = lk - be * lc
def taus(title, groups):
    print(" ", title)
    dec2 = np.zeros(len(y), bool)
    for lab, m in groups:
        bt = max(((gain(s > t, m & tr)[0], t) for t in TAU))
        dec2 |= m & (s > bt[1])
        print(f"    {lab:18s} n={int(m.sum()):7d} τ={bt[1]:+.2f}  train {bt[0]:+.3f}  test {gain(s > bt[1], m & te)[0]:+.3f}  (全体τでの test {gain(dec, m & te)[0]:+.3f})")
    print(f"    → 区分別τの全体 test {gain(dec2, te)[0]:+.3f}")
taus("人数", [(f"{k}人", n == k) for k in NS])
taus("生存人数", [(f"alive {a}", alive == a) for a in range(2, 11)])
taus("時間帯×相手", [(f"{'子供' if c else '大人'}/{'山札' if d else '席'}", (child == c) & (deck == d)) for c in (0, 1) for d in (0, 1)])
print("β を区分ごとに: 時間帯×相手")
for c in (0, 1):
    for d in (0, 1):
        m = (child == c) & (deck == d); g, b2, t2 = best(m)
        print(f"    {'子供' if c else '大人'}/{'山札' if d else '席'}  β={b2:.1f} τ={t2:+.2f} train {g:+.3f} test {gain((lk - b2 * lc) > t2, m & te)[0]:+.3f}")

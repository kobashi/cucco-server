import pickle, sys, json, itertools
import numpy as np
from lab import PANEL, POLIDX, KN, MB, BAS, mbin
data = pickle.load(open(sys.argv[1], "rb"))
N = len(data)
li = np.array([d[0] for d in data]); chips = np.minimum(np.array([d[1] for d in data]), 100); rk = np.array([d[2] for d in data])
LP = np.array([[0] + [POLIDX[p] for p in l] for l in PANEL]); pols = LP[li]
K = len(KN); NB = len(MB)
mb = np.array([mbin(m) for m in range(101)])[chips.min(1)]
bk = np.array([b[0] for b in BAS])[chips]; bw = np.array([b[1] for b in BAS])[chips]
idx0 = mb[:, None] * K + bk; idx1 = idx0 + 1
rng = np.random.default_rng(0); perm = rng.permutation(N); te = perm[: N // 7]; tr = perm[N // 7:]
def scores(P, off, s):
    return P[idx0[s]] * bw[s] + P[idx1[s]] * (1 - bw[s]) + off[pols[s]]
def softmax(z, mask=None):
    if mask is not None: z = np.where(mask, z, -1e9)
    z = z - z.max(1, keepdims=True); e = np.exp(z); return e / e.sum(1, keepdims=True)
def loss_grad(P, off, s, kind):
    z = scores(P, off, s); r = rk[s]; n = len(s)
    g = np.zeros_like(z); L = 0.0
    stages = [0] if kind == "win" else [0, 1, 2]
    for st in stages:
        mask = r >= st; p = softmax(z, mask); tgt = (r == st)
        L -= np.log(np.maximum((p * tgt).sum(1), 1e-12)).sum()
        g += p - tgt
    g /= n
    gP = np.bincount(idx0[s].ravel(), (g * bw[s]).ravel(), NB * K) + np.bincount(idx1[s].ravel(), (g * (1 - bw[s])).ravel(), NB * K)
    go = np.bincount(pols[s].ravel(), g.ravel(), 4)
    return L / n, gP, go
def fit(kind):
    P = np.tile(0.08 * np.array(KN, float), NB); off = np.zeros(4)
    m = [np.zeros_like(P), np.zeros(4)]; v = [np.zeros_like(P), np.zeros(4)]
    for it in range(1, 501):
        L, gP, go = loss_grad(P, off, tr, kind)
        for j, (x, g) in enumerate(((P, gP), (off, go))):
            m[j] = 0.9 * m[j] + 0.1 * g; v[j] = 0.999 * v[j] + 0.001 * g * g
            x -= 0.05 * (m[j] / (1 - 0.9 ** it)) / (np.sqrt(v[j] / (1 - 0.999 ** it)) + 1e-9)
        if it % 100 == 0: print(kind, it, round(L, 5), round(loss_grad(P, off, te, kind)[0], 5), flush=True)
    return P, off
out = {}
PERMS = np.array(list(itertools.permutations(range(4))))
for kind in ("win", "rank"):
    P, off = fit(kind); out[kind] = {"P": P.reshape(NB, K).tolist(), "b": off.tolist()}
    print(kind, "policy offsets", dict(zip(POLIDX, off.round(3))))
    z = scores(P, off, te); r = rk[te]
    if kind == "win":
        pred = softmax(z); act = (r == 0).astype(float)
    else:
        e = np.exp(z - z.max(1, keepdims=True)); pred = np.zeros_like(z)
        for pm in PERMS:
            pr = np.ones(len(te)); rem = e.sum(1)
            for i in pm[:3]:
                pr *= e[:, i] / rem; rem = rem - e[:, i]
            for pos, i in enumerate(pm): pred[:, i] += pr * (pos + 1)
        act = r + 1.0
    q = np.quantile(pred.ravel(), np.linspace(0, 1, 13)); b = np.clip(np.searchsorted(q, pred.ravel()) - 1, 0, 11)
    print(" calibration pred/actual:", " ".join(f"{pred.ravel()[b==i].mean():.3f}/{act.ravel()[b==i].mean():.3f}" for i in range(12)))
    for name, pi in POLIDX.items():
        mk = pols[te] == pi; print(f"  {name}: pred {pred[mk].mean():.4f} actual {act[mk].mean():.4f}")
    for l in range(4):
        mk = li[te] == l; print(f"  lineup{l} P0: pred {pred[mk, 0].mean():.4f} actual {act[mk, 0].mean():.4f}")
json.dump(out, open(sys.argv[2], "w"))

"""usage: fit.py N vdata.pkl v.json"""
import pickle, sys, json
import numpy as np
from lab import panel, POLIDX, KN, MB, BAS, mbin, CMAX
NPL = int(sys.argv[1]); data = pickle.load(open(sys.argv[2], "rb")); N = len(data)
li = np.array([d[0] for d in data]); chips = np.minimum(np.array([d[1] for d in data]), CMAX); rk = np.array([d[2] for d in data])
LP = np.array([[0] + [POLIDX[p] for p in l] for l in panel(NPL)]); pols = LP[li]
K = len(KN); NB = len(MB)
mb = np.array([mbin(m) for m in range(CMAX + 1)])[chips.min(1)]
bk = np.array([b[0] for b in BAS])[chips]; bw = np.array([b[1] for b in BAS])[chips]
idx0 = mb[:, None] * K + bk; idx1 = idx0 + 1
perm = np.random.default_rng(0).permutation(N); te = perm[: N // 7]; tr = perm[N // 7:]
def scores(P, off, s): return P[idx0[s]] * bw[s] + P[idx1[s]] * (1 - bw[s]) + off[pols[s]]
def softmax(z, mask=None):
    if mask is not None: z = np.where(mask, z, -1e9)
    z = z - z.max(1, keepdims=True); e = np.exp(z); return e / e.sum(1, keepdims=True)
def loss_grad(P, off, s, kind):
    z = scores(P, off, s); r = rk[s]; n = len(s); g = np.zeros_like(z); L = 0.0
    for st in ([0] if kind == "win" else range(NPL - 1)):
        mask = r >= st; p = softmax(z, mask); tgt = (r == st)
        L -= np.log(np.maximum((p * tgt).sum(1), 1e-12)).sum(); g += p - tgt
    g /= n
    gP = np.bincount(idx0[s].ravel(), (g * bw[s]).ravel(), NB * K) + np.bincount(idx1[s].ravel(), (g * (1 - bw[s])).ravel(), NB * K)
    return L / n, gP, np.bincount(pols[s].ravel(), g.ravel(), 4)
out = {}
for kind in ("win", "rank"):
    P = np.tile(0.08 * np.minimum(np.array(KN, float), 60), NB); off = np.zeros(4)
    m = [np.zeros_like(P), np.zeros(4)]; v = [np.zeros_like(P), np.zeros(4)]
    for it in range(1, 251):
        L, gP, go = loss_grad(P, off, tr, kind)
        for j, (x, g) in enumerate(((P, gP), (off, go))):
            m[j] = 0.9 * m[j] + 0.1 * g; v[j] = 0.999 * v[j] + 0.001 * g * g
            x -= 0.05 * (m[j] / (1 - 0.9 ** it)) / (np.sqrt(v[j] / (1 - 0.999 ** it)) + 1e-9)
    out[kind] = {"P": P.reshape(NB, K).tolist(), "b": off.tolist()}
    z = scores(P, off, te); r = rk[te]
    if kind == "win": pred = softmax(z); act = (r == 0).astype(float)
    else:
        e = np.exp(z - z.max(1, keepdims=True)); pred = 1.0 + (e[:, None, :] / (e[:, :, None] + e[:, None, :])).sum(2) - 0.5; act = r + 1.0
    q = np.quantile(pred.ravel(), np.linspace(0, 1, 9)); b = np.clip(np.searchsorted(q, pred.ravel()) - 1, 0, 7)
    print(NPL, kind, "test loss", round(loss_grad(P, off, te, kind)[0], 4), "calib:", " ".join(f"{pred.ravel()[b==i].mean():.3f}/{act.ravel()[b==i].mean():.3f}" for i in range(8)), flush=True)
json.dump(out, open(sys.argv[3], "w"))

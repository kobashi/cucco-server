import json, math
from cucco.domain.config import GameConfig
CFG = GameConfig(mode="evaluation", game_count=1, starting_chips=25)
POLIDX = {"base": 0, "counting_conservative": 1, "matrix": 2, "counting_aggressive": 3}
CYC = ["matrix", "counting_aggressive", "counting_conservative"]
def panel(n):
    return [[CYC[i % 3] for i in range(n - 1)], ["counting_conservative"] * (n - 1)]
KN = list(range(0, 11)) + list(range(12, 41, 3)) + list(range(46, 101, 6)) + list(range(120, 261, 20))
CMAX = KN[-1]
MB = [1, 2, 3, 4, 6, 9, 13, 18]
def mbin(m):
    b = 0
    for i, lo in enumerate(MB):
        if m >= lo: b = i
    return b
def _basis(c):
    for k in range(len(KN) - 1):
        if KN[k] <= c <= KN[k + 1]:
            return k, 1 - (c - KN[k]) / (KN[k + 1] - KN[k])
BAS = [_basis(c) for c in range(CMAX + 1)]
class Value:
    def __init__(self, path):
        d = json.load(open(path)); self.win, self.rank = d["win"], d["rank"]
    def _e(self, m, chips, pols):
        b = mbin(min(chips)); r = m["P"][b]; off = m["b"]; z = []
        for c, p in zip(chips, pols):
            k, w = BAS[min(c, CMAX)]; z.append(r[k] * w + r[k + 1] * (1 - w) + off[p])
        mx = max(z); return [math.exp(x - mx) for x in z]
    def pwin(self, chips, pols, me=0):
        e = self._e(self.win, chips, pols); return e[me] / sum(e)
    def erank(self, chips, pols, me=0):
        e = self._e(self.rank, chips, pols); return 1.0 + sum(e[j] / (e[me] + e[j]) for j in range(len(e)) if j != me)

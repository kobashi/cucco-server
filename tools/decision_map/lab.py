"""共通: パネル、価値関数(勝率・期待順位)。"""
import itertools, json, math
from cucco.domain.config import GameConfig
CFG = GameConfig(mode="evaluation", game_count=1, starting_chips=25)
PANEL = [["counting_conservative"] * 3, ["matrix", "counting_aggressive", "counting_conservative"],
         ["matrix"] * 3, ["counting_aggressive"] * 3]
POLIDX = {"base": 0, "counting_conservative": 1, "matrix": 2, "counting_aggressive": 3}
KN = list(range(0, 11)) + list(range(12, 41, 3)) + list(range(46, 101, 6))
MB = [1, 2, 3, 4, 6, 9, 13, 18]
def mbin(m):
    b = 0
    for i, lo in enumerate(MB):
        if m >= lo: b = i
    return b
def _basis(c):
    c = min(c, KN[-1])
    for k in range(len(KN) - 1):
        if KN[k] <= c <= KN[k + 1]:
            return k, 1 - (c - KN[k]) / (KN[k + 1] - KN[k])
BAS = [_basis(c) for c in range(101)]
PERMS = list(itertools.permutations(range(4)))

class Value:
    def __init__(self, path):
        d = json.load(open(path)); self.win, self.rank = d["win"], d["rank"]
    def _z(self, m, chips, pols):
        b = mbin(min(chips)); r = m["P"][b]; off = m["b"]
        out = []
        for c, p in zip(chips, pols):
            k, w = BAS[min(c, 100)]; out.append(r[k] * w + r[k + 1] * (1 - w) + off[p])
        return out
    def pwin(self, chips, pols, me=0):
        z = self._z(self.win, chips, pols); mx = max(z); e = [math.exp(x - mx) for x in z]
        return e[me] / sum(e)
    def erank(self, chips, pols, me=0):
        z = self._z(self.rank, chips, pols); mx = max(z); e = [math.exp(x - mx) for x in z]
        tot = 0.0
        for perm in PERMS:
            p = 1.0; rem = sum(e)
            for i in perm[:3]:
                p *= e[i] / rem; rem -= e[i]
            tot += p * (perm.index(me) + 1)
        return tot

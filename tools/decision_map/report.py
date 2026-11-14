"""usage: report.py dv2.pkl  — 勝率(W)と期待順位(R)の両方で、しきい値のずれと継続の表を出す。"""
import pickle, sys, math, collections
rows = pickle.load(open(sys.argv[1], "rb"))
ch = [r for r in rows if r[0] == "change"]; co = [r for r in rows if r[0] == "continue"]
def ms(xs):
    n = len(xs); m = sum(xs) / n; return m, math.sqrt(sum((x - m) ** 2 for x in xs) / max(n - 1, 1) / n)
print("samples", len(rows), "change", len(ch), "continue", len(co))
for nm, rs in (("交換", ch), ("継続", co)):
    for j, lab in ((2, "勝率pt"), (3, "順位")):
        m, se = ms([(r[j] if r[1] else -r[j]) for r in rs]); sc = 100 if j == 2 else 1
        print(f"  {nm}: 基準の手の優位 {lab} {m*sc:+.3f} ±{se*sc:.3f}")
def shift(title, key, W=0.15, minn=2500):
    print(f"\n== しきい値のずれ / {title} ==   (cutoff に足すべき量)")
    g = collections.defaultdict(list)
    for r in ch:
        f = r[4]
        if not f["cut"] or abs(f["margin"]) > W: continue
        g[key(f)].append((f["margin"], r[2], r[3]))
    for k in sorted(g, key=str):
        xs = g[k]; n = len(xs)
        if n < minn: continue
        out = []
        for j in (1, 2):
            mx = sum(x[0] for x in xs) / n; my = sum(x[j] for x in xs) / n
            sxx = sum((x[0] - mx) ** 2 for x in xs); sxy = sum((x[0] - mx) * (x[j] - my) for x in xs)
            b = sxy / sxx; a = my - b * mx
            res = sum((x[j] - a - b * x[0]) ** 2 for x in xs) / (n - 2)
            se = math.sqrt(res * (1 / n + mx * mx / sxx)) / abs(b)
            out.append(f"{-a/b:+.3f}±{se:.3f}")
        print(f"  {str(k):30s} n={n:6d}  W:{out[0]}  R:{out[1]}")
def table(title, items, key, minn=100):
    print(f"\n== {title} ==  (する − しない)")
    g = collections.defaultdict(list)
    for r in items: g[key(r[4])].append(r)
    for k in sorted(g, key=lambda x: tuple(x) if isinstance(x, tuple) else (x,)):
        rs = g[k]; n = len(rs)
        if n < minn: continue
        yes = sum(1 for r in rs if r[1]) / n
        mw, sw = ms([r[2] for r in rs]); mr, sr = ms([r[3] for r in rs])
        print(f"  {str(k):22s} n={n:6d} する率={yes:4.2f}  W={mw*100:+6.2f}±{sw*100:4.2f}pt  R={mr:+.4f}±{sr:.4f}")
ph = lambda f: "child" if f["child"] else "adult"
shift("全体", lambda f: "all")
shift("卓", lambda f: f["li"])
shift("時間帯×先行席", lambda f: (ph(f), f["cfg"]))
shift("人数", lambda f: f["alive"])
shift("山札か", lambda f: f["deck"])
shift("自分よりチップが多い席数", lambda f: f["ahead"])
shift("時間帯×多い席数", lambda f: (ph(f), f["ahead"]))
shift("自分のチップ", lambda f: min(f["chips"], 40) // 8 * 8)
shift("他席の最少チップ", lambda f: min(f["minother"], 12) // 3 * 3)
shift("ポット", lambda f: min(f["pot"], 24) // 6 * 6)
shift("時間帯×ポット", lambda f: (ph(f), min(f["pot"], 24) // 6 * 6))
shift("卓×時間帯×先行席", lambda f: (f["li"], ph(f), f["cfg"]), minn=1500)
table("特殊札・しきい値外 / 札", [r for r in ch if not r[4]["cut"]], lambda f: f["rank"])
table("継続 / 必要枚数", co, lambda f: f["req"])
table("継続 / 必要×払った後の残り", co, lambda f: (f["req"], min(f["chips"] - f["req"], 14) // 2 * 2), 60)
table("継続 / 必要×ポット", co, lambda f: (f["req"], min(f["pot"], 20) // 4 * 4), 60)
table("継続 / 残り<=req+1 のとき 卓", [r for r in co if r[4]["chips"] - r[4]["req"] < r[4]["req"] + 2], lambda f: f["li"], 60)
table("継続 / 必要×多い席数", co, lambda f: (f["req"], f["ahead"]), 60)

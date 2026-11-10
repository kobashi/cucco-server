"""子供の時間の「払って残るか降りるか」を厳密に解き、方策の判断と照合する。

継続判断は交換判断と違い、1手先では決まらない。払えばポットに残れるが、
以降のディールでさらに失う危険も背負う。そこでポット終了までを後退帰納法で
解き、「払う」「降りる」の期待チップを厳密に比較する。

モデル(docs/rules/final_rules.md 「子供の時間」「大人の時間」):
- 子供の時間(1〜3ディール目)の敗者は、ディール番号と同じ枚数を払えば残れる。
  払えない/辞退すればそのポットから脱落する。
- 4ディール目以降の敗者は、支払いなしで脱落する(選択の余地なし)。
- 最後の1人がポットを総取りする。
- 払った枚数はポットに加わる。

近似(ここが厳密解の範囲):
- **各ディールの敗者は参加者から一様に1人**とみなす。実際は最弱札の保持者で、
  タイなら複数出るが、全員が同程度の方策で打つなら一様に近い。
- 相手の継続方針は --opp-continue で指定する(既定: 払えるなら払う)。
- チップ0枚でゲーム全体が終わる効果は --zero-cost で外から与える
  (既定0=考慮しない)。
- ラウンド上限で終わる卓は扱わない。

使い方:
    python tools/continue_oracle.py --selftest
    python tools/continue_oracle.py

**この数字は勝率と一致しない。**ここで最小化しているのは「ポット終了時の期待
チップ」だが、このゲームの勝敗は順位と生存(既定の終了条件 chips_zero)で決まる。
実測では、リグレット0.543の counting_conservative がリグレット0.061の
counting_aggressive より勝率で3.6pt 強い。リグレット最小の形に差し替えて
7,000ゲーム対戦させても勝率は -0.73pt(SE 1.04pt)で改善しなかった。目的関数を
勝敗に合わせて直すまで、この出力で方策を動かさないこと。交換判断の
oracle_check.py は目的関数が「このディールで敗者になる確率」なので勝率と整合する。
"""

from __future__ import annotations

import argparse
import random
from functools import lru_cache

from cucco.ai.context import PolicyContext
from cucco.ai.plugins import load_policy_dir
from cucco.ai.policies import POLICIES

CHILD_TIME_LAST_DEAL = 3


def make_solver(opp_continue: bool, zero_cost: float, my_rule: str):
    """ポット終了までの自分の期待チップ増減を返す関数を作る。"""

    @lru_cache(maxsize=None)
    def play(deal: int, m: int, pot: int, chips: int) -> float:
        """これからディール deal を戦う。自分は参加中、参加者 m 人、ポット pot。"""
        if m <= 1:
            return float(pot)          # 最後の1人 = 総取り
        if deal > 60:                  # 到達しないが保険
            return float(pot) / m
        mine = pay_or_drop(deal, m, pot, chips)
        if deal <= CHILD_TIME_LAST_DEAL and opp_continue:
            others = play(deal + 1, m, pot + deal, chips)
        elif m - 1 == 1:
            others = float(pot)        # 相手が降りて自分だけが残る
        else:
            others = play(deal + 1, m - 1, pot, chips)
        return (mine + (m - 1) * others) / m

    @lru_cache(maxsize=None)
    def pay_or_drop(deal: int, m: int, pot: int, chips: int) -> float:
        """自分がこのディールの敗者になった場合の価値。"""
        if deal > CHILD_TIME_LAST_DEAL:
            return 0.0                 # 大人の時間: 選択の余地なく脱落
        if chips < deal:
            return 0.0                 # 支払い不能 = 脱落
        pay = value_of_paying(deal, m, pot, chips)
        if my_rule == "always":
            return pay
        if my_rule == "never":
            return 0.0
        return max(pay, 0.0)           # optimal

    def value_of_paying(deal: int, m: int, pot: int, chips: int) -> float:
        left = chips - deal
        penalty = zero_cost if left == 0 else 0.0
        return -deal + play(deal + 1, m, pot + deal, left) - penalty

    return play, value_of_paying


def mc_value(deal, m, pot, chips, *, opp_continue, zero_cost, my_rule, rng, trials=200000):
    """同じモデルを素朴に回して検算する(my_rule は always / never のみ)。"""
    total = 0.0
    for _ in range(trials):
        d, mm, p, c, gain, alive = deal, m, pot, chips, 0.0, True
        while True:
            if mm <= 1:
                gain += p
                break
            if rng.randrange(mm) == 0:          # 自分が敗者
                if d > CHILD_TIME_LAST_DEAL or c < d or my_rule == "never":
                    alive = False
                    break
                gain -= d
                c -= d
                if c == 0:
                    gain -= zero_cost
                p += d
            else:                                # 相手が敗者
                if d <= CHILD_TIME_LAST_DEAL and opp_continue:
                    p += d
                else:
                    mm -= 1
                    if mm == 1:
                        gain += p
                        break
            d += 1
            if d > 60:
                gain += p / mm
                break
        total += gain if alive or True else gain
    return total / trials


def selftest(seed: int) -> None:
    rng = random.Random(seed)
    print("厳密解 vs モンテカルロ(200,000試行、許容差 0.05チップ)")
    print(f"{'状態':<34}{'自分の方針':<12}{'厳密':>9}{'MC':>9}{'差':>8}")
    worst = 0.0
    for deal in (1, 2, 3):
        for m in (2, 4, 6):
            pot, chips = m + 2 * (deal - 1), 20
            for rule in ("always", "never"):
                play, _ = make_solver(True, 0.0, rule)
                exact = play(deal, m, pot, chips)
                mc = mc_value(deal, m, pot, chips, opp_continue=True, zero_cost=0.0,
                              my_rule=rule, rng=rng)
                d = abs(exact - mc)
                worst = max(worst, d)
                flag = "" if d <= 0.05 else "  <-- 不一致"
                label = f"{deal}ディール目 {m}人 ポット{pot}"
                print(f"{label:<36}{rule:<12}{exact:>9.3f}{mc:>9.3f}{d:>8.3f}{flag}")
    print(f"\n最大の差 {worst:.3f} -> {'一致' if worst <= 0.05 else '不一致あり'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--seed", type=int, default=20261110)
    ap.add_argument("--policies", nargs="*", default=None)
    ap.add_argument("--policy-dir", default="policies")
    ap.add_argument("--opp-continue", choices=["always", "never"], default="always")
    ap.add_argument("--as-shipped", action="store_true",
                    help="方策には required_chips=1 を渡す(現状のbot.pyの挙動を再現)")
    ap.add_argument("--zero-cost", type=float, default=0.0,
                    help="支払いでチップが0になることの追加コスト(チップ換算)")
    args = ap.parse_args()

    if args.selftest:
        selftest(args.seed)
        return

    import pathlib
    load_policy_dir(pathlib.Path(args.policy_dir))
    names = args.policies or sorted(POLICIES)
    policies = {n: POLICIES[n]() for n in names}
    _, value_of_paying = make_solver(args.opp_continue == "always", args.zero_cost, "optimal")

    deals = (1, 2, 3)
    ms = (2, 3, 4, 5, 6)
    pots = (2, 4, 6, 8, 12, 16, 24)
    chip_levels = (1, 2, 3, 4, 6, 10, 16, 25)

    acc = {n: [0, 0.0, 0] for n in names}
    by_deal = {n: {d: [0, 0.0, 0] for d in deals} for n in names}
    rows = []
    for deal in deals:
        for m in ms:
            for pot in pots:
                # 到達可能性: 参加料で全員1枚払っているのでポットは最低 m 枚。
                # さらに前のディールの敗者の支払いが乗る(最低 1+2+...+(deal-1))。
                if pot < m + deal * (deal - 1) // 2:
                    continue
                for chips in chip_levels:
                    if chips < deal:
                        continue        # 支払い不能なので選択が存在しない
                    v = value_of_paying(deal, m, pot, chips)
                    if abs(v) <= 1e-9:
                        continue        # 無差別(どちらでも損しない)なので採点しない
                    want_pay = v > 0
                    ctx = PolicyContext(
                        own_rank="5", alive_count=m, deal_number=deal, pot_chips=pot,
                        my_chips=chips, is_dealer=False, unseen_counts={}, known_held={},
                        turn_actions_this_deal=0,
                        required_chips=1 if args.as_shipped else deal)
                    for name, pol in policies.items():
                        got = bool(pol.decide_continue_ctx(ctx))
                        regret = 0.0 if got == want_pay else abs(v)
                        for bucket in (acc[name], by_deal[name][deal]):
                            bucket[0] += int(got != want_pay)
                            bucket[1] += regret
                            bucket[2] += 1
                    rows.append((deal, m, pot, chips, v,
                                 {n: bool(policies[n].decide_continue_ctx(ctx)) for n in names}))

    n_states = acc[names[0]][2]
    print(f"状態数 {n_states}(ディール{deals} × 人数{ms} × ポット{pots} × 手持ち{chip_levels})")
    print(f"相手の継続方針: {args.opp_continue} / チップ0のコスト: {args.zero_cost}\n")
    print("== 継続判断が厳密解と違う率・平均リグレット(チップ) ==")
    print(f"{'方策':<24}{'1ディール':>12}{'2ディール':>12}{'3ディール':>12}{'全体':>12}{'リグレット':>12}")
    for name in names:
        cells = []
        for d in deals:
            mm, rr, nn = by_deal[name][d]
            cells.append(f"{100*mm/nn:>11.1f}%")
        mm, rr, nn = acc[name]
        print(f"{name:<26}" + "".join(cells) + f"{100*mm/nn:>11.1f}%{rr/nn:>12.3f}")

    print("\n== 厳密解が「降りるべき」と言う状態(抜粋) ==")
    drops = [r for r in rows if r[4] < 0]
    print(f"全{n_states}状態のうち降りるべきは {len(drops)} 件 ({100*len(drops)/n_states:.1f}%)")
    print(f"{'ディール':>8}{'人数':>6}{'ポット':>8}{'手持ち':>8}{'払う価値':>10}   方策の判断(払う=1)")
    for deal, m, pot, chips, v, got in drops[:12]:
        flags = " ".join(f"{n.split('_')[0][:4]}:{int(got[n])}" for n in names)
        print(f"{deal:>8}{m:>6}{pot:>8}{chips:>8}{v:>10.3f}   {flags}")


if __name__ == "__main__":
    main()

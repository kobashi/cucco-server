"""捨て札を仕込んだ局面で、方策の「交換するか」を厳密解と照合する。

勝率で方策を比べると3ptの差に7,000ゲーム必要なのに対し、カウンティングが
判断を変えるのは全判断の3.7%しかない。そこで勝率を介さず、局面ごとに
「このディールで自分が敗者になる確率」を厳密計算し、方策がその最小値を
選べているかを直接数える。分散がほぼ無いので、1万局面でも数秒で済む。

厳密解の範囲(重要):
- **1手先だけを見る近視眼的な最適解**。ディール中の以降の交換・宣言は
  考慮しない。方策自身が近似しようとしている量と同じ土俵なので「推定が
  合っているか」の尺度としては妥当だが、ゲーム論的最適解ではない。
- 相手の手札は未見札からの一様な引きとみなす(公開情報が無いときの
  正しい事後分布)。公開札がある局面は扱わない。
- タイは全員が敗者(src/cucco/domain/deal.py の open 判定と同じ)。
- 猫による自爆(自分の持ち札の元の持ち主が自分)は除外している
  (--own-original で有効化)。

使い方:
    python tools/oracle_check.py                # 既定の掃き出し
    python tools/oracle_check.py --samples 500  # 局面数を増やす
"""

from __future__ import annotations

import argparse
import math
import random
from collections import Counter

from cucco.ai.context import FULL_DECK_COUNTS, PolicyContext, strength_of
from cucco.ai.plugins import load_policy_dir
from cucco.ai.policies import POLICIES

JOKER, HUMAN, HORSE, CAT, HOUSE, CUCCO = "道化", "人間", "馬", "猫", "家", "クク"
PASS_THROUGH = (HORSE, HOUSE)


def _comb(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def p_all_at_least(unseen: Counter, threshold: int, k: int) -> float:
    """k人が未見札から引いたとき、全員の強さが threshold 以上になる確率。"""
    if k <= 0:
        return 1.0
    n = sum(unseen.values())
    if k > n:
        return 0.0
    strong = sum(c for r, c in unseen.items() if strength_of(r) >= threshold)
    return _comb(strong, k) / _comb(n, k)


def _without(unseen: Counter, rank: str) -> Counter:
    rest = Counter(unseen)
    rest[rank] -= 1
    if rest[rank] <= 0:
        del rest[rank]
    return rest


def p_lose_keep(own: str, unseen: Counter, k: int) -> float:
    """そのまま持ち続けた場合に自分が敗者になる確率。"""
    return p_all_at_least(unseen, strength_of(own), k)


def p_lose_deck(own: str, unseen: Counter, k: int, own_original: bool, depth: int = 0) -> float:
    """親が山札と交換した場合(docs/rules/final_rules.md の山札欄)。"""
    n = sum(unseen.values())
    if n == 0:
        return p_lose_keep(own, unseen, k)
    acc = 0.0
    for rank, count in unseen.items():
        p = count / n
        rest = _without(unseen, rank)
        if rank == JOKER:
            outcome = 0.0                      # 山札の道化は例外的に最強
        elif rank == HUMAN:
            outcome = 1.0                      # 引いた本人が即失格
        elif rank == CAT:
            outcome = 1.0 if own_original else p_lose_keep(own, rest, k)
        elif rank in PASS_THROUGH:
            outcome = (p_lose_keep(own, rest, k) if depth >= 3
                       else p_lose_deck(own, rest, k, own_original, depth + 1))
        elif rank == CUCCO:
            outcome = p_lose_keep(own, rest, k)  # 交換不成立=現状維持
        else:
            outcome = p_all_at_least(rest, strength_of(rank), k)
        acc += p * outcome
    return acc


def p_lose_swap(own: str, unseen: Counter, k: int, own_original: bool) -> float:
    """非親がプレイヤーと交換した場合。相手は未見札からの一様な引きとみなす。"""
    n = sum(unseen.values())
    if n == 0 or k <= 0:
        return p_lose_keep(own, unseen, k)
    own_s = strength_of(own)
    acc = 0.0
    for rank, count in unseen.items():
        p = count / n
        rest = _without(unseen, rank)
        if rank == HUMAN:
            outcome = 1.0                      # 要求者が即失格
        elif rank == JOKER:
            outcome = 1.0                      # 道化を受け取った側が即失格
        elif rank == CUCCO:
            outcome = 0.0                      # 最強札を受け取る
        elif rank == CAT:
            # 拒否される。自爆しなければ手札はそのままで、相手は猫を持ったまま残る。
            if own_original:
                outcome = 1.0
            else:
                outcome = (p_all_at_least(rest, own_s, k - 1)
                           if strength_of(CAT) >= own_s else 0.0)
        elif rank in PASS_THROUGH:
            # 拒否され手札は変わらない。相手は馬/家を持ったまま残るので、
            # それが自分より弱ければ自分は敗者にならない。
            outcome = (p_all_at_least(rest, own_s, k - 1)
                       if strength_of(rank) >= own_s else 0.0)
        else:
            new_s = strength_of(rank)
            # 交換後、相手は自分の元の札を持つ。それが新しい札より弱ければ自分は敗者にならない。
            outcome = 0.0 if own_s < new_s else p_all_at_least(rest, new_s, k - 1)
        acc += p * outcome
    return acc


def make_state(rng: random.Random, seen: int) -> tuple[str, Counter, bool]:
    """未見札から seen 枚を抜いた局面を1つ作る。"""
    deck = [r for r, c in FULL_DECK_COUNTS.items() for _ in range(c)]
    rng.shuffle(deck)
    own = deck.pop()
    for _ in range(min(seen, len(deck) - 8)):   # 相手に配る分を残す
        deck.pop()
    return own, Counter(deck), False


def context_for(own: str, unseen: Counter, alive: int, is_dealer: bool, deal_number: int) -> PolicyContext:
    return PolicyContext(
        own_rank=own, alive_count=alive, deal_number=deal_number,
        pot_chips=alive, my_chips=20, is_dealer=is_dealer,
        unseen_counts=dict(unseen), known_held={}, turn_actions_this_deal=0,
    )



def _mc_lose(own, unseen, k, own_original, action, rng, trials=40000):
    """厳密解の検算用モンテカルロ。1手先モデルをそのまま素朴に回す。"""
    deck = [r for r, c in unseen.items() for _ in range(c)]
    own_s = strength_of(own)
    lost = 0
    for _ in range(trials):
        hand = deck[:]
        rng.shuffle(hand)
        draw = iter(hand)
        mine_s = own_s
        partner_s = None
        if action == "swap":
            got = next(draw)
            if got in (HUMAN, JOKER):
                lost += 1
                continue
            if got == CUCCO:
                continue
            if got == CAT:
                if own_original:
                    lost += 1
                    continue
                partner_s = strength_of(CAT)
            elif got in PASS_THROUGH:
                partner_s = strength_of(got)
            else:
                partner_s = own_s
                mine_s = strength_of(got)
        elif action == "deck":
            dq = False
            while True:
                got = next(draw)
                if got == JOKER:
                    mine_s = None
                    break
                if got == HUMAN:
                    dq = True
                    break
                if got == CAT:
                    dq = own_original
                    break
                if got in PASS_THROUGH:
                    continue
                if got == CUCCO:
                    break
                mine_s = strength_of(got)
                break
            if dq:
                lost += 1
                continue
            if mine_s is None:
                continue
        need = k - (1 if partner_s is not None else 0)
        others = [strength_of(next(draw)) for _ in range(need)]
        if partner_s is not None:
            others.append(partner_s)
        if all(s >= mine_s for s in others):
            lost += 1
    return lost / trials


def selftest(seed: int) -> None:
    rng = random.Random(seed)
    print("厳密解 vs モンテカルロ(40,000試行、許容差 0.01)")
    print(f"{'局面':<36}{'手':<6}{'厳密':>8}{'MC':>8}{'差':>8}")
    worst = 0.0
    for depth in (0, 12, 24):
        for alive in (2, 4, 6):
            own, unseen, _ = make_state(rng, depth)
            k = alive - 1
            for action, exact in (
                ("keep", p_lose_keep(own, unseen, k)),
                ("swap", p_lose_swap(own, unseen, k, False)),
                ("deck", p_lose_deck(own, unseen, k, False)),
            ):
                mc = _mc_lose(own, unseen, k, False, action, rng)
                d = abs(exact - mc)
                worst = max(worst, d)
                flag = "" if d <= 0.01 else "  <-- 不一致"
                label = f"{own} 見えた{depth}枚 {alive}人"
                print(f"{label:<38}{action:<6}{exact:>8.4f}{mc:>8.4f}{d:>8.4f}{flag}")
    print(f"\n最大の差 {worst:.4f} -> {'一致' if worst <= 0.01 else '不一致あり'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=int, default=400, help="(見えた枚数 × 人数)あたりの局面数")
    ap.add_argument("--seed", type=int, default=20261110)
    ap.add_argument("--policies", nargs="*", default=None)
    ap.add_argument("--policy-dir", default="policies")
    ap.add_argument("--own-original", action="store_true", help="猫の自爆リスクを有効にする")
    ap.add_argument("--repeats", type=int, default=5, help="確率的な方策を何回引くか")
    ap.add_argument("--selftest", action="store_true", help="厳密解をモンテカルロで検算して終了")
    args = ap.parse_args()

    if args.selftest:
        selftest(args.seed)
        return

    builtin = set(POLICIES)
    load_policy_dir(__import__("pathlib").Path(args.policy_dir))
    names = args.policies or sorted(POLICIES)
    policies = {n: POLICIES[n]() for n in names}
    rng = random.Random(args.seed)

    depths = (0, 5, 10, 15, 20, 25)
    alives = (2, 3, 4, 5, 6)
    # [policy][depth] -> [mismatches, regret_sum, n]
    acc = {n: {d: [0, 0.0, 0] for d in depths} for n in names}
    acc_dealer = {n: [0, 0.0, 0] for n in names}
    acc_player = {n: [0, 0.0, 0] for n in names}
    acc_alive = {n: {a: [0, 0.0, 0] for a in alives} for n in names}

    for depth in depths:
        for alive in alives:
            for _ in range(args.samples):
                own, unseen, _ = make_state(rng, depth)
                own_original = args.own_original
                k = alive - 1
                for is_dealer in (False, True):
                    keep = p_lose_keep(own, unseen, k)
                    change = (p_lose_deck(own, unseen, k, own_original) if is_dealer
                              else p_lose_swap(own, unseen, k, own_original))
                    best = min(keep, change)
                    want_change = change < keep
                    ctx = context_for(own, unseen, alive, is_dealer, rng.choice((1, 2, 3, 4, 5)))
                    for name, pol in policies.items():
                        # 乱数を使う方策は1局面で何度も引いて平均を取る。
                        # 内蔵の方策は決定的なので1回でよい。
                        reps = 1 if name in builtin else args.repeats
                        for _ in range(reps):
                            got = bool(pol.decide_change_ctx(ctx))
                            regret = (change if got else keep) - best
                            bucket = acc[name][depth]
                            bucket[0] += int(got != want_change)
                            bucket[1] += regret
                            bucket[2] += 1
                            for side in (acc_dealer[name] if is_dealer else acc_player[name],
                                         acc_alive[name][alive]):
                                side[0] += int(got != want_change)
                                side[1] += regret
                                side[2] += 1

    total = min(sum(acc[n][d][2] for d in depths) for n in names)
    print(f"局面数 {total:,}(方策ごと) / 捨て札の深さ {depths} / 人数 {alives}")
    print(f"猫の自爆リスク: {'有効' if args.own_original else '無効'}\n")

    head = "見えた枚数 " + " ".join(f"{d:>7}" for d in depths) + "   全体"
    for label, idx, unit in (("判断が厳密解と違う率", 0, "%"), ("平均リグレット(敗北確率の損)", 1, "")):
        print(f"== {label} ==")
        print(head)
        for name in names:
            cells = []
            for d in depths:
                m, r, n = acc[name][d]
                cells.append(f"{100*m/n:>6.1f}%" if idx == 0 else f"{r/n:>7.4f}")
            m = sum(acc[name][d][0] for d in depths)
            r = sum(acc[name][d][1] for d in depths)
            n = sum(acc[name][d][2] for d in depths)
            tail = f"{100*m/n:>6.1f}%" if idx == 0 else f"{r/n:>7.4f}"
            print(f"{name:<24}" + " ".join(cells) + f"  {tail}")
        print()

    print("== 親(山札交換)と非親(プレイヤー交換)の内訳 ==")
    print(f"{'方策':<24}{'親 違い率':>10}{'親 リグレット':>14}{'非親 違い率':>12}{'非親 リグレット':>16}")
    for name in names:
        dm, dr, dn = acc_dealer[name]
        pm, pr, pn = acc_player[name]
        print(f"{name:<26}{100*dm/dn:>8.1f}%{dr/dn:>14.4f}{100*pm/pn:>11.1f}%{pr/pn:>16.4f}")

    print("\n== 人数別の平均リグレット ==")
    print("人数" + " ".join(f"{a:>8}人" for a in alives))
    for name in names:
        cells = " ".join(f"{acc_alive[name][a][1]/acc_alive[name][a][2]:>8.4f}" for a in alives)
        print(f"{name:<24}" + cells)


if __name__ == "__main__":
    main()

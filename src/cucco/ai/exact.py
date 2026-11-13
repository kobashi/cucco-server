"""「このディールで自分が敗者になる確率」の厳密解(近視眼的)。

tools/oracle_check.py が方策の採点に使っていた計算をここへ出した。方策側
(`CountingExact`)と採点側が**同じ式**を使うようにするため。

モデルの範囲(tools/oracle_check.py の見出しと同じ):
- **1手先だけ**を見る。自分が今する判断の直後に開示が来るとみなし、以降の
  他家の交換やクク宣言は数えない。方策が近似しようとしている量そのもの。
- 相手の手札は未見札からの一様な引きとみなす(公開情報が無いときの正しい
  事後分布)。札が割れている席は呼び出し側が人数から外すこと。
- タイは全員が敗者(src/cucco/domain/deal.py の open 判定と同じ)。
- `own_original` = 自分が今持っている札の**元の持ち主が自分**か。猫を引く/
  猫に拒否されると、失格するのは「要求者の札の元の持ち主」なので、ここが
  True だと交換は自爆の危険を伴う(deal.py の provenance)。

計算は未見札を**強さ順の本数ベクトル**にしてから回す。素朴に Counter を
コピーしながら再帰すると1判断 2.3ms かかり、評価走行の処理能力が半分に
なってしまう。抜いた札は「何を抜いたか」のリストだけ持ち回り、累積和から
その場で引く(抜く枚数はたかだか4枚)。
"""

from __future__ import annotations

import math

from cucco.domain.cards import RANK_ORDER

JOKER, HUMAN, HORSE, CAT, HOUSE, CUCCO = "道化", "人間", "馬", "猫", "家", "クク"
PASS_THROUGH = (HORSE, HOUSE)  # 拒否して次の席へ回す札

_N_RANKS = len(RANK_ORDER)
_RANK_AT = tuple(r.value for r in RANK_ORDER)       # 強さ順の札名
_INDEX = {r.value: i for i, r in enumerate(RANK_ORDER)}
_S_JOKER = _INDEX[JOKER]
_S_HUMAN = _INDEX[HUMAN]
_S_CAT = _INDEX[CAT]
_S_CUCCO = _INDEX[CUCCO]
_S_PASS = frozenset({_INDEX[HORSE], _INDEX[HOUSE]})

_comb_cache: dict[tuple[int, int], int] = {}


def _comb(n: int, k: int) -> int:
    if k < 0 or k > n or n < 0:
        return 0
    key = (n, k)
    hit = _comb_cache.get(key)
    if hit is None:
        hit = _comb_cache[key] = math.comb(n, k)
    return hit


def _vector(unseen) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    """未見札 -> (本数ベクトル, 「強さ t 以上」の累積和, 総枚数)。"""
    counts = [0] * _N_RANKS
    for rank, n in unseen.items():
        if n > 0:
            counts[_INDEX[rank]] = n
    suffix = [0] * (_N_RANKS + 1)
    for t in range(_N_RANKS - 1, -1, -1):
        suffix[t] = suffix[t + 1] + counts[t]
    return tuple(counts), tuple(suffix), suffix[0]


def _p_all_at_least(suffix, total, gone, threshold: int, k: int) -> float:
    """k人が未見札から引いたとき、全員の強さが threshold 以上になる確率。

    `gone` は既に抜いた札の強さの並び(たかだか4枚)。
    """
    if k <= 0:
        return 1.0
    n = total - len(gone)
    if k > n:
        return 0.0
    strong = suffix[threshold] - sum(1 for s in gone if s >= threshold)
    return _comb(strong, k) / _comb(n, k)


def _keep(suffix, total, gone, own_s: int, k: int) -> float:
    return _p_all_at_least(suffix, total, gone, own_s, k)


def _deck(counts, suffix, total, gone, own_s: int, k: int, own_original: bool, depth: int) -> float:
    n = total - len(gone)
    if n == 0:
        return _keep(suffix, total, gone, own_s, k)
    acc = 0.0
    for s in range(_N_RANKS):
        left = counts[s] - gone.count(s)
        if left <= 0:
            continue
        p = left / n
        rest = gone + (s,)
        if s == _S_JOKER:
            outcome = 0.0                       # 山札の道化は例外的に最強
        elif s == _S_HUMAN:
            outcome = 1.0                       # 引いた本人が即失格
        elif s == _S_CAT:
            outcome = 1.0 if own_original else _keep(suffix, total, rest, own_s, k)
        elif s in _S_PASS:
            outcome = (_keep(suffix, total, rest, own_s, k) if depth >= 3
                       else _deck(counts, suffix, total, rest, own_s, k, own_original, depth + 1))
        elif s == _S_CUCCO:
            outcome = _keep(suffix, total, rest, own_s, k)   # 交換不成立=現状維持
        else:
            outcome = _p_all_at_least(suffix, total, rest, s, k)
        acc += p * outcome
    return acc


def _swap(counts, suffix, total, own_s: int, k: int, own_original: bool) -> float:
    n = total
    if n == 0 or k <= 0:
        return _keep(suffix, total, (), own_s, k)
    acc = 0.0
    for s in range(_N_RANKS):
        left = counts[s]
        if left <= 0:
            continue
        p = left / n
        rest = (s,)
        if s == _S_HUMAN:
            outcome = 1.0                       # 要求者が即失格
        elif s == _S_JOKER:
            outcome = 1.0                       # 道化を受け取った側が即失格
        elif s == _S_CUCCO:
            outcome = 0.0                       # 最強札を受け取る
        elif s == _S_CAT:
            # 拒否される。自爆しなければ手札はそのままで、相手は猫を持ったまま残る。
            if own_original:
                outcome = 1.0
            else:
                outcome = (_p_all_at_least(suffix, total, rest, own_s, k - 1)
                           if _S_CAT >= own_s else 0.0)
        elif s in _S_PASS:
            # 拒否され手札は変わらない。相手は馬/家を持ったまま残るので、
            # それが自分より弱ければ自分は敗者にならない。
            outcome = (_p_all_at_least(suffix, total, rest, own_s, k - 1)
                       if s >= own_s else 0.0)
        else:
            # 交換後、相手は自分の元の札を持つ。それが新しい札より弱ければ自分は敗者にならない。
            outcome = 0.0 if own_s < s else _p_all_at_least(suffix, total, rest, s, k - 1)
        acc += p * outcome
    return acc


# -- 公開API(未見札は Counter / dict のどちらでもよい)----------------------

def p_all_at_least(unseen, threshold: int, k: int) -> float:
    _counts, suffix, total = _vector(unseen)
    return _p_all_at_least(suffix, total, (), threshold, k)


def p_lose_keep(own: str, unseen, k: int) -> float:
    """そのまま持ち続けた場合に自分が敗者になる確率。"""
    _counts, suffix, total = _vector(unseen)
    return _keep(suffix, total, (), _INDEX[own], k)


def p_lose_deck(own: str, unseen, k: int, own_original: bool, depth: int = 0) -> float:
    """山札と交換した場合(docs/rules/final_rules.md の山札欄)。"""
    counts, suffix, total = _vector(unseen)
    return _deck(counts, suffix, total, (), _INDEX[own], k, own_original, depth)


def p_lose_swap(own: str, unseen, k: int, own_original: bool) -> float:
    """プレイヤーと交換した場合。相手は未見札からの一様な引きとみなす。"""
    counts, suffix, total = _vector(unseen)
    return _swap(counts, suffix, total, _INDEX[own], k, own_original)

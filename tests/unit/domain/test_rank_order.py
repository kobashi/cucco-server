from cucco.domain.cards import RANK_ORDER, Rank, full_deck, strength


def test_rank_order_has_22_ranks_weakest_to_strongest():
    assert len(RANK_ORDER) == 22
    assert RANK_ORDER[0] is Rank.JOKER
    assert RANK_ORDER[-1] is Rank.CUCCO
    # Exact sequence from docs/rules/final_rules.md:
    # 道化 < 獅子 < 仮面 < 桶 < 0 < 1 < ... < 12 < 家 < 猫 < 馬 < 人間 < クク
    expected = (
        Rank.JOKER, Rank.LION, Rank.MASK, Rank.BUCKET,
        Rank.N0, Rank.N1, Rank.N2, Rank.N3, Rank.N4, Rank.N5, Rank.N6,
        Rank.N7, Rank.N8, Rank.N9, Rank.N10, Rank.N11, Rank.N12,
        Rank.HOUSE, Rank.CAT, Rank.HORSE, Rank.HUMAN, Rank.CUCCO,
    )
    assert RANK_ORDER == expected


def test_strength_is_monotonically_increasing_along_rank_order():
    strengths = [strength(rank) for rank in RANK_ORDER]
    assert strengths == sorted(strengths)
    assert len(set(strengths)) == 22


def test_elevated_joker_is_stronger_than_cucco():
    assert strength(Rank.JOKER, elevated=True) > strength(Rank.CUCCO)


def test_only_joker_can_be_elevated():
    import pytest

    with pytest.raises(ValueError):
        strength(Rank.CUCCO, elevated=True)


def test_full_deck_has_44_cards_two_of_each_rank():
    deck = full_deck()
    assert len(deck) == 44
    for rank in Rank:
        assert deck.count(rank) == 2

"""CountingPolicy decision rules (docs/ai-advanced-policies.md 案A/案D)."""

import pytest

from cucco.ai.context import FULL_DECK_COUNTS, PolicyContext
from cucco.ai.policies import CountingAggressive, CountingConservative, make_policy


def ctx_with(**overrides) -> PolicyContext:
    base = dict(
        own_rank="6",
        alive_count=4,
        deal_number=5,  # 大人の時間 by default
        pot_chips=4,
        my_chips=20,
        is_dealer=False,
        unseen_counts=dict(FULL_DECK_COUNTS),
        known_held={},
        turn_actions_this_deal=0,
        required_chips=1,
    )
    base.update(overrides)
    return PolicyContext(**base)


def test_registered_and_constructible():
    assert make_policy("counting_aggressive").name == "counting_aggressive"
    assert make_policy("counting_conservative").name == "counting_conservative"


@pytest.mark.parametrize("rank", ["クク", "人間", "馬", "猫", "家"])
def test_specials_never_change(rank):
    assert CountingAggressive().decide_change_ctx(ctx_with(own_rank=rank)) is False


@pytest.mark.parametrize("rank", ["道化", "獅子", "仮面", "桶"])
def test_weakest_tier_always_changes(rank):
    assert CountingConservative().decide_change_ctx(ctx_with(own_rank=rank)) is True


def test_certainly_weakest_changes():
    # Every card weaker than our 1 is in the discard pile: we hold the
    # weakest live card, so we must change no matter the thresholds.
    unseen = dict(FULL_DECK_COUNTS)
    for rank in ("道化", "獅子", "仮面", "桶", "0"):
        unseen[rank] = 0
    unseen["1"] = 1  # our own card removed
    assert CountingConservative().decide_change_ctx(ctx_with(own_rank="1", unseen_counts=unseen)) is True


def test_known_weaker_holder_means_no_change():
    # An opponent's card is publicly known (e.g. a revealed refusal) and is
    # weaker than ours: we cannot be the weakest, so never change -- even
    # for the aggressive variant holding a low number.
    assert (
        CountingAggressive().decide_change_ctx(ctx_with(own_rank="2", known_held={"opp": "獅子"}))
        is False
    )


def test_high_card_holds_and_low_card_changes():
    aggressive = CountingAggressive()
    assert aggressive.decide_change_ctx(ctx_with(own_rank="12")) is False
    assert aggressive.decide_change_ctx(ctx_with(own_rank="0")) is True


def test_matches_the_matrix_rows_on_a_fresh_deck():
    # The aggressive cutoff reproduces play_summary's チェンジ基準 rows when
    # nothing has been counted yet: ≤7 changes at n=3, ≤3 at n=7.
    aggressive = CountingAggressive()

    def probe(rank, alive):
        unseen = dict(FULL_DECK_COUNTS)
        unseen[rank] -= 1
        return aggressive.decide_change_ctx(ctx_with(own_rank=rank, alive_count=alive, unseen_counts=unseen))

    assert probe("7", 3) is True
    assert probe("8", 3) is False
    assert probe("3", 7) is True
    assert probe("4", 7) is False


def test_conservative_needs_more_certainty_than_aggressive():
    # A mid card at a 3-player table: aggressive changes, conservative holds.
    unseen = dict(FULL_DECK_COUNTS)
    unseen["6"] = 1
    probe = ctx_with(own_rank="6", alive_count=3, unseen_counts=unseen)
    assert CountingAggressive().decide_change_ctx(probe) is True
    assert CountingConservative().decide_change_ctx(probe) is False


def test_counting_shifts_the_decision_as_weak_cards_leave_play():
    # Same hand, same table size -- but the discard pile has swallowed the
    # weak tier, so what was a comfortable hold becomes a forced-ish change.
    aggressive = CountingAggressive()
    fresh = dict(FULL_DECK_COUNTS)
    fresh["9"] = 1
    assert aggressive.decide_change_ctx(ctx_with(own_rank="9", alive_count=3, unseen_counts=fresh)) is False
    depleted = dict(fresh)
    for rank in ("道化", "獅子", "仮面", "桶", "0", "1", "2", "3", "4", "5"):
        depleted[rank] = 0
    assert aggressive.decide_change_ctx(ctx_with(own_rank="9", alive_count=3, unseen_counts=depleted)) is True


def test_child_time_discourages_risky_changes():
    unseen = dict(FULL_DECK_COUNTS)
    unseen["5"] = 1
    adult = ctx_with(own_rank="5", alive_count=3, unseen_counts=unseen, deal_number=6)
    child = ctx_with(own_rank="5", alive_count=3, unseen_counts=unseen, deal_number=2)
    conservative = CountingConservative()
    assert conservative.decide_change_ctx(adult) is True
    assert conservative.decide_change_ctx(child) is False


def test_high_refusal_danger_discourages_changing():
    # 人間/猫 all four still unseen vs. all gone: the borderline hand flips.
    conservative = CountingConservative()
    unseen = dict(FULL_DECK_COUNTS)
    unseen["5"] = 1
    dangerous = ctx_with(own_rank="5", alive_count=3, unseen_counts=unseen, deal_number=6)
    safe_counts = dict(unseen)
    safe_counts["人間"] = 0
    safe_counts["猫"] = 0
    safe = ctx_with(own_rank="5", alive_count=3, unseen_counts=safe_counts, deal_number=6)
    danger_of = lambda c: (c.unseen_counts["人間"] + c.unseen_counts["猫"]) / c.unseen_total  # noqa: E731
    assert danger_of(dangerous) > danger_of(safe)
    # Lower danger can only make changing MORE likely, never less.
    assert (not conservative.decide_change_ctx(dangerous)) or conservative.decide_change_ctx(safe)


def test_cucco_declared_at_own_prompt():
    assert CountingAggressive().decide_cucco_declare_ctx(ctx_with(own_rank="クク")) is True


def test_continue_aggressive_pays_whenever_a_chip_remains():
    aggressive = CountingAggressive()
    assert aggressive.decide_continue_ctx(ctx_with(my_chips=3, required_chips=2)) is True
    assert aggressive.decide_continue_ctx(ctx_with(my_chips=2, required_chips=2)) is False


def test_continue_conservative_wants_a_buffer_and_a_worthwhile_pot():
    conservative = CountingConservative()
    assert conservative.decide_continue_ctx(ctx_with(my_chips=10, required_chips=2, pot_chips=4)) is True
    # No buffer left after paying: decline.
    assert conservative.decide_continue_ctx(ctx_with(my_chips=5, required_chips=3, pot_chips=6)) is False
    # The pot isn't worth the price: decline.
    assert conservative.decide_continue_ctx(ctx_with(my_chips=20, required_chips=3, pot_chips=2)) is False


def test_legacy_signature_still_works():
    # A student harness calling the old two-argument API gets matrix
    # behavior instead of an exception.
    assert CountingAggressive().decide_change("道化", 4) is True
    assert CountingAggressive().decide_change("クク", 4) is False

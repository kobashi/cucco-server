import pytest

from clients.mock_ai.policies import (
    DEFAULT_MATRIX,
    AlwaysChange,
    AlwaysNoChange,
    MatrixPolicy,
    make_policy,
    matrix_row,
)


def test_always_change_changes_even_the_strongest_card():
    policy = AlwaysChange()
    assert policy.decide_change("クク", 5) is True
    assert policy.decide_change("道化", 2) is True


def test_always_no_change_keeps_even_the_weakest_card():
    policy = AlwaysNoChange()
    assert policy.decide_change("道化", 5) is False
    assert policy.decide_change("0", 7) is False


def test_matrix_never_changes_the_refusing_or_punishing_specials():
    policy = MatrixPolicy()
    for alive in (2, 4, 7, 10):
        for rank in ("クク", "人間", "馬", "猫", "家"):
            assert policy.decide_change(rank, alive) is False, (rank, alive)


def test_matrix_always_changes_the_weak_plain_cards():
    policy = MatrixPolicy()
    for alive in (2, 4, 7, 10):
        for rank in ("道化", "獅子", "仮面", "桶"):
            assert policy.decide_change(rank, alive) is True, (rank, alive)


def test_matrix_number_thresholds_follow_the_play_summary_table():
    policy = MatrixPolicy()
    # 3人以下: 7以下チェンジ / 7人: 3以下チェンジ (docs/rules/play_summary_granpere.md)
    assert policy.decide_change("7", 3) is True
    assert policy.decide_change("8", 3) is False
    assert policy.decide_change("3", 7) is True
    assert policy.decide_change("4", 7) is False
    # Unknown large table sizes fall back to the most conservative row.
    assert policy.decide_change("3", 12) is True
    assert policy.decide_change("4", 12) is False


def test_matrix_rows_are_exact_sets_not_just_thresholds():
    row = matrix_row(5)
    assert "5" in row and "6" not in row
    assert "道化" in row and "クク" not in row


def test_custom_matrix_overrides_the_default():
    policy = MatrixPolicy(matrix={2: frozenset({"12"})})
    assert policy.decide_change("12", 2) is True
    assert policy.decide_change("0", 2) is False  # only what the custom row lists
    # Counts missing from the custom matrix use the built-in fallback row.
    assert policy.decide_change("3", 9) is True


def test_every_baseline_policy_declares_cucco_immediately():
    # Seminar decision: 低位のMockAIはクク宣言について即時宣言とする.
    for policy in (MatrixPolicy(), AlwaysChange(), AlwaysNoChange()):
        assert policy.decide_cucco_declare("クク", 4) is True, policy.name


def test_make_policy_resolves_names_and_rejects_unknown():
    assert isinstance(make_policy("matrix"), MatrixPolicy)
    assert isinstance(make_policy("always_change"), AlwaysChange)
    with pytest.raises(ValueError):
        make_policy("nope")


def test_default_matrix_covers_the_documented_player_counts():
    assert set(DEFAULT_MATRIX) == {2, 3, 4, 5, 6, 7}

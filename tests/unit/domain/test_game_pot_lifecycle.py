import random

from cucco.domain.config import GameConfig
from cucco.domain.events import GameEnded, PotStarted, PotWipedOut, PotWon
from cucco.domain.game import Game
from cucco.domain.pot import CHILD_TIME_DEALS


def make_game(seats: list[str], config: GameConfig | None = None, seed: int = 0) -> Game:
    return Game(seats, config or GameConfig(), random.Random(seed))


def test_start_first_pot_charges_entry_fee_to_everyone():
    game = make_game(["A", "B", "C"])
    events = game.start_first_pot()
    assert game.chips == {"A": 24, "B": 24, "C": 24}
    started = events[0]
    assert isinstance(started, PotStarted)
    assert started.pot_number == 1
    assert set(started.participants) == {"A", "B", "C"}
    assert started.chips_now == {"A": 24, "B": 24, "C": 24}
    assert started.entry_fee_waived is False


def test_pot_won_starts_next_pot_with_fresh_entry_fee_when_no_end_condition_met():
    game = make_game(["A", "B", "C"])
    game.start_first_pot()
    game.chips["A"] = 30  # simulate pot 1 having been won by A; B, C stay at 24
    events = game.process_pot_outcome(PotWon(winner="A", amount=6, chips_now=30))

    assert not game.is_finished
    started = next(e for e in events if isinstance(e, PotStarted))
    assert started.pot_number == 2
    assert started.entry_fee_waived is False
    assert game.chips["A"] == 29  # paid entry fee for pot 2


def test_pot_won_ends_the_game_if_someone_is_at_zero_chips():
    game = make_game(["A", "B", "C"])
    game.start_first_pot()
    game.chips.update({"A": 30, "B": 0, "C": 24})

    events = game.process_pot_outcome(PotWon(winner="A", amount=6, chips_now=30))

    assert game.is_finished
    ended = next(e for e in events if isinstance(e, GameEnded))
    assert ended.ranking[0] == ("A", 30)
    assert not any(isinstance(e, PotStarted) for e in events)


def test_pot_won_excludes_insolvent_seats_from_the_next_pot_under_round_limit():
    # Under "round_limit", reaching 0 chips does NOT end the game by itself
    # (unlike "chips_zero") -- but an insolvent seat must still be excluded
    # from the next pot's entry-fee charge, or their chips go negative.
    config = GameConfig(end_condition="round_limit", round_limit=100)
    game = make_game(["A", "B", "C"], config=config)
    game.start_first_pot()
    game.chips.update({"A": 30, "B": 0, "C": 24})  # B went insolvent in pot 1

    events = game.process_pot_outcome(PotWon(winner="A", amount=6, chips_now=30))

    assert not game.is_finished
    started = next(e for e in events if isinstance(e, PotStarted))
    assert set(started.participants) == {"A", "C"}
    assert game.chips["B"] == 0  # not charged an entry fee it can't afford


def test_pot_won_ends_the_game_under_round_limit_if_fewer_than_two_seats_are_solvent():
    config = GameConfig(end_condition="round_limit", round_limit=100)
    game = make_game(["A", "B", "C"], config=config)
    game.start_first_pot()
    game.chips.update({"A": 30, "B": 0, "C": 0})

    events = game.process_pot_outcome(PotWon(winner="A", amount=6, chips_now=30))

    assert game.is_finished
    ended = next(e for e in events if isinstance(e, GameEnded))
    assert ended.ranking[0] == ("A", 30)
    assert not any(isinstance(e, PotStarted) for e in events)


def test_wipeout_with_one_nonzero_survivor_wins_immediately_without_a_deal():
    game = make_game(["A", "B", "C"])
    game.start_first_pot()
    game.chips.update({"A": 0, "B": 0, "C": 10})  # only C has chips left

    events = game.process_pot_outcome(PotWipedOut(amount=6))

    won = next(e for e in events if isinstance(e, PotWon))
    assert won.winner == "C"
    assert won.amount == 6
    assert game.chips["C"] == 16
    # A and B are still at 0 chips, so once this pot's destination is
    # decided the game ends immediately -- no further pot is started.
    assert game.is_finished
    assert any(isinstance(e, GameEnded) for e in events)
    assert not any(isinstance(e, PotStarted) for e in events)


def test_wipeout_with_zero_survivors_revives_everyone_at_adult_time_fee_waived():
    game = make_game(["A", "B", "C"])
    game.start_first_pot()
    game.chips.update({"A": 0, "B": 0, "C": 0})

    events = game.process_pot_outcome(PotWipedOut(amount=9))

    started = next(e for e in events if isinstance(e, PotStarted))
    assert set(started.participants) == {"A", "B", "C"}
    assert started.entry_fee_waived is True
    assert game.chips == {"A": 0, "B": 0, "C": 0}  # nobody paid anything
    assert not game.is_finished
    assert game.current_pot is not None
    assert game.current_pot.pot_chips == 9
    assert game.current_pot.deal_number == CHILD_TIME_DEALS  # next deal is adult time (4)


def test_wipeout_with_multiple_survivors_continues_without_a_new_entry_fee():
    game = make_game(["A", "B", "C", "D"])
    game.start_first_pot()
    game.chips.update({"A": 0, "B": 5, "C": 5, "D": 0})

    events = game.process_pot_outcome(PotWipedOut(amount=4))

    started = next(e for e in events if isinstance(e, PotStarted))
    assert set(started.participants) == {"B", "C"}
    assert started.entry_fee_waived is True
    assert game.chips["B"] == 5 and game.chips["C"] == 5  # unchanged, no fee
    assert game.current_pot.pot_chips == 4


def test_round_limit_end_condition():
    config = GameConfig(end_condition="round_limit", round_limit=2)
    game = make_game(["A", "B", "C"], config=config)
    game.start_first_pot()
    game.chips.update({"A": 30, "B": 24, "C": 24})

    game.note_deal_played()
    events = game.process_pot_outcome(PotWon(winner="A", amount=6, chips_now=30))
    assert not game.is_finished  # only 1 deal played so far
    assert any(isinstance(e, PotStarted) for e in events)

    game.note_deal_played()
    events2 = game.process_pot_outcome(PotWon(winner="A", amount=6, chips_now=36))
    assert game.is_finished  # round_limit (2) reached
    assert any(isinstance(e, GameEnded) for e in events2)


def test_force_end_produces_a_final_ranking():
    game = make_game(["A", "B", "C"])
    game.start_first_pot()
    game.chips.update({"A": 10, "B": 30, "C": 8})

    events = game.force_end()

    assert game.is_finished
    ended = next(e for e in events if isinstance(e, GameEnded))
    assert ended.ranking == (("B", 30), ("A", 10), ("C", 8))

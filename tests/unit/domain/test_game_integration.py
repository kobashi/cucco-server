"""An end-to-end smoke test driving real Deal/Pot/Game objects together
(no mocked outcomes), to catch integration issues the isolated Pot/Game
unit tests (which simulate outcomes directly) wouldn't see."""

import random

import pytest

from cucco.domain.config import GameConfig
from cucco.domain.events import ContinuePrompted, GameEnded, PotWipedOut, PotWon
from cucco.domain.game import Game


def _play_one_deal_all_no_change(game: Game) -> tuple:
    deal = game.current_pot.start_next_deal()
    for pid in deal.order:
        deal.submit_no_change(pid)
    opened = deal.open()[0]
    game.note_deal_played()
    return deal, opened.losers


@pytest.mark.parametrize("seed", range(20))
def test_full_game_runs_to_completion_with_a_real_deck(seed):
    config = GameConfig(starting_chips=5)  # small stack so the game ends quickly
    game = Game(["A", "B", "C"], config, random.Random(seed))
    game.start_first_pot()

    # Drive deals until the game reports finished, using a real shuffled
    # deck and only "no change" declarations (the simplest possible policy).
    # Everyone declining every turn always converges on a decision (weakest
    # dealt hand loses) so this terminates.
    guard = 0
    while not game.is_finished:
        guard += 1
        assert guard < 500, "game did not terminate -- likely an integration bug"

        pot = game.current_pot
        deal, losers = _play_one_deal_all_no_change(game)
        events = pot.resolve_losers(deal, losers)

        for e in events:
            if isinstance(e, ContinuePrompted):
                pot.submit_continue_declare(e.player_id, True)
        assert not pot.awaiting_continue_declare

        outcome_events = pot.finalize_deal()
        conclusion = next((e for e in outcome_events if isinstance(e, (PotWon, PotWipedOut))), None)
        if conclusion is not None:
            game.process_pot_outcome(conclusion)

    assert game.is_finished
    assert game.final_ranking is not None
    total_chips = sum(chips for _, chips in game.final_ranking)
    # Total chips in the system is conserved: starting stacks minus whatever
    # (if anything) is still sitting unclaimed in a wiped-out pot.
    starting_total = 5 * 3
    assert total_chips <= starting_total

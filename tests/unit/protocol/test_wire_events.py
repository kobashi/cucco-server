from cucco.domain.cards import Rank
from cucco.domain.events import (
    ChipsPaid,
    ContinuePrompted,
    CuccoDeclared,
    DealerChanged,
    DealOpened,
    Declaration,
    DeckDrawRefused,
    DeckExchangeAccepted,
    DeckReshuffled,
    ExchangeAccepted,
    ExchangeRefused,
    GameEnded,
    PlayerDisqualified,
    PlayerLeftPot,
    PotStarted,
    PotWipedOut,
    PotWon,
)
from cucco.protocol.wire_events import translate


def test_no_change_declaration_translates_to_public_event():
    wire = translate(Declaration(player_id="A", action="no_change"))
    assert wire.type == "no_change_declared"
    assert wire.for_recipient(None) == {"player_id": "A"}


def test_timed_out_no_change_translates_to_turn_timeout_consumed():
    wire = translate(Declaration(player_id="A", action="no_change", via_timeout=True))
    assert wire.type == "turn_timeout_consumed"


def test_cambio_declaration_has_no_direct_wire_event():
    assert translate(Declaration(player_id="A", action="cambio")) is None


def test_cucco_declare_declaration_has_no_direct_wire_event():
    # The accompanying CuccoDeclared event carries the wire notification.
    assert translate(Declaration(player_id="A", action="cucco_declare")) is None


def test_exchange_accepted_hides_card_values_from_bystanders():
    wire = translate(
        ExchangeAccepted(requester="A", target="B", requester_new_card=Rank.N5, target_new_card=Rank.N6)
    )
    assert wire.type == "exchange_result"
    bystander_view = wire.for_recipient("C")
    assert "your_new_card" not in bystander_view
    assert bystander_view == {"result": "accepted", "requester": "A", "target": "B"}

    requester_view = wire.for_recipient("A")
    assert requester_view["your_new_card"] == Rank.N5.value
    target_view = wire.for_recipient("B")
    assert target_view["your_new_card"] == Rank.N6.value


def test_deck_exchange_accepted_reveals_only_to_the_actor():
    wire = translate(DeckExchangeAccepted(actor="A", new_card=Rank.N10, given_up_card=Rank.N3))
    assert wire.for_recipient("A")["new_card"] == Rank.N10.value
    assert wire.for_recipient("A")["given_up_card"] == Rank.N3.value
    bystander = wire.for_recipient("B")
    assert "new_card" not in bystander


def test_exchange_refused_is_fully_public():
    wire = translate(
        ExchangeRefused(requester="A", target="B", reason="cat_meow", revealed_rank=Rank.CAT)
    )
    assert wire.type == "exchange_result"
    assert wire.for_recipient(None) == {
        "result": "refused",
        "requester": "A",
        "target": "B",
        "reason": "cat_meow",
        "revealed_rank": Rank.CAT.value,
    }


def test_exchange_refused_horse_house_reveal_off_has_no_revealed_rank():
    wire = translate(ExchangeRefused(requester="A", target="B", reason="house_horse_skip", revealed_rank=None))
    assert wire.for_recipient(None)["revealed_rank"] is None


def test_deck_draw_refused_is_public_since_card_is_already_discarded():
    wire = translate(DeckDrawRefused(actor="A", drawn_rank=Rank.HUMAN, reason="human_deck_draw"))
    assert wire.for_recipient(None) == {
        "result": "deck_draw_refused",
        "actor": "A",
        "drawn_rank": Rank.HUMAN.value,
        "reason": "human_deck_draw",
    }


def test_player_disqualified_card_none_when_deferred():
    wire = translate(PlayerDisqualified(player_id="A", cause="received_joker", card=None))
    assert wire.for_recipient(None)["card"] is None


def test_player_disqualified_card_present_when_immediate():
    wire = translate(PlayerDisqualified(player_id="A", cause="received_joker", card=Rank.JOKER))
    assert wire.for_recipient(None)["card"] == Rank.JOKER.value


def test_deal_opened_serializes_hands_and_losers():
    wire = translate(
        DealOpened(hands={"A": Rank.N5, "B": Rank.N2}, elevated_joker_holders=frozenset(), losers=("B",))
    )
    assert wire.type == "deal_opened"
    payload = wire.for_recipient(None)
    assert payload["hands"] == {"A": "5", "B": "2"}
    assert payload["losers"] == ["B"]


def test_deck_reshuffled():
    wire = translate(DeckReshuffled(remaining_count=44))
    assert wire.type == "deck_reshuffled"
    assert wire.for_recipient(None) == {"remaining_count": 44}


def test_pot_level_events():
    assert translate(ChipsPaid(player_id="A", amount=2, chips_now=22)).for_recipient(None) == {
        "player_id": "A",
        "amount": 2,
        "chips_now": 22,
    }
    assert translate(PlayerLeftPot(player_id="A", reason="insolvent")).for_recipient(None) == {
        "player_id": "A",
        "reason": "insolvent",
    }
    assert translate(ContinuePrompted(player_id="A", required_chips=3)).for_recipient(None) == {
        "player_id": "A",
        "required_chips": 3,
    }
    assert translate(DealerChanged(player_id="B")).for_recipient(None) == {"player_id": "B"}
    assert translate(PotWon(winner="A", amount=6, chips_now=30)).for_recipient(None) == {
        "winner": "A",
        "amount": 6,
        "chips_now": 30,
    }
    assert translate(PotWipedOut(amount=4)).for_recipient(None) == {"amount": 4}


def test_game_level_events():
    started = translate(
        PotStarted(
            pot_number=2, dealer_id="B", participants=("A", "B", "C"), chips_now={"A": 24, "B": 24, "C": 24}, pot_chips=3
        )
    )
    assert started.type == "pot_started"
    assert started.for_recipient(None)["participants"] == ["A", "B", "C"]
    assert started.for_recipient(None)["entry_fee_waived"] is False
    assert started.for_recipient(None)["pot_chips"] == 3

    ended = translate(GameEnded(ranking=(("B", 30), ("A", 10))))
    assert ended.type == "game_ended"
    assert ended.for_recipient(None)["ranking"] == [["B", 30], ["A", 10]]

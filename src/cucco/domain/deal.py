"""One Deal: dealing, turn order, exchange resolution, and "open".

The trickiest rules in docs/rules/final_rules.md all reduce to a single idea:
turn order is a fixed, non-wrapping list with the dealer last. `next_target()`
walks forward from a given position, skipping disqualified seats, and returns
`None` when it runs off the end of the list. `None` always means "the deck" —
never "wrap back to the start" — which is exactly the rule that a horse/house
refusal chain never loops back to the original requester, and that whoever
ends up last in turn order (even if the literal dealer was disqualified
mid-deal) inherits the deck-exchange turn.

`Deal` is fully synchronous and does no I/O: every `submit_*` method validates
the action, mutates state, and returns the list of domain events it produced.
All asyncio/timeout/broadcast concerns live in `cucco.server.runner`.
"""

from __future__ import annotations

from cucco.domain.cards import CHAINING_RANKS, Rank, strength

# Cards whose refusal effect requires an active declaration under the
# effect_declaration="declared" rule variant. 道化 is deliberately absent
# (its receive-and-lose effect stays automatic), and クク has no refusal
# power to declare in the first place.
DECLARABLE_RANKS = frozenset({Rank.HUMAN, Rank.HORSE, Rank.CAT, Rank.HOUSE})
from cucco.domain.config import GameConfig
from cucco.domain.deck import Deck, DiscardEntry
from cucco.domain.errors import IllegalAction
from cucco.domain.events import (
    CuccoDeclared,
    Declaration,
    DeckDrawRefused,
    DeckExchangeAccepted,
    DeckReshuffled,
    DealEvent,
    DealOpened,
    ExchangeAccepted,
    ExchangeRefused,
    PlayerDisqualified,
)


def _rotate_order_dealer_last(participants: list[str], dealer_id: str) -> list[str]:
    idx = participants.index(dealer_id)
    return participants[idx + 1 :] + participants[: idx + 1]


# _disqualify's `cause` values, grouped by which of GameConfig's three
# per-cause disclosure settings governs them.
_DISCLOSURE_FIELD_BY_CAUSE = {
    "received_joker": "joker_disclosure",
    "human_refusal": "human_disclosure",
    "human_deck_draw": "human_disclosure",
    "cat_refusal": "cat_disclosure",
    "cat_deck_draw": "cat_disclosure",
}


class Deal:
    def __init__(self, participants: list[str], dealer_id: str, deck: Deck, config: GameConfig) -> None:
        if dealer_id not in participants:
            raise ValueError("dealer_id must be one of the participants")
        if len(set(participants)) != len(participants):
            raise ValueError("participants must not contain duplicates")

        self.dealer_id = dealer_id
        self.order: list[str] = _rotate_order_dealer_last(participants, dealer_id)
        self.deck = deck
        self.config = config

        # Attach the reshuffle hook BEFORE dealing: with few cards left in
        # the shared deck (e.g. late in a pot), even the initial deal-out
        # can exhaust the draw pile and trigger a reshuffle, which must be
        # reported the same as any other reshuffle.
        self._reshuffle_events: list[DeckReshuffled] = []
        self.deck.on_reshuffle = self._on_deck_reshuffled

        self.hands: dict[str, Rank] = {}
        self.provenance: dict[str, str | None] = {}
        for pid in self.order:
            card = deck.draw()
            self.hands[pid] = card
            self.provenance[pid] = pid

        self.disqualified: set[str] = set()
        self.elevated_joker_holders: set[str] = set()
        self.turn_acted: set[str] = set()
        self.cucco_declared_by: str | None = None
        self.declarations: list[Declaration] = []
        self.deferred_discards: list[DiscardEntry] = []

        # Step-wise cambio in progress (effect_declaration="declared" only):
        # (requester, current target awaiting an effect decision). The
        # runner drives begin_cambio/resolve_* to completion within one
        # turn, so this never survives past the turn that set it.
        self._pending_exchange: tuple[str, str] | None = None

        self._opened = False

    # -- reshuffle plumbing -------------------------------------------------

    def _on_deck_reshuffled(self) -> None:
        self._reshuffle_events.append(DeckReshuffled(self.deck.remaining_count))

    def _draw(self, events: list[DealEvent]) -> Rank:
        card = self.deck.draw()
        if self._reshuffle_events:
            events.extend(self._reshuffle_events)
            self._reshuffle_events.clear()
        return card

    def take_pending_events(self) -> list[DealEvent]:
        """Pop any events accumulated outside of a `submit_*` call -- in
        practice, only a `DeckReshuffled` triggered by exhausting the deck
        during the initial deal-out in `__init__`. Callers (Pot/runner)
        should call this immediately after construction and broadcast the
        result before `deal_started`."""
        events = list(self._reshuffle_events)
        self._reshuffle_events.clear()
        return events

    # -- turn order -----------------------------------------------------------

    def next_target(self, from_index: int) -> str | None:
        """Walk forward from `from_index`, skipping disqualified seats.

        Returns `None` ("the deck") if we fall off the end of `order` — this
        is the only place the "never loop back to the requester" rule is
        enforced: `order` never wraps, so falling off the end always means
        the deck, never index 0.
        """
        for idx in range(from_index + 1, len(self.order)):
            pid = self.order[idx]
            if pid not in self.disqualified:
                return pid
        return None

    def legal_actor(self) -> str | None:
        """The next player whose turn it is: the first seat in `order` that is
        neither disqualified nor has already acted this deal.

        This single rule covers ordinary turn progression AND the "whoever
        ends up last in turn order takes the deck-exchange turn" rule (that
        player is simply whoever `legal_actor()` returns last) AND the "the
        role can only be inherited by someone who hasn't already acted"
        condition (only not-yet-acted seats are ever returned).

        Note that a house/horse holder who merely REFUSED someone else's
        chained request (and was never themselves the active requester)
        does not count as having acted -- they still get their own normal
        turn later, even if that earlier chain already resolved against the
        deck on someone else's behalf. This can mean the deck is exchanged
        with more than once in a single deal (once per player whose own
        turn happens to fall off the end of `order`).
        """
        for pid in self.order:
            if pid in self.disqualified or pid in self.turn_acted:
                continue
            return pid
        return None

    @property
    def is_awaiting_open(self) -> bool:
        return self.cucco_declared_by is not None or self.legal_actor() is None

    @property
    def is_opened(self) -> bool:
        return self._opened

    def current_cucco_holders(self) -> set[str]:
        return {pid for pid, card in self.hands.items() if card is Rank.CUCCO and pid not in self.disqualified}

    # -- player actions -------------------------------------------------------

    def _validate_turn(self, player_id: str) -> None:
        if self._opened:
            raise IllegalAction("deal has already been opened")
        if self.cucco_declared_by is not None:
            raise IllegalAction("deal already ended by a cucco declaration")
        if self.legal_actor() != player_id:
            raise IllegalAction(f"it is not {player_id}'s turn")

    def _mark_acted(self, player_id: str) -> None:
        self.turn_acted.add(player_id)

    def _record_declaration(self, player_id: str, action: str, *, via_timeout: bool = False) -> Declaration:
        decl = Declaration(player_id=player_id, action=action, via_timeout=via_timeout)
        self.declarations.append(decl)
        return decl

    def submit_cambio(self, player_id: str) -> list[DealEvent]:
        self._validate_turn(player_id)
        self._mark_acted(player_id)
        events: list[DealEvent] = [self._record_declaration(player_id, "cambio")]
        idx = self.order.index(player_id)
        target = self.next_target(idx)
        if target is None:
            events += self._resolve_against_deck(player_id)
        else:
            events += self._resolve_against_player(player_id, target)
        return events

    def submit_no_change(self, player_id: str, *, via_timeout: bool = False) -> list[DealEvent]:
        self._validate_turn(player_id)
        self._mark_acted(player_id)
        return [self._record_declaration(player_id, "no_change", via_timeout=via_timeout)]

    def submit_cucco_declare(self, player_id: str) -> list[DealEvent]:
        """Declare クク, ending the deal immediately.

        A klop is legal at any time during a deal EXCEPT while an atomic
        exchange is being resolved (docs/rules/final_rules.md). The server
        layer receives declarations asynchronously (as pending flags, never
        as prompt answers) and calls this only at safe points between atomic
        steps -- these guards are the domain's own enforcement of the same
        boundaries. Declining is simply not declaring; there is no pass.
        """
        if self._opened:
            raise IllegalAction("deal has already been opened")
        if self._pending_exchange is not None:
            # Exchange processing is atomic (docs/rules/final_rules.md) --
            # クク cannot interrupt a mid-chain declared-mode exchange.
            raise IllegalAction("an exchange is being resolved; cucco cannot interrupt it")
        if self.cucco_declared_by is not None:
            raise IllegalAction("deal already ended by a cucco declaration")
        if player_id in self.disqualified:
            raise IllegalAction(f"{player_id} is disqualified and cannot declare cucco")
        if self.hands.get(player_id) is not Rank.CUCCO:
            raise IllegalAction(f"{player_id} does not hold クク")
        self.cucco_declared_by = player_id
        return [self._record_declaration(player_id, "cucco_declare"), CuccoDeclared(player_id)]

    # -- step-wise cambio (effect_declaration="declared") -----------------------
    #
    # Base-rule exchanges resolve synchronously inside submit_cambio. Under
    # the declared-effects variant every hop of a request needs the target's
    # decision first, so the exchange is split into steps the (async) runner
    # drives:
    #
    #   begin_cambio(A)                 -> events, target|None
    #   resolve_effect_declared(A, T)   -> events, next_target|None
    #   resolve_exchange_accept(A, T)   -> events (always terminal)
    #
    # A returned target of None ALWAYS means "this turn is fully resolved" —
    # a chain that runs off the end of `order` resolves against the deck
    # internally (deck-drawn specials keep their automatic behavior: the
    # deck has nobody to declare for it). The 馬/家 skip chain is therefore
    # just: declare -> get the next target -> ask them, with no wrap-around
    # (next_target() never loops, same as the base rules).

    def begin_cambio(self, player_id: str) -> tuple[list[DealEvent], str | None]:
        """Start a declared-mode cambio turn. Returns the first target to
        ask, or None if the request went straight to the deck (dealer /
        last-in-order turns) and has already fully resolved."""
        self._validate_turn(player_id)
        self._mark_acted(player_id)
        events: list[DealEvent] = [self._record_declaration(player_id, "cambio")]
        target = self.next_target(self.order.index(player_id))
        if target is None:
            events += self._resolve_against_deck(player_id)
            return events, None
        self._pending_exchange = (player_id, target)
        return events, target

    def _validate_pending(self, requester: str, target: str) -> None:
        if self._pending_exchange != (requester, target):
            raise IllegalAction(
                f"no exchange from {requester} to {target} is awaiting a decision"
            )

    def resolve_exchange_accept(self, requester: str, target: str) -> list[DealEvent]:
        """The target stayed silent (or holds no declarable card): the
        exchange goes through as a plain swap. Terminal — the turn is over.
        道化 receipt still disqualifies automatically (it is not part of the
        declared-effects variant)."""
        self._validate_pending(requester, target)
        self._pending_exchange = None
        return self._do_swap(requester, target)

    def resolve_effect_declared(self, requester: str, target: str) -> tuple[list[DealEvent], str | None]:
        """The target actively declared their card's effect. Returns the
        next target to ask when a 馬/家 declaration moves the request on, or
        None when the turn fully resolved (人間/猫 fired, or the chain
        reached the deck and auto-resolved there)."""
        self._validate_pending(requester, target)
        rank = self.hands[target]
        if rank not in DECLARABLE_RANKS:
            raise IllegalAction(f"{target}'s card has no declarable effect")
        self._pending_exchange = None
        if rank is Rank.HUMAN:
            return self._resolve_human_refusal(requester, target), None
        if rank is Rank.CAT:
            return self._resolve_cat_refusal(requester, target), None
        # 馬 / 家: the request skips onward.
        revealed = rank if self.config.horse_house_reveal else None
        events: list[DealEvent] = [
            ExchangeRefused(requester=requester, target=target, reason="house_horse_skip", revealed_rank=revealed)
        ]
        next_target = self.next_target(self.order.index(target))
        if next_target is None:
            events += self._resolve_against_deck(requester)
            return events, None
        self._pending_exchange = (requester, next_target)
        return events, next_target

    # -- exchange resolution ---------------------------------------------------

    def _resolve_against_player(self, requester: str, target: str) -> list[DealEvent]:
        rank = self.hands[target]
        if rank in CHAINING_RANKS:  # 馬 / 家
            return self._resolve_house_horse(requester, target)
        if rank is Rank.HUMAN:
            return self._resolve_human_refusal(requester, target)
        if rank is Rank.CAT:
            return self._resolve_cat_refusal(requester, target)
        # クク・道化・数字札/桶/仮面/獅子: must accept the exchange.
        return self._do_swap(requester, target)

    def _resolve_house_horse(self, requester: str, target: str) -> list[DealEvent]:
        rank = self.hands[target]
        revealed = rank if self.config.horse_house_reveal else None
        events: list[DealEvent] = [
            ExchangeRefused(requester=requester, target=target, reason="house_horse_skip", revealed_rank=revealed)
        ]
        next_target = self.next_target(self.order.index(target))
        if next_target is None:
            events += self._resolve_against_deck(requester)
        else:
            events += self._resolve_against_player(requester, next_target)
        return events

    def _resolve_human_refusal(self, requester: str, target: str) -> list[DealEvent]:
        events: list[DealEvent] = [
            ExchangeRefused(requester=requester, target=target, reason="human_refusal", revealed_rank=Rank.HUMAN)
        ]
        events += self._disqualify(requester, cause="human_refusal")
        return events

    def _resolve_cat_refusal(self, requester: str, target: str) -> list[DealEvent]:
        events: list[DealEvent] = [
            ExchangeRefused(requester=requester, target=target, reason="cat_meow", revealed_rank=Rank.CAT)
        ]
        original = self.provenance.get(requester)
        if original is not None and original not in self.disqualified:
            events += self._disqualify(original, cause="cat_refusal")
        # else: fizzle — the original holder is already gone from this deal.
        return events

    def _resolve_against_deck(self, actor: str) -> list[DealEvent]:
        events: list[DealEvent] = []
        while True:
            card = self._draw(events)
            if card in CHAINING_RANKS:  # 馬 / 家
                self.deck.discard(card, original_holder=None, via="deck_draw")
                events.append(DeckDrawRefused(actor=actor, drawn_rank=card, reason="horse_house_chain"))
                continue
            if card is Rank.CUCCO:
                self.deck.discard(card, original_holder=None, via="deck_draw")
                events.append(DeckDrawRefused(actor=actor, drawn_rank=card, reason="cucco_refusal"))
                return events
            if card is Rank.HUMAN:
                self.deck.discard(card, original_holder=None, via="deck_draw")
                events.append(DeckDrawRefused(actor=actor, drawn_rank=card, reason="human_deck_draw"))
                events += self._disqualify(actor, cause="human_deck_draw")
                return events
            if card is Rank.CAT:
                self.deck.discard(card, original_holder=None, via="deck_draw")
                events.append(DeckDrawRefused(actor=actor, drawn_rank=card, reason="cat_deck_draw"))
                original = self.provenance.get(actor)
                if original is not None and original not in self.disqualified:
                    events += self._disqualify(original, cause="cat_deck_draw")
                return events
            if card is Rank.JOKER:
                events += self._do_deck_swap(actor, card)
                self.elevated_joker_holders.add(actor)
                return events
            # 数字札・桶・仮面・獅子: normal successful exchange.
            events += self._do_deck_swap(actor, card)
            return events

    def _do_swap(self, a: str, b: str) -> list[DealEvent]:
        self.hands[a], self.hands[b] = self.hands[b], self.hands[a]
        self.provenance[a], self.provenance[b] = self.provenance[b], self.provenance[a]
        events: list[DealEvent] = [
            ExchangeAccepted(requester=a, target=b, requester_new_card=self.hands[a], target_new_card=self.hands[b])
        ]
        # Whoever now holds a Joker via this live exchange is disqualified —
        # checked on both sides, since either participant could be the one
        # who ends up "receiving" it.
        if self.hands[a] is Rank.JOKER:
            events += self._disqualify(a, cause="received_joker")
        if self.hands[b] is Rank.JOKER:
            events += self._disqualify(b, cause="received_joker")
        return events

    def _do_deck_swap(self, actor: str, drawn: Rank) -> list[DealEvent]:
        old_card = self.hands[actor]
        old_original = self.provenance.get(actor)
        self.hands[actor] = drawn
        self.provenance[actor] = None  # deck-origin card: no original holder
        self.deck.discard(old_card, original_holder=old_original, via="dealer_swap")
        return [DeckExchangeAccepted(actor=actor, new_card=drawn, given_up_card=old_card)]

    def _disqualify(self, player_id: str, *, cause: str) -> list[DealEvent]:
        self.disqualified.add(player_id)
        card = self.hands.pop(player_id)
        original_holder = self.provenance.pop(player_id, None)
        disclosure_field = _DISCLOSURE_FIELD_BY_CAUSE[cause]
        if getattr(self.config, disclosure_field) == "immediate":
            self.deck.discard(card, original_holder=original_holder, via="disqualification")
            return [PlayerDisqualified(player_id=player_id, cause=cause, card=card)]
        self.deferred_discards.append(
            DiscardEntry(card=card, original_holder=original_holder, discarded_via="disqualification")
        )
        return [PlayerDisqualified(player_id=player_id, cause=cause, card=None)]

    # -- open -------------------------------------------------------------------

    def open(self) -> list[DealEvent]:
        if self._opened:
            raise IllegalAction("deal has already been opened")
        if self._pending_exchange is not None:
            raise IllegalAction("cannot open: an exchange is awaiting an effect decision")
        if self.legal_actor() is not None and self.cucco_declared_by is None:
            raise IllegalAction("cannot open: turns remain")
        self._opened = True

        for entry in self.deferred_discards:
            self.deck.discard_pile.append(entry)
        self.deferred_discards.clear()

        remaining = {pid: card for pid, card in self.hands.items() if pid not in self.disqualified}

        def _strength(pid: str, card: Rank) -> int:
            return strength(card, elevated=pid in self.elevated_joker_holders)

        if len(remaining) > 1:
            weakest = min(_strength(pid, card) for pid, card in remaining.items())
            losers = tuple(pid for pid, card in remaining.items() if _strength(pid, card) == weakest)
        else:
            # 0 remaining: everyone was mid-deal disqualified (e.g. a mutual
            # Joker exchange) -- `deal.disqualified` already carries the
            # full loser set for Pot purposes, no "weakest" to compute.
            # 1 remaining: a lone survivor of mid-deal disqualifications has
            # nobody to be weaker than and is not a loser of this deal.
            losers = ()

        # All cards compared at "open" (not just the losers') become
        # face-up discards, per docs/rules/final_rules.md.
        for pid, card in remaining.items():
            self.deck.discard(card, original_holder=self.provenance.get(pid), via="open")

        return [
            DealOpened(
                hands=dict(remaining),
                elevated_joker_holders=frozenset(self.elevated_joker_holders),
                losers=losers,
            )
        ]

class IllegalAction(Exception):
    """A client action is not legal given the current deal/pot/game state.

    The server layer maps this to an `action_rejected` event.
    """

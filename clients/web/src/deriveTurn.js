// Best-effort "whose turn is it" tracker for the table screen's seat
// indicator.
//
// IMPORTANT: this is NOT how input is gated. `turn_prompt`, `cucco_window`
// and `dealer_ready` are sent by the server directly (and only) to the
// addressed session -- src/cucco/server/runner.py's `_prompt` calls
// `_send_to`, not `_broadcast`. Bystanders are never told in real time
// whose turn it is; they can only infer it from the broadcast resolution
// events (`no_change_declared`, `turn_timeout_consumed`, `exchange_result`)
// that follow each turn. This module reconstructs a display-only guess from
// those events. If it's ever wrong, the only consequence is a stale seat
// highlight -- nobody's input buttons are gated by it.
export function turnOrderFor(table, excludeIds = null) {
  const seats = (table?.seats ?? [])
    .filter((s) => s.in_current_pot !== false && !excludeIds?.has(s.player_id))
    .map((s) => s.player_id);
  const dealer = table?.dealer_seat;
  if (!seats.length || !dealer || !seats.includes(dealer)) return seats;
  const dealerIdx = seats.indexOf(dealer);
  const rotated = [...seats.slice(dealerIdx + 1), ...seats.slice(0, dealerIdx + 1)];
  return rotated; // last entry is the dealer (deck exchange)
}

export function advanceTurn(state, resolvedPlayerId) {
  const order = turnOrderFor(state.table, state.disqualifiedIdsThisDeal);
  if (!order.length) return;
  const idx = order.indexOf(resolvedPlayerId);
  if (idx === -1 || idx + 1 >= order.length) {
    state.currentTurnSeat = null; // dealer just resolved (or unknown) -- deal is ending
    return;
  }
  state.currentTurnSeat = order[idx + 1];
}

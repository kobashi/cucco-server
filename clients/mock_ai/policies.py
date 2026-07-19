"""Compatibility shim: the policies moved to `cucco.ai.policies` so the
server-embedded bots (`create_table`'s `ai_players`) and this external
reference client share one implementation. Import paths used by the guides
and by seminar students' AIs keep working through this re-export.
"""

from cucco.ai.policies import (  # noqa: F401
    ALWAYS_CHANGE_RANKS,
    DEFAULT_MATRIX,
    DEFAULT_MATRIX_FALLBACK,
    NEVER_CHANGE_RANKS,
    NUMBER_RANKS,
    POLICIES,
    AlwaysChange,
    AlwaysNoChange,
    BasePolicy,
    CountingAggressive,
    CountingConservative,
    CountingPolicy,
    MatrixPolicy,
    make_policy,
    matrix_row,
)

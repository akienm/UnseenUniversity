"""Back-compat shim — canonical module is coactivation_counter.py (T-cc-walk-12)."""

from .coactivation_counter import (  # noqa: F401
    set_word_graph,
    get_word_graph,
    reinforce_query_tokens,
    wg_boost_search,
    record_retrieval_boost,
    wg_predict_for_activation,
    _ENABLED,
    _WG_BOOST_MAX,
    _ARSL_BOOST_CAP,
    _QUERY_BOOST_BASE,
)

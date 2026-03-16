"""
Tools package - imports all tool modules so they register themselves.
Any reasoner that imports this gets all tools without knowing about Anthropic.
"""

from . import (
    filesystem,
    web_search,
    self_edit,
    gmail,
    discord,
    senses,
    runner,
    confluence,
    budget,
    github,
    openrouter_reasoner,
    browser,
    blobs,
    word_graph,
    metrics,
    training,
    cluster_ssh,
    notebook,
    ebook_reader,
    interpretive,
    google_calendar,
    google_contacts,
    hot_reload,
    learner,
    watchlist,
    ops,
)
from ..arbiter import (
    queue as _arbiter_queue,
)  # noqa: F401 — registers arbiter_submit tool

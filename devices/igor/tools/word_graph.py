"""
Word graph tools — let Igor index text into the in-memory word graph.

index_text_into_word_graph: add any text as a named document.
  Words co-occur → enriches predict_next() generation direction.
  Does NOT affect habit trigger scoring unless a habit id is used as doc_id.

query_word_graph_stats: show current graph size and top co-occurring words.
"""

from .registry import Tool, registry


def _get_wg():
    """Get the live word graph from basal_ganglia (injected at boot)."""
    from ..cognition import basal_ganglia
    wg = basal_ganglia._word_graph
    if wg is None:
        raise RuntimeError("Word graph not initialised — Igor must be fully booted.")
    return wg


def index_text(
    doc_id: str,
    text: str,
    weight: float = 1.0,
    **_,
) -> str:
    """
    Index text into the word graph under doc_id.
    Weight 1.0 = normal; 2.0 = high-signal (like a habit trigger).
    Rebuilds IDF and persists cache after indexing.
    """
    try:
        wg = _get_wg()
        from pathlib import Path
        before = len(wg._word_to_ids)
        wg.index(doc_id, text, weight=float(weight))
        wg.build_idf()
        cache = Path.home() / ".TheIgors" / "word_graph.json"
        wg.save(cache)
        after = len(wg._word_to_ids)
        new_words = after - before
        return (
            f"Indexed '{doc_id}' ({len(text)} chars, weight={weight}).\n"
            f"  New words added: {new_words}  Total vocabulary: {after}"
        )
    except Exception as e:
        return f"Error indexing text: {e}"


def query_stats(
    context: str = "",
    top_n: int = 10,
    **_,
) -> str:
    """
    Show word graph size and, optionally, top co-occurring words for a context phrase.
    context: text to predict next words for (leave blank for stats only).
    top_n: how many predicted words to show (default 10).
    """
    try:
        wg = _get_wg()
        vocab = len(wg._word_to_ids)
        docs = wg._doc_count
        result = f"Word graph: {vocab} words, {docs} docs indexed."
        if context.strip():
            predictions = wg.predict_next(context.strip(), n=int(top_n))
            if predictions:
                pred_str = "  ".join(f"{w}({s:.1f})" for w, s in predictions)
                result += f"\nTop {len(predictions)} words after '{context}': {pred_str}"
            else:
                result += f"\nNo predictions for '{context}' (words not in graph yet)."
        return result
    except Exception as e:
        return f"Error querying word graph: {e}"


registry.register(Tool(
    name="index_text_into_word_graph",
    description=(
        "Index a block of text into the word graph under a named doc_id. "
        "Builds word co-occurrence data that enriches both pattern recognition "
        "and next-word prediction. Use this to feed source text (books, code, "
        "conversations) into Igor's fast semantic substrate. "
        "weight=1.0 is normal; weight=2.0 for high-signal material."
    ),
    parameters={
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "Unique name for this document (e.g. 'hamlet_act1', 'theigors_source').",
            },
            "text": {
                "type": "string",
                "description": "The text to index. Can be any length.",
            },
            "weight": {
                "type": "number",
                "description": "Word weight: 1.0 = normal, 2.0 = high-signal. Default 1.0.",
            },
        },
        "required": ["doc_id", "text"],
    },
    fn=index_text,
))

registry.register(Tool(
    name="query_word_graph_stats",
    description=(
        "Show the current word graph vocabulary size and doc count. "
        "Optionally provide a context phrase to see predicted next words."
    ),
    parameters={
        "type": "object",
        "properties": {
            "context": {
                "type": "string",
                "description": "Optional phrase to get next-word predictions for.",
            },
            "top_n": {
                "type": "integer",
                "description": "How many predicted words to return (default 10).",
            },
        },
        "required": [],
    },
    fn=query_stats,
))

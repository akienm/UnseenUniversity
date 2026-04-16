"""
GeminiSearchChannel — free web search + synthesis via Igor's browser profile.

Uses the free Gemini API accessed through Igor's browser (no auth required).
Medium reliability (depends on Gemini availability + browser).
Medium cost model (no direct cost but uses browser resources).
"""

from __future__ import annotations

from datetime import datetime

from ...igor_base import IgorBase
from . import (
    Channel,
    ChannelReliability,
    AcquireRequest,
    AcquireResult,
    ChannelFailure,
    BlobMeta,
)


class GeminiSearchChannel(Channel, IgorBase):
    """
    Search the web using free Gemini + browser.

    Query is a search term. The channel uses the browser to access
    Gemini's free web search feature, then asks Gemini to synthesize
    a summary of the top results.
    """

    def __init__(self):
        super().__init__(
            name="GeminiSearchChannel",
            constraints=["NO persistent login required"],
            cost_per_call_usd=0.0,
            reliability=ChannelReliability.MEDIUM,
            one_way=False,
            short_circuits=False,
        )

    def acquire(self, request: AcquireRequest) -> AcquireResult | ChannelFailure:
        """
        Use browser to search Gemini for the query and get a synthesis.

        Returns a markdown-formatted summary of top results.
        """
        try:
            from ...tools.browser import browser_use_task

            query = request.query.strip()
            if not query:
                return ChannelFailure(
                    channel_name=self.name,
                    reason="Empty search query",
                    cost_usd=0.0,
                )

            # Task: navigate to Gemini, search, and summarize results
            task = f"""
Go to https://gemini.google.com and search for: {query}

Steps:
1. Click in the search/input field
2. Type the query exactly as given
3. Submit the search (press Enter or click Search)
4. Wait for results to load
5. Read the top 3-5 results
6. Synthesize a markdown summary with:
   - Query searched for
   - Top 3-5 result titles + URLs
   - 1-2 sentence synthesis of what you learned

Return only the markdown summary, no other text.
"""

            try:
                result_text = browser_use_task(
                    task, url="https://gemini.google.com", max_steps=15, timeout=120
                )
            except Exception as e:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"Browser task failed: {str(e)[:200]}",
                    cost_usd=0.0,
                )

            if not result_text:
                return ChannelFailure(
                    channel_name=self.name,
                    reason="Browser returned empty result",
                    cost_usd=0.0,
                )

            blob = result_text.encode("utf-8")

            meta = BlobMeta(
                title=f"Gemini search: {query}",
                source=self.name,
                url=f"https://gemini.google.com",
                format="markdown",
                size_bytes=len(blob),
                retrieved_at=datetime.utcnow().isoformat() + "Z",
            )

            return AcquireResult(
                blob=blob,
                meta=meta,
                cost_usd=0.0,
            )

        except ImportError:
            return ChannelFailure(
                channel_name=self.name,
                reason="browser_use_task not available",
                cost_usd=0.0,
            )
        except Exception as e:
            return ChannelFailure(
                channel_name=self.name,
                reason=f"Error: {type(e).__name__}: {str(e)[:200]}",
                cost_usd=0.0,
            )

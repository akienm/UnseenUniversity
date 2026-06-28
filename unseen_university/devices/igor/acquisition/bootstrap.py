"""
Bootstrap — register all acquisition channels.

Called once at module import to populate the default registry.
"""

from . import get_registry
from .calibre import CalibreChannel
from .file_inbox import FileInboxChannel
from .direct_url import DirectURLChannel
from .gemini_search import GeminiSearchChannel
from .browser_use import BrowserUseChannel


def bootstrap_channels() -> None:
    """Register all channels in priority order."""
    registry = get_registry()

    # Priority order (D231):
    # 1. FileInboxChannel (short_circuits=True)
    # 2. DirectURLChannel (short_circuits=True)
    # 3. CalibreChannel (local search)
    # 4. GeminiSearchChannel (free web search)
    # 5. BrowserUseChannel (last resort with constraints)

    registry.register(FileInboxChannel())
    registry.register(DirectURLChannel())
    registry.register(CalibreChannel())
    registry.register(GeminiSearchChannel())
    registry.register(BrowserUseChannel())


# Auto-bootstrap on import
bootstrap_channels()

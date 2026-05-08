"""
Web search tool - HTTP-level, no browser needed.
Uses DuckDuckGo HTML endpoint. No API key required.
"""

import certifi
import requests
from bs4 import BeautifulSoup
from lab.utility_closet.registry import Tool, registry

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo. Returns titles, URLs, and snippets."""
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=HEADERS,
            timeout=10,
            verify=certifi.where(),
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        results = []

        for result in soup.select(".result")[:max_results]:
            title_el = result.select_one(".result__title")
            snippet_el = result.select_one(".result__snippet")
            url_el = result.select_one(".result__url")

            title = title_el.get_text(strip=True) if title_el else "No title"
            snippet = snippet_el.get_text(strip=True) if snippet_el else "No snippet"
            url = url_el.get_text(strip=True) if url_el else ""

            results.append(f"**{title}**\n{url}\n{snippet}")

        if not results:
            return f"No results found for: {query}"

        return f"Search results for '{query}':\n\n" + "\n\n".join(results)

    except requests.Timeout:
        return f"Error: Search timed out for query: {query}"
    except Exception as e:
        return f"Error searching for '{query}': {e}"


def read_webpage(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return its readable text content."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15, verify=certifi.where())
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "button", "iframe"]):
            tag.decompose()

        # Prefer main content blocks
        main = (soup.find("main") or soup.find("article")
                or soup.find(id="content") or soup.find(class_="content")
                or soup.body or soup)

        text = main.get_text(separator="\n", strip=True)

        # Collapse whitespace
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        text = "\n".join(lines)

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars]"

        return f"[{url}]\n\n{text}"

    except requests.Timeout:
        return f"Error: Timed out fetching {url}"
    except requests.HTTPError as e:
        return f"Error: HTTP {e.response.status_code} fetching {url}"
    except Exception as e:
        return f"Error reading {url}: {e}"


# Register tools
registry.register(Tool(
    name="read_webpage",
    description="Fetch a URL and return its readable text content. Use this to read articles, docs, or any web page after finding a URL via web_search.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to fetch"},
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 8000)",
            },
        },
        "required": ["url"],
    },
    fn=read_webpage,
))

registry.register(Tool(
    name="web_search",
    description="Search the web using DuckDuckGo. Returns titles, URLs, and snippets for the top results.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5)",
            },
        },
        "required": ["query"],
    },
    fn=web_search,
))

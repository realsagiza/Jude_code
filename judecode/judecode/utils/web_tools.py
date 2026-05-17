"""Web search and fetch utilities.

Supports:
- Tavily AI Search (primary, API key in .env as TAVILY_API_KEY)
- DuckDuckGo HTML scraping (fallback if Tavily is unavailable)
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from re import sub as re_sub
import re


def _get_tavily_key() -> str:
    """Get Tavily API key from environment."""
    return os.environ.get("TAVILY_API_KEY", "") or os.environ.get("JUDECODE_TAVILY_API_KEY", "")


def web_fetch(url: str, timeout: int = 30) -> str:
    """Fetch content from a URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[1].split(";")[0].strip()
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} - {e.reason} for URL {url}"
    except urllib.error.URLError as e:
        return f"Error: Connection failed - {e.reason} for URL {url}"
    except Exception as e:
        return f"Error fetching {url}: {type(e).__name__}: {e}"


def _tavily_search(query: str, num_results: int = 5) -> str:
    """
    Perform a web search via Tavily AI Search API.
    Requires TAVILY_API_KEY in .env file.
    """
    api_key = _get_tavily_key()
    if not api_key:
        return ""

    try:
        payload = json.dumps({
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": num_results,
            "include_answer": True,
            "include_raw_content": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "JudeCode/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        results = []

        # Include Tavily's AI-generated answer if available
        answer = data.get("answer", "")
        if answer:
            results.append(f"🔍 AI Overview: {answer}\n")

        for i, result in enumerate(data.get("results", [])[:num_results]):
            title = result.get("title", "No title")
            url = result.get("url", "")
            content = result.get("content", "")
            results.append(
                f"{i+1}. {title}\n   URL: {url}\n   {content}\n"
            )

        return "\n".join(results) if results else "No results found."

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"Tavily API error: HTTP {e.code} - {body}"
    except Exception as e:
        return f"Tavily search failed: {type(e).__name__}: {e}"


def _duckduckgo_search(query: str, num_results: int = 5) -> str:
    """Fallback search via DuckDuckGo HTML scraping."""
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")

        results = []
        result_blocks = re.findall(
            r'<a rel="nofollow" class="result__a" href="(.*?)">(.*?)</a>',
            html,
            re.DOTALL,
        )
        snippet_blocks = re.findall(
            r'<a class="result__snippet"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        for i, (href, title) in enumerate(result_blocks[:num_results]):
            title_clean = re_sub(r"<[^>]+>", "", unescape(title)).strip()
            snippet = ""
            if i < len(snippet_blocks):
                snippet = re_sub(
                    r"<[^>]+>", "", unescape(snippet_blocks[i])
                ).strip()
            results.append(
                f"{i+1}. {title_clean}\n   URL: {href}\n   {snippet}\n"
            )
        return "\n".join(results) if results else "No results found."
    except Exception as e:
        return f"DuckDuckGo search failed: {type(e).__name__}: {e}"


def web_search(query: str, num_results: int = 5) -> str:
    """
    Perform a web search.

    Uses Tavily AI Search by default (requires TAVILY_API_KEY in .env).
    Falls back to DuckDuckGo HTML scraping if Tavily is not configured.

    Returns a formatted string of results.
    """
    # Try Tavily first (if API key is available)
    if _get_tavily_key():
        result = _tavily_search(query, num_results)
        if result and not result.startswith("Tavily"):
            return result

    # Fallback to DuckDuckGo
    return _duckduckgo_search(query, num_results)

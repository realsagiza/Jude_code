"""Web search and fetch utilities."""

import urllib.error
import urllib.parse
import urllib.request
from html import unescape
import re


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


def web_search(query: str, num_results: int = 5) -> str:
    """
    Perform a web search via DuckDuckGo HTML scraping.
    Returns a formatted string of results.
    """
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
        # DuckDuckGo HTML result parsing
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
            title_clean = re.sub(r"<[^>]+>", "", unescape(title)).strip()
            snippet = ""
            if i < len(snippet_blocks):
                snippet = re.sub(
                    r"<[^>]+>", "", unescape(snippet_blocks[i])
                ).strip()
            results.append(
                f"{i+1}. {title_clean}\n   URL: {href}\n   {snippet}\n"
            )
        return "\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Search failed: {type(e).__name__}: {e}"

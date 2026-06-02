import re
import httpx
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                    "li", "tr", "br", "hr", "blockquote"):
            self._text.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self) -> str:
        return "".join(self._text)


def extract_text(html: str, max_length: int = 50000) -> str:
    """Extract clean text from HTML."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return html[:max_length]

    text = parser.get_text()
    # Collapse whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    text = text.strip()
    return text[:max_length]


async def fetch_and_extract(url: str, max_length: int = 50000) -> dict:
    """Fetch a URL and extract text content."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            headers = {
                "User-Agent": "SearchV2/0.1 (knowledge acquisition system)",
            }
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                text = extract_text(resp.text, max_length)
            else:
                text = resp.text[:max_length]

            return {
                "url": url,
                "title": _extract_title(resp.text) if "text/html" in content_type else url,
                "text": text,
                "content_type": content_type,
                "status_code": resp.status_code,
            }
    except Exception as e:
        return {"url": url, "error": str(e)}


def _extract_title(html: str) -> str:
    import html as html_mod
    match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    if match:
        return html_mod.unescape(match.group(1).strip())
    return ""

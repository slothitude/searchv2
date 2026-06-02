import re
import httpx
import ipaddress
import socket
from urllib.parse import urlparse
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
    if _is_private_url(url):
        return {"url": url, "error": "URL blocked (private/internal address)"}
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


# ── SSRF protection ────────────────────────────────────────

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("0.0.0.0/8"),
]

_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _is_private_url(url: str) -> bool:
    """Check if a URL points to a private/internal address. Blocks SSRF."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True
        hostname = hostname.lower()
        if hostname in _BLOCKED_HOSTS:
            return True
        # Resolve hostname to IPs
        try:
            addr_infos = socket.getaddrinfo(hostname, parsed.port or 443, socket.AF_UNSPEC)
            for family, _, _, _, sockaddr in addr_infos:
                ip = ipaddress.ip_address(sockaddr[0])
                if any(ip in net for net in _PRIVATE_NETWORKS):
                    return True
        except socket.gaierror:
            return True  # can't resolve = don't fetch
    except Exception:
        return True
    return False

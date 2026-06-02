import httpx
from config import settings


async def search_searxng(
    query: str,
    categories: str = "general",
    max_results: int = 10,
) -> list[dict]:
    """Search via SearXNG."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            settings.searxng_url + "/search",
            params={
                "q": query,
                "format": "json",
                "categories": categories,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "score": r.get("score", 0),
                "engine": r.get("engine", ""),
            })
        return results


async def quick_search(query: str, max_results: int = 5) -> list[dict]:
    """Quick search wrapper."""
    return await search_searxng(query, max_results=max_results)

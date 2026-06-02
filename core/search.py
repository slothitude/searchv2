import httpx
from config import settings


async def search_searxng(
    query: str,
    categories: str = "general",
    max_results: int = 10,
    engines: str | None = None,
) -> list[dict]:
    """Search via SearXNG. Uses pinned engines from config by default."""
    if engines is None:
        engines = settings.searxng_engines
    async with httpx.AsyncClient(timeout=30) as client:
        params = {
            "q": query,
            "format": "json",
            "categories": categories,
        }
        if engines:
            params["engines"] = engines
        resp = await client.get(
            settings.searxng_url + "/search",
            params=params,
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

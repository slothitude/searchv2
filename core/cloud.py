import httpx
from config import settings


async def call_nim(
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    temperature: float = 0.5,
    max_tokens: int = 2048,
) -> str:
    """Call NVIDIA NIM API (cloud/nuclear tier)."""
    if not settings.nim_api_key:
        raise RuntimeError("NIM API key not configured")

    model = model or settings.nim_model
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.nim_base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.nim_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def arbitrate(
    position_a: str,
    position_b: str,
    context: str,
) -> str:
    """Nuclear arbitration: resolve disagreement between tiers."""
    prompt = f"""Two AI tiers disagree. Resolve this disagreement.

Position A: {position_a}
Position B: {position_b}
Context: {context}

Provide your final decision with reasoning. Be concise."""
    return await call_nim(prompt, temperature=0.3)

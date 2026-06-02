from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "SearchV2"
    host: str = "0.0.0.0"
    port: int = 7710
    debug: bool = False
    data_dir: Path = Path("data")
    db_url: str = "sqlite+aiosqlite:///data/searchv2.db"

    # Ollama
    ollama_base_url: str = "http://100.84.161.63:11434"
    model_reflex: str = "qwen3.5:0.8b"
    model_attention: str = "qwen3.5:2b"
    model_reasoning: str = "qwen3.5:4b"
    model_action: str = "qwen3.5:9b"

    # Cloud (NVIDIA NIM)
    nim_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "meta/llama-3.1-405b-instruct"

    # SearXNG
    searxng_url: str = "http://100.84.161.63:8888"
    searxng_engines: str = "google,duckduckgo,bing"  # comma-separated, pinned engines

    # Swarm
    swarm_name: str = ""
    swarm_peers: str = ""
    swarm_key: str = ""

    # Observation
    observation_interval: int = 60
    auto_accept_utility_threshold: float = 10.0

    # Knowledge
    confidence_decay_enabled: bool = True
    confidence_decay_check_interval: int = 3600
    re_verification_threshold: float = 0.5
    min_claim_confidence: float = 0.3

    # Skills
    skill_detection_threshold: int = 5

    # MCP
    mcp_enabled: bool = True
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 7711

    # Queue
    queue_enabled: bool = True
    queue_poll_interval: float = 2.0

    # Auth
    secret_key: str = "searchv2-dev-secret-change-me"
    bootstrap_token: str = ""

    # RSS
    rss_feeds: dict = {
        "ABC News": "https://www.abc.net.au/news/feed/51120/rss.xml",
        "ABC Just In": "https://www.abc.net.au/news/feed/46182/rss.xml",
        "Guardian Australia": "https://www.theguardian.com/au/rss",
        "Google News AU": "https://news.google.com/rss?hl=en-AU&gl=AU&ceid=AU:en",
        "SBS News": "https://www.sbs.com.au/news/feed",
        "Sky News AU": "https://feeds.skynews.com.au/rss.xml",
        "9News": "https://feeds.9news.com.au/rss",
        "SMH": "https://www.smh.com.au/rss/headlines.xml",
        "The Age": "https://www.theage.com.au/rss/headlines.xml",
        "news.com.au": "https://www.news.com.au/rss",
    }
    rss_poll_interval: int = 3600  # seconds between RSS polls

    model_config = {"env_prefix": "SEARCHV2_"}


settings = Settings()

from pydantic import model_validator
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
    secret_key: str = ""  # Set via SEARCHV2_SECRET_KEY, or auto-generated on first run
    bootstrap_token: str = ""

    # RSS
    rss_feeds: dict = {
        "ABC News": "https://www.abc.net.au/news/feed/51120/rss.xml",
        "ABC Just In": "https://www.abc.net.au/news/feed/46182/rss.xml",
        "Guardian Australia": "https://www.theguardian.com/au/rss",
        "Google News AU": "https://news.google.com/rss?hl=en-AU&gl=AU&ceid=AU:en",
        "SBS News": "https://www.sbs.com.au/news/feed",
        "9News": "https://www.9news.com.au/rss",
        "BBC Australia": "https://feeds.bbci.co.uk/news/world/australia/rss.xml",
        "Crikey": "https://www.crikey.com.au/feed/",
        "7News": "https://7news.com.au/rss",
        "Perth Now": "https://www.perthnow.com.au/news/rss",
    }
    rss_poll_interval: int = 3600  # seconds between RSS polls
    rss_max_retries: int = 3
    rss_disable_after_errors: int = 10

    # Belief Propagation
    belief_enabled: bool = True
    belief_interval: int = 1800              # 30 min between sweeps
    belief_max_hops: int = 4
    belief_damping: float = 0.5
    belief_min_delta: float = 0.01
    belief_max_llm_checks: int = 10
    belief_contradiction_llm_threshold: float = 0.6
    belief_curiosity_threshold: float = 0.15

    # Curiosity
    curiosity_enabled: bool = True
    curiosity_interval: int = 600           # seconds between scans (10 min)
    curiosity_max_per_cycle: int = 3       # max jobs per cycle
    curiosity_dedup_window: int = 1800      # suppress dupes for 30 min
    curiosity_decay_limit: int = 5          # max decayed entities per cycle
    curiosity_hypothesis_threshold: float = 0.3
    curiosity_prediction_stale_days: int = 7
    curiosity_skill_min_usage: int = 3
    curiosity_skill_min_rate: float = 0.5
    curiosity_desert_claim_threshold: int = 2

    @model_validator(mode="after")
    def resolve_paths(self):
        self.data_dir = self.data_dir.resolve()
        self.db_url = f"sqlite+aiosqlite:///{self.data_dir}/searchv2.db"
        # Auto-generate and persist secret key if not set
        if not self.secret_key:
            key_file = self.data_dir / ".secret_key"
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                import secrets
                self.secret_key = secrets.token_hex(32)
                key_file.write_text(self.secret_key)
        return self

    model_config = {"env_prefix": "SEARCHV2_"}


settings = Settings()

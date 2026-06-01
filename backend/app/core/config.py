import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class AppConfig:
    app_name: str = os.getenv("APP_NAME", "不求人")
    dev_mode: bool = os.getenv("DEV_MODE", "false").lower() == "true"
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    minimax_api_key: str = os.getenv("MINIMAX_API_KEY", "")
    minimax_base_url: str = os.getenv("MINIMAX_BASE_URL", "")
    minimax_model: str = os.getenv("MINIMAX_MODEL", "")

    search_provider: str = os.getenv("SEARCH_PROVIDER", "firecrawl_mcp")
    firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "")
    firecrawl_api_url: str = os.getenv("FIRECRAWL_API_URL", "http://176.126.87.5:3002")
    firecrawl_mcp_command: str = os.getenv("FIRECRAWL_MCP_COMMAND", "npx")
    firecrawl_mcp_args: str = os.getenv("FIRECRAWL_MCP_ARGS", "-y firecrawl-mcp")
    firecrawl_mcp_timeout_seconds: int = int(os.getenv("FIRECRAWL_MCP_TIMEOUT_SECONDS", "60"))

    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))
    max_search_results: int = int(os.getenv("MAX_SEARCH_RESULTS", "6"))
    max_fetch_pages: int = int(os.getenv("MAX_FETCH_PAGES", "1"))
    guide_freshness_days: int = int(os.getenv("GUIDE_FRESHNESS_DAYS", "30"))
    guide_expiry_days: int = int(os.getenv("GUIDE_EXPIRY_DAYS", "90"))
    http_user_agent: str = os.getenv("HTTP_USER_AGENT", "BuQiuRenBot/1.0")
    kb_path: str = os.getenv("BUQIUREN_KB_PATH", str(BASE_DIR / "data" / "buqiuren_production_guide_kb.json"))

    @property
    def cors_origin_list(self) -> list[str]:
        configured = [x.strip() for x in self.cors_origins.split(",") if x.strip()]
        dev_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:5180",
            "http://127.0.0.1:5180",
            "http://localhost:5181",
            "http://127.0.0.1:5181",
            "http://localhost:5182",
            "http://127.0.0.1:5182",
        ]
        return list(dict.fromkeys([*configured, *dev_origins]))

CONFIG = AppConfig()

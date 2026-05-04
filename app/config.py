from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageBackend(str, Enum):
    memory = "memory"
    mongodb = "mongodb"
    sqlite = "sqlite"


class QueueBackend(str, Enum):
    memory = "memory"
    redis = "redis"


class LLMProvider(str, Enum):
    none = "none"
    openai = "openai"
    anthropic = "anthropic"


class ProxyRotation(str, Enum):
    round_robin = "round_robin"
    random = "random"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = True
    secret_key: str = "change-me-in-production"

    # Search
    brave_search_api_key: Optional[str] = None

    # LLM
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    llm_provider: LLMProvider = LLMProvider.none
    llm_model: str = "gpt-4o-mini"

    # Storage
    storage_backend: StorageBackend = StorageBackend.memory
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "leadgen"

    # Queue
    queue_backend: QueueBackend = QueueBackend.memory
    redis_url: str = "redis://localhost:6379/0"

    # Proxies
    proxy_list: str = ""
    proxy_rotation: ProxyRotation = ProxyRotation.round_robin
    proxy_enabled: bool = False

    # Scraping
    request_timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 2.0
    rate_limit_rps: float = 2.0
    max_concurrent_crawls: int = 10
    use_playwright: bool = True

    # SERP
    serp_results_per_query: int = 10
    max_serp_queries: int = 5

    # Lead quality
    min_lead_score: int = 0          # 0 = return all; raise to filter low-quality leads
    dedupe_threshold: float = 0.85

    # LinkedIn
    linkedin_enabled: bool = True
    linkedin_max_per_job: int = 5
    linkedin_delay_min: float = 5.0
    linkedin_delay_max: float = 10.0

    @property
    def proxy_list_parsed(self) -> List[str]:
        if not self.proxy_list:
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]


settings = Settings()

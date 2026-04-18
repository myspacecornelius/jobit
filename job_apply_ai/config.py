"""Centralized configuration for the Job Application AI Agent.

All tunables live here. Values are read from environment variables (with
`.env` auto-loaded by `job_apply_ai/__init__.py`) and fall back to sane
defaults. Import `get_config()` anywhere that needs a setting.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value if value else default


def _env_int(key: str, default: int) -> int:
    value = os.environ.get(key)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_path(key: str, default: Path) -> Path:
    value = os.environ.get(key)
    return Path(value).expanduser().resolve() if value else default


def _env_list(key: str, default: List[str]) -> List[str]:
    value = os.environ.get(key)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Paths:
    root: Path
    outputs: Path
    jobs_dir: Path
    cvs_dir: Path
    uploads_dir: Path


@dataclass
class WebConfig:
    host: str
    port: int
    debug: bool
    secret_key: str


@dataclass
class ScraperConfig:
    headless: bool
    page_timeout: int
    scroll_count: int
    scroll_pause: float
    scroll_pixels: int
    max_retries: int
    retry_backoff: float
    max_days_old: int
    user_agent: str
    search_url_template: str
    job_card_selectors: List[str]
    title_selectors: List[str]
    company_selectors: List[str]
    description_selectors: List[str]


@dataclass
class NLPConfig:
    spacy_model: str
    auto_download: bool


@dataclass
class OpenAIConfig:
    api_key: Optional[str]
    model: str
    temperature: float
    max_tokens: int


@dataclass
class Config:
    paths: Paths
    web: WebConfig
    scraper: ScraperConfig
    nlp: NLPConfig
    openai: OpenAIConfig
    log_level: str


def _default_paths() -> Paths:
    outputs = _env_path("JOBIT_OUTPUT_DIR", PROJECT_ROOT / "outputs")
    return Paths(
        root=PROJECT_ROOT,
        outputs=outputs,
        jobs_dir=_env_path("JOBIT_JOBS_DIR", outputs / "jobs"),
        cvs_dir=_env_path("JOBIT_CVS_DIR", outputs / "cvs"),
        uploads_dir=_env_path("JOBIT_UPLOADS_DIR", outputs / "uploads"),
    )


def _default_web() -> WebConfig:
    secret = os.environ.get("JOBIT_SECRET_KEY") or os.environ.get("SECRET_KEY")
    return WebConfig(
        host=_env_str("JOBIT_HOST", "127.0.0.1"),
        port=_env_int("JOBIT_PORT", 5000),
        debug=_env_bool("JOBIT_DEBUG", False),
        secret_key=secret or secrets.token_hex(32),
    )


def _default_scraper() -> ScraperConfig:
    return ScraperConfig(
        headless=_env_bool("JOBIT_HEADLESS", True),
        page_timeout=_env_int("JOBIT_PAGE_TIMEOUT", 20),
        scroll_count=_env_int("JOBIT_SCROLL_COUNT", 3),
        scroll_pause=float(_env_str("JOBIT_SCROLL_PAUSE", "2.0")),
        scroll_pixels=_env_int("JOBIT_SCROLL_PIXELS", 800),
        max_retries=_env_int("JOBIT_MAX_RETRIES", 2),
        retry_backoff=float(_env_str("JOBIT_RETRY_BACKOFF", "2.0")),
        max_days_old=_env_int("JOBIT_MAX_DAYS_OLD", 14),
        user_agent=_env_str(
            "JOBIT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ),
        search_url_template=_env_str(
            "JOBIT_SEARCH_URL",
            "https://www.linkedin.com/jobs/search?keywords={keyword}&location={location}",
        ),
        job_card_selectors=_env_list(
            "JOBIT_JOB_CARD_SELECTORS",
            ["base-card", "job-search-card"],
        ),
        title_selectors=_env_list(
            "JOBIT_TITLE_SELECTORS",
            ["h1.topcard__title", "h1.top-card-layout__title", "h1"],
        ),
        company_selectors=_env_list(
            "JOBIT_COMPANY_SELECTORS",
            [
                "a.topcard__org-name-link",
                "span.topcard__flavor",
                ".top-card-layout__entity-info a",
            ],
        ),
        description_selectors=_env_list(
            "JOBIT_DESCRIPTION_SELECTORS",
            ["description__text", "show-more-less-html__markup"],
        ),
    )


def _default_nlp() -> NLPConfig:
    return NLPConfig(
        spacy_model=_env_str("JOBIT_SPACY_MODEL", "en_core_web_sm"),
        auto_download=_env_bool("JOBIT_SPACY_AUTO_DOWNLOAD", False),
    )


def _default_openai() -> OpenAIConfig:
    return OpenAIConfig(
        api_key=os.environ.get("OPENAI_API_KEY"),
        model=_env_str("JOBIT_OPENAI_MODEL", "gpt-4o-mini"),
        temperature=float(_env_str("JOBIT_OPENAI_TEMPERATURE", "0.5")),
        max_tokens=_env_int("JOBIT_OPENAI_MAX_TOKENS", 1200),
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the process-wide config. Cached after first access."""
    return Config(
        paths=_default_paths(),
        web=_default_web(),
        scraper=_default_scraper(),
        nlp=_default_nlp(),
        openai=_default_openai(),
        log_level=_env_str("JOBIT_LOG_LEVEL", "INFO").upper(),
    )


def reset_config_cache() -> None:
    """Clear the cached config (used in tests after mutating env)."""
    get_config.cache_clear()

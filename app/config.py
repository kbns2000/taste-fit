"""Runtime configuration for Taste-Fit.

Everything comes from environment variables so the same image runs locally,
in CI (mock data), and on the hosted demo (live Qloo). No secret is ever
written to disk or logged; `summary()` only reports *whether* a key exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # --- Qloo -----------------------------------------------------------------
    # Hackathon keys only work against the hackathon host; production keys use
    # https://api.qloo.com. Both expose the same v2 surface.
    qloo_api_key: str = field(default_factory=lambda: _env("QLOO_API_KEY"))
    qloo_base_url: str = field(
        default_factory=lambda: _env("QLOO_BASE_URL", "https://hackathon.api.qloo.com")
    )
    # Force mock data even when a key exists (tests, offline demos).
    qloo_mode: str = field(default_factory=lambda: _env("QLOO_MODE", "auto"))  # auto|live|mock
    qloo_timeout_s: float = field(default_factory=lambda: float(_env("QLOO_TIMEOUT_S", "20")))
    qloo_cache_ttl_s: int = field(default_factory=lambda: _env_int("QLOO_CACHE_TTL_S", 6 * 3600))
    # Quota guard: keep the hosted demo inside the event quota.
    qloo_max_calls_per_run: int = field(default_factory=lambda: _env_int("QLOO_MAX_CALLS_PER_RUN", 16))
    qloo_daily_budget: int = field(default_factory=lambda: _env_int("QLOO_DAILY_BUDGET", 800))

    # --- LLM (optional) -------------------------------------------------------
    # Anthropic is the first-class provider. An OpenAI-compatible endpoint
    # (Hugging Face router, Gemini's OpenAI endpoint, Groq, ...) is accepted so
    # the demo can run on free tiers. With neither, the scripted planner and
    # template writer take over and the product still works end to end.
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-opus-5-5"))
    anthropic_effort: str = field(default_factory=lambda: _env("ANTHROPIC_EFFORT", "medium"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o-mini"))
    llm_mode: str = field(default_factory=lambda: _env("LLM_MODE", "auto"))  # auto|anthropic|openai|off
    llm_max_tool_calls: int = field(default_factory=lambda: _env_int("LLM_MAX_TOOL_CALLS", 12))

    # --- App ------------------------------------------------------------------
    runs_per_ip_per_10min: int = field(default_factory=lambda: _env_int("RUNS_PER_IP_PER_10MIN", 8))
    public_repo_url: str = field(
        default_factory=lambda: _env("PUBLIC_REPO_URL", "https://github.com/kbns2000/taste-fit")
    )
    app_version: str = "0.1.0"

    @property
    def qloo_live(self) -> bool:
        if self.qloo_mode == "mock":
            return False
        if self.qloo_mode == "live":
            return bool(self.qloo_api_key)
        return bool(self.qloo_api_key)

    @property
    def llm_provider(self) -> str:
        """Which LLM provider is active: 'anthropic', 'openai' or 'none'."""
        if self.llm_mode == "off":
            return "none"
        if self.llm_mode == "anthropic":
            return "anthropic" if self.anthropic_api_key else "none"
        if self.llm_mode == "openai":
            return "openai" if self.openai_api_key else "none"
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key:
            return "openai"
        return "none"

    def summary(self) -> dict:
        """Safe-to-publish view of the configuration (no secret values)."""
        return {
            "qloo_source": "live" if self.qloo_live else "mock",
            "qloo_base_url": self.qloo_base_url if self.qloo_live else None,
            "llm_provider": self.llm_provider,
            "llm_model": (
                self.anthropic_model
                if self.llm_provider == "anthropic"
                else self.openai_model if self.llm_provider == "openai" else None
            ),
            "version": self.app_version,
        }


settings = Settings()

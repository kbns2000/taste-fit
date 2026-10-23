"""Qloo adapter package: `get_client()` returns the live REST client when a key
is configured (and mock mode is not forced), otherwise the deterministic mock."""

from __future__ import annotations

import threading
from typing import Optional

from ..config import Settings, settings as default_settings
from .client import CallRecord, LiveQlooClient, QlooClient
from .mock import MockQlooClient
from .models import Demographics, Entity, HeatCell, QlooError, QuotaExceeded, Tag, TrendPoint

__all__ = [
    "CallRecord", "Demographics", "Entity", "HeatCell", "LiveQlooClient", "MockQlooClient",
    "QlooClient", "QlooError", "QuotaExceeded", "Tag", "TrendPoint", "get_client", "get_mock",
]

_lock = threading.Lock()
_live: Optional[LiveQlooClient] = None
_mock: Optional[MockQlooClient] = None


def get_mock() -> MockQlooClient:
    global _mock
    with _lock:
        if _mock is None:
            _mock = MockQlooClient()
        return _mock


def get_client(cfg: Optional[Settings] = None) -> QlooClient:
    """Process-wide client. Live when `QLOO_API_KEY` is set (unless QLOO_MODE=mock)."""
    global _live
    cfg = cfg or default_settings
    if not cfg.qloo_live:
        return get_mock()
    with _lock:
        if _live is None:
            _live = LiveQlooClient(
                api_key=cfg.qloo_api_key,
                base_url=cfg.qloo_base_url,
                timeout_s=cfg.qloo_timeout_s,
                cache_ttl_s=cfg.qloo_cache_ttl_s,
                daily_budget=cfg.qloo_daily_budget,
            )
        return _live

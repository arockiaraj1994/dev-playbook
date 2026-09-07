"""
cache.py - the in-memory standards corpus, with a manual reload.

Standards are boot-cached: they change weekly and are CI-gated, so there is no
TTL poll. The dashboard's "Reload corpus" button (POST /dashboard/reload) is the
one way to pick up edits without restarting. Reload is atomic (swap, never
mutate) and single-flight under an asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from corpus import CorpusSpec
from loader import RuleDoc, parse_corpus

logger = logging.getLogger(__name__)


class CorpusCache:
    """Boot-cached corpus with an explicit reload. Swaps, never mutates."""

    def __init__(
        self,
        spec: CorpusSpec,
        *,
        on_reload: Callable[[str, list[RuleDoc]], None] | None = None,
    ) -> None:
        self._spec = spec
        self._docs: list[RuleDoc] = []
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()
        self._on_reload = on_reload

    @property
    def spec(self) -> CorpusSpec:
        return self._spec

    def load_sync(self) -> list[RuleDoc]:
        """Synchronous initial load (used at server startup)."""
        self._docs = parse_corpus(self._spec)
        self._loaded_at = time.monotonic()
        logger.info("CorpusCache[%s] loaded %d docs.", self._spec.name, len(self._docs))
        return self._docs

    def snapshot(self) -> list[RuleDoc]:
        """Return the current docs without triggering a reload."""
        return self._docs

    async def force_reload(self) -> list[RuleDoc]:
        """Manual reload (dashboard POST /reload)."""
        async with self._lock:
            fresh = await asyncio.to_thread(parse_corpus, self._spec)
            self._docs = fresh  # atomic swap
            self._loaded_at = time.monotonic()
            logger.info("CorpusCache[%s] force-reloaded %d docs.", self._spec.name, len(fresh))
            if self._on_reload is not None:
                self._on_reload(self._spec.name, fresh)
            return self._docs

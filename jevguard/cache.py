"""
jevguard.cache - Zero-Token Deterministic Hashing Cache for TypeSafe AI / Jev.
Stores decisions indexed by SHA-256 fingerprints of canonical JSON with volatile key masking.
"""

import contextlib
import hashlib
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, Generator, Iterable, Optional, Set

import os

logger = logging.getLogger("jevguard.cache")

DEFAULT_VOLATILE_KEYS: Set[str] = {
    "timestamp",
    "trace_id",
    "span_id",
    "request_id",
    "correlation_id",
    "nonce",
    "traceid",
    "requestid",
    "x_trace_id",
    "x_request_id",
    "x_correlation_id",
    "xtraceid",
    "xrequestid"
}


class DeterministicCache:
    """Provides instant 0-token response retrieval for repeated queries."""

    def __init__(
        self,
        db_path: str = "jevguard_cache.db",
        max_memory_items: int = 500,
        default_ignore_keys: Optional[Iterable[str]] = None,
        ttl_seconds: Optional[float] = None
    ):
        self.db_path = db_path
        self.max_memory_items = max_memory_items
        raw_ttl = os.environ.get("JEVGUARD_CACHE_TTL")
        if raw_ttl is not None:
            try:
                self.ttl_seconds = float(raw_ttl)
            except (ValueError, TypeError):
                self.ttl_seconds = 3600.0
        elif ttl_seconds is not None:
            self.ttl_seconds = float(ttl_seconds)
        else:
            self.ttl_seconds = 3600.0
        self.default_ignore_keys = (
            set(DEFAULT_VOLATILE_KEYS).union({str(k).strip().lower().replace("-", "_") for k in default_ignore_keys})
            if default_ignore_keys is not None
            else set(DEFAULT_VOLATILE_KEYS)
        )
        self._memory_lru: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._local = threading.local()
        self._shared_conn: Optional[sqlite3.Connection] = None

        if self.db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row

        self.stats = {
            "hits": 0,
            "misses": 0,
            "tokens_saved": 0
        }
        self._init_db()

    @contextlib.contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        if self._shared_conn is not None:
            with self._lock:
                yield self._shared_conn
        else:
            conn = getattr(self._local, "conn", None)
            if conn is None:
                conn = sqlite3.connect(self.db_path, timeout=20.0)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                except Exception as e:
                    logger.debug("PRAGMA setup notice: %s", e)
                self._local.conn = conn
            with self._lock:
                yield conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_cache (
                    fingerprint TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    tokens_estimate INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    @classmethod
    def _strip_volatile_keys(cls, data: Any, ignore_keys: Set[str], seen: Optional[Set[int]] = None) -> Any:
        if seen is None:
            seen = set()

        if isinstance(data, (dict, list, tuple, set, frozenset)):
            obj_id = id(data)
            if obj_id in seen:
                return "<cyclic_ref>"
            seen.add(obj_id)
        else:
            obj_id = None

        try:
            if isinstance(data, dict):
                cleaned = {}
                for k, v in data.items():
                    norm_k = str(k).strip().lower().replace("-", "_")
                    if norm_k in ignore_keys:
                        continue
                    cleaned[str(k)] = cls._strip_volatile_keys(v, ignore_keys, seen=seen)
                return cleaned
            elif isinstance(data, list):
                return [cls._strip_volatile_keys(item, ignore_keys, seen=seen) for item in data]
            elif isinstance(data, tuple):
                return tuple(cls._strip_volatile_keys(item, ignore_keys, seen=seen) for item in data)
            elif isinstance(data, (set, frozenset)):
                items = [cls._strip_volatile_keys(item, ignore_keys, seen=seen) for item in data]
                try:
                    return sorted(items)
                except TypeError:
                    return sorted(items, key=lambda x: str(x))
            return data
        finally:
            if obj_id is not None:
                seen.remove(obj_id)

    @classmethod
    def compute_fingerprint(
        cls,
        model: str = "jev-latest",
        state: Any = None,
        wire_questions: Optional[Dict[str, Any]] = None,
        ignore_keys: Optional[Iterable[str]] = None
    ) -> str:
        # Detect positional invocation without model: compute_fingerprint(state, questions, ignore_keys)
        if isinstance(model, (dict, list)):
            if wire_questions is not None and not isinstance(wire_questions, dict):
                ignore_keys = wire_questions
            wire_questions = state if isinstance(state, dict) else {}
            state = model
            model = "jev-latest"

        target_model = model or "jev-latest"
        target_state = state if state is not None else {}
        target_questions = wire_questions if wire_questions is not None else {}

        keys_to_ignore = (
            set(DEFAULT_VOLATILE_KEYS).union({str(k).strip().lower().replace("-", "_") for k in ignore_keys})
            if ignore_keys is not None
            else set(DEFAULT_VOLATILE_KEYS)
        )
        filtered_state = cls._strip_volatile_keys(target_state, keys_to_ignore) if keys_to_ignore else target_state

        canonical_struct = {
            "model": target_model.strip().lower(),
            "state": filtered_state,
            "questions": target_questions
        }
        canonical_bytes = json.dumps(
            canonical_struct,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def set(
        self,
        fingerprint: str,
        data: Dict[str, Any],
        model: str = "jev-latest",
        input_tokens_estimate: int = 0
    ) -> None:
        """Alias for put() following standard key-value cache conventions."""
        self.put(fingerprint, model, data, input_tokens_estimate)

    def get(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if fingerprint in self._memory_lru:
                item = self._memory_lru[fingerprint]
                entry_time = item.get("created_at", 0.0)
                if self.ttl_seconds > 0 and (time.time() - entry_time) > self.ttl_seconds:
                    del self._memory_lru[fingerprint]
                else:
                    self.stats["hits"] += 1
                    tokens = item.get("tokens_estimate", 0)
                    self.stats["tokens_saved"] += tokens
                    self._promote_lru(fingerprint, item["data"], tokens, created_at=entry_time)
                    return item["data"]

        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT response_json, tokens_estimate, created_at FROM evaluation_cache WHERE fingerprint = ?",
                    (fingerprint,)
                )
                row = cur.fetchone()
                if row:
                    entry_created_at = row["created_at"]
                    if self.ttl_seconds > 0 and (time.time() - entry_created_at) > self.ttl_seconds:
                        conn.execute("DELETE FROM evaluation_cache WHERE fingerprint = ?", (fingerprint,))
                        conn.commit()
                    else:
                        with self._lock:
                            self.stats["hits"] += 1
                            data = json.loads(row["response_json"])
                            tokens = row["tokens_estimate"]
                            self.stats["tokens_saved"] += tokens
                            self._promote_lru(fingerprint, data, tokens, created_at=entry_created_at)
                        return data
        except Exception as err:
            logger.warning("Cache lookup error for %s: %s", fingerprint, err)

        with self._lock:
            self.stats["misses"] += 1
        return None

    def put(self, fingerprint: str, model: str, response_data: Dict[str, Any], input_tokens_estimate: int) -> None:
        now = time.time()
        try:
            raw_json = json.dumps(response_data, separators=(",", ":"))
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO evaluation_cache (
                        fingerprint, model, response_json, created_at, hit_count, tokens_estimate
                    ) VALUES (?, ?, ?, ?, COALESCE((SELECT hit_count FROM evaluation_cache WHERE fingerprint = ?), 0), ?)
                """, (fingerprint, model, raw_json, now, fingerprint, input_tokens_estimate))
                conn.commit()

            with self._lock:
                self._promote_lru(fingerprint, response_data, input_tokens_estimate, created_at=now)
        except Exception as err:
            logger.warning("Cache store error for %s: %s", fingerprint, err)

    def _promote_lru(self, fingerprint: str, data: Dict[str, Any], tokens_estimate: int, created_at: Optional[float] = None) -> None:
        if fingerprint in self._memory_lru:
            existing = self._memory_lru.pop(fingerprint)
            hit_count = existing.get("hit_count", 0) + 1
            entry_time = existing.get("created_at", created_at or time.time())
        else:
            hit_count = 0
            entry_time = created_at or time.time()

        if len(self._memory_lru) >= self.max_memory_items:
            oldest_key = next(iter(self._memory_lru))
            del self._memory_lru[oldest_key]
        self._memory_lru[fingerprint] = {
            "data": data,
            "tokens_estimate": tokens_estimate,
            "hit_count": hit_count,
            "created_at": entry_time
        }

    def clear(self) -> None:
        with self._lock:
            self._memory_lru.clear()
            self.stats["hits"] = 0
            self.stats["misses"] = 0
            self.stats["tokens_saved"] = 0
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM evaluation_cache")
                conn.commit()
        except Exception as err:
            logger.warning("Cache clear error: %s", err)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.stats["hits"] + self.stats["misses"]
            rate = round((self.stats["hits"] / total) * 100, 2) if total > 0 else 0.0
            return {
                "hits": self.stats["hits"],
                "misses": self.stats["misses"],
                "hit_rate_pct": rate,
                "tokens_saved": self.stats["tokens_saved"]
            }

    def close(self) -> None:
        with self._lock:
            if self._shared_conn is not None:
                try:
                    self._shared_conn.close()
                except Exception:
                    pass
                self._shared_conn = None
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None

    def __del__(self) -> None:
        self.close()


SemanticCache = DeterministicCache

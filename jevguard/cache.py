"""
jevguard.cache - Zero-Token Deterministic Hashing Cache for TypeSafe AI / Jev.
Stores decisions indexed by SHA-256 fingerprints of canonical JSON.
"""

import contextlib
import hashlib
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger("jevguard.cache")


class DeterministicCache:
    """Provides instant 0-token response retrieval for repeated queries."""

    def __init__(self, db_path: str = "jevguard_cache.db", max_memory_items: int = 500):
        self.db_path = db_path
        self.max_memory_items = max_memory_items
        self._memory_lru: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
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
            conn = sqlite3.connect(self.db_path, timeout=20.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:
                logger.debug("PRAGMA setup notice: %s", e)
            try:
                yield conn
            finally:
                conn.close()

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
    def compute_fingerprint(cls, model: str, state: Any, wire_questions: Dict[str, Any]) -> str:
        canonical_struct = {
            "model": model.strip().lower(),
            "state": state,
            "questions": wire_questions
        }
        canonical_bytes = json.dumps(
            canonical_struct,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def get(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if fingerprint in self._memory_lru:
                self.stats["hits"] += 1
                item = self._memory_lru[fingerprint]
                self.stats["tokens_saved"] += item.get("tokens_estimate", 0)
                self._increment_db_hit(fingerprint)
                return item["data"]

        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT response_json, tokens_estimate FROM evaluation_cache WHERE fingerprint = ?",
                    (fingerprint,)
                )
                row = cur.fetchone()
                if row:
                    with self._lock:
                        self.stats["hits"] += 1
                        data = json.loads(row["response_json"])
                        tokens = row["tokens_estimate"]
                        self.stats["tokens_saved"] += tokens
                        self._promote_lru(fingerprint, data, tokens)
                    self._increment_db_hit(fingerprint)
                    return data
        except Exception as err:
            logger.warning("Cache lookup error for %s: %s", fingerprint, err)

        with self._lock:
            self.stats["misses"] += 1
        return None

    def put(self, fingerprint: str, model: str, response_data: Dict[str, Any], input_tokens_estimate: int) -> None:
        try:
            raw_json = json.dumps(response_data, separators=(",", ":"))
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO evaluation_cache (
                        fingerprint, model, response_json, created_at, hit_count, tokens_estimate
                    ) VALUES (?, ?, ?, ?, COALESCE((SELECT hit_count FROM evaluation_cache WHERE fingerprint = ?), 0), ?)
                """, (fingerprint, model, raw_json, time.time(), fingerprint, input_tokens_estimate))
                conn.commit()

            with self._lock:
                self._promote_lru(fingerprint, response_data, input_tokens_estimate)
        except Exception as err:
            logger.warning("Cache store error for %s: %s", fingerprint, err)

    def _increment_db_hit(self, fingerprint: str) -> None:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE evaluation_cache SET hit_count = hit_count + 1 WHERE fingerprint = ?",
                    (fingerprint,)
                )
                conn.commit()
        except Exception as err:
            logger.debug("Cache hit increment notice: %s", err)

    def _promote_lru(self, fingerprint: str, data: Dict[str, Any], tokens_estimate: int) -> None:
        if len(self._memory_lru) >= self.max_memory_items:
            oldest_key = next(iter(self._memory_lru))
            del self._memory_lru[oldest_key]
        self._memory_lru[fingerprint] = {
            "data": data,
            "tokens_estimate": tokens_estimate
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

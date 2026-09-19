"""
jevguard.memory - Episodic Session Memory for TypeSafe AI / Jev.
Stores multi-turn interaction traces in SQLite and builds compact rolling context
summaries without bloating input tokens.
"""

import contextlib
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger("jevguard.memory")


class EpisodicMemory:
    """Stores interaction turns and builds rolling context summaries."""

    def __init__(self, db_path: str = "jevguard_memory.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._local = threading.local()
        self._shared_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
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
                CREATE TABLE IF NOT EXISTS session_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    answers_json TEXT NOT NULL,
                    calibration_verdict TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sess ON session_turns(session_id, turn_number)")
            conn.commit()

    def record_turn(
        self,
        session_id: str,
        state: Any,
        answers: Dict[str, Any],
        verdict: str
    ) -> int:
        if not session_id or not isinstance(session_id, str):
            return 0

        clean_session = session_id.strip()
        state_str = json.dumps(state, separators=(",", ":"))
        answers_str = json.dumps(answers, separators=(",", ":"))

        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT COALESCE(MAX(turn_number), 0) + 1 AS next_turn FROM session_turns WHERE session_id = ?",
                    (clean_session,)
                )
                next_turn = cur.fetchone()["next_turn"]

                conn.execute("""
                    INSERT INTO session_turns (
                        session_id, turn_number, state_json, answers_json, calibration_verdict, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (clean_session, next_turn, state_str, answers_str, verdict, time.time()))
                conn.commit()
                return next_turn

    def get_session_history(self, session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not session_id:
            return []

        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT turn_number, state_json, answers_json, calibration_verdict, timestamp
                FROM session_turns
                WHERE session_id = ?
                ORDER BY turn_number DESC
                LIMIT ?
            """, (session_id.strip(), limit))

            rows = cur.fetchall()
            history = []
            for r in reversed(rows):
                history.append({
                    "turn_number": r["turn_number"],
                    "state": json.loads(r["state_json"]),
                    "answers": json.loads(r["answers_json"]),
                    "verdict": r["calibration_verdict"],
                    "timestamp": r["timestamp"]
                })
            return history

    def build_rolling_context(self, session_id: str, max_turns: int = 3) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None

        history = self.get_session_history(session_id, limit=max_turns)
        if not history:
            return None

        compact_turns = []
        for h in history:
            compact_answers = {}
            for q_name, ans in h.get("answers", {}).items():
                if isinstance(ans, dict):
                    q_type = ans.get("type")
                    if q_type == "noul":
                        compact_answers[q_name] = ans.get("noul")
                    elif q_type == "score":
                        compact_answers[q_name] = ans.get("score")
                    elif q_type == "choice":
                        compact_answers[q_name] = ans.get("choice")

            compact_turns.append({
                "turn": h["turn_number"],
                "verdict": h["verdict"],
                "outcomes": compact_answers
            })

        return {
            "session_id": session_id,
            "prior_turns_count": len(history),
            "recent_verdicts": compact_turns
        }

    def clear_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._get_connection() as conn:
            conn.execute("DELETE FROM session_turns WHERE session_id = ?", (session_id.strip(),))
            conn.commit()

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

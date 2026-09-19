"""
jevguard.client - JevGuardClient evaluation client for TypeSafe AI / Jev.
Provides drop-in compatibility with TypeSafeClient while adding deterministic caching,
state pruning, closed-world escape injection, and certainty calibration.
"""

import os
import time
import json
import urllib.request
import urllib.error
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .models import EvaluationResponse, Question, Noul, Score, Choice
from .optimizer import QuestionOptimizer, StatePruner
from .calibrator import ResponseCalibrator
from .cache import DeterministicCache
from .memory import EpisodicMemory

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


class JevGuardClient:
    """
    High-performance, deterministic evaluation client for TypeSafe AI System One / Jev.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        cache_db_path: str = "jevguard_cache.db",
        memory_db_path: str = "jevguard_memory.db",
        enable_cache: bool = True,
        enable_memory: bool = True,
        auto_inject_escapes: bool = True,
        cache_ignore_keys: Optional[Iterable[str]] = None
    ):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "").strip()
        self.endpoint = endpoint
        self.model = model
        self.enable_cache = enable_cache
        self.enable_memory = enable_memory
        self.auto_inject_escapes = auto_inject_escapes
        self.cache_ignore_keys = cache_ignore_keys

        self.optimizer = QuestionOptimizer(default_model=self.model, auto_inject_escapes=self.auto_inject_escapes)
        self.calibrator = ResponseCalibrator()
        self.cache = (
            DeterministicCache(db_path=cache_db_path, default_ignore_keys=cache_ignore_keys)
            if enable_cache
            else None
        )
        self.memory = EpisodicMemory(db_path=memory_db_path) if enable_memory else None

    def evaluate(
        self,
        state: Any,
        questions: Union[Dict[str, Any], List[Dict[str, Any]]],
        session_id: Optional[str] = None,
        bypass_cache: bool = False,
        timeout: float = 30.0,
        auto_inject_escapes: Optional[bool] = None,
        cache_ignore_keys: Optional[Iterable[str]] = None
    ) -> EvaluationResponse:
        """
        Executes the deterministic JevGuard evaluation pipeline:
        1. Prunes state to eliminate nulls and empty values.
        2. Injects closed-world escape alternatives when missing (unless auto_inject_escapes is False).
        3. Looks up deterministic SHA-256 cache with volatile key masking (0 tokens on hit).
        4. Dispatches wire payload to TypeSafe AI System One.
        5. Calibrates output probabilities and verifies certainty.
        6. Writes to cache and logs episodic session turns.
        """
        t0 = time.perf_counter()

        should_inject = self.auto_inject_escapes if auto_inject_escapes is None else auto_inject_escapes
        wire_payload, opt_metadata = self.optimizer.optimize_and_wire(
            state=state,
            questions=questions,
            model=self.model,
            auto_inject_escapes=should_inject
        )
        tokens_estimate = opt_metadata["estimated_tokens"]

        fingerprint = None
        if self.enable_cache and not bypass_cache and self.cache:
            ignore_keys = cache_ignore_keys if cache_ignore_keys is not None else self.cache_ignore_keys
            fingerprint = self.cache.compute_fingerprint(
                model=wire_payload["model"],
                state=wire_payload["state"],
                wire_questions=wire_payload["questions"],
                ignore_keys=ignore_keys
            )
            cached_item = self.cache.get(fingerprint)
            if cached_item:
                t1 = time.perf_counter()
                total_latency = round((t1 - t0) * 1000, 3)

                resp_dict = {
                    "success": True,
                    "cached": True,
                    "cache_fingerprint": fingerprint,
                    "session_id": session_id,
                    "telemetry": {
                        "latency_total_ms": total_latency,
                        "latency_inference_ms": 0.0,
                        "tokens_consumed": 0,
                        "tokens_saved": tokens_estimate,
                        "mode": "deterministic_cache_hit"
                    },
                    "optimization": opt_metadata,
                    "calibration": cached_item["calibration"],
                    "answers": cached_item["answers"]
                }
                return EvaluationResponse(resp_dict)

        if not self.api_key:
            raise RuntimeError(
                "TYPESAFE_API_KEY is not configured. Set environment variable or pass api_key to JevGuardClient."
            )

        upstream_res = self._dispatch_wire(wire_payload, timeout=timeout)
        raw_answers = upstream_res.get("data", {}).get("answers", {})

        t_cal0 = time.perf_counter()
        calibrated_answers, calib_summary = self.calibrator.calibrate(raw_answers)
        t_cal1 = time.perf_counter()
        calib_ms = round((t_cal1 - t_cal0) * 1000, 3)

        if self.enable_cache and self.cache and fingerprint:
            self.cache.put(
                fingerprint=fingerprint,
                model=wire_payload["model"],
                response_data={
                    "answers": calibrated_answers,
                    "calibration": calib_summary
                },
                input_tokens_estimate=tokens_estimate
            )

        turn_number = 0
        if session_id and self.enable_memory and self.memory:
            turn_number = self.memory.record_turn(
                session_id=session_id,
                state=wire_payload["state"],
                answers=calibrated_answers,
                verdict=calib_summary["verdict"]
            )

        t_end = time.perf_counter()
        total_latency = round((t_end - t0) * 1000, 3)

        resp_dict = {
            "success": True,
            "cached": False,
            "cache_fingerprint": fingerprint,
            "session_id": session_id,
            "turn_number": turn_number,
            "telemetry": {
                "latency_total_ms": total_latency,
                "latency_inference_ms": upstream_res.get("latency_ms", 0.0),
                "latency_calibration_ms": calib_ms,
                "tokens_consumed": tokens_estimate,
                "tokens_saved": 0,
                "mode": "live_upstream"
            },
            "optimization": opt_metadata,
            "calibration": calib_summary,
            "answers": calibrated_answers,
            "wire_payload": wire_payload
        }
        return EvaluationResponse(resp_dict)

    def system_one(
        self,
        state: Any,
        questions: Union[Dict[str, Any], List[Dict[str, Any]]],
        session_id: Optional[str] = None,
        bypass_cache: bool = False
    ) -> EvaluationResponse:
        """Alias matching official TypeSafeClient.system_one interface."""
        return self.evaluate(state, questions, session_id=session_id, bypass_cache=bypass_cache)

    def _dispatch_wire(self, wire_payload: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        raw_payload = json.dumps(wire_payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "JevGuard-Runtime/1.0"
        }

        req = urllib.request.Request(self.endpoint, data=raw_payload, headers=headers, method="POST")
        t0 = time.perf_counter()

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                t1 = time.perf_counter()
                latency_ms = round((t1 - t0) * 1000, 3)
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body else {}
                return {
                    "success": True,
                    "status_code": resp.getcode(),
                    "latency_ms": latency_ms,
                    "data": data
                }
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            try:
                err_data = json.loads(err_body)
            except Exception:
                err_data = {"raw": err_body}
            raise RuntimeError(f"TypeSafe AI HTTP Error {err.code}: {err_data}")
        except Exception as err:
            raise RuntimeError(f"TypeSafe AI connection error: {err}")

    def close(self) -> None:
        if self.cache is not None:
            self.cache.close()
        if self.memory is not None:
            self.memory.close()

    def __del__(self) -> None:
        self.close()

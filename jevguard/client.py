"""
jevguard.client - JevGuardClient evaluation client for TypeSafe AI / Jev.
Provides drop-in compatibility with TypeSafeClient while adding deterministic caching,
state pruning, closed-world escape injection, certainty calibration, resilient retries,
and high-concurrency batch execution.
"""

import os
import time
import json
import random
import socket
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .models import EvaluationResponse, Question, Noul, Score, Choice
from .optimizer import QuestionOptimizer, StatePruner
from .calibrator import ResponseCalibrator
from .cache import DeterministicCache
from .memory import EpisodicMemory
from .exceptions import (
    JevGuardError,
    JevGuardConfigError,
    JevGuardNetworkError,
    JevGuardTimeoutError,
    JevGuardHTTPError,
    JevGuardAuthenticationError,
    JevGuardRateLimitError,
    JevGuardServerError
)

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuses to follow HTTP redirects to prevent authorization header leakage."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            newurl, code, f"HTTP redirect ({code}) to '{newurl}' blocked for security.", headers, fp
        )


def is_authorized_endpoint(endpoint: str) -> bool:
    """Strictly validates that endpoint targets an authorized TypeSafe AI hostname without userinfo or spoofing."""
    try:
        parsed = urllib.parse.urlsplit(endpoint.strip())
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    if parsed.username or parsed.password:
        return False

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        return False

    allowed_hosts = {"api.typesafe.ai", "typesafe.ai"}
    custom_allowed = os.environ.get("JEVGUARD_ALLOWED_ENDPOINTS", "")
    if custom_allowed:
        for entry in custom_allowed.split(","):
            entry = entry.strip()
            if entry:
                if any(c in entry for c in "*?[]"):
                    continue
                try:
                    custom_parsed = urllib.parse.urlsplit(entry if "://" in entry else f"https://{entry}")
                    if custom_parsed.hostname and not any(c in custom_parsed.hostname for c in "*?[]"):
                        allowed_hosts.add(custom_parsed.hostname.lower())
                except Exception:
                    pass

    return hostname in allowed_hosts


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
        cache_ignore_keys: Optional[Iterable[str]] = None,
        max_retries: int = 3,
        initial_backoff: float = 0.5
    ):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "").strip()
        self._endpoint = str(endpoint or "").strip()
        if not is_authorized_endpoint(self._endpoint):
            raise JevGuardConfigError(
                f"Endpoint '{self._endpoint}' is not permitted. Only official TypeSafe AI endpoints or JEVGUARD_ALLOWED_ENDPOINTS are authorized."
            )
        self.model = model
        self.enable_cache = enable_cache
        self.enable_memory = enable_memory
        self.auto_inject_escapes = auto_inject_escapes
        self.cache_ignore_keys = cache_ignore_keys
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff

        self.optimizer = QuestionOptimizer(default_model=self.model, auto_inject_escapes=self.auto_inject_escapes)
        self.calibrator = ResponseCalibrator()
        self.cache = (
            DeterministicCache(db_path=cache_db_path, default_ignore_keys=cache_ignore_keys)
            if enable_cache
            else None
        )
        self.memory = EpisodicMemory(db_path=memory_db_path) if enable_memory else None

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @endpoint.setter
    def endpoint(self, value: str) -> None:
        val = str(value or "").strip()
        if not is_authorized_endpoint(val):
            raise JevGuardConfigError(
                f"Endpoint '{val}' is not permitted. Only official TypeSafe AI endpoints or JEVGUARD_ALLOWED_ENDPOINTS are authorized."
            )
        self._endpoint = val

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
        4. Dispatches wire payload to TypeSafe AI System One with resilient retry backoff.
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
            raise JevGuardConfigError(
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

    def batch_evaluate(
        self,
        batch_items: List[Tuple[Any, Union[Dict[str, Any], List[Dict[str, Any]]]]],
        max_workers: int = 10,
        bypass_cache: bool = False,
        timeout: float = 30.0
    ) -> List[EvaluationResponse]:
        """
        Executes concurrent batch evaluations using an internal thread pool,
        preserving the exact input order of responses.
        """
        if not batch_items:
            return []

        def _eval_worker(item_tuple: Tuple[int, Tuple[Any, Any]]) -> Tuple[int, EvaluationResponse]:
            idx, (item_state, item_questions) = item_tuple
            res = self.evaluate(
                state=item_state,
                questions=item_questions,
                bypass_cache=bypass_cache,
                timeout=timeout
            )
            return idx, res

        indexed_items = list(enumerate(batch_items))
        with ThreadPoolExecutor(max_workers=min(max_workers, len(batch_items))) as executor:
            indexed_results = list(executor.map(_eval_worker, indexed_items))

        indexed_results.sort(key=lambda x: x[0])
        return [res for _, res in indexed_results]

    async def async_evaluate(
        self,
        state: Any,
        questions: Union[Dict[str, Any], List[Dict[str, Any]]],
        session_id: Optional[str] = None,
        bypass_cache: bool = False,
        timeout: float = 30.0,
        auto_inject_escapes: Optional[bool] = None,
        cache_ignore_keys: Optional[Iterable[str]] = None
    ) -> EvaluationResponse:
        """Asynchronous evaluation compatible with asyncio event loops."""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.evaluate(
                state=state,
                questions=questions,
                session_id=session_id,
                bypass_cache=bypass_cache,
                timeout=timeout,
                auto_inject_escapes=auto_inject_escapes,
                cache_ignore_keys=cache_ignore_keys
            )
        )

    def system_one(
        self,
        state: Any,
        questions: Union[Dict[str, Any], List[Dict[str, Any]]],
        session_id: Optional[str] = None,
        bypass_cache: bool = False
    ) -> EvaluationResponse:
        """Alias matching official TypeSafeClient.system_one interface."""
        return self.evaluate(state, questions, session_id=session_id, bypass_cache=bypass_cache)

    def _compute_backoff_delay(self, attempt: int, retry_after_header: Optional[str] = None) -> float:
        """Calculates exponential backoff delay with jitter, respecting Retry-After header if present."""
        if retry_after_header:
            try:
                val = float(retry_after_header)
                if val >= 0:
                    return val
            except (ValueError, TypeError):
                pass
        return self.initial_backoff * (2 ** attempt) + random.uniform(0.05, 0.25)

    def _dispatch_wire(self, wire_payload: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        canonical_endpoint = self.endpoint
        if not is_authorized_endpoint(canonical_endpoint):
            raise JevGuardConfigError(
                f"Endpoint '{canonical_endpoint}' is not permitted. Only official TypeSafe AI endpoints or JEVGUARD_ALLOWED_ENDPOINTS are authorized."
            )
        raw_payload = json.dumps(wire_payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "JevGuard-Runtime/1.0"
        }

        attempts = 0
        max_attempts = max(1, self.max_retries + 1)
        opener = urllib.request.build_opener(NoRedirectHandler())

        while attempts < max_attempts:
            attempts += 1
            req = urllib.request.Request(canonical_endpoint, data=raw_payload, headers=headers, method="POST")
            t0 = time.perf_counter()

            try:
                with opener.open(req, timeout=timeout) as resp:
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

                code = err.code
                retry_after_hdr = err.headers.get("Retry-After") if err.headers else None
                retry_after = None
                if retry_after_hdr:
                    try:
                        parsed_val = float(retry_after_hdr)
                        if parsed_val >= 0:
                            retry_after = parsed_val
                    except (ValueError, TypeError):
                        pass

                # Non-retryable client errors
                if code in (400, 401, 403, 404):
                    if code in (401, 403):
                        raise JevGuardAuthenticationError(code, str(err_data), err_data, retry_after)
                    raise JevGuardHTTPError(code, str(err_data), err_data, retry_after)

                # Retryable rate limit (429) or server errors (500, 502, 503, 504)
                if attempts < max_attempts:
                    sleep_time = self._compute_backoff_delay(attempts - 1, retry_after_hdr)
                    time.sleep(sleep_time)
                    continue

                if code == 429:
                    raise JevGuardRateLimitError(code, str(err_data), err_data, retry_after)
                if code in (500, 502, 503, 504):
                    raise JevGuardServerError(code, str(err_data), err_data, retry_after)
                raise JevGuardHTTPError(code, str(err_data), err_data, retry_after)

            except (socket.timeout, TimeoutError) as err:
                if attempts < max_attempts:
                    sleep_time = self.initial_backoff * (2 ** (attempts - 1)) + random.uniform(0.05, 0.25)
                    time.sleep(sleep_time)
                    continue
                raise JevGuardTimeoutError(f"Connection to {self.endpoint} timed out after {timeout}s: {err}", timeout=timeout)

            except Exception as err:
                if attempts < max_attempts:
                    sleep_time = self.initial_backoff * (2 ** (attempts - 1)) + random.uniform(0.05, 0.25)
                    time.sleep(sleep_time)
                    continue
                raise JevGuardNetworkError(f"TypeSafe AI connection error: {err}")

        raise JevGuardNetworkError("Failed to reach TypeSafe AI after maximum retry attempts.")

    def close(self) -> None:
        cache = getattr(self, "cache", None)
        if cache is not None:
            try:
                cache.close()
            except Exception:
                pass
        memory = getattr(self, "memory", None)
        if memory is not None:
            try:
                memory.close()
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

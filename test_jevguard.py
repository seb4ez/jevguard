"""
test_jevguard.py - Comprehensive Unit, Concurrency, and Resilience Test Suite for JevGuard.
Pure Python standard library (unittest, concurrent.futures, tempfile, os, time, asyncio).
"""

import os
import tempfile
import time
import asyncio
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

from jevguard import (
    JevGuardClient,
    Noul,
    Score,
    Choice,
    NoulAnswer,
    ScoreAnswer,
    ChoiceAnswer,
    EvaluationResponse,
    EvaluationResult,
    StatePruner,
    QuestionOptimizer,
    ResponseCalibrator,
    DeterministicCache,
    EpisodicMemory,
    ESCAPE_OPTION_KEY,
    JevGuardError,
    JevGuardConfigError,
    JevGuardNetworkError,
    JevGuardTimeoutError,
    JevGuardHTTPError,
    JevGuardAuthenticationError,
    JevGuardRateLimitError,
    JevGuardServerError
)


class TestJevGuardPrimitives(unittest.TestCase):
    def test_noul_wire_format(self):
        n = Noul(instructions="Is CPU high?")
        wire = n.to_wire()
        self.assertEqual(wire["type"], "noul")
        self.assertEqual(wire["instructions"], "Is CPU high?")

    def test_score_wire_format(self):
        s = Score(instructions="Severity level", criteria=["P3", "P2", "P1"])
        wire = s.to_wire()
        self.assertEqual(wire["type"], "score")
        self.assertEqual(len(wire["criteria"]), 3)

    def test_choice_wire_format(self):
        c = Choice(instructions="Routing", criteria={"billing": "Payment issues", "tech": "Server errors"})
        wire = c.to_wire()
        self.assertEqual(wire["type"], "choice")
        self.assertIn("billing", wire["criteria"])

    def test_choice_closed_world_flag(self):
        c = Choice("Strict route", {"a": "A", "b": "B"}, closed_world=True)
        self.assertTrue(c.closed_world)
        self.assertFalse(c.auto_inject_escape)

    def test_evaluation_result_alias(self):
        self.assertIs(EvaluationResult, EvaluationResponse)


class TestJevGuardOptimizer(unittest.TestCase):
    def setUp(self):
        self.opt = QuestionOptimizer()

    def test_state_pruning_dict(self):
        state = {
            "valid": "ok",
            "empty": "",
            "none_field": None,
            "nested": {"deep_null": None, "number": 42}
        }
        pruned = StatePruner.prune(state)
        self.assertEqual(pruned, {"valid": "ok", "nested": {"number": 42}})

    def test_state_pruning_heterogeneous_types(self):
        """Sets, tuples, and floats (NaN/Inf) must be cleanly pruned without JSON serialization failure."""
        state = {
            "tags": {"python", "ai"},
            "coordinates": (10.5, 20.2),
            "nan_val": float("nan"),
            "inf_val": float("inf"),
            100: "integer_key"
        }
        pruned = StatePruner.prune(state)
        self.assertIn("tags", pruned)
        self.assertIsInstance(pruned["tags"], list)
        self.assertEqual(pruned["tags"], ["ai", "python"])
        self.assertEqual(pruned["coordinates"], (10.5, 20.2))
        self.assertNotIn("nan_val", pruned)
        self.assertNotIn("inf_val", pruned)
        self.assertIn("100", pruned)

        # Must be valid JSON
        import json
        dumped = json.dumps(pruned)
        self.assertIn("integer_key", dumped)

    def test_state_pruning_preserves_list_positions(self):
        arr = ["first", "", "third", None]
        pruned = StatePruner.prune(arr)
        self.assertEqual(len(pruned), 4)
        self.assertEqual(pruned[0], "first")
        self.assertEqual(pruned[1], "")
        self.assertEqual(pruned[2], "third")
        self.assertIsNone(pruned[3])

    def test_cycle_reference_protection(self):
        cyclic = {"name": "loop"}
        cyclic["self"] = cyclic
        pruned = StatePruner.prune(cyclic)
        self.assertEqual(pruned["self"], "<cyclic_ref>")

    def test_closed_world_escape_injection(self):
        questions = {
            "department": Choice(
                instructions="Route request",
                criteria={"support": "Technical support", "sales": "Enterprise inquiries"}
            )
        }
        wire_payload, metadata = self.opt.optimize_and_wire({"text": "Hello"}, questions)
        criteria = wire_payload["questions"]["department"]["criteria"]
        self.assertIn(ESCAPE_OPTION_KEY, criteria)
        self.assertTrue(metadata["has_injected_escapes"])

    def test_domain_none_does_not_block_escape_injection(self):
        questions = {
            "symptoms": Choice(
                instructions="Symptom check",
                criteria={"none": "No symptoms reported", "mild": "Mild discomfort"}
            )
        }
        wire_payload, metadata = self.opt.optimize_and_wire({"patient": "123"}, questions)
        criteria = wire_payload["questions"]["symptoms"]["criteria"]
        self.assertIn("none", criteria)
        self.assertIn(ESCAPE_OPTION_KEY, criteria)
        self.assertTrue(metadata["has_injected_escapes"])

    def test_closed_world_opt_out_preserves_strict_enum(self):
        questions = {
            "action": Choice(
                instructions="Approval decision",
                criteria={"APPROVED": "Approve request", "REJECTED": "Reject request"},
                closed_world=True
            )
        }
        wire_payload, metadata = self.opt.optimize_and_wire({"amount": 100}, questions)
        criteria = wire_payload["questions"]["action"]["criteria"]
        self.assertNotIn(ESCAPE_OPTION_KEY, criteria)
        self.assertEqual(len(criteria), 2)
        self.assertFalse(metadata["has_injected_escapes"])

    def test_global_auto_inject_escapes_disabled(self):
        opt_strict = QuestionOptimizer(auto_inject_escapes=False)
        questions = {
            "team": Choice(
                instructions="Route",
                criteria={"team_a": "Team A", "team_b": "Team B"}
            )
        }
        wire_payload, metadata = opt_strict.optimize_and_wire({"task": "build"}, questions)
        criteria = wire_payload["questions"]["team"]["criteria"]
        self.assertNotIn(ESCAPE_OPTION_KEY, criteria)
        self.assertFalse(metadata["has_injected_escapes"])


class TestJevGuardCalibrator(unittest.TestCase):
    def setUp(self):
        self.cal = ResponseCalibrator()

    def test_choice_ambiguous_state_detected(self):
        raw = {
            "routing": {
                "type": "choice",
                "choice": "opt_a",
                "confidence": 0.35,
                "probabilities": {"opt_a": 0.35, "opt_b": 0.33, "opt_c": 0.32}
            }
        }
        calibrated, summary = self.cal.calibrate(raw)
        self.assertTrue(summary["has_ambiguity"])
        self.assertEqual(summary["verdict"], "AMBIGUOUS_STATE")

    def test_choice_confident_state_accepted(self):
        raw = {
            "routing": {
                "type": "choice",
                "choice": "opt_a",
                "confidence": 0.88,
                "probabilities": {"opt_a": 0.88, "opt_b": 0.08, "opt_c": 0.04}
            }
        }
        calibrated, summary = self.cal.calibrate(raw)
        self.assertFalse(summary["has_ambiguity"])
        self.assertEqual(summary["verdict"], "CONFIDENT")

    def test_score_bimodal_tie_detected_as_ambiguous(self):
        raw = {
            "severity": {
                "type": "score",
                "score": 1,
                "confidence": 0.48,
                "probabilities": {"0": 0.05, "1": 0.48, "2": 0.47}
            }
        }
        calibrated, summary = self.cal.calibrate(raw)
        self.assertTrue(summary["has_ambiguity"])
        self.assertEqual(summary["verdict"], "AMBIGUOUS_STATE")
        self.assertIn("flat_distribution", calibrated["severity"]["calibration"]["reasons"])

    def test_noul_boundary_uncertainty_detected(self):
        raw = {
            "is_malicious": {
                "type": "noul",
                "noul": 0.56
            }
        }
        calibrated, summary = self.cal.calibrate(raw)
        self.assertTrue(summary["has_ambiguity"])
        self.assertEqual(summary["verdict"], "AMBIGUOUS_STATE")
        self.assertIn("boundary_uncertainty", calibrated["is_malicious"]["calibration"]["reasons"])

    def test_calibrator_malformed_probabilities_safe(self):
        raw = {
            "route": {
                "type": "choice",
                "choice": "A",
                "confidence": "not_a_number",
                "probabilities": {"A": "invalid", "B": 0.85}
            },
            "score": {
                "type": "score",
                "score": 1,
                "confidence": None,
                "probabilities": {"0": "invalid_num", "1": 0.9}
            }
        }
        calibrated, summary = self.cal.calibrate(raw)
        self.assertIn("route", calibrated)
        self.assertIn("score", calibrated)
        self.assertEqual(calibrated["route"]["calibration"]["top_choice"], "B")
        self.assertEqual(calibrated["route"]["calibration"]["top_probability"], 0.85)


class TestJevGuardCacheAndMemory(unittest.TestCase):
    def test_cache_hits_and_invariance(self):
        cache = DeterministicCache(db_path=":memory:")
        fp1 = cache.compute_fingerprint("jev-latest", {"a": 1, "b": 2}, {"q": {"type": "noul"}})
        fp2 = cache.compute_fingerprint("jev-latest", {"b": 2, "a": 1}, {"q": {"type": "noul"}})
        self.assertEqual(fp1, fp2)

        cache.put(fp1, "jev-latest", {"answers": {"q": {"noul": 0.99}}}, input_tokens_estimate=25)
        item = cache.get(fp1)
        self.assertIsNotNone(item)
        self.assertEqual(cache.get_stats()["hits"], 1)
        cache.close()

    def test_volatile_keys_masking_with_heterogeneous_structures(self):
        """Volatile keys inside dicts, tuples, sets and with non-string/dashed keys must produce cache hits."""
        cache = DeterministicCache(db_path=":memory:")
        state_1 = {
            "user_id": "usr_99",
            100: "num_key",
            "tuple_meta": ({"timestamp": 1726778900}, "constant"),
            "set_meta": {"tag1", "tag2"},
            "request-id": "req-001"
        }
        state_2 = {
            "user_id": "usr_99",
            100: "num_key",
            "tuple_meta": ({"timestamp": 1726778999}, "constant"),
            "set_meta": {"tag2", "tag1"},
            "request-id": "req-999"
        }
        questions = {"is_valid": {"type": "noul"}}

        fp1 = cache.compute_fingerprint("jev-latest", state_1, questions)
        fp2 = cache.compute_fingerprint("jev-latest", state_2, questions)
        self.assertEqual(fp1, fp2)

        cache.put(fp1, "jev-latest", {"answers": {"is_valid": {"noul": 1.0}}}, 15)
        hit_for_state_2 = cache.get(fp2)
        self.assertIsNotNone(hit_for_state_2)
        self.assertEqual(cache.get_stats()["hits"], 1)
        cache.close()

    def test_episodic_memory(self):
        mem = EpisodicMemory(db_path=":memory:")
        t1 = mem.record_turn("s1", {"event": "start"}, {"q": {"noul": 0.9}}, "CONFIDENT")
        t2 = mem.record_turn("s1", {"event": "step2"}, {"q": {"noul": 0.1}}, "CONFIDENT")
        self.assertEqual(t1, 1)
        self.assertEqual(t2, 2)
        ctx = mem.build_rolling_context("s1")
        self.assertEqual(ctx["prior_turns_count"], 2)
        mem.close()

    def test_multithreaded_concurrency(self):
        cache = DeterministicCache(db_path=":memory:")
        mem = EpisodicMemory(db_path=":memory:")

        def worker(idx: int):
            fp = f"fingerprint_{idx % 5}"
            cache.put(fp, "jev-latest", {"val": idx}, 10)
            res = cache.get(fp)
            turn = mem.record_turn("sess_concurrent", {"idx": idx}, {"res": res}, "CONFIDENT")
            return turn

        with ThreadPoolExecutor(max_workers=8) as executor:
            turns = list(executor.map(worker, range(40)))

        self.assertEqual(len(turns), 40)
        history = mem.get_session_history("sess_concurrent", limit=50)
        self.assertEqual(len(history), 40)
        cache.close()
        mem.close()

    def test_disk_connection_lifecycle_and_cleanup(self):
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_lifecycle.db")
        try:
            cache = DeterministicCache(db_path=db_path)
            cache.put("fp1", "jev-latest", {"data": "test"}, 15)
            val = cache.get("fp1")
            self.assertEqual(val["data"], "test")
            cache.close()
        finally:
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except Exception:
                    pass
            for extra in [db_path + "-wal", db_path + "-shm"]:
                if os.path.exists(extra):
                    try:
                        os.remove(extra)
                    except Exception:
                        pass
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass


class TestJevGuardClientAndBatch(unittest.TestCase):
    def test_client_config_error_without_api_key(self):
        client = JevGuardClient(api_key="", enable_cache=False)
        with self.assertRaises(JevGuardConfigError):
            client.evaluate({"state": 1}, {"q": Noul("test")})
        client.close()

    def test_batch_evaluate_preserves_order(self):
        client = JevGuardClient(api_key="mock_key", cache_db_path=":memory:", memory_db_path=":memory:")
        # Mock _dispatch_wire
        client._dispatch_wire = lambda payload, timeout=30.0: {
            "data": {"answers": {"q": {"type": "noul", "noul": float(payload["state"]["val"]) / 100.0}}}
        }

        items = [
            ({"val": 10}, {"q": Noul("Q1")}),
            ({"val": 50}, {"q": Noul("Q2")}),
            ({"val": 90}, {"q": Noul("Q3")})
        ]

        responses = client.batch_evaluate(items, max_workers=3)
        self.assertEqual(len(responses), 3)
        self.assertAlmostEqual(responses[0].nouls["q"].noul, 0.10)
        self.assertAlmostEqual(responses[1].nouls["q"].noul, 0.50)
        self.assertAlmostEqual(responses[2].nouls["q"].noul, 0.90)
        client.close()

    def test_async_evaluate_execution(self):
        client = JevGuardClient(api_key="mock_key", cache_db_path=":memory:", memory_db_path=":memory:")
        client._dispatch_wire = lambda payload, timeout=30.0: {
            "data": {"answers": {"q": {"type": "noul", "noul": 0.95}}}
        }

        async def run_async():
            return await client.async_evaluate({"text": "async test"}, {"q": Noul("Q")})

        resp = asyncio.run(run_async())
        self.assertAlmostEqual(resp.nouls["q"].noul, 0.95)
        client.close()

    def test_retry_after_float_parsing(self):
        client = JevGuardClient(api_key="mock_key", cache_db_path=":memory:", memory_db_path=":memory:")
        delay = client._compute_backoff_delay(0, retry_after_header="2.5")
        self.assertEqual(delay, 2.5)
        delay_invalid = client._compute_backoff_delay(0, retry_after_header="not_a_number")
        self.assertGreater(delay_invalid, 0.0)
        client.close()


class TestJevGuardCLI(unittest.TestCase):
    def test_cli_empty_stdin_exits_cleanly(self):
        cmd = [sys.executable, "-m", "jevguard.cli"]
        proc = subprocess.run(cmd, input="", capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Error", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_cli_malformed_json_exits_cleanly(self):
        cmd = [sys.executable, "-m", "jevguard.cli"]
        proc = subprocess.run(cmd, input="{invalid_json}", capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Error: Invalid JSON on stdin", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_cli_missing_rules_exits_cleanly(self):
        cmd = [sys.executable, "-m", "jevguard.cli", "--state", '{"a": 1}']
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Error: --rules", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class TestHardeningRemediation(unittest.TestCase):
    """Verifies library remediation for fail-open, whitespace, cache collision, and TTL."""

    def test_pruner_preserves_multiline_code_and_diffs(self):
        patch = (
            "--- a/calculator.py\n"
            "+++ b/calculator.py\n"
            "@@ -1,4 +1,4 @@\n"
            " def add(a: int, b: int) -> int:\n"
            "-    return a - b\n"
            "+    return a + b"
        )
        pruned = StatePruner.prune(patch)
        self.assertEqual(pruned, patch)

    def test_calibrator_score_missing_confidence_fails_closed(self):
        calibrator = ResponseCalibrator()
        answers = {"risk": {"type": "score", "score": 4}}
        calibrated, summary = calibrator.calibrate(answers)
        self.assertTrue(calibrated["risk"]["is_ambiguous"])
        self.assertIn("low_confidence", calibrated["risk"]["calibration"]["reasons"])
        self.assertEqual(summary["verdict"], "AMBIGUOUS_STATE")

    def test_calibrator_noul_missing_value_fails_closed(self):
        calibrator = ResponseCalibrator()
        answers = {"is_valid": {"type": "noul"}}
        calibrated, summary = calibrator.calibrate(answers)
        self.assertTrue(calibrated["is_valid"]["is_ambiguous"])
        self.assertIn("missing_noul_value", calibrated["is_valid"]["calibration"]["reasons"])
        self.assertEqual(summary["verdict"], "AMBIGUOUS_STATE")

    def test_calibrator_unknown_question_type_fails_closed(self):
        calibrator = ResponseCalibrator()
        answers = {"custom": {"type": "matrix_eval", "val": [1, 2, 3]}}
        calibrated, summary = calibrator.calibrate(answers)
        self.assertTrue(calibrated["custom"]["is_ambiguous"])
        self.assertIn("unknown_question_type", calibrated["custom"]["calibration"]["reasons"])
        self.assertEqual(summary["verdict"], "AMBIGUOUS_STATE")

    def test_cache_volatile_keys_does_not_strip_time(self):
        fp1 = DeterministicCache.compute_fingerprint(
            model="jev-latest",
            state={"action": "schedule", "time": "09:00"}
        )
        fp2 = DeterministicCache.compute_fingerprint(
            model="jev-latest",
            state={"action": "schedule", "time": "17:00"}
        )
        self.assertNotEqual(fp1, fp2, "Domain time field must not cause collision")

    def test_cache_ignore_keys_unions_with_defaults(self):
        state1 = {"user": "bob", "tenant_id": "tenant_1", "timestamp": 1000}
        state2 = {"user": "bob", "tenant_id": "tenant_2", "timestamp": 2000}
        fp1 = DeterministicCache.compute_fingerprint(
            model="jev-latest",
            state=state1,
            ignore_keys=["tenant_id"]
        )
        fp2 = DeterministicCache.compute_fingerprint(
            model="jev-latest",
            state=state2,
            ignore_keys=["tenant_id"]
        )
        self.assertEqual(fp1, fp2, "Both tenant_id and timestamp must be ignored")

    def test_cache_ttl_expiration(self):
        cache = DeterministicCache(db_path=":memory:", ttl_seconds=0.1)
        cache.put("fp_test_ttl", "jev-latest", {"status": "ok"}, input_tokens_estimate=5)
        self.assertEqual(cache.get("fp_test_ttl"), {"status": "ok"})
        time.sleep(0.15)
        self.assertIsNone(cache.get("fp_test_ttl"))

    def test_created_at_and_updated_at_not_masked_by_default(self):
        fp1 = DeterministicCache.compute_fingerprint(
            model="jev-latest",
            state={"order_id": "ord_100", "created_at": 1000, "updated_at": 1000}
        )
        fp2 = DeterministicCache.compute_fingerprint(
            model="jev-latest",
            state={"order_id": "ord_100", "created_at": 2000, "updated_at": 2000}
        )
        self.assertNotEqual(fp1, fp2, "created_at and updated_at must NOT be stripped by default to prevent version collisions")

    def test_ssrf_unauthorized_endpoints_blocked(self):
        with self.assertRaises(JevGuardConfigError):
            JevGuardClient(api_key="test", endpoint="https://api.typesafe.ai@evil.com/v1/systemone")

        with self.assertRaises(JevGuardConfigError):
            JevGuardClient(api_key="test", endpoint="https://api.typesafe.ai.evil.com/v1/systemone")

        with self.assertRaises(JevGuardConfigError):
            JevGuardClient(api_key="test", endpoint="http://api.typesafe.ai/v1/systemone")

    def test_http_redirect_raises_blocked_error(self):
        import urllib.error
        from jevguard.client import NoRedirectHandler
        handler = NoRedirectHandler()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            handler.redirect_request(None, None, 302, "Found", {}, "https://evil.com/leak")
        self.assertEqual(ctx.exception.code, 302)
        self.assertIn("blocked for security", str(ctx.exception.reason))

    def test_calibrator_nan_and_inf_fails_closed(self):
        calibrator = ResponseCalibrator()
        answers = {
            "c": {"type": "choice", "choice": "a", "confidence": float("nan"), "probabilities": {"a": float("inf")}},
            "n": {"type": "noul", "noul": float("nan")},
            "s": {"type": "score", "confidence": float("inf"), "score": 2}
        }
        calibrated, summary = calibrator.calibrate(answers)
        self.assertTrue(calibrated["c"]["is_ambiguous"])
        self.assertTrue(calibrated["n"]["is_ambiguous"])
        self.assertTrue(calibrated["s"]["is_ambiguous"])
        self.assertEqual(summary["verdict"], "AMBIGUOUS_STATE")

    def test_pruner_literal_command_whitespace_preservation(self):
        cmd = 'rm -rf "/tmp/a  b"'
        pruned = StatePruner.prune(cmd, collapse_whitespace=False)
        self.assertEqual(pruned, cmd)

    def test_client_endpoint_property_setter_validation(self):
        client = JevGuardClient(api_key="test")
        with self.assertRaises(JevGuardConfigError):
            client.endpoint = "https://attacker.com/v1"
        client.endpoint = "https://api.typesafe.ai/v1/systemone"
        self.assertEqual(client.endpoint, "https://api.typesafe.ai/v1/systemone")

    def test_choice_explicit_escape_and_closed_world(self):
        c1 = Choice("route", {"a": "A", "b": "B"}, closed_world=True, auto_inject_escape=True)
        self.assertTrue(c1.auto_inject_escape)
        c2 = Choice("route", {"a": "A", "b": "B"}, closed_world=False, auto_inject_escape=False)
        self.assertFalse(c2.auto_inject_escape)

    def test_optimizer_missing_type_raises_error(self):
        opt = QuestionOptimizer()
        with self.assertRaises(ValueError) as ctx:
            opt.optimize_and_wire({"k": 1}, {"q1": {"instructions": "do stuff"}})
        self.assertIn("missing required 'type' field", str(ctx.exception).lower())

    def test_answer_models_resilient_float_parsing(self):
        na = NoulAnswer({"noul": "not_a_number"})
        self.assertEqual(na.noul, 0.0)
        sa = ScoreAnswer({"score": "bad", "confidence": float("nan")})
        self.assertEqual(sa.score, 0.0)
        self.assertEqual(sa.confidence, 0.0)
        ca = ChoiceAnswer({"choice": "opt", "confidence": float("inf")})
        self.assertEqual(ca.confidence, 0.0)

    def test_episodic_memory_concurrent_threadpool_records(self):
        import tempfile
        import os
        from concurrent.futures import ThreadPoolExecutor

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            mem = EpisodicMemory(db_path=db_path)
            sess_id = "concurrent_session"

            def _record(idx: int) -> int:
                return mem.record_turn(sess_id, {"idx": idx}, {"ans": idx}, "CONFIDENT")

            with ThreadPoolExecutor(max_workers=10) as executor:
                turn_numbers = list(executor.map(_record, range(20)))

            self.assertEqual(len(set(turn_numbers)), 20)
            self.assertEqual(sorted(turn_numbers), list(range(1, 21)))
        finally:
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)

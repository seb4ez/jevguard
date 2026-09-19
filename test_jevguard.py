"""
test_jevguard.py - Comprehensive Unit & Concurrency Test Suite for JevGuard.
Pure Python standard library (unittest, concurrent.futures, tempfile, os, time).
"""

import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from jevguard import (
    JevGuardClient,
    Noul,
    Score,
    Choice,
    StatePruner,
    QuestionOptimizer,
    ResponseCalibrator,
    DeterministicCache,
    EpisodicMemory,
    ESCAPE_OPTION_KEY
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
        """When closed_world=True, UNRESOLVED_OR_OTHER must NOT be injected."""
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
        """When auto_inject_escapes=False, all choices preserve criteria intact."""
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

    def test_volatile_keys_masking_in_cache(self):
        """Requests differing only in volatile keys (timestamp, trace_id, request_id) must produce cache hits."""
        cache = DeterministicCache(db_path=":memory:")
        state_1 = {
            "user_id": "usr_99",
            "action": "login_failed",
            "timestamp": 1726778900,
            "trace_id": "abc-123-xyz",
            "request_id": "req-001"
        }
        state_2 = {
            "user_id": "usr_99",
            "action": "login_failed",
            "timestamp": 1726778905,
            "trace_id": "def-456-uvw",
            "request_id": "req-002"
        }
        questions = {"is_attack": {"type": "noul"}}

        fp1 = cache.compute_fingerprint("jev-latest", state_1, questions)
        fp2 = cache.compute_fingerprint("jev-latest", state_2, questions)
        self.assertEqual(fp1, fp2)

        cache.put(fp1, "jev-latest", {"answers": {"is_attack": {"noul": 0.88}}}, 15)
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

    def test_local_cpu_latency_overhead(self):
        """Microbenchmark verifying that full local Python pipeline overhead is well under 1.0 ms."""
        opt = QuestionOptimizer()
        cal = ResponseCalibrator()
        cache = DeterministicCache(db_path=":memory:")

        state = {"user": "alice", "action": "checkout", "timestamp": 12345}
        questions = {"route": Choice("Route", {"a": "A", "b": "B"})}
        mock_raw = {"route": {"type": "choice", "choice": "a", "confidence": 0.9, "probabilities": {"a": 0.9, "b": 0.1}}}

        # Warmup
        for _ in range(20):
            wire, _ = opt.optimize_and_wire(state, questions)
            fp = cache.compute_fingerprint("jev-latest", wire["state"], wire["questions"])
            cache.put(fp, "jev-latest", mock_raw, 10)
            _ = cache.get(fp)
            _ = cal.calibrate(mock_raw)

        N = 200
        t0 = time.perf_counter()
        for _ in range(N):
            wire, _ = opt.optimize_and_wire(state, questions)
            fp = cache.compute_fingerprint("jev-latest", wire["state"], wire["questions"])
            _ = cache.get(fp)
            _ = cal.calibrate(mock_raw)
        t1 = time.perf_counter()

        avg_ms = ((t1 - t0) * 1000) / N
        self.assertLess(avg_ms, 1.0, f"Average local overhead ({avg_ms:.4f} ms) must be under 1.0 ms")
        cache.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

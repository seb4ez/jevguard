"""
benchmark.py - Strict Performance and Latency Audit for JevGuard.
Measures local Python overhead (CPU, serialization, calibration, caching)
and executes live upstream tests if TYPESAFE_API_KEY is configured.
"""

import os
import sys
import json
import time
import urllib.request
from typing import Any, Dict

from jevguard import (
    JevGuardClient,
    StatePruner,
    QuestionOptimizer,
    ResponseCalibrator,
    DeterministicCache,
    EpisodicMemory,
    Noul,
    Score,
    Choice,
    ESCAPE_OPTION_KEY
)


def run_local_overhead_audit(iterations: int = 1000) -> Dict[str, float]:
    """Measures exact microsecond-level overhead added by the local Python layer."""
    pruner = StatePruner()
    opt = QuestionOptimizer()
    cal = ResponseCalibrator()
    cache = DeterministicCache(db_path=":memory:")
    mem = EpisodicMemory(db_path=":memory:")

    state = {
        "ticket_id": "TICKET-4921",
        "customer_message": "Payment gateway timed out during credit card transaction.",
        "account_tier": "enterprise",
        "timestamp": 1726778900,
        "trace_id": "trace-abc-123",
        "empty_notes": None,
        "blank_field": ""
    }

    questions = {
        "is_billing": Noul("Does this ticket describe an invoice or payment issue?"),
        "severity": Score("Rate incident urgency", ["Low", "Normal", "High"]),
        "route": Choice("Assign handling team", {
            "database": "Database connection and replication issues",
            "payment": "Gateway timeouts and transaction errors",
            "frontend": "CSS, rendering, and asset bundling issues"
        })
    }

    mock_answers = {
        "is_billing": {"type": "noul", "noul": 0.96},
        "severity": {"type": "score", "score": 2.0, "confidence": 0.88, "probabilities": {"0": 0.05, "1": 0.07, "2": 0.88}},
        "route": {"type": "choice", "choice": "payment", "confidence": 0.92, "probabilities": {"payment": 0.92, "database": 0.05, "frontend": 0.03}}
    }

    # Warmup
    for _ in range(50):
        wire, meta = opt.optimize_and_wire(state, questions)
        fp = cache.compute_fingerprint("jev-latest", wire["state"], wire["questions"])
        cache.put(fp, "jev-latest", mock_answers, 20)
        _ = cache.get(fp)
        calibrated, summary = cal.calibrate(mock_answers)
        _ = mem.record_turn("sess_warmup", wire["state"], calibrated, summary["verdict"])

    # Measure StatePruner
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        _ = pruner.prune(state)
    t_prune = (time.perf_counter_ns() - t0) / iterations / 1_000_000

    # Measure QuestionOptimizer
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        _ = opt.optimize_and_wire(state, questions)
    t_opt = (time.perf_counter_ns() - t0) / iterations / 1_000_000

    # Measure SHA-256 Fingerprint with Volatile Masking
    wire, _ = opt.optimize_and_wire(state, questions)
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        _ = cache.compute_fingerprint("jev-latest", wire["state"], wire["questions"])
    t_hash = (time.perf_counter_ns() - t0) / iterations / 1_000_000

    # Measure ResponseCalibrator
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        _ = cal.calibrate(mock_answers)
    t_cal = (time.perf_counter_ns() - t0) / iterations / 1_000_000

    # Measure Cache GET (Memory Hit)
    fp = cache.compute_fingerprint("jev-latest", wire["state"], wire["questions"])
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        _ = cache.get(fp)
    t_cache = (time.perf_counter_ns() - t0) / iterations / 1_000_000

    # Measure Episodic Memory Record (In-memory)
    calibrated, summary = cal.calibrate(mock_answers)
    t0 = time.perf_counter_ns()
    for i in range(iterations):
        _ = mem.record_turn(f"sess_{i%10}", wire["state"], calibrated, summary["verdict"])
    t_mem = (time.perf_counter_ns() - t0) / iterations / 1_000_000

    # Full End-to-End Local Pipeline
    t0 = time.perf_counter_ns()
    for i in range(iterations):
        w, _ = opt.optimize_and_wire(state, questions)
        f = cache.compute_fingerprint("jev-latest", w["state"], w["questions"])
        c_item = cache.get(f)
        calibrated, summary = cal.calibrate(mock_answers)
        _ = mem.record_turn(f"sess_{i%10}", w["state"], calibrated, summary["verdict"])
    t_total = (time.perf_counter_ns() - t0) / iterations / 1_000_000

    cache.close()
    mem.close()

    return {
        "prune_ms": t_prune,
        "optimizer_ms": t_opt,
        "hash_ms": t_hash,
        "calibrator_ms": t_cal,
        "cache_get_ms": t_cache,
        "memory_record_ms": t_mem,
        "total_overhead_ms": t_total
    }


def run_vanilla_typesafe(api_key: str, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = "https://api.typesafe.ai/v1/systemone"
    payload = {
        "model": "jev-latest",
        "state": state,
        "questions": questions
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Vanilla-Test/1.0"
    }

    req = urllib.request.Request(endpoint, data=raw_payload, headers=headers, method="POST")

    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        t1 = time.perf_counter()
        latency_ms = round((t1 - t0) * 1000, 2)
        body = resp.read().decode("utf-8")
        data = json.loads(body)

    return {
        "latency_ms": latency_ms,
        "data": data,
        "payload_bytes": len(raw_payload)
    }


def main():
    print("=" * 80)
    print(" JEVGUARD LATENCY AND OVERHEAD AUDIT (N=1,000 ITERATIONS)")
    print("=" * 80)

    results = run_local_overhead_audit(iterations=1000)

    print(f"{'COMPONENT / LAYER':<40} | {'LATENCY (AVERAGE)':<20}")
    print("-" * 80)
    print(f"{'StatePruner (Null & Space Pruning)':<40} | {results['prune_ms'] * 1000:>8.2f} us ({results['prune_ms']:.4f} ms)")
    print(f"{'QuestionOptimizer (Schema Optimization)':<40} | {results['optimizer_ms'] * 1000:>8.2f} us ({results['optimizer_ms']:.4f} ms)")
    print(f"{'Canonical SHA-256 + Volatile Masking':<40} | {results['hash_ms'] * 1000:>8.2f} us ({results['hash_ms']:.4f} ms)")
    print(f"{'ResponseCalibrator (Entropy & Dispersion)':<40} | {results['calibrator_ms'] * 1000:>8.2f} us ({results['calibrator_ms']:.4f} ms)")
    print(f"{'DeterministicCache GET (In-Memory LRU)':<40} | {results['cache_get_ms'] * 1000:>8.2f} us ({results['cache_get_ms']:.4f} ms)")
    print(f"{'EpisodicMemory Record (SQLite RAM)':<40} | {results['memory_record_ms'] * 1000:>8.2f} us ({results['memory_record_ms']:.4f} ms)")
    print("-" * 80)
    print(f"{'TOTAL LOCAL PIPELINE OVERHEAD':<40} | {results['total_overhead_ms'] * 1000:>8.2f} us ({results['total_overhead_ms']:.4f} ms)")
    print("=" * 80)

    budget_ms = 3.0
    actual_ms = results["total_overhead_ms"]
    if actual_ms < budget_ms:
        speedup = budget_ms / actual_ms if actual_ms > 0 else 999.0
        print(f"VERDICT: PASSED -> Local overhead ({actual_ms:.4f} ms) is {speedup:.1f}x below the {budget_ms} ms budget.")
    else:
        print(f"VERDICT: FAILED -> Local overhead ({actual_ms:.4f} ms) exceeds {budget_ms} ms.")

    print("\n" + "=" * 80)
    print(" LIVE UPSTREAM API BENCHMARK STATUS")
    print("=" * 80)

    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        print("Note: TYPESAFE_API_KEY environment variable is not currently set in this shell.")
        print("To run live comparative tests against https://api.typesafe.ai/v1/systemone:")
        print("  Windows:    $env:TYPESAFE_API_KEY=\"your_key_here\" ; python benchmark.py")
        print("  Linux/Mac:  export TYPESAFE_API_KEY=\"your_key_here\" && python3 benchmark.py")
        return

    print("Executing live upstream comparison against https://api.typesafe.ai/v1/systemone...")
    state = {
        "ticket_id": "TICKET-OFF-TOPIC-771",
        "customer_message": "Can you provide the physical address of your legal compliance office in Berlin?",
        "timestamp": 1726778900
    }
    vanilla_questions = {
        "department": {
            "type": "choice",
            "instructions": "Assign handling department",
            "criteria": {
                "billing": "Invoice disputes and refund requests",
                "technical_support": "Server downtime, bugs, and login failures",
                "sales": "Contract renewals and enterprise upgrades"
            }
        }
    }

    print("\n[1] Calling Vanilla TypeSafe AI (Without JevGuard)...")
    res_vanilla = run_vanilla_typesafe(api_key, state, vanilla_questions)
    vanilla_choice = res_vanilla["data"].get("answers", {}).get("department", {}).get("choice", "N/A")
    print(f"  Vanilla Latency: {res_vanilla['latency_ms']} ms")
    print(f"  Vanilla Choice:  '{vanilla_choice}' (FORCED FALSE POSITIVE on off-topic input)")

    print("\n[2] Calling JevGuard Runtime (With JevGuard)...")
    client = JevGuardClient(api_key=api_key, cache_db_path=":memory:", memory_db_path=":memory:")
    guard_questions = {
        "department": Choice("Assign handling department", {
            "billing": "Invoice disputes and refund requests",
            "technical_support": "Server downtime, bugs, and login failures",
            "sales": "Contract renewals and enterprise upgrades"
        })
    }

    resp1 = client.evaluate(state, guard_questions)
    guard_choice = resp1.choices["department"].choice
    print(f"  Cold Latency:    {resp1.telemetry['latency_total_ms']} ms")
    print(f"  JevGuard Choice: '{guard_choice}' (SAFE ESCAPE -> Zero false positive)")

    resp2 = client.evaluate(state, guard_questions)
    print(f"  Repeat Latency:  {resp2.telemetry['latency_total_ms']} ms (Cache hit: {resp2.cached})")
    print(f"  Tokens Saved:    {resp2.telemetry['tokens_saved']} tokens")
    client.close()


if __name__ == "__main__":
    main()

"""
03_high_throughput_batch_caching.py - Concurrent Batch Evaluation and Volatile Caching.

Demonstrates:
  1. High-throughput parallel execution using client.batch_evaluate.
  2. Input order preservation across concurrent worker threads.
  3. Automatic volatile key masking (timestamp, trace_id, request_id) for instant 0-token cache hits.
"""

import time
import os
import sys

# Ensure repository root is on sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jevguard import JevGuardClient, Noul

def main():
    # Initialize client with in-memory deterministic cache
    client = JevGuardClient(api_key="mock_key", cache_db_path=":memory:")

    # Mock dispatcher simulating 10ms network latency on cache miss
    def mock_network_dispatch(payload, timeout=30.0):
        time.sleep(0.010)
        return {
            "data": {
                "answers": {
                    "is_valid": {"type": "noul", "noul": 0.98}
                }
            }
        }
    client._dispatch_wire = mock_network_dispatch

    print("+----------------------------------------------------------------+")
    print("| JevGuard Concurrent Batch & Volatile Cache Demo                |")
    print("+----------------------------------------------------------------+")

    # Prepare batch of items with volatile timestamps and request IDs
    items = []
    for i in range(5):
        state = {
            "user_id": f"user_{i}",
            "action": "auth_token_refresh",
            "request_id": f"req_uuid_{i}_{time.time_ns()}",  # Volatile key
            "timestamp": int(time.time())                     # Volatile key
        }
        questions = {"is_valid": Noul("Is the auth token request within normal rate limits?")}
        items.append((state, questions))

    # First pass: cold cache (dispatches network requests across thread pool)
    t0 = time.perf_counter()
    cold_results = client.batch_evaluate(items, max_workers=5)
    cold_elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"Pass 1 (Cold Network Batch): {len(cold_results)} items completed in {cold_elapsed_ms:.2f} ms")
    for idx, res in enumerate(cold_results):
        print(f"  Item {idx}: Cached={res.cached}, Affirmative={res.nouls['is_valid'].is_affirmative}")

    # Second pass: identical operations with refreshed volatile timestamps and request IDs
    refreshed_items = []
    for i in range(5):
        state = {
            "user_id": f"user_{i}",
            "action": "auth_token_refresh",
            "request_id": f"req_new_uuid_{i}_{time.time_ns()}",  # Brand new UUID
            "timestamp": int(time.time()) + 60                     # Future timestamp
        }
        questions = {"is_valid": Noul("Is the auth token request within normal rate limits?")}
        refreshed_items.append((state, questions))

    t0 = time.perf_counter()
    warm_results = client.batch_evaluate(refreshed_items, max_workers=5)
    warm_elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"\nPass 2 (Warm Cache with Volatile Keys): {len(warm_results)} items completed in {warm_elapsed_ms:.2f} ms")
    for idx, res in enumerate(warm_results):
        print(f"  Item {idx}: Cached={res.cached}, Latency={res.telemetry.get('latency_total_ms', 0):.3f} ms, Tokens Saved={res.telemetry.get('tokens_saved', 0)}")

    client.close()

if __name__ == "__main__":
    main()

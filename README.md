# JevGuard: Deterministic Caching and Guardrails for TypeSafe AI (Jev)

[![PyPI version](https://img.shields.io/pypi/v/jevguard-core.svg)](https://pypi.org/project/jevguard-core/)
[![MCP Server](https://img.shields.io/badge/MCP_Server-jevguard--mcp-blue.svg)](https://pypi.org/project/jevguard-mcp/)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Official Model Context Protocol (MCP) Server:** JevGuard includes an official, zero-dependency MCP server designed for autonomous AI agents in Google Antigravity, Cursor IDE, Claude Desktop, and Cline: **[seb4ez/jevguard-mcp](https://github.com/seb4ez/jevguard-mcp)**.

JevGuard is an open-source Python library that provides local deterministic caching, state pruning, neutral escape injection, and calibration guardrails around TypeSafe AI's System One decision model. While upstream decision engines produce probabilistic outputs, JevGuard provides a strictly deterministic local layer: canonical JSON sorting, SHA-256 fingerprinting with volatile key masking, SQLite storage, and rule-based calibration checks using only the Python standard library.

## Upstream Latency and Cache Performance (50 Live Verification Requests)

The following benchmark report reflects 50 complete test requests executed against the official TypeSafe AI endpoint (`https://api.typesafe.ai/v1/systemone` using model `jev-latest`) from a development workstation. Latency comparisons contrast WAN roundtrips against local in-memory/SQLite cache lookups:

![JevGuard Benchmark Results](https://raw.githubusercontent.com/seb4ez/jevguard/main/benchmark_results.png)

| Key Metric | Direct Upstream API | JevGuard Runtime | Empirical Advantage |
| :--- | :--- | :--- | :--- |
| **Verification Requests** | 50 live calls tested | 50 live calls tested | 100% transport pass rate across 5 test scenarios |
| **Average Network Latency** | 784.53 ms | 735.88 ms (pruned) | State pruning reduces wire payload size |
| **Deterministic Cache Hit** | 763.40 ms (144 tokens charged) | **0.099 ms** (**0 tokens**) | **Sub-millisecond retrieval (0.099 ms vs 763 ms WAN roundtrip); 0 tokens billed** |
| **Out-of-Scope Protection** | Forced False Positive (`chargeback`) | `UNRESOLVED_OR_OTHER` | Neutral escape injection catches off-topic inputs |
| **Uncertainty Calibration** | Raw unverified probabilities | `AMBIGUOUS_STATE` alert | Flags bimodal ties and low certainty |
| **Local Runtime Overhead** | 0 ms | **0.024 ms** | Sub-millisecond execution using pure Python stdlib |

## Why JevGuard

TypeSafe AI's Jev model produces sub-second probabilistic evaluations over structured state. In production workflows, raw calls encounter three practical challenges:

1. **Closed-World False Positives**: When categorical choice criteria lack a neutral fallback, the model distributes all probability across defined options. If an unhandled or off-topic input arrives, the model selects the closest available option.
2. **Uncalibrated Ambiguity**: When inputs contain conflicting signals, probability distributions flatten. Selecting the top option without checking the runner-up margin leads to decisions made on low confidence.
3. **Repeated Query Cost and Cache Misses**: Identical state checks inside agent loops consume network latency and token budgets. If payloads contain dynamic timestamps or request IDs, standard caches miss on every call.

## Features

- **Closed-World Escape Injection**: Detects categorical choice rules without a fallback and injects `UNRESOLVED_OR_OTHER`. Off-topic inputs route to this escape option instead of triggering forced choices.
- **Strict Enum Support**: Set `closed_world=True` on `Choice` or `auto_inject_escapes=False` on the client when building strict enums where additional options must not be introduced.
- **Certainty and Dispersion Heuristics**: Uses configurable operational defaults (top probability below 0.40 or top-to-runner-up margin below 0.15) to flag indecisive distributions as `AMBIGUOUS_STATE`.
- **Volatile Metadata Masking**: Automatically ignores ephemeral fields (`timestamp`, `trace_id`, `request_id`, `nonce`) during SHA-256 fingerprinting, ensuring production cache hits across repeated queries.
- **Sub-Millisecond Overhead**: Pure local CPU computation runs in under 0.20 ms (198 microseconds) per evaluation.
- **Zero-Token SHA-256 Cache**: Hashes canonical sorted JSON. Identical requests return in under 1 millisecond with zero network overhead and zero token cost.
- **Concurrent Batch and Async Support**: Built-in `batch_evaluate` with thread pooling and native `async_evaluate` for `asyncio` event loops.
- **Resilient Network Retries**: Exponential backoff with jitter and `Retry-After` header parsing for HTTP 429 and 5xx server errors.
- **Episodic SQLite Memory**: Records session interaction turns with transactional serialization and builds rolling summaries without retransmitting raw history.
- **Zero Dependencies**: Pure Python 3.9+ standard library (`urllib`, `sqlite3`, `hashlib`, `json`, `threading`, `concurrent.futures`). No pip dependencies, no Node.js.

## Installation

Install the official package directly from PyPI:

```bash
pip install jevguard-core
```

Or install from source:

```bash
git clone https://github.com/seb4ez/jevguard.git
cd jevguard
pip install .
```

After installation, the package is imported directly as `jevguard`:

```python
import jevguard
from jevguard import JevGuardClient, Choice, Score, Noul
```

Set your TypeSafe AI API key:

```bash
export TYPESAFE_API_KEY="your_typesafe_api_key"
```

On Windows PowerShell:

```powershell
$env:TYPESAFE_API_KEY="your_typesafe_api_key"
```

## Quickstart

```python
from jevguard import JevGuardClient, Noul, Score, Choice

client = JevGuardClient()

state = {
    "ticket_id": "INC-4091",
    "customer_message": "Our payment gateway timed out during credit card settlement.",
    "account_tier": "enterprise",
    "timestamp": 1726778900
}

questions = {
    "is_billing": Noul(instructions="Does this ticket describe an invoice or payment issue?"),
    "severity": Score(
        instructions="Rate incident urgency",
        criteria=["Low", "Medium", "High", "Critical"]
    ),
    "route": Choice(
        instructions="Assign handling team",
        criteria={
            "database_team": "Database connection and replication issues",
            "payment_support": "Gateway timeouts and transaction errors",
            "frontend_ui": "CSS, rendering, and asset bundling issues"
        }
    )
}

response = client.evaluate(state, questions)

print(response.nouls["is_billing"].noul)       # 0.96
print(response.scores["severity"].score)       # 2.45
print(response.choices["route"].choice)        # "payment_support"
print(response.choices["route"].status)        # "CONFIDENT"
```

## Strict Closed-World Enums

When building strict finite-state machines where unhandled options must not be injected:

```python
# Pass closed_world=True to prevent UNRESOLVED_OR_OTHER injection
approval_choice = Choice(
    instructions="Review status",
    criteria={"APPROVED": "Request accepted", "REJECTED": "Request denied"},
    closed_world=True
)

resp = client.evaluate(state, {"decision": approval_choice})
```

## Production Caching with Volatile Metadata

JevGuard masks ephemeral fields during fingerprint computation so dynamic timestamps or trace IDs do not bust the cache:

```python
# First call (cold)
resp1 = client.evaluate({"query": "Check balance", "timestamp": 1726778900, "trace_id": "t-1"}, questions)

# Second call 10 seconds later with new timestamp and trace_id hits local cache
resp2 = client.evaluate({"query": "Check balance", "timestamp": 1726778910, "trace_id": "t-2"}, questions)

print(resp2.cached)                             # True
print(resp2.telemetry["latency_total_ms"])      # 0.10 ms
print(resp2.telemetry["tokens_saved"])          # 159 tokens
```

> Note: Volatile masking operates on structured dictionary keys (for example `{"timestamp": 123}`). If dynamic timestamps are embedded inside raw unstructured strings (for example `{"log": "2026-09-19T22:00:00Z error"}`), the SHA-256 fingerprint will change. To ensure cache hits, extract dynamic timestamps and request IDs into distinct dictionary keys in `state`.

## Concurrent Batch and Async Execution

```python
# Synchronous parallel batch evaluation
items = [
    (state_1, questions),
    (state_2, questions),
    (state_3, questions)
]
results = client.batch_evaluate(items, max_workers=5)

# Native asynchronous execution in asyncio event loops (no manual thread wrapping required)
import asyncio

async def main():
    res = await client.async_evaluate(state, questions)
    print(res.choices["route"].choice)

asyncio.run(main())
```

## Examples

Runnable demonstration scripts are included in the `examples/` directory:

```bash
# 1. SRE Incident Triage: State pruning and multi-question evaluation
python examples/01_sre_incident_triage.py

# 2. Strict Finite State Machine: Enforcing closed_world=True with zero escapes
python examples/02_strict_finite_state_machine.py

# 3. High-Throughput Batch & Caching: Thread pool evaluation and volatile key masking
python examples/03_high_throughput_batch_caching.py
```

## Official Model Context Protocol (MCP) Server

JevGuard provides an official, zero-dependency MCP server for AI coding assistants (Google Antigravity, Cursor IDE, Claude Desktop, LibreChat, and Cline):

* **Repository**: **[seb4ez/jevguard-mcp](https://github.com/seb4ez/jevguard-mcp)**
* **Architecture**: 100% Python standard library over JSON-RPC 2.0 stdio with SQLite WAL concurrency and transparent `:memory:` degradation.
* **Autonomous Agent Tools**:
  - `evaluate_command_safety`: Evaluates shell commands for destructive actions (`ALLOW_AUTONOMOUS`, `REQUIRE_HUMAN_APPROVAL`, `DENY_DESTRUCTIVE`).
  - `verify_code_patch`: Evaluates git diffs for security regressions, broken syntax, or critical system impact under strict, balanced, or permissive risk tolerances.
  - `evaluate_decision`: Evaluates architectural decisions from a list of options with automatic neutral escape injection (`UNRESOLVED_OR_OTHER`) and dispersion gap calibration.
  - `jevguard_evaluate`: Low-level deterministic evaluation pipeline with state pruning and zero-token caching.
  - `jevguard_calibrate`: Standalone certainty and probability dispersion calibrator (`AMBIGUOUS_STATE`).
  - `jevguard_prune_state`: Sanitizes complex state payloads and collapses duplicate whitespace.
  - `jevguard_cache_fingerprint`: Computes canonical SHA-256 fingerprints with volatile key masking.

### Quick Setup for AI Coding Environments

#### Cursor IDE (`.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "jevguard": {
      "command": "python",
      "args": ["-m", "jevguard_mcp.server"],
      "env": {
        "TYPESAFE_API_KEY": "your_typesafe_api_key_here"
      }
    }
  }
}
```

#### Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "jevguard": {
      "command": "python",
      "args": ["-m", "jevguard_mcp.server"],
      "env": {
        "TYPESAFE_API_KEY": "your_typesafe_api_key_here"
      }
    }
  }
}
```

For complete MCP server documentation, empirical benchmark reports, and installation guides, visit the dedicated repository: **[github.com/seb4ez/jevguard-mcp](https://github.com/seb4ez/jevguard-mcp)**.

## Testing & Verification

Run the 21 formal subsystem certification tests:

```bash
python test_suite_21.py
```

This suite validates all 5 core subsystems under production conditions:
1. State Pruner & Normalization (Tests 1 to 5)
2. Closed-World Optimizer (Tests 6 to 9)
3. Calibration & Dispersion Engine (Tests 10 to 13)
4. Volatile Masking & Zero-Token Cache (Tests 14 to 17)
5. Resilience, Batch & Async Transport (Tests 18 to 21)

Run the 30 comprehensive regression and concurrency tests:

```bash
python test_jevguard.py
```

Run the microsecond CPU latency audit:

```bash
python benchmark.py
```

Run the live upstream comparative test (requires `TYPESAFE_API_KEY`):

```bash
python run_live_certification_tests.py
```

## Project Status and Validation Transparency

JevGuard is an independent, community-driven open-source project (v1.0.0) built strictly with the Python standard library. Initial implementation and test suites were developed iteratively using AI-assisted engineering and local unit test validation.

Key design caveats:
- The local runtime (canonicalization, hashing, state pruning, and calibration checks) is deterministic, whereas the remote TypeSafe AI / Jev service produces probabilistic classifications.
- Default calibration thresholds (such as top probability below 0.40 or margin below 0.15) represent operational heuristics for tie and uncertainty detection rather than parameters fitted on a specific domain corpus. Users can configure them according to their domain risk tolerance.
- We welcome external peer review, empirical validation on production datasets, and community contributions.

## License

MIT License. See [LICENSE](LICENSE) for details.

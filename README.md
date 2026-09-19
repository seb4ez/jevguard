# JevGuard: Deterministic Decision Runtime for TypeSafe AI (Jev)

JevGuard is a deterministic evaluation, caching, and calibration runtime for TypeSafe AI's Jev model (System One). It wraps standard System One evaluations with closed-world escape injection, certainty calibration, state pruning, volatile key masking, zero-token SHA-256 caching, and episodic session memory using only the Python standard library.

## Why JevGuard

TypeSafe AI's Jev model produces sub-second probabilistic evaluations over structured state. In production, raw calls run into three operational bottlenecks:

1. **Closed-World False Positives**: When categorical choice criteria lack a neutral fallback, Jev distributes all probability across defined options. If an unhandled or off-topic input arrives, the model is forced into a false positive.
2. **Uncalibrated Ambiguity**: When inputs contain conflicting signals, probability distributions flatten. Selecting the top option without checking the runner-up margin leads to decisions made on near-coin-flip confidence.
3. **Repeated Query Cost & Cache Misses**: Identical state checks inside agent loops consume network latency and token budgets. If payloads contain timestamps or request IDs, naive caches miss on every call.

## Features

- **Closed-World Escape Injection**: Detects categorical choice rules without a fallback and automatically injects `UNRESOLVED_OR_OTHER`. Off-topic inputs route to this escape option instead of triggering false positives.
- **Strict Enum Support**: Set `closed_world=True` on `Choice` or `auto_inject_escapes=False` on the client when building strict, exhaustive enums where third-party options must not be introduced.
- **Certainty & Dispersion Calibrator**: Flags decisions where top probability is below 0.40 or the margin between the first and second choices is below 0.15 as `AMBIGUOUS_STATE`.
- **Volatile Metadata Masking**: Automatically ignores ephemeral fields (`timestamp`, `created_at`, `trace_id`, `request_id`, `nonce`) during SHA-256 fingerprinting, ensuring real-world production cache hits.
- **Sub-Millisecond Overhead**: Pure local CPU computation runs in under 0.10 ms (96 microseconds) per evaluation.
- **Zero-Token SHA-256 Cache**: Hashes canonical sorted JSON. Identical requests return in under 1 millisecond with zero network overhead and zero token cost.
- **Episodic SQLite Memory**: Records session interaction turns with connection reuse and builds rolling summaries without retransmitting raw history.
- **Zero Dependencies**: Pure Python 3.9+ standard library (`urllib`, `sqlite3`, `hashlib`, `json`, `threading`). No pip dependencies, no Node.js.

## Installation

Install locally with pip:

```bash
git clone https://github.com/seb4ez/jevguard.git
cd jevguard
pip install .
```

Or copy the `jevguard` directory directly into your project.

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
print(resp2.telemetry["latency_total_ms"])      # 0.12 ms
print(resp2.telemetry["tokens_saved"])          # 210 tokens
```

## Multi-Turn Session Memory

```python
# Pass session_id to maintain turn history
resp = client.evaluate(state, questions, session_id="session_user_42")

history = client.memory.get_session_history("session_user_42", limit=5)
for turn in history:
    print(f"Turn {turn['turn_number']}: verdict={turn['verdict']}")
```

## Command Line Interface

```bash
# Evaluate from JSON files
python -m jevguard.cli --state state.json --rules rules.json

# Pipe JSON from stdin
echo '{"state": {"err": "timeout"}, "questions": {"q": {"type": "noul", "instructions": "Timeout?"}}}' | python -m jevguard.cli
```

## Testing

Run the test suite (21 unit, concurrency, lifecycle, and latency tests):

```bash
python test_jevguard.py
```

Run the comparative benchmark against the upstream API:

```bash
python benchmark.py
```

## License

MIT License. See [LICENSE](LICENSE) for details.

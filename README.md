# JevGuard: Deterministic Decision Runtime for TypeSafe AI (Jev)

JevGuard is a deterministic evaluation and caching runtime for TypeSafe AI's Jev model (System One). It wraps standard System One evaluations with closed-world escape injection, certainty calibration, state pruning, zero-token SHA-256 caching, and episodic session memory using only the Python standard library.

## Why JevGuard

TypeSafe AI's Jev model produces sub-second probabilistic evaluations over structured state. In production, raw calls run into three operational bottlenecks:

1. **Closed-World False Positives**: When categorical choice criteria lack a neutral fallback, Jev distributes all probability across defined options. If an unhandled or off-topic input arrives, the model is forced into a false positive.
2. **Uncalibrated Ambiguity**: When inputs contain conflicting signals, probability distributions flatten. Selecting the top option without checking the runner-up margin leads to decisions made on near-coin-flip confidence.
3. **Repeated Query Cost**: Identical state checks inside agent loops repeatedly consume network latency and token budgets.

## Features

- **Closed-World Escape Injection**: Detects categorical choice rules without a fallback and automatically injects `UNRESOLVED_OR_OTHER`. Off-topic inputs route to this escape option instead of triggering false positives.
- **Certainty & Dispersion Calibrator**: Flags decisions where top probability is below 0.40 or the margin between the first and second choices is below 0.15 as `AMBIGUOUS_STATE`.
- **State Pruner**: Recursively strips null values, empty collections, and excessive whitespace before dispatch, preserving positional list indices and reducing payload size.
- **Zero-Token SHA-256 Cache**: Hashes canonical sorted JSON. Identical requests return in under 1 millisecond with zero network overhead and zero token cost.
- **Episodic SQLite Memory**: Records session interaction turns and builds rolling summaries without retransmitting raw history.
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
    "account_tier": "enterprise"
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

## Running Repeated Queries (Cache Demonstration)

```python
# The second call with identical state and questions hits local cache
second_response = client.evaluate(state, questions)

print(second_response.cached)                                    # True
print(second_response.telemetry["latency_total_ms"])             # 0.15 ms
print(second_response.telemetry["tokens_saved"])                 # 210
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

Run the full test suite (unit tests, multithreaded concurrency, and SQLite lifecycle):

```bash
python test_jevguard.py
```

Run the comparative benchmark against the upstream API:

```bash
python benchmark.py
```

## License

MIT License. See [LICENSE](LICENSE) for details.

---
name: jevguard
description: |
  Deterministic evaluation and calibration runtime for TypeSafe AI / Jev.
  Use when an agent or pipeline requires typed decision making, triage,
  routing, scoring, or binary assertions without text hallucination or closed-world traps.
---

# JevGuard: Deterministic Decision Runtime for Jev

JevGuard executes typed evaluations using TypeSafe AI's Jev model (System One). It adds state pruning to cut token consumption, closed-world escape injection to prevent false positives, certainty calibration to identify flat probability distributions, and local SHA-256 caching for zero-token hits on repeated queries.

## When to Use

Use JevGuard instead of standard generative LLM completions when:
- You need a typed decision: a boolean probability (`Noul`), an ordinal severity rating (`Score`), or a categorical selection (`Choice`).
- Latency matters: Jev responds in 70 to 500 ms compared to multi-second generative reasoning models.
- You must prevent closed-world false positives: standard classifiers force off-topic inputs into the closest category. JevGuard automatically injects `UNRESOLVED_OR_OTHER` so unhandled cases fail cleanly.
- You need certainty verification: JevGuard flags decisions where top probability is below 0.40 or the margin between the first and second choices is below 0.15 as `AMBIGUOUS_STATE`.
- You want zero-cost repeat evaluations: identical state and rules return in under 1 ms with 0 input tokens consumed.

## Core Primitives

```python
from jevguard import JevGuardClient, Noul, Score, Choice

client = JevGuardClient()  # Reads TYPESAFE_API_KEY from environment

# 1. Noul: Boolean verification (probability 0.0 to 1.0)
noul_q = Noul(instructions="Does this ticket describe an outage?")

# 2. Score: Ordinal metric (0 to N-1 based on criteria list)
score_q = Score(
    instructions="Rate incident urgency",
    criteria=["Low / Info", "Degraded / Medium", "Critical / High"]
)

# 3. Choice: Discrete categorization
choice_q = Choice(
    instructions="Route to team",
    criteria={
        "database": "Storage and replication issues",
        "network": "Gateway and CDN errors",
        "billing": "Invoice discrepancies"
    }
)
```

## Running Evaluations

### Basic Usage
```python
state = {
    "service": "checkout-api",
    "event": "Database connection pool saturated at 99.8%.",
    "error_rate": "14.2%"
}

response = client.evaluate(
    state=state,
    questions={
        "is_outage": noul_q,
        "urgency": score_q,
        "team": choice_q
    }
)

# Inspect results
print(response.nouls["is_outage"].noul)           # e.g. 0.98
print(response.scores["urgency"].score)          # e.g. 2.10
print(response.choices["team"].choice)           # e.g. "database"
print(response.choices["team"].status)           # "CONFIDENT" or "AMBIGUOUS_STATE"
```

### Episodic Session Tracking
Pass `session_id` to record evaluation history in SQLite and track multi-turn state transitions without bloating prompt tokens:

```python
response = client.evaluate(
    state={"update": "Replica lagged behind by 300s."},
    questions={"team": choice_q},
    session_id="incident-2026-09-19"
)

# Access prior session turns
history = client.memory.get_session_history("incident-2026-09-19")
for turn in history:
    print(f"Turn {turn['turn_number']}: verdict={turn['verdict']}")
```

### Command Line Interface
```bash
# Evaluate from files
python -m jevguard.cli --state incident.json --rules rules.json

# Pipe JSON directly
echo '{"state": {"msg": "charge failed"}, "questions": {"is_bill": {"type": "noul", "instructions": "Billing?"}}}' | python -m jevguard.cli
```

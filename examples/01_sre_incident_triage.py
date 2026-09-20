"""
01_sre_incident_triage.py - SRE Incident Triage Pipeline with JevGuard.

Demonstrates:
  1. Automatic state pruning (eliminating None and empty fields from telemetry).
  2. Multi-question evaluation (Noul affirmative check, Score urgency, Choice routing).
  3. Real-time certainty calibration and telemetry reporting.
"""

import os
import sys

# Ensure repository root is on sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jevguard import JevGuardClient, Noul, Score, Choice

def main():
    # Initialize client (uses TYPESAFE_API_KEY from environment or mock for demonstration)
    api_key = os.getenv("TYPESAFE_API_KEY", "mock_typesafe_key")
    client = JevGuardClient(api_key=api_key, enable_cache=True)

    # Messy incoming SRE alert payload containing nulls and empty metadata
    raw_telemetry = {
        "service": "checkout-api",
        "cluster": "us-east-prod-1",
        "error_rate_5m": 0.142,
        "p99_latency_ms": 1850,
        "database_pool_exhausted": True,
        "debug_trace": None,
        "empty_tags": [],
        "auxiliary_metrics": {},
        "timestamp": 1726778900
    }

    # Evaluation schema
    questions = {
        "is_customer_facing_outage": Noul(
            instructions="Does the high error rate and p99 latency indicate an active customer-facing outage?"
        ),
        "incident_severity": Score(
            instructions="Score the incident priority based on database pool exhaustion and service impact",
            criteria=["P4 (Low)", "P3 (Moderate)", "P2 (Major)", "P1 (Critical)"]
        ),
        "escalation_target": Choice(
            instructions="Route the incident to the responsible engineering team",
            criteria={
                "database_infra": "Database connectivity, pooling, and replica lag",
                "network_edge": "CDN, ingress routing, and edge proxy timeouts",
                "application_core": "Business logic crashes, 500 errors, and null pointers"
            }
        )
    }

    print("+----------------------------------------------------------------+")
    print("| JevGuard SRE Triage Pipeline                                  |")
    print("+----------------------------------------------------------------+")

    # Evaluate (if mock key, demonstrate with local cache/dry evaluation)
    try:
        response = client.evaluate(raw_telemetry, questions)
        print(f"Status:             {response.choices['escalation_target'].status}")
        print(f"Is Outage:          {response.nouls['is_customer_facing_outage'].is_affirmative} (prob: {response.nouls['is_customer_facing_outage'].noul:.2f})")
        print(f"Severity:           {response.scores['incident_severity'].score:.1f} / 4.0")
        print(f"Assigned Team:      {response.choices['escalation_target'].choice}")
        print(f"Cache Status:       {'HIT' if response.cached else 'MISS'}")
        print(f"Latency:            {response.telemetry.get('latency_total_ms', 0):.2f} ms")
    except Exception as err:
        print(f"Execution notice:   {err}")
        print("Note: Set TYPESAFE_API_KEY environment variable to run against live upstream.")

if __name__ == "__main__":
    main()

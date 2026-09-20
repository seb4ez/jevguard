"""
02_strict_finite_state_machine.py - Strict Finite State Machine with JevGuard.

Demonstrates:
  1. closed_world=True to enforce mathematical enum completeness.
  2. Guarantees 0 neutral escape options (UNRESOLVED_OR_OTHER) are injected.
  3. Strict transitions for mission-critical banking and regulatory workflows.
"""

import os
import sys

# Ensure repository root is on sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jevguard import JevGuardClient, Choice

def main():
    api_key = os.getenv("TYPESAFE_API_KEY", "mock_typesafe_key")
    client = JevGuardClient(api_key=api_key)

    # Loan application approval state
    loan_application = {
        "applicant_id": "APPL-88219",
        "credit_score": 745,
        "debt_to_income_ratio": 0.28,
        "requested_amount_usd": 45000,
        "kyc_verified": True,
        "fraud_risk_score": 0.02
    }

    # Strict closed-world enum: Only these three exact transitions are valid.
    # No fallback or unhandled escape should be introduced into the state machine.
    approval_fsm = Choice(
        instructions="Determine the exact state transition for this loan application based on underwriting criteria",
        criteria={
            "APPROVE_AUTOMATED": "Credit score >= 700, DTI < 0.35, KYC verified, low fraud score",
            "REJECT_AUTOMATED": "Credit score < 600 or DTI > 0.50 or KYC failed",
            "MANUAL_UNDERWRITING": "Borderline credit profile requiring human credit officer review"
        },
        closed_world=True  # Strictly opt-out of UNRESOLVED_OR_OTHER injection
    )

    print("+----------------------------------------------------------------+")
    print("| JevGuard Strict Finite State Machine (closed_world=True)       |")
    print("+----------------------------------------------------------------+")
    print(f"Declared Transitions: {list(approval_fsm.criteria.keys())}")
    print(f"Auto-inject Escapes:  {approval_fsm.auto_inject_escape}")

    # Inspect wire representation prior to network dispatch
    wire_repr = approval_fsm.to_wire()
    print(f"Wire Criteria Count:  {len(wire_repr['criteria'])} (Exact enum match)")
    print(f"Escape Injected:      {'UNRESOLVED_OR_OTHER' in wire_repr['criteria']}")

if __name__ == "__main__":
    main()

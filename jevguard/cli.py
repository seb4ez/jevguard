"""
jevguard.cli - Command Line Interface for JevGuard.
Allows direct execution, shell piping, and batch evaluations.
"""

import sys
import json
import argparse
from typing import Any

from .client import JevGuardClient


def main():
    parser = argparse.ArgumentParser(
        description="JevGuard: Deterministic Decision Runtime for TypeSafe AI / Jev"
    )
    parser.add_argument("--state", type=str, help="Path to state JSON file or raw JSON string")
    parser.add_argument("--rules", "--questions", type=str, dest="questions", help="Path to rules JSON file or raw JSON string")
    parser.add_argument("--session", type=str, default=None, help="Optional episodic session ID")
    parser.add_argument("--no-cache", action="store_true", help="Bypass deterministic cache")
    parser.add_argument("--model", type=str, default="jev-latest", help="Target Jev model")
    parser.add_argument("--api-key", type=str, default=None, help="Explicit TypeSafe API Key")

    args = parser.parse_args()

    if not args.state:
        if not sys.stdin.isatty():
            raw_input = sys.stdin.read().strip()
            data = json.loads(raw_input)
            state_data = data.get("state", {})
            questions_data = data.get("questions") or data.get("rules", {})
            session_id = data.get("session_id", args.session)
        else:
            parser.error("--state and --rules are required unless input is piped via stdin.")
    else:
        state_data = _load_json_or_file(args.state)
        questions_data = _load_json_or_file(args.questions)
        session_id = args.session

    client = JevGuardClient(
        api_key=args.api_key,
        model=args.model,
        enable_cache=not args.no_cache
    )

    try:
        res = client.evaluate(
            state=state_data,
            questions=questions_data,
            session_id=session_id,
            bypass_cache=args.no_cache
        )
        print(json.dumps(res.raw, indent=2))
    except Exception as err:
        sys.stderr.write(f"Error: {err}\n")
        sys.exit(1)


def _load_json_or_file(target: str) -> Any:
    target_str = target.strip()
    if target_str.startswith(("{", "[")):
        return json.loads(target_str)
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()

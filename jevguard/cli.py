"""
jevguard.cli - Command Line Interface for JevGuard.
Allows direct execution, shell piping, and batch evaluations with clean error handling.
"""

import sys
import json
import argparse
from typing import Any, Optional

from .client import JevGuardClient
from .exceptions import JevGuardError


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
            try:
                raw_input = sys.stdin.read().strip()
                if not raw_input:
                    sys.stderr.write("Error: Empty JSON payload received on stdin.\n")
                    sys.exit(2)
                data = json.loads(raw_input)
                if not isinstance(data, dict):
                    sys.stderr.write("Error: Expected a JSON object on stdin with 'state' and 'questions' keys.\n")
                    sys.exit(2)
                state_data = data.get("state", {})
                questions_data = data.get("questions") or data.get("rules", {})
                if not questions_data:
                    sys.stderr.write("Error: Missing 'questions' or 'rules' key in piped JSON.\n")
                    sys.exit(2)
                session_id = data.get("session_id", args.session)
            except json.JSONDecodeError as err:
                sys.stderr.write(f"Error: Invalid JSON on stdin: {err}\n")
                sys.exit(2)
        else:
            parser.error("--state and --rules are required unless input is piped via stdin.")
            sys.exit(2)
    else:
        if not args.questions:
            sys.stderr.write("Error: --rules (or --questions) is required when --state is specified.\n")
            sys.exit(2)
        try:
            state_data = _load_json_or_file(args.state, "--state")
            questions_data = _load_json_or_file(args.questions, "--rules")
            session_id = args.session
        except (ValueError, OSError) as err:
            sys.stderr.write(f"Error reading input: {err}\n")
            sys.exit(2)

    try:
        client = JevGuardClient(
            api_key=args.api_key,
            model=args.model,
            enable_cache=not args.no_cache
        )
        res = client.evaluate(
            state=state_data,
            questions=questions_data,
            session_id=session_id,
            bypass_cache=args.no_cache
        )
        print(json.dumps(res.raw, indent=2))
        client.close()
    except JevGuardError as err:
        sys.stderr.write(f"JevGuard Error: {err}\n")
        sys.exit(1)
    except Exception as err:
        sys.stderr.write(f"Unexpected Error: {err}\n")
        sys.exit(1)


def _load_json_or_file(target: Optional[str], arg_name: str) -> Any:
    if not target or not isinstance(target, str):
        raise ValueError(f"Missing argument for {arg_name}")
    target_str = target.strip()
    if target_str.startswith(("{", "[")):
        try:
            return json.loads(target_str)
        except json.JSONDecodeError as err:
            raise ValueError(f"Malformed JSON in {arg_name}: {err}")
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise OSError(f"File not found: '{target}'")
    except json.JSONDecodeError as err:
        raise ValueError(f"File '{target}' contains invalid JSON: {err}")
    except OSError as err:
        raise OSError(f"Could not read file '{target}': {err}")


if __name__ == "__main__":
    main()

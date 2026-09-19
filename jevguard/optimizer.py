"""
jevguard.optimizer - Pre-inference Schema Validation and Closed-World Optimization.
Provides state pruning to remove empty keys/whitespace and injects neutral escape
alternatives into categorical choices to eliminate false positives.
"""

from typing import Any, Dict, List, Optional, Set, Tuple, Union
from .models import Question, Noul, Score, Choice

ESCAPE_OPTION_KEY = "UNRESOLVED_OR_OTHER"
ESCAPE_OPTION_DESC = "State does not match defined criteria"

ESCAPE_CANDIDATE_KEYS = {
    "unresolved_or_other", "other", "unknown", "unresolved",
    "not_applicable", "n/a", "neither", "no_match",
    "none_of_the_above", "none_of_above", "unhandled", "misc"
}


class StatePruner:
    """Removes empty values, nulls, and duplicate whitespace to reduce input tokens."""

    @classmethod
    def prune(cls, data: Any, prune_lists: bool = False, seen: Optional[Set[int]] = None) -> Any:
        if seen is None:
            seen = set()

        if isinstance(data, (dict, list)):
            obj_id = id(data)
            if obj_id in seen:
                return "<cyclic_ref>"
            seen.add(obj_id)
        else:
            obj_id = None

        try:
            if isinstance(data, dict):
                pruned = {}
                for k, v in data.items():
                    if v is None:
                        continue
                    pruned_v = cls.prune(v, prune_lists=prune_lists, seen=seen)
                    if pruned_v is None:
                        continue
                    if isinstance(pruned_v, (str, dict)) and len(pruned_v) == 0:
                        continue
                    pruned[k] = pruned_v
                return pruned

            elif isinstance(data, list):
                if prune_lists:
                    pruned_list = []
                    for item in data:
                        if item is None:
                            continue
                        pruned_item = cls.prune(item, prune_lists=prune_lists, seen=seen)
                        if pruned_item is None:
                            continue
                        if isinstance(pruned_item, (str, dict)) and len(pruned_item) == 0:
                            continue
                        pruned_list.append(pruned_item)
                    return pruned_list
                else:
                    return [cls.prune(item, prune_lists=prune_lists, seen=seen) for item in data]

            elif isinstance(data, str):
                return " ".join(data.strip().split())

            return data
        finally:
            if obj_id is not None:
                seen.remove(obj_id)

    @classmethod
    def estimate_tokens(cls, text_or_data: Any) -> int:
        if isinstance(text_or_data, str):
            return max(1, len(text_or_data) // 4)
        import json
        raw = json.dumps(text_or_data, separators=(",", ":"))
        return max(1, len(raw) // 4)


class QuestionOptimizer:
    """Prepares and validates questions prior to upstream Jev dispatch."""

    def __init__(self, default_model: str = "jev-latest"):
        self.default_model = default_model

    def optimize_and_wire(
        self,
        state: Any,
        questions: Union[Dict[str, Any], List[Dict[str, Any]]],
        model: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        target_model = (model or self.default_model).strip() or self.default_model
        pruned_state = StatePruner.prune(state)

        wire_questions, injected_escapes = self.normalize_questions(questions)

        wire_payload = {
            "model": target_model,
            "state": pruned_state,
            "questions": wire_questions
        }

        metadata = {
            "model": target_model,
            "total_questions": len(wire_questions),
            "injected_escapes": injected_escapes,
            "has_injected_escapes": len(injected_escapes) > 0,
            "estimated_tokens": StatePruner.estimate_tokens(wire_payload)
        }

        return wire_payload, metadata

    def normalize_questions(
        self,
        questions: Union[Dict[str, Any], List[Dict[str, Any]]]
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        if not questions:
            raise ValueError("Questions payload cannot be empty.")

        wire_questions: Dict[str, Dict[str, Any]] = {}
        injected_escapes: Dict[str, str] = {}

        if isinstance(questions, dict):
            for name, q in questions.items():
                wire_q, injected = self._process_question(name, q)
                wire_questions[name] = wire_q
                if injected:
                    injected_escapes[name] = ESCAPE_OPTION_KEY

        elif isinstance(questions, list):
            for idx, q in enumerate(questions):
                if not isinstance(q, dict):
                    raise ValueError(f"Question at index {idx} must be a dictionary or Question instance.")
                name = str(q.get("name") or f"q_{idx + 1}").strip()
                wire_q, injected = self._process_question(name, q)
                wire_questions[name] = wire_q
                if injected:
                    injected_escapes[name] = ESCAPE_OPTION_KEY
        else:
            raise ValueError(f"Questions must be a dictionary or list, got {type(questions).__name__}")

        return wire_questions, injected_escapes

    def _process_question(self, name: str, q: Any) -> Tuple[Dict[str, Any], bool]:
        if isinstance(q, Question):
            wire_dict = q.to_wire()
        elif isinstance(q, dict):
            q_type = q.get("type", "noul").lower()
            instructions = q.get("instructions") or q.get("question") or ""
            if q_type == "noul":
                wire_dict = Noul(instructions, q.get("criteria")).to_wire()
            elif q_type == "score":
                wire_dict = Score(instructions, q.get("criteria", [])).to_wire()
            elif q_type == "choice":
                crit = q.get("criteria") if isinstance(q.get("criteria"), dict) else q.get("options", {})
                wire_dict = Choice(instructions, crit).to_wire()
            else:
                raise ValueError(f"Unsupported question type '{q_type}' in '{name}'")
        else:
            raise ValueError(f"Invalid question definition for '{name}': {q}")

        injected_escape = False

        if wire_dict["type"] == "choice":
            criteria = dict(wire_dict.get("criteria", {}))
            has_escape = any(k.strip().lower() in ESCAPE_CANDIDATE_KEYS for k in criteria.keys())
            if not has_escape:
                criteria[ESCAPE_OPTION_KEY] = ESCAPE_OPTION_DESC
                injected_escape = True
            wire_dict["criteria"] = criteria

        return wire_dict, injected_escape

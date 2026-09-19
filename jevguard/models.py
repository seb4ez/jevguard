"""
jevguard.models - Canonical question primitives and answer wrappers for TypeSafe AI / Jev.
Drop-in compatibility with official typesafe-sdk specification.
"""

from typing import Any, Dict, List, Optional, Union


class Question:
    """Base question model for TypeSafe AI evaluation primitives."""

    def __init__(self, question_type: str, instructions: str):
        self.type = question_type
        self.instructions = instructions.strip()

    def to_wire(self) -> Dict[str, Any]:
        raise NotImplementedError


class Noul(Question):
    """
    Boolean assertion primitive. Returns continuous probability in [0.0, 1.0].
    Values >= 0.5 favor True/Yes; values < 0.5 favor False/No.
    """

    def __init__(self, instructions: str, criteria: Optional[Dict[str, str]] = None):
        super().__init__("noul", instructions)
        self.criteria = criteria

    def to_wire(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "type": "noul",
            "instructions": self.instructions
        }
        if self.criteria and isinstance(self.criteria, dict):
            data["criteria"] = self.criteria
        return data


class Score(Question):
    """
    Ordinal metric primitive. Evaluates state against an ordered criteria array.
    Position in the array determines the numeric score starting at 0.
    """

    def __init__(self, instructions: str, criteria: List[str]):
        super().__init__("score", instructions)
        if not isinstance(criteria, list) or len(criteria) == 0:
            raise ValueError("Score primitive requires 'criteria' as a non-empty list of strings.")
        self.criteria = criteria

    def to_wire(self) -> Dict[str, Any]:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": self.criteria
        }


class Choice(Question):
    """
    Categorical routing primitive. Selects an option from a defined criteria map.
    """

    def __init__(
        self,
        instructions: str,
        criteria: Dict[str, Optional[str]],
        closed_world: bool = False,
        auto_inject_escape: bool = True
    ):
        super().__init__("choice", instructions)
        if not isinstance(criteria, dict) or len(criteria) == 0:
            raise ValueError("Choice primitive requires 'criteria' as a non-empty dictionary.")
        self.criteria = {k: (v or "") for k, v in criteria.items()}
        self.closed_world = closed_world
        self.auto_inject_escape = auto_inject_escape and not closed_world

    def to_wire(self) -> Dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": self.criteria
        }


class NoulAnswer:
    def __init__(self, raw: Dict[str, Any]):
        self.type = "noul"
        self.noul: float = float(raw.get("noul", 0.0))
        self.is_affirmative: bool = self.noul >= 0.5
        self.is_ambiguous: bool = bool(raw.get("is_ambiguous", False))
        self.status: str = raw.get("status", "CONFIDENT")

    def __repr__(self) -> str:
        return f"NoulAnswer(noul={self.noul:.4f}, affirmative={self.is_affirmative}, status={self.status})"


class ScoreAnswer:
    def __init__(self, raw: Dict[str, Any]):
        self.type = "score"
        self.score: float = float(raw.get("score", 0.0))
        self.confidence: float = float(raw.get("confidence", 1.0))
        self.legend: Dict[str, str] = raw.get("legend", {})
        self.probabilities: Dict[str, float] = raw.get("probabilities", {})
        self.is_ambiguous: bool = bool(raw.get("is_ambiguous", False))
        self.status: str = raw.get("status", "CONFIDENT")

    def __repr__(self) -> str:
        return f"ScoreAnswer(score={self.score:.2f}, confidence={self.confidence:.2f}, status={self.status})"


class ChoiceAnswer:
    def __init__(self, raw: Dict[str, Any]):
        self.type = "choice"
        self.choice: str = str(raw.get("choice", ""))
        self.confidence: float = float(raw.get("confidence", 0.0))
        self.probabilities: Dict[str, float] = raw.get("probabilities", {})
        self.is_ambiguous: bool = bool(raw.get("is_ambiguous", False))
        self.status: str = raw.get("status", "CONFIDENT")
        self.calibration: Dict[str, Any] = raw.get("calibration", {})

    def __repr__(self) -> str:
        return f"ChoiceAnswer(choice='{self.choice}', confidence={self.confidence:.2f}, status={self.status})"


class EvaluationResponse:
    """Wrapper around evaluated Jev answers and telemetry."""

    def __init__(self, raw_data: Dict[str, Any]):
        self.raw = raw_data
        self.success: bool = raw_data.get("success", True)
        self.cached: bool = raw_data.get("cached", False)
        self.session_id: Optional[str] = raw_data.get("session_id")
        self.telemetry: Dict[str, Any] = raw_data.get("telemetry", {})
        self.calibration: Dict[str, Any] = raw_data.get("calibration", {})
        self.optimization: Dict[str, Any] = raw_data.get("optimization", {})

        self.answers: Dict[str, Union[NoulAnswer, ScoreAnswer, ChoiceAnswer]] = {}
        self.nouls: Dict[str, NoulAnswer] = {}
        self.scores: Dict[str, ScoreAnswer] = {}
        self.choices: Dict[str, ChoiceAnswer] = {}

        raw_answers = raw_data.get("answers", {})
        for name, ans_data in raw_answers.items():
            if not isinstance(ans_data, dict):
                continue
            ans_type = ans_data.get("type", "").lower()
            if ans_type == "noul":
                parsed = NoulAnswer(ans_data)
                self.answers[name] = parsed
                self.nouls[name] = parsed
            elif ans_type == "score":
                parsed = ScoreAnswer(ans_data)
                self.answers[name] = parsed
                self.scores[name] = parsed
            elif ans_type == "choice":
                parsed = ChoiceAnswer(ans_data)
                self.answers[name] = parsed
                self.choices[name] = parsed

    def __repr__(self) -> str:
        return f"EvaluationResponse(success={self.success}, cached={self.cached}, answers={list(self.answers.keys())})"

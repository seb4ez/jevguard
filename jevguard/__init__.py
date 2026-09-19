"""
JevGuard - High-Performance Deterministic Runtime for TypeSafe AI / Jev.
Provides closed-world optimization, certainty calibration, state pruning,
zero-token deterministic caching, and episodic session memory.
"""

from .models import (
    Question,
    Noul,
    Score,
    Choice,
    NoulAnswer,
    ScoreAnswer,
    ChoiceAnswer,
    EvaluationResponse
)
from .client import JevGuardClient
from .optimizer import StatePruner, QuestionOptimizer, ESCAPE_OPTION_KEY
from .calibrator import ResponseCalibrator
from .cache import DeterministicCache
from .memory import EpisodicMemory

__version__ = "1.0.0"
__all__ = [
    "JevGuardClient",
    "Question",
    "Noul",
    "Score",
    "Choice",
    "NoulAnswer",
    "ScoreAnswer",
    "ChoiceAnswer",
    "EvaluationResponse",
    "StatePruner",
    "QuestionOptimizer",
    "ResponseCalibrator",
    "DeterministicCache",
    "EpisodicMemory",
    "ESCAPE_OPTION_KEY"
]

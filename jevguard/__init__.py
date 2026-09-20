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
    EvaluationResponse,
    EvaluationResult
)
from .client import JevGuardClient
from .optimizer import StatePruner, QuestionOptimizer, ESCAPE_OPTION_KEY
from .calibrator import ResponseCalibrator, CertaintyCalibrator
from .cache import DeterministicCache, SemanticCache
from .memory import EpisodicMemory
from .exceptions import (
    JevGuardError,
    JevGuardConfigError,
    JevGuardNetworkError,
    JevGuardTimeoutError,
    JevGuardHTTPError,
    JevGuardAuthenticationError,
    JevGuardRateLimitError,
    JevGuardServerError
)

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
    "EvaluationResult",
    "StatePruner",
    "QuestionOptimizer",
    "ResponseCalibrator",
    "CertaintyCalibrator",
    "DeterministicCache",
    "SemanticCache",
    "EpisodicMemory",
    "ESCAPE_OPTION_KEY",
    "JevGuardError",
    "JevGuardConfigError",
    "JevGuardNetworkError",
    "JevGuardTimeoutError",
    "JevGuardHTTPError",
    "JevGuardAuthenticationError",
    "JevGuardRateLimitError",
    "JevGuardServerError"
]

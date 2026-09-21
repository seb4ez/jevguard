"""
jevguard.calibrator - Certainty and Dispersion Analyzer for TypeSafe AI / Jev.
Identifies low confidence (< 0.40) and flat probability distributions (gap < 0.15),
marking results as AMBIGUOUS_STATE to prevent false certainty.
"""

import math
from typing import Any, Dict, List, Optional, Tuple


class ResponseCalibrator:
    """Evaluates probability spread on Jev decisions."""

    MIN_TOP_PROBABILITY: float = 0.40
    MIN_DISPERSION_GAP: float = 0.15
    DEFAULT_NOUL_UNCERTAINTY_MARGIN: float = 0.12

    def __init__(
        self,
        min_top_prob: float = MIN_TOP_PROBABILITY,
        min_dispersion_gap: float = MIN_DISPERSION_GAP,
        noul_uncertainty_margin: float = DEFAULT_NOUL_UNCERTAINTY_MARGIN
    ):
        self.min_top_prob = min_top_prob
        self.min_dispersion_gap = min_dispersion_gap
        self.noul_margin = noul_uncertainty_margin

    def calibrate(self, raw_answers: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        calibrated: Dict[str, Any] = {}
        ambiguous_questions: List[str] = []

        for name, ans in raw_answers.items():
            if not isinstance(ans, dict):
                calibrated[name] = {
                    "is_ambiguous": True,
                    "status": "AMBIGUOUS_STATE",
                    "calibration": {"reasons": ["invalid_answer_structure"]},
                    "raw": ans
                }
                ambiguous_questions.append(name)
                continue

            item = dict(ans)
            q_type = str(item.get("type", "")).strip().lower()

            if q_type == "choice":
                self._calibrate_choice(item)
            elif q_type == "score":
                self._calibrate_score(item)
            elif q_type == "noul":
                self._calibrate_noul(item)
            else:
                item["is_ambiguous"] = True
                item["status"] = "AMBIGUOUS_STATE"
                item["calibration"] = {
                    "reasons": ["unknown_question_type"],
                    "question_type": q_type
                }

            if item.get("is_ambiguous", False):
                ambiguous_questions.append(name)

            calibrated[name] = item

        summary = {
            "total_evaluated": len(calibrated),
            "ambiguous_count": len(ambiguous_questions),
            "ambiguous_questions": ambiguous_questions,
            "has_ambiguity": len(ambiguous_questions) > 0,
            "verdict": "AMBIGUOUS_STATE" if ambiguous_questions else "CONFIDENT"
        }

        return calibrated, summary

    def _calibrate_choice(self, item: Dict[str, Any]) -> None:
        probs = item.get("probabilities", {})
        parsed_pairs: List[Tuple[str, float]] = []
        has_invalid = False
        if isinstance(probs, dict):
            for k, v in probs.items():
                try:
                    val = float(v)
                    if math.isnan(val) or math.isinf(val):
                        has_invalid = True
                    else:
                        parsed_pairs.append((str(k), val))
                except (ValueError, TypeError):
                    has_invalid = True

        if not parsed_pairs:
            try:
                raw_conf = item.get("confidence", 0.0)
                conf = float(raw_conf)
                if math.isnan(conf) or math.isinf(conf):
                    conf = 0.0
                    has_invalid = True
            except (ValueError, TypeError):
                conf = 0.0
                has_invalid = True

            reasons = []
            if has_invalid:
                reasons.append("invalid_probability")
            if conf < self.min_top_prob:
                reasons.append("low_confidence")

            is_amb = len(reasons) > 0
            item["is_ambiguous"] = is_amb
            item["status"] = "AMBIGUOUS_STATE" if is_amb else "CONFIDENT"
            item["calibration"] = {
                "top_choice": item.get("choice"),
                "top_probability": round(conf, 4),
                "runner_up_choice": None,
                "runner_up_probability": 0.0,
                "dispersion_gap": round(conf, 4),
                "reasons": reasons
            }
            return

        sorted_pairs = sorted(parsed_pairs, key=lambda x: x[1], reverse=True)
        top_k, top_p = sorted_pairs[0]
        runner_k, runner_p = sorted_pairs[1] if len(sorted_pairs) > 1 else (None, 0.0)
        gap = top_p - runner_p

        reasons = []
        if has_invalid:
            reasons.append("invalid_probability")
        if top_p < self.min_top_prob:
            reasons.append("low_confidence")
        if len(sorted_pairs) > 1 and gap < self.min_dispersion_gap:
            reasons.append("flat_distribution")

        is_amb = len(reasons) > 0
        item["is_ambiguous"] = is_amb
        item["status"] = "AMBIGUOUS_STATE" if is_amb else "CONFIDENT"
        item["calibration"] = {
            "top_choice": top_k,
            "top_probability": round(top_p, 4),
            "runner_up_choice": runner_k,
            "runner_up_probability": round(runner_p, 4),
            "dispersion_gap": round(gap, 4),
            "reasons": reasons
        }

    def _calibrate_score(self, item: Dict[str, Any]) -> None:
        reasons: List[str] = []
        raw_conf = item.get("confidence")
        if raw_conf is None:
            conf = 0.0
            reasons.append("low_confidence")
        else:
            try:
                conf = float(raw_conf)
                if math.isnan(conf) or math.isinf(conf):
                    conf = 0.0
                    reasons.append("invalid_probability")
            except (ValueError, TypeError):
                conf = 0.0
                reasons.append("invalid_probability")

        probs = item.get("probabilities", {})

        if conf < self.min_top_prob and "low_confidence" not in reasons:
            reasons.append("low_confidence")

        dispersion_gap = conf
        if isinstance(probs, dict) and len(probs) >= 2:
            parsed_probs: List[float] = []
            for v in probs.values():
                try:
                    val = float(v)
                    if math.isnan(val) or math.isinf(val):
                        if "invalid_probability" not in reasons:
                            reasons.append("invalid_probability")
                    else:
                        parsed_probs.append(val)
                except (ValueError, TypeError):
                    if "invalid_probability" not in reasons:
                        reasons.append("invalid_probability")
            if len(parsed_probs) >= 2:
                sorted_probs = sorted(parsed_probs, reverse=True)
                top_p = sorted_probs[0]
                runner_p = sorted_probs[1]
                dispersion_gap = top_p - runner_p
                if top_p < self.min_top_prob and "low_confidence" not in reasons:
                    reasons.append("low_confidence")
                if dispersion_gap < self.min_dispersion_gap and "flat_distribution" not in reasons:
                    reasons.append("flat_distribution")

        is_amb = len(reasons) > 0
        item["is_ambiguous"] = is_amb
        item["status"] = "AMBIGUOUS_STATE" if is_amb else "CONFIDENT"
        item["calibration"] = {
            "confidence": round(conf, 4),
            "dispersion_gap": round(dispersion_gap, 4),
            "reasons": reasons
        }

    def _calibrate_noul(self, item: Dict[str, Any]) -> None:
        val = item.get("noul")
        if val is None:
            item["is_ambiguous"] = True
            item["status"] = "AMBIGUOUS_STATE"
            item["calibration"] = {
                "probability": 0.0,
                "boundary_distance": 0.0,
                "reasons": ["missing_noul_value"]
            }
            return
        try:
            prob = float(val)
        except (ValueError, TypeError):
            item["is_ambiguous"] = True
            item["status"] = "AMBIGUOUS_STATE"
            item["calibration"] = {
                "probability": 0.0,
                "boundary_distance": 0.0,
                "reasons": ["invalid_probability"]
            }
            return

        if math.isnan(prob) or math.isinf(prob):
            item["is_ambiguous"] = True
            item["status"] = "AMBIGUOUS_STATE"
            item["calibration"] = {
                "probability": 0.0,
                "boundary_distance": 0.0,
                "reasons": ["invalid_probability"]
            }
            return

        dist = abs(prob - 0.50)
        is_amb = dist < self.noul_margin

        item["is_ambiguous"] = is_amb
        item["status"] = "AMBIGUOUS_STATE" if is_amb else "CONFIDENT"
        item["calibration"] = {
            "probability": round(prob, 4),
            "boundary_distance": round(dist, 4),
            "reasons": ["boundary_uncertainty"] if is_amb else []
        }

    def calibrate_choice(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Convenience method to calibrate a single choice answer dictionary in-place."""
        c = dict(item)
        self._calibrate_choice(c)
        return c

    def calibrate_score(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Convenience method to calibrate a single score answer dictionary in-place."""
        s = dict(item)
        self._calibrate_score(s)
        return s

    def calibrate_noul(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Convenience method to calibrate a single noul answer dictionary in-place."""
        n = dict(item)
        self._calibrate_noul(n)
        return n


CertaintyCalibrator = ResponseCalibrator

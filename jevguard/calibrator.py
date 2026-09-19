"""
jevguard.calibrator - Certainty and Dispersion Analyzer for TypeSafe AI / Jev.
Identifies low confidence (< 0.40) and flat probability distributions (gap < 0.15),
marking results as AMBIGUOUS_STATE to prevent false certainty.
"""

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
                calibrated[name] = ans
                continue

            item = dict(ans)
            q_type = item.get("type", "").lower()

            if q_type == "choice":
                self._calibrate_choice(item)
            elif q_type == "score":
                self._calibrate_score(item)
            elif q_type == "noul":
                self._calibrate_noul(item)

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
        if not probs or not isinstance(probs, dict):
            conf = float(item.get("confidence", 0.0))
            is_amb = conf < self.min_top_prob
            item["is_ambiguous"] = is_amb
            item["status"] = "AMBIGUOUS_STATE" if is_amb else "CONFIDENT"
            item["calibration"] = {
                "top_probability": conf,
                "runner_up_probability": 0.0,
                "dispersion_gap": conf,
                "reasons": ["low_confidence"] if is_amb else []
            }
            return

        sorted_pairs = sorted([(k, float(v)) for k, v in probs.items()], key=lambda x: x[1], reverse=True)
        top_k, top_p = sorted_pairs[0]
        runner_k, runner_p = sorted_pairs[1] if len(sorted_pairs) > 1 else (None, 0.0)
        gap = top_p - runner_p

        reasons = []
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
        conf = float(item.get("confidence", 1.0))
        probs = item.get("probabilities", {})
        reasons = []

        if conf < self.min_top_prob:
            reasons.append("low_confidence")

        dispersion_gap = conf
        if isinstance(probs, dict) and len(probs) >= 2:
            sorted_probs = sorted([float(v) for v in probs.values()], reverse=True)
            top_p = sorted_probs[0]
            runner_p = sorted_probs[1]
            dispersion_gap = top_p - runner_p
            if top_p < self.min_top_prob:
                if "low_confidence" not in reasons:
                    reasons.append("low_confidence")
            if dispersion_gap < self.min_dispersion_gap:
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
            return
        try:
            prob = float(val)
        except (ValueError, TypeError):
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

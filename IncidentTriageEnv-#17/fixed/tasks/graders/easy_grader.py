from __future__ import annotations
from typing import Any

GROUND_TRUTH = {
    "root_cause_service": "payment-service",
    "root_cause": "database_overload",
    "severity": "P2",
    "responsible_team": "database",
    "resolution_keywords": ["pool", "database", "connection", "restart", "scale", "config"],
}

_FLOOR = 0.02
_CEIL  = 0.98


def grade_episode(actions_taken: list[dict[str, Any]]) -> float:
    score = _FLOOR

    for action in actions_taken:
        kind = action.get("kind")

        if kind == "diagnose":
            service_ok = (action.get("service") or "").lower() == GROUND_TRUTH["root_cause_service"].lower()
            cause_ok   = (action.get("root_cause") or "") == GROUND_TRUTH["root_cause"]
            if service_ok and cause_ok:
                score += 0.33
            elif service_ok:
                score += 0.17
            elif cause_ok:
                score += 0.11

        elif kind == "set_severity":
            level = action.get("level") or ""
            if level == GROUND_TRUTH["severity"]:
                score += 0.24
            elif level in ("P1", "P3"):
                score += 0.07

        elif kind == "escalate":
            if (action.get("team") or "").lower() == GROUND_TRUTH["responsible_team"].lower():
                score += 0.24

        elif kind == "resolve":
            msg = (action.get("message") or "").lower()
            q = 0.0
            if len(msg) >= 20:
                q += 0.05
            if GROUND_TRUTH["root_cause_service"].lower() in msg:
                q += 0.04
            if any(kw in msg for kw in GROUND_TRUTH["resolution_keywords"]):
                q += 0.04
            score += q

    return min(max(score, _FLOOR), _CEIL)

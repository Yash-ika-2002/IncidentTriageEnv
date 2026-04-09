from __future__ import annotations
from typing import Any

GROUND_TRUTH = {
    "root_cause_service": "payment-service",
    "root_cause": "database_overload",
    "severity": "P2",
    "responsible_team": "database",
    "resolution_keywords": ["pool", "database", "connection", "restart", "scale", "config"],
}


def grade_episode(actions_taken: list[dict[str, Any]]) -> float:
    score = 0.02

    for action in actions_taken:
        kind = action.get("kind")

        if kind == "diagnose":
            service_ok = action.get("service", "").lower() == GROUND_TRUTH["root_cause_service"].lower()
            cause_ok   = action.get("root_cause", "") == GROUND_TRUTH["root_cause"]
            if service_ok and cause_ok:
                score += 0.35
            elif service_ok:
                score += 0.18
            elif cause_ok:
                score += 0.12

        elif kind == "set_severity":
            if action.get("level", "") == GROUND_TRUTH["severity"]:
                score += 0.25
            elif action.get("level", "") in ("P1", "P3"):
                score += 0.08

        elif kind == "escalate":
            if action.get("team", "").lower() == GROUND_TRUTH["responsible_team"].lower():
                score += 0.25

        elif kind == "resolve":
            msg = action.get("message", "").lower()
            q = 0.0
            if len(msg) >= 20:
                q += 0.05
            if GROUND_TRUTH["root_cause_service"].lower() in msg:
                q += 0.05
            if any(kw in msg for kw in GROUND_TRUTH["resolution_keywords"]):
                q += 0.05
            score += min(q, 0.15)

    return min(max(score, 0.02), 0.98)

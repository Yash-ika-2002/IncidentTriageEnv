from __future__ import annotations
from typing import Any

GROUND_TRUTH = {
    "root_cause_service": "auth-service",
    "root_cause": "misconfiguration",
    "severity": "P1",
    "responsible_team": "backend",
    "resolution_keywords": ["auth", "config", "session", "redis", "rollback", "revert", "postgres"],
    "red_herring_services": ["api-gateway", "postgres-primary", "user-service"],
}

_SERVICE_PARTIAL = {
    "api-gateway": 0.12,
    "postgres-primary": 0.10,
}

_TEAM_PARTIAL = {
    "database": 0.10,
    "infrastructure": 0.05,
}


def grade_episode(actions_taken: list[dict[str, Any]]) -> float:
    score = 0.02

    for action in actions_taken:
        kind = action.get("kind")

        if kind == "diagnose":
            service  = action.get("service", "").lower()
            cause    = action.get("root_cause", "")
            service_ok = service == GROUND_TRUTH["root_cause_service"].lower()
            cause_ok   = cause == GROUND_TRUTH["root_cause"]

            if service_ok and cause_ok:
                score += 0.35
            elif service_ok:
                score += 0.20
            elif cause_ok:
                score += 0.10
            else:
                score += _SERVICE_PARTIAL.get(service, 0.0)

        elif kind == "set_severity":
            level = action.get("level", "")
            if level == GROUND_TRUTH["severity"]:
                score += 0.25
            elif level == "P2":
                score += 0.10

        elif kind == "escalate":
            team = action.get("team", "").lower()
            if team == GROUND_TRUTH["responsible_team"].lower():
                score += 0.25
            else:
                score += _TEAM_PARTIAL.get(team, 0.0)

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

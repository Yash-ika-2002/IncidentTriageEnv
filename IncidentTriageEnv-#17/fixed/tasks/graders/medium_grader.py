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
    "api-gateway": 0.11,
    "postgres-primary": 0.09,
}

_TEAM_PARTIAL = {
    "database": 0.09,
    "infrastructure": 0.04,
}

_FLOOR = 0.02
_CEIL  = 0.98


def grade_episode(actions_taken: list[dict[str, Any]]) -> float:
    score = _FLOOR

    for action in actions_taken:
        kind = action.get("kind")

        if kind == "diagnose":
            service    = (action.get("service") or "").lower()
            cause      = (action.get("root_cause") or "")
            service_ok = service == GROUND_TRUTH["root_cause_service"].lower()
            cause_ok   = cause == GROUND_TRUTH["root_cause"]

            if service_ok and cause_ok:
                score += 0.33
            elif service_ok:
                score += 0.19
            elif cause_ok:
                score += 0.09
            else:
                score += _SERVICE_PARTIAL.get(service, 0.0)

        elif kind == "set_severity":
            level = action.get("level") or ""
            if level == GROUND_TRUTH["severity"]:
                score += 0.24
            elif level == "P2":
                score += 0.09

        elif kind == "escalate":
            team = (action.get("team") or "").lower()
            if team == GROUND_TRUTH["responsible_team"].lower():
                score += 0.24
            else:
                score += _TEAM_PARTIAL.get(team, 0.0)

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

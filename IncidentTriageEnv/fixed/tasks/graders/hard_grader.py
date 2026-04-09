from __future__ import annotations
from typing import Any

GROUND_TRUTH = {
    "root_cause_service": "redis-cluster-prod",
    "root_cause": "network_partition",
    "severity": "P2",
    "responsible_team": "infrastructure",
    "resolution_keywords": ["redis", "network", "partition", "subnet", "cluster", "node", "quorum", "split"],
    "red_herring_services": ["recommendation-service", "search-service", "notification-service"],
}

_NO_CREDIT_TEAMS = {"backend", "database", "frontend", "security"}


def grade_episode(actions_taken: list[dict[str, Any]]) -> float:
    score = 0.02

    for action in actions_taken:
        kind = action.get("kind")

        if kind == "diagnose":
            service    = (action.get("service") or "").lower()
            cause      = (action.get("root_cause") or "")
            service_ok = service == GROUND_TRUTH["root_cause_service"].lower()
            cause_ok   = cause == GROUND_TRUTH["root_cause"]

            if service_ok and cause_ok:
                score += 0.35
            elif cause_ok and not service_ok:
                if service not in {s.lower() for s in GROUND_TRUTH["red_herring_services"]}:
                    score += 0.15
                else:
                    score += 0.08
            elif service_ok and not cause_ok:
                score += 0.10

        elif kind == "set_severity":
            level = action.get("level") or ""
            if level == GROUND_TRUTH["severity"]:
                score += 0.25
            elif level == "P1":
                score += 0.08

        elif kind == "escalate":
            team = (action.get("team") or "").lower()
            if team == GROUND_TRUTH["responsible_team"].lower():
                score += 0.25
            elif team == "networking":
                score += 0.15
            elif team in _NO_CREDIT_TEAMS:
                pass  # no credit for known-wrong teams
            else:
                score += 0.05

        elif kind == "resolve":
            msg = (action.get("message") or "").lower()
            q = 0.0
            if len(msg) >= 20:
                q += 0.05
            if GROUND_TRUTH["root_cause_service"].lower() in msg:
                q += 0.05
            if any(kw in msg for kw in GROUND_TRUTH["resolution_keywords"]):
                q += 0.05
            score += min(q, 0.15)

    return min(max(score, 0.02), 0.98)

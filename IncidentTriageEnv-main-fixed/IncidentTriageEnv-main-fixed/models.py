"""
models.py — Typed Pydantic models for IncidentTriageEnv.
All OpenEnv-required types: Observation, Action, Reward, plus
internal state and action union helpers.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, List, Literal, Optional, Union
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RootCause(str, Enum):
    DATABASE_OVERLOAD  = "database_overload"
    MEMORY_LEAK        = "memory_leak"
    NETWORK_PARTITION  = "network_partition"
    DEPENDENCY_TIMEOUT = "dependency_timeout"
    MISCONFIGURATION   = "misconfiguration"
    TRAFFIC_SPIKE      = "traffic_spike"
    CERT_EXPIRY        = "cert_expiry"
    UNKNOWN            = "unknown"


class Severity(str, Enum):
    P1 = "P1"  # Complete outage
    P2 = "P2"  # Major degradation
    P3 = "P3"  # Partial impact
    P4 = "P4"  # Minor / cosmetic


class Team(str, Enum):
    BACKEND        = "backend"
    FRONTEND       = "frontend"
    INFRASTRUCTURE = "infrastructure"
    DATABASE       = "database"
    SECURITY       = "security"
    PLATFORM       = "platform"
    NETWORKING     = "networking"


# ---------------------------------------------------------------------------
# Observation (returned by reset() and step())
# ---------------------------------------------------------------------------

class IncidentObservation(BaseModel):
    """What the agent sees at each step."""

    incident_id: str = Field(
        ..., description="Unique incident identifier, e.g. INC-2024-0042"
    )
    timestamp: str = Field(
        ..., description="ISO-8601 datetime when the incident was first detected"
    )
    service_name: str = Field(
        ..., description="Primary service currently reporting anomalies"
    )
    error_rate: float = Field(
        ..., ge=0.0, le=1.0,
        description="Fraction of requests returning 5xx (0.0 = none, 1.0 = all)"
    )
    p99_latency_ms: int = Field(
        ..., ge=0,
        description="99th-percentile request latency in milliseconds"
    )
    log_snippet: str = Field(
        ..., description="Recent relevant log lines from the affected service"
    )
    affected_endpoints: List[str] = Field(
        default_factory=list,
        description="API endpoints currently returning errors"
    )
    step_count: int = Field(
        0, ge=0,
        description="Number of actions taken so far in this episode"
    )


# ---------------------------------------------------------------------------
# Actions — one union type with a 'kind' discriminator
# ---------------------------------------------------------------------------

class DiagnoseAction(BaseModel):
    kind: Literal["diagnose"] = "diagnose"
    service: str = Field(
        ..., description="Name of the service believed to be the root cause"
    )
    root_cause: RootCause = Field(
        ..., description="Identified failure mode"
    )


class SetSeverityAction(BaseModel):
    kind: Literal["set_severity"] = "set_severity"
    level: Severity = Field(
        ..., description="Severity level assigned to the incident"
    )


class EscalateAction(BaseModel):
    kind: Literal["escalate"] = "escalate"
    team: Team = Field(
        ..., description="On-call team to page"
    )


class ResolveAction(BaseModel):
    kind: Literal["resolve"] = "resolve"
    message: str = Field(
        ..., min_length=20,
        description="Resolution summary: what happened and what was fixed"
    )


# Discriminated union — FastAPI deserializes via the 'kind' field
IncidentAction = Annotated[
    Union[
        DiagnoseAction,
        SetSeverityAction,
        EscalateAction,
        ResolveAction,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Reward
# ---------------------------------------------------------------------------

class RewardBreakdown(BaseModel):
    """Granular reward signal for transparency and partial-credit training."""

    correct_service: float = Field(
        0.0, ge=0.0, le=0.35,
        description="+0.35 if diagnosed service matches ground truth"
    )
    correct_severity: float = Field(
        0.0, ge=0.0, le=0.25,
        description="+0.25 if severity matches ground truth"
    )
    correct_escalation: float = Field(
        0.0, ge=0.0, le=0.25,
        description="+0.25 if escalated team matches ground truth"
    )
    resolution_quality: float = Field(
        0.0, ge=0.0, le=0.15,
        description="+0.15 for a well-formed resolution message"
    )
    step_penalty: float = Field(
        0.0, le=0.0,
        description="-0.1 per step beyond threshold (≥10 steps)"
    )
    repeat_penalty: float = Field(
        0.0, le=0.0,
        description="-0.05 per repeated identical action"
    )


class IncidentReward(BaseModel):
    """Total reward returned by step()."""

    total: float = Field(
        ..., ge=0.0, le=1.0,
        description="Clamped total reward for this step"
    )
    breakdown: RewardBreakdown = Field(
        default_factory=RewardBreakdown,
        description="Per-criterion reward components"
    )


# ---------------------------------------------------------------------------
# Step result — wraps observation + reward + done flag
# ---------------------------------------------------------------------------

class StepResult(BaseModel):
    observation: IncidentObservation
    reward: Optional[float] = None
    reward_detail: Optional[IncidentReward] = None
    done: bool = False
    info: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal episode state (returned by state())
# ---------------------------------------------------------------------------

class EpisodeState(BaseModel):
    """Full internal state snapshot for debugging and logging."""

    task_id: str
    scenario_id: str
    step_count: int
    done: bool
    ground_truth: dict = Field(
        description="Hidden ground truth: correct service, severity, team"
    )
    actions_taken: List[dict] = Field(default_factory=list)
    cumulative_reward: float = 0.0
    reward_breakdown: RewardBreakdown = Field(default_factory=RewardBreakdown)
    last_action_kind: Optional[str] = None

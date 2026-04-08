"""
app/main.py - FastAPI implementation for IncidentTriageEnv.

OpenEnv spec compliance:
  POST /reset  -> IncidentObservation
  POST /step   -> StepResult
  GET  /state  -> EpisodeState
  GET  /health -> {"status": "ok"}
"""

from __future__ import annotations

import importlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel

from models import (
    DiagnoseAction,
    EpisodeState,
    EscalateAction,
    IncidentAction,
    IncidentObservation,
    IncidentReward,
    ResolveAction,
    RewardBreakdown,
    SetSeverityAction,
    StepResult,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IncidentTriageEnv",
    version="1.0.0",
    description="OpenEnv environment for production API incident triage",
)

BASE_DIR = Path(__file__).parent.parent
SCENARIOS_DIR = BASE_DIR / "tasks"

# ---------------------------------------------------------------------------
# In-memory episode state
# ---------------------------------------------------------------------------

_episode: Optional[EpisodeState] = None
_scenario_cache: dict = {}


def _get_episode() -> EpisodeState:
    if _episode is None:
        raise HTTPException(
            status_code=400, detail="No active episode. Call /reset first."
        )
    return _episode


# ---------------------------------------------------------------------------
# Scenario loader
# ---------------------------------------------------------------------------

def _load_scenario(task_id: str) -> dict:
    if task_id in _scenario_cache:
        return _scenario_cache[task_id]
    name = task_id.replace("task_", "")
    scenario_file = SCENARIOS_DIR / f"{name}_scenario.json"
    if not scenario_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Scenario not found: {scenario_file}. Valid: task_easy, task_medium, task_hard",
        )
    with open(scenario_file) as f:
        data = json.load(f)
    _scenario_cache[task_id] = data
    return data


def _scenario_to_observation(scenario: dict, step_count: int = 0, overrides: dict = None) -> IncidentObservation:
    base = {
        "incident_id": scenario.get("incident_id", f"INC-{uuid.uuid4().hex[:8].upper()}"),
        "timestamp": scenario.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "service_name": scenario["initial_service"],
        "error_rate": scenario["initial_error_rate"],
        "p99_latency_ms": scenario["initial_p99_latency_ms"],
        "log_snippet": scenario["initial_log_snippet"],
        "affected_endpoints": scenario.get("affected_endpoints", []),
        "step_count": step_count,
    }
    if overrides:
        base.update(overrides)
    return IncidentObservation(**base)


# ---------------------------------------------------------------------------
# Grader loader
# ---------------------------------------------------------------------------

def _load_grader(task_id: str):
    grader_map = {
        "task_easy": "tasks.graders.easy_grader",
        "task_medium": "tasks.graders.medium_grader",
        "task_hard": "tasks.graders.hard_grader",
    }
    module_path = grader_map.get(task_id)
    if not module_path:
        raise HTTPException(status_code=404, detail=f"No grader for task_id={task_id}")
    return importlib.import_module(module_path)


# ---------------------------------------------------------------------------
# Reward evaluators
# ---------------------------------------------------------------------------

def _evaluate_diagnose(action: DiagnoseAction, episode: EpisodeState) -> float:
    gt = episode.ground_truth
    service_ok = action.service.lower() == gt.get("root_cause_service", "").lower()
    cause_ok = action.root_cause.value == gt.get("root_cause", "")
    if service_ok and cause_ok:
        return 0.35
    elif service_ok:
        return 0.18
    elif cause_ok:
        return 0.12
    return 0.0


def _evaluate_severity(action: SetSeverityAction, episode: EpisodeState) -> float:
    gt_severity = episode.ground_truth.get("severity", "")
    if action.level.value == gt_severity:
        return 0.25
    severity_order = ["P4", "P3", "P2", "P1"]
    try:
        agent_idx = severity_order.index(action.level.value)
        gt_idx = severity_order.index(gt_severity)
        if abs(agent_idx - gt_idx) == 1:
            return 0.08
    except ValueError:
        pass
    return 0.0


def _evaluate_escalation(action: EscalateAction, episode: EpisodeState) -> float:
    gt_team = episode.ground_truth.get("responsible_team", "")
    if action.team.value.lower() == gt_team.lower():
        return 0.25
    if gt_team == "infrastructure" and action.team.value == "networking":
        return 0.12
    return 0.0


def _evaluate_resolution(action: ResolveAction, episode: EpisodeState) -> float:
    msg = action.message.lower()
    gt = episode.ground_truth
    score = 0.0
    if len(msg) >= 20:
        score += 0.05
    if gt.get("root_cause_service", "").lower() in msg:
        score += 0.05
    keywords = gt.get("resolution_keywords", [])
    if any(kw.lower() in msg for kw in keywords):
        score += 0.05
    return min(score, 0.15)


def _step_penalty(step_count: int, threshold: int = 10) -> float:
    if step_count > threshold:
        return -0.1 * (step_count - threshold)
    return 0.0


def _repeat_penalty(action_kind: str, episode: EpisodeState) -> float:
    prev_kinds = [a.get("kind") for a in episode.actions_taken]
    if action_kind in prev_kinds:
        return -0.05
    return 0.0


def _compute_reward(episode: EpisodeState) -> IncidentReward:
    bd = episode.reward_breakdown
    raw = (
        bd.correct_service
        + bd.correct_severity
        + bd.correct_escalation
        + bd.resolution_quality
        + bd.step_penalty
        + bd.repeat_penalty
    )
    total = max(0.0, min(1.0, raw))
    return IncidentReward(total=total, breakdown=bd)


def _is_done(episode: EpisodeState, max_steps: int) -> bool:
    if episode.last_action_kind == "resolve":
        return True
    if episode.step_count >= max_steps:
        return True
    return False


def _next_observation(episode: EpisodeState, scenario: dict) -> IncidentObservation:
    gt = episode.ground_truth
    overrides: dict = {}
    bd = episode.reward_breakdown
    total_correct = bd.correct_service + bd.correct_severity + bd.correct_escalation
    base_error_rate = scenario["initial_error_rate"]
    base_latency = scenario["initial_p99_latency_ms"]
    improvement_factor = min(max(total_correct / 0.85, 0.0), 1.0)
    new_error_rate = round(base_error_rate * (1.0 - improvement_factor * 0.7), 3)
    new_latency = int(base_latency * (1.0 - improvement_factor * 0.6))
    overrides["error_rate"] = new_error_rate
    overrides["p99_latency_ms"] = max(50, new_latency)
    overrides["step_count"] = episode.step_count
    last_kind = episode.last_action_kind
    log_suffix = ""
    if last_kind == "diagnose" and bd.correct_service > 0:
        log_suffix = f"\n[SYS] Root cause identified: {gt['root_cause_service']} - investigating..."
    elif last_kind == "escalate" and bd.correct_escalation > 0:
        log_suffix = f"\n[PD] On-call from {gt['responsible_team']} acknowledged. ETA: 5 min."
    elif last_kind == "resolve":
        log_suffix = "\n[SYS] Incident marked RESOLVED."
        overrides["error_rate"] = 0.0
        overrides["p99_latency_ms"] = 120
    overrides["log_snippet"] = scenario["initial_log_snippet"] + log_suffix
    return _scenario_to_observation(scenario, step_count=episode.step_count, overrides=overrides)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class ResetRequest(BaseModel):
    task_id: str = "task_easy"


@app.post("/reset", response_model=IncidentObservation)
def reset(request: Optional[ResetRequest] = Body(default=None)) -> IncidentObservation:
    """
    Reset the environment to a fresh episode.
    Accepts: no body, empty body {}, or {"task_id": "task_easy|task_medium|task_hard"}
    """
    global _episode

    if request is None:
        request = ResetRequest()

    scenario = _load_scenario(request.task_id)

    _episode = EpisodeState(
        task_id=request.task_id,
        scenario_id=scenario.get("incident_id", request.task_id),
        step_count=0,
        done=False,
        ground_truth=scenario.get("ground_truth", {}),
        actions_taken=[],
        cumulative_reward=0.0,
        reward_breakdown=RewardBreakdown(),
        last_action_kind=None,
    )

    return _scenario_to_observation(scenario, step_count=0)


@app.post("/step", response_model=StepResult)
def step(action: IncidentAction) -> StepResult:
    """Advance the environment by one action."""
    episode = _get_episode()

    if episode.done:
        raise HTTPException(
            status_code=400,
            detail="Episode is done. Call /reset to start a new episode.",
        )

    scenario = _load_scenario(episode.task_id)
    max_steps = scenario.get("max_steps", 20)

    episode.step_count += 1
    episode.last_action_kind = action.kind
    bd = episode.reward_breakdown

    if isinstance(action, DiagnoseAction):
        bd.correct_service = max(bd.correct_service, _evaluate_diagnose(action, episode))
    elif isinstance(action, SetSeverityAction):
        bd.correct_severity = max(bd.correct_severity, _evaluate_severity(action, episode))
    elif isinstance(action, EscalateAction):
        bd.correct_escalation = max(bd.correct_escalation, _evaluate_escalation(action, episode))
    elif isinstance(action, ResolveAction):
        bd.resolution_quality = _evaluate_resolution(action, episode)

    bd.step_penalty = _step_penalty(episode.step_count, threshold=10)
    bd.repeat_penalty = bd.repeat_penalty + _repeat_penalty(action.kind, episode)

    episode.actions_taken.append(action.model_dump())

    reward_obj = _compute_reward(episode)
    episode.cumulative_reward = reward_obj.total
    episode.done = _is_done(episode, max_steps)

    obs = _next_observation(episode, scenario)

    return StepResult(
        observation=obs,
        reward=reward_obj.total,
        reward_detail=reward_obj,
        done=episode.done,
        info={
            "step_count": episode.step_count,
            "max_steps": max_steps,
            "cumulative_reward": episode.cumulative_reward,
            "breakdown": reward_obj.breakdown.model_dump(),
        },
    )


@app.get("/state", response_model=EpisodeState)
def state() -> EpisodeState:
    """Return full internal state snapshot."""
    return _get_episode()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": "IncidentTriageEnv", "version": "1.0.0"}

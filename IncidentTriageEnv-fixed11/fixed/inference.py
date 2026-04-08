from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

import httpx
from openai import OpenAI

API_BASE_URL: str = os.environ.get("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME: str   = os.environ.get("MODEL_NAME", "gpt-4o-mini")
API_KEY: str      = os.environ.get("HF_TOKEN", "")
LOCAL_IMAGE_NAME: str = os.environ.get("LOCAL_IMAGE_NAME", "")
ENV_BASE_URL: str = os.environ.get("ENV_BASE_URL", "http://0.0.0.0:7860")

BENCHMARK         = "IncidentTriageEnv"
SUCCESS_THRESHOLD = 0.6

TASKS: list[dict[str, Any]] = [
    {"task_id": "task_easy",   "name": "Single-Service 5xx Spike",         "max_steps": 10},
    {"task_id": "task_medium", "name": "Cascading Failure (auth-api-db)",   "max_steps": 15},
    {"task_id": "task_hard",   "name": "Ambiguous Multi-Signal Incident",   "max_steps": 20},
]


def log_start(*, task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}", flush=True)


def log_step(*, step: int, action: dict | None, reward: float, done: bool, error: str | None) -> None:
    action_str = json.dumps(action, separators=(",", ":")) if action else "null"
    done_str   = "true" if done else "false"
    error_str  = error if error else "null"
    print(f"[STEP] step={step} action={action_str} reward={reward:.3f} done={done_str} error={error_str}", flush=True)


def log_end(*, success: bool, steps: int, score: float, rewards: list[float]) -> None:
    success_str = "true" if success else "false"
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    print(f"[END] success={success_str} steps={steps} score={score:.3f} rewards={rewards_str}", flush=True)


def env_reset(task_id: str, *, timeout: float = 30.0) -> dict:
    resp = httpx.post(f"{ENV_BASE_URL}/reset", json={"task_id": task_id}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def env_step(action: dict, *, timeout: float = 30.0) -> dict:
    resp = httpx.post(f"{ENV_BASE_URL}/step", json=action, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def env_health(*, timeout: float = 10.0) -> bool:
    try:
        resp = httpx.get(f"{ENV_BASE_URL}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


SYSTEM_PROMPT = """\
You are an expert SRE agent in a live incident triage environment.

Respond with EXACTLY ONE JSON object per turn. No prose, no markdown fences. Raw JSON only.

Available actions:

1. {"kind": "diagnose", "service": "<service-name>", "root_cause": "<one of: database_overload|memory_leak|network_partition|dependency_timeout|misconfiguration|traffic_spike|cert_expiry|unknown>"}

2. {"kind": "set_severity", "level": "<one of: P1|P2|P3|P4>"}
   P1=complete outage  P2=major degradation  P3=partial impact  P4=minor

3. {"kind": "escalate", "team": "<one of: backend|frontend|infrastructure|database|security|platform|networking>"}

4. {"kind": "resolve", "message": "<at least 20 chars describing what happened and what was fixed>"}

Strategy:
- Read log snippets carefully for root cause clues
- Optimal order: diagnose -> set_severity -> escalate -> resolve
- Do not repeat the same action kind
- Penalties apply after step 10, be efficient
"""


def build_user_message(obs: dict, step: int, last_reward: float, history: list[str]) -> str:
    history_block = "\n".join(history[-5:]) if history else "None yet"
    endpoints = ", ".join(obs.get("affected_endpoints", [])) or "none"
    return (
        f"Step {step}\n\n"
        f"Incident ID   : {obs.get('incident_id')}\n"
        f"Service       : {obs.get('service_name')}\n"
        f"Error Rate    : {obs.get('error_rate', 0) * 100:.1f}%\n"
        f"P99 Latency   : {obs.get('p99_latency_ms')} ms\n"
        f"Endpoints     : {endpoints}\n\n"
        f"Log Snippet:\n{obs.get('log_snippet', '')}\n\n"
        f"Last reward   : {last_reward:+.3f}\n"
        f"History:\n{history_block}\n\n"
        f"Respond with exactly one JSON action."
    )


def get_agent_action(
    client: OpenAI,
    obs: dict,
    step: int,
    last_reward: float,
    history: list[str],
) -> tuple[dict, str | None]:
    user_msg = build_user_message(obs, step, last_reward, history)
    raw_content = ""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=256,
            temperature=0.2,
        )
        raw_content = response.choices[0].message.content.strip()

        if raw_content.startswith("```"):
            lines = raw_content.splitlines()
            inner = lines[1:-1] if len(lines) > 2 else lines[1:]
            raw_content = "\n".join(inner).strip()

        return json.loads(raw_content), None

    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]+\}", raw_content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group()), None
            except json.JSONDecodeError:
                pass
        fallback = {"kind": "diagnose", "service": obs.get("service_name", "unknown"), "root_cause": "unknown"}
        return fallback, f"JSON parse failed. Raw: {raw_content[:120]}"

    except Exception as exc:
        fallback = {"kind": "diagnose", "service": obs.get("service_name", "unknown"), "root_cause": "unknown"}
        return fallback, f"LLM error: {exc}"


def run_task(client: OpenAI, task: dict[str, Any]) -> float:
    task_id   = task["task_id"]
    task_name = task["name"]
    max_steps = task["max_steps"]

    log_start(task=task_name, env=BENCHMARK, model=MODEL_NAME)

    rewards: list[float] = []
    history: list[str]   = []
    steps_taken = 0
    score       = 0.01
    success     = False
    last_reward = 0.01

    try:
        obs  = env_reset(task_id)
        done = False

        for step in range(1, max_steps + 1):
            if done:
                break

            action_dict, error_msg = get_agent_action(client, obs, step, last_reward, history)

            try:
                result      = env_step(action_dict)
                obs         = result["observation"]
                reward      = float(result.get("reward") if result.get("reward") is not None else 0.02)
                reward      = min(max(reward, 0.02), 0.98)
                done        = bool(result.get("done", False))
            except Exception as exc:
                reward    = 0.01
                done      = False
                error_msg = f"env_step error: {exc}"

            rewards.append(reward)
            last_reward = reward
            steps_taken = step

            log_step(step=step, action=action_dict, reward=reward, done=done, error=error_msg)
            history.append(f"Step {step}: {action_dict.get('kind')} => reward {reward:+.3f}")

            if done:
                break

        score   = rewards[-1] if rewards else 0.02
        score   = min(max(score, 0.02), 0.98)
        success = score >= SUCCESS_THRESHOLD

    except Exception as exc:
        print(f"[ERROR] Task {task_id} crashed: {exc}", file=sys.stderr, flush=True)

    finally:
        log_end(success=success, steps=steps_taken, score=score, rewards=rewards)

    return score


def main() -> None:
    if not API_KEY:
        print("[ERROR] HF_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)

    print("[INFO] Waiting for environment to be ready...", flush=True)
    deadline = time.time() + 60
    healthy  = False
    while time.time() < deadline:
        if env_health():
            healthy = True
            break
        print("[INFO] Retrying in 3s...", flush=True)
        time.sleep(3)

    if not healthy:
        print("[ERROR] Environment did not become healthy within 60s.", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Environment ready at {ENV_BASE_URL}", flush=True)

    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)
    all_scores: list[float] = []

    for task in TASKS:
        score = run_task(client, task)
        all_scores.append(score)
        print(f"[RESULT] {task['task_id']}: {score:.3f}", flush=True)

    mean_score = sum(all_scores) / len(all_scores)
    for task, score in zip(TASKS, all_scores):
        print(f"  {task['task_id']:<15} {score:.3f}", flush=True)
    print(f"  {'mean':<15} {mean_score:.3f}", flush=True)


if __name__ == "__main__":
    main()

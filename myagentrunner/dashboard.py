from __future__ import annotations

import logging
import math
import os
import re
from datetime import timedelta
from pathlib import Path
from statistics import mean
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from myagentsdk import AgentConfig, ConfigError, load_config, load_environment
from myagentsdk.observability import DashboardStore, configure_logging

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
CUSTOMER_ROOT = Path(__file__).resolve().parent / "customer"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
METRIC_LABELS = {
    "e2e_latency": "End of user speech to agent response",
    "llm_node_ttft": "LLM time to first token",
    "tts_node_ttfb": "TTS time to first audio",
    "transcription_delay": "Speech transcription delay",
    "end_of_turn_delay": "End-of-turn detection delay",
}

load_environment(ROOT / ".env")
configure_logging("dashboard")
store = DashboardStore()
app = FastAPI(title="MYvoiveagent Dashboard API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
TRANSCRIPT_TOPIC = "myvoiceagent.transcript"


class CreateCallRequest(BaseModel):
    agent_name: str = Field(min_length=1, max_length=80)
    direction: str = "outbound"


def _agent_config(agent_dir: Path) -> AgentConfig:
    try:
        return load_config(agent_dir / "config.json")
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _agent_directory(agent_name: str, direction: str) -> Path:
    if not AGENT_NAME_PATTERN.fullmatch(agent_name):
        raise HTTPException(status_code=400, detail="Invalid agent name")
    candidate = (CUSTOMER_ROOT / agent_name / direction).resolve()
    if (
        not candidate.is_relative_to(CUSTOMER_ROOT.resolve())
        or not candidate.is_dir()
        or not (candidate / "agent.py").is_file()
        or not (candidate / "config.json").is_file()
        or not (candidate / "prompt" / "prompt.md").is_file()
    ):
        raise HTTPException(status_code=404, detail="Agent direction not found")
    return candidate


def _quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _metrics_response(agent_name: str | None) -> list[dict[str, float | int | str]]:
    samples = store.metric_samples(agent_name, direction="outbound")
    return [
        {
            "key": key,
            "label": METRIC_LABELS[key],
            "count": len(values),
            "p50_ms": round(_quantile(values, 0.5) * 1000, 1),
            "p90_ms": round(_quantile(values, 0.9) * 1000, 1),
            "mean_ms": round(mean(values) * 1000, 1),
        }
        for key, values in sorted(samples.items())
        if key in METRIC_LABELS and values
    ]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/agents")
def list_agents() -> list[dict]:
    result = []
    if not CUSTOMER_ROOT.is_dir():
        return result
    for folder in sorted(CUSTOMER_ROOT.iterdir()):
        if not folder.is_dir() or not AGENT_NAME_PATTERN.fullmatch(folder.name):
            continue
        directions = [
            "outbound"
        ] if (
            (folder / "outbound" / "agent.py").is_file()
            and (folder / "outbound" / "config.json").is_file()
            and (folder / "outbound" / "prompt" / "prompt.md").is_file()
        ) else []
        if not directions:
            continue
        config = _agent_config(folder / directions[0])
        result.append(
            {
                "name": folder.name,
                "display_name": config.name,
                "directions": directions,
                "models": {
                    "stt": config.models.stt.model,
                    "llm": config.models.llm.model,
                    "tts": config.models.tts.model,
                },
            }
        )
    return result


@app.get("/api/calls")
def list_calls(agent_name: str | None = Query(default=None)) -> list[dict]:
    return store.calls(agent_name, direction="outbound")


@app.get("/api/transcripts")
def list_transcripts(agent_name: str | None = Query(default=None)) -> list[dict]:
    return [
        call
        for call in store.calls(agent_name, direction="outbound")
        if call["transcript_available"]
    ]


@app.get("/api/transcripts/{call_id}")
def get_transcript(call_id: str) -> list[dict]:
    call = store.call(call_id)
    if not call or call["direction"] != "outbound":
        raise HTTPException(status_code=404, detail="Call not found")
    try:
        transcripts = store.transcripts(call_id)
    except (OSError, ValueError) as exc:
        logger.exception("Could not read transcript for call %s", call_id)
        raise HTTPException(status_code=500, detail="Could not read this call transcript") from exc
    if not transcripts:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcripts


@app.post("/api/calls/{call_id}/end")
def end_call(call_id: str) -> dict[str, str]:
    if not store.call(call_id):
        raise HTTPException(status_code=404, detail="Call not found")
    try:
        store.end_call(call_id)
    except Exception as exc:
        logger.exception("Could not close call %s", call_id)
        raise HTTPException(status_code=500, detail="Could not close this call") from exc
    return {"call_id": call_id, "status": "ended"}


@app.post("/api/calls")
def create_call(request: CreateCallRequest) -> dict[str, str]:
    if request.direction != "outbound":
        raise HTTPException(status_code=422, detail="The dashboard supports outbound agents only")
    agent_dir = _agent_directory(request.agent_name, request.direction)
    config = _agent_config(agent_dir)
    from livekit import api

    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    livekit_url = os.getenv("LIVEKIT_URL")
    if not api_key or not api_secret or not livekit_url:
        raise HTTPException(status_code=503, detail="LiveKit credentials are not configured")
    call_id = f"call-{uuid4().hex}"
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"dashboard-{uuid4().hex[:12]}")
        .with_name("Dashboard caller")
        .with_grants(api.VideoGrants(room_join=True, room=call_id))
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=config.name)]))
        .with_ttl(timedelta(minutes=15))
        .to_jwt()
    )
    try:
        store.create_call(call_id, request.agent_name, request.direction)
    except Exception as exc:
        logger.exception("Could not persist call %s", call_id)
        raise HTTPException(status_code=500, detail="Could not save call details") from exc
    logger.info("Created outbound browser call %s for agent %s.", call_id, config.name)
    return {"call_id": call_id, "token": token, "url": livekit_url}


@app.get("/api/metrics")
def metrics(agent_name: str | None = Query(default=None)) -> list[dict[str, float | int | str]]:
    return _metrics_response(agent_name)


def main() -> None:
    import uvicorn

    uvicorn.run("myagentrunner.dashboard:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()

"""Validated JSON configuration for customer agents."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class ConfigError(ValueError):
    """Raised when an agent configuration is missing or invalid."""


TurnDetection = Literal["auto", "stt", "vad", "realtime_llm", "manual"]
EndpointingMode = Literal["fixed", "dynamic"]
InterruptionMode = Literal["adaptive", "vad"]


@dataclass(frozen=True)
class ModelConfig:
    stt: str
    llm: str
    tts: str


@dataclass(frozen=True)
class SessionConfig:
    user_away_timeout: float | None = 15.0
    min_endpointing_delay: float = 0.5
    max_endpointing_delay: float = 3.0
    endpointing_mode: EndpointingMode = "fixed"
    endpointing_alpha: float = 0.9
    turn_detection: TurnDetection = "auto"
    interruption_mode: InterruptionMode = "adaptive"


@dataclass(frozen=True)
class AgentConfig:
    name: str
    models: ModelConfig
    session: SessionConfig


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"'{field}' must be a JSON object")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{field}' must be a non-empty string")
    return value


def _number(value: Any, field: str, *, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{field}' must be a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ConfigError(f"'{field}' must be a finite number") from exc
    if not math.isfinite(number) or number < minimum or (
        maximum is not None and number > maximum
    ):
        bounds = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise ConfigError(f"'{field}' must be a finite number {bounds}")
    return number


def _check_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(value.keys() - allowed)
    if unknown:
        raise ConfigError(f"Unknown {field} field(s): {', '.join(unknown)}")


def load_config(path: str | Path) -> AgentConfig:
    """Load and validate an agent's config.json."""
    config_path = Path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read agent config '{config_path}': {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Invalid JSON in agent config '{config_path}': {exc}") from exc

    root = _object(data, "config")
    _check_keys(root, {"name", "models", "session"}, "config")

    models_data = _object(root.get("models"), "models")
    _check_keys(models_data, {"stt", "llm", "tts"}, "models")
    models = ModelConfig(
        stt=_string(models_data.get("stt"), "models.stt"),
        llm=_string(models_data.get("llm"), "models.llm"),
        tts=_string(models_data.get("tts"), "models.tts"),
    )

    session_data = _object(root.get("session", {}), "session")
    _check_keys(
        session_data,
        {"user_away_timeout", "endpointing", "turn_detection", "interruption_mode"},
        "session",
    )
    timeout = session_data.get("user_away_timeout", 15.0)
    if timeout is not None:
        timeout = _number(timeout, "session.user_away_timeout", minimum=0)
        if timeout == 0:
            raise ConfigError("'session.user_away_timeout' must be positive or null")

    endpointing_data = _object(session_data.get("endpointing", {}), "session.endpointing")
    _check_keys(endpointing_data, {"mode", "min_delay", "max_delay", "alpha"}, "session.endpointing")
    endpointing_mode = endpointing_data.get("mode", "fixed")
    if endpointing_mode not in ("fixed", "dynamic"):
        raise ConfigError("'session.endpointing.mode' must be 'fixed' or 'dynamic'")
    min_delay = _number(
        endpointing_data.get("min_delay", 0.5),
        "session.endpointing.min_delay",
        minimum=0,
    )
    max_delay = _number(
        endpointing_data.get("max_delay", 3.0),
        "session.endpointing.max_delay",
        minimum=0,
    )
    if max_delay == 0 or min_delay > max_delay:
        raise ConfigError(
            "'session.endpointing.max_delay' must be positive and at least min_delay"
        )
    alpha = _number(
        endpointing_data.get("alpha", 0.9),
        "session.endpointing.alpha",
        minimum=0,
        maximum=1,
    )

    turn_detection = session_data.get("turn_detection", "auto")
    if turn_detection == "adaptive":
        turn_detection = "auto"
    if turn_detection not in ("auto", "stt", "vad", "realtime_llm", "manual"):
        raise ConfigError(
            "'session.turn_detection' must be 'auto', 'adaptive', 'stt', 'vad', "
            "'realtime_llm', or 'manual'"
        )

    interruption_mode = session_data.get("interruption_mode", "adaptive")
    if interruption_mode not in ("adaptive", "vad"):
        raise ConfigError("'session.interruption_mode' must be 'adaptive' or 'vad'")

    return AgentConfig(
        name=_string(root.get("name"), "name"),
        models=models,
        session=SessionConfig(
            user_away_timeout=timeout,
            min_endpointing_delay=min_delay,
            max_endpointing_delay=max_delay,
            endpointing_mode=endpointing_mode,
            endpointing_alpha=alpha,
            turn_detection=turn_detection,
            interruption_mode=interruption_mode,
        ),
    )

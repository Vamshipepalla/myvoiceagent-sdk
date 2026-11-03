"""Validated JSON configuration for customer agents."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast


class ConfigError(ValueError):
    """Raised when an agent configuration is missing or invalid."""


TurnDetection = Literal["auto", "stt", "vad", "realtime_llm", "manual"]
EndpointingMode = Literal["fixed", "dynamic"]
InterruptionMode = Literal["adaptive", "vad"]
ModelProvider = Literal["inference", "deepgram", "google", "sarvam"]


@dataclass(frozen=True)
class ModelSpec:
    provider: ModelProvider
    model: str
    language: str | None = None
    api_key_env: str | None = None
    speaker: str | None = None
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelConfig:
    stt: ModelSpec
    llm: ModelSpec
    tts: ModelSpec


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
    prompt: str | None
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


def _model_spec(value: Any, field: str, allowed_providers: set[str]) -> ModelSpec:
    if isinstance(value, str):
        return ModelSpec(provider="inference", model=_string(value, field))

    data = _object(value, field)
    _check_keys(
        data,
        {"provider", "model", "language", "api_key_env", "speaker", "languages"},
        field,
    )
    provider = _string(data.get("provider"), f"{field}.provider")
    if provider not in allowed_providers:
        choices = ", ".join(sorted(allowed_providers))
        raise ConfigError(f"'{field}.provider' must be one of: {choices}")
    language = data.get("language")
    if language is not None:
        language = _string(language, f"{field}.language")
    api_key_env = data.get("api_key_env")
    if api_key_env is not None:
        api_key_env = _string(api_key_env, f"{field}.api_key_env")
        if not api_key_env.replace("_", "").isalnum():
            raise ConfigError(f"'{field}.api_key_env' must be an environment variable name")
    speaker = data.get("speaker")
    if speaker is not None:
        speaker = _string(speaker, f"{field}.speaker")
    languages_data = data.get("languages", [])
    if not isinstance(languages_data, list) or not all(
        isinstance(language_code, str) and language_code.strip()
        for language_code in languages_data
    ):
        raise ConfigError(f"'{field}.languages' must be an array of non-empty language codes")
    languages = tuple(languages_data)
    if len(set(languages)) != len(languages):
        raise ConfigError(f"'{field}.languages' must not contain duplicates")
    if provider == "sarvam":
        if not api_key_env:
            raise ConfigError(f"'{field}.api_key_env' is required for Sarvam TTS")
        if not speaker:
            raise ConfigError(f"'{field}.speaker' is required for Sarvam TTS")
        supported_languages = {
            "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN",
            "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
        }
        configured_languages = set(languages)
        if language is not None:
            configured_languages.add(language)
        unsupported_languages = configured_languages - supported_languages
        if unsupported_languages:
            raise ConfigError(
                f"'{field}' has unsupported Sarvam language code(s): "
                f"{', '.join(sorted(unsupported_languages))}"
            )
        if language is not None and languages and language not in languages:
            raise ConfigError(f"'{field}.language' must be included in '{field}.languages'")
    elif api_key_env is not None or speaker is not None or languages:
        raise ConfigError(
            f"'{field}.api_key_env', '{field}.speaker', and '{field}.languages' "
            "are only supported for Sarvam TTS"
        )
    return ModelSpec(
        provider=cast(ModelProvider, provider),
        model=_string(data.get("model"), f"{field}.model"),
        language=language,
        api_key_env=api_key_env,
        speaker=speaker,
        languages=languages,
    )


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
    _check_keys(root, {"name", "prompt", "models", "session"}, "config")
    prompt = root.get("prompt")
    if prompt is not None:
        prompt = _string(prompt, "prompt")

    models_data = _object(root.get("models"), "models")
    _check_keys(models_data, {"stt", "llm", "tts"}, "models")
    models = ModelConfig(
        stt=_model_spec(models_data.get("stt"), "models.stt", {"inference", "deepgram"}),
        llm=_model_spec(models_data.get("llm"), "models.llm", {"inference", "google"}),
        tts=_model_spec(models_data.get("tts"), "models.tts", {"inference", "sarvam"}),
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
        prompt=prompt,
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

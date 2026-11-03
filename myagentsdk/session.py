import logging
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from .config import AgentConfig, ModelSpec

if TYPE_CHECKING:
    from livekit.agents import Agent, AgentSession, JobContext

logger = logging.getLogger(__name__)


class AgentSessionBuilder:
    """Create and start a LiveKit session from an agent's validated config."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._vad: object | None = None
        self._vad_loader: Callable[[], object] | None = None

    def register_plugins(self) -> None:
        """Register provider plugins on the main thread before LiveKit starts workers."""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Register LiveKit plugins on the main thread before starting workers")

        for spec in (
            self.config.models.stt,
            self.config.models.llm,
            self.config.models.tts,
        ):
            if spec.provider == "inference":
                continue
            if spec.provider == "deepgram":
                from livekit.plugins import deepgram  # noqa: F401
            elif spec.provider == "google":
                from livekit.plugins import google  # noqa: F401
            elif spec.provider == "sarvam":
                from livekit.plugins import sarvam  # noqa: F401
            else:
                raise ValueError(f"Unsupported provider '{spec.provider}'")
        if self.config.session.turn_detection == "vad":
            from livekit.plugins import silero

            self._vad_loader = silero.VAD.load

    def load_vad(self) -> None:
        """Load the VAD model during process setup without importing plugins on its thread."""
        if self.config.session.turn_detection == "vad" and self._vad is None:
            if self._vad_loader is None:
                raise RuntimeError(
                    "Register agent plugins on the main thread before loading Silero VAD."
                )
            logger.info("Loading Silero VAD during worker setup.")
            self._vad = self._vad_loader()
            logger.info("Silero VAD is ready.")

    @staticmethod
    def _build_model(
        spec: ModelSpec,
        modality: str,
        *,
        language: str | None = None,
    ) -> object:
        if spec.provider == "inference":
            return spec.model

        if spec.provider == "deepgram":
            from livekit.plugins import deepgram

            return deepgram.STT(model=spec.model, language=spec.language or "en")
        if spec.provider == "google":
            from livekit.plugins import google

            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "Set 'GOOGLE_API_KEY' to use the configured Google Gemini model"
                )
            return google.LLM(model=spec.model, api_key=api_key, vertexai=False)
        if spec.provider == "sarvam":
            from livekit.plugins import sarvam

            api_key = os.getenv(spec.api_key_env or "")
            if not api_key:
                raise RuntimeError(
                    f"Set '{spec.api_key_env}' to a Sarvam API key before starting the agent"
                )
            return sarvam.TTS(
                target_language_code=language or spec.language or "en-IN",
                model=spec.model,
                speaker=spec.speaker,
                api_key=api_key,
                speech_sample_rate=22050,
                output_audio_codec="mp3",
            )
        raise ValueError(f"Unsupported {modality} provider '{spec.provider}'")

    def create_tts(self, language: str) -> object:
        """Create a configured TTS instance for one supported agent language."""
        spec = self.config.models.tts
        if spec.provider != "sarvam":
            raise ValueError("Language-specific TTS instances are only supported for Sarvam")
        if language not in (spec.languages or (spec.language or "en-IN",)):
            raise ValueError(f"Unsupported configured Sarvam language '{language}'")
        return self._build_model(spec, "TTS", language=language)

    def create(self) -> "AgentSession":
        from livekit.agents import AgentSession, TurnHandlingOptions

        session_config = self.config.session
        turn_handling: TurnHandlingOptions = {
            "endpointing": {
                "mode": session_config.endpointing_mode,
                "min_delay": session_config.min_endpointing_delay,
                "max_delay": session_config.max_endpointing_delay,
                "alpha": session_config.endpointing_alpha,
            },
            "interruption": {"mode": session_config.interruption_mode},
        }
        if session_config.turn_detection != "auto":
            turn_handling["turn_detection"] = session_config.turn_detection

        if session_config.turn_detection == "vad" and self._vad is None:
            raise RuntimeError(
                "The configured Silero VAD is not loaded. Load agent plugins during worker setup."
            )

        stt = self._build_model(self.config.models.stt, "STT")
        llm = self._build_model(self.config.models.llm, "LLM")
        tts = self._build_model(self.config.models.tts, "TTS")
        if session_config.turn_detection == "vad":
            return AgentSession(
                stt=stt,
                vad=self._vad,
                llm=llm,
                tts=tts,
                user_away_timeout=session_config.user_away_timeout,
                turn_handling=turn_handling,
            )
        return AgentSession(
            stt=stt,
            llm=llm,
            tts=tts,
            user_away_timeout=session_config.user_away_timeout,
            turn_handling=turn_handling,
        )

    async def start(self, ctx: "JobContext", agent: "Agent") -> "AgentSession":
        session = self.create()
        await session.start(agent=agent, room=ctx.room)
        return session

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from .config import AgentConfig, ConfigError, load_config
from .observability import DashboardStore, configure_logging
from .session import AgentSessionBuilder

if TYPE_CHECKING:
    from livekit.agents import Agent, AgentSession, JobContext

logger = logging.getLogger(__name__)
TRANSCRIPT_TOPIC = "myvoiceagent.transcript"


def _tts_language_for_transcript(text: str, default_language: str) -> str:
    if any("\u0c00" <= character <= "\u0c7f" for character in text):
        return "te-IN"
    return default_language


def _load_module(module_name: str, file_path: Path) -> ModuleType:
    if not file_path.is_file():
        raise ConfigError(f"Agent module not found: '{file_path}'")
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Cannot load agent module: '{file_path}'")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class AgentRuntime:
    """Loads a customer agent and runs it with the configured LiveKit session."""

    def __init__(self, agent_dir: str | Path) -> None:
        self.agent_dir = Path(agent_dir).resolve()
        self.config = load_config(self.agent_dir / "config.json")
        self.session_builder = AgentSessionBuilder(self.config)
        self.instructions = self._load_prompt()
        module_name = (
            f"myagentsdk_customer_{self.agent_dir.parent.parent.name}_"
            f"{self.agent_dir.parent.name}_{self.agent_dir.name}"
        )
        package = ModuleType(module_name)
        package.__path__ = [str(self.agent_dir)]
        sys.modules[module_name] = package
        agent_module = _load_module(
            f"{module_name}.agent",
            self.agent_dir / "agent.py",
        )
        create_agent = getattr(agent_module, "create_agent", None)
        if not callable(create_agent):
            raise ConfigError(
                f"'{self.agent_dir / 'agent.py'}' must define create_agent(config, instructions)"
            )
        self._create_agent: Callable[[AgentConfig, str], Agent] = create_agent

    def _load_prompt(self) -> str:
        prompt_value = self.config.prompt
        if prompt_value is None:
            prompts_dir = self.agent_dir / "prompts"
            candidates = sorted(prompts_dir.glob("*.md")) if prompts_dir.is_dir() else []
            if len(candidates) == 1:
                prompt_path = candidates[0]
            elif not candidates and (self.agent_dir / "prompt.md").is_file():
                prompt_path = self.agent_dir / "prompt.md"
            else:
                raise ConfigError(
                    f"Set 'prompt' in '{self.agent_dir / 'config.json'}' to choose a prompt file"
                )
        else:
            relative_path = Path(prompt_value)
            if relative_path.is_absolute():
                raise ConfigError("'prompt' must be a path relative to the agent folder")
            prompt_path = (self.agent_dir / relative_path).resolve()
            if not prompt_path.is_relative_to(self.agent_dir):
                raise ConfigError("'prompt' must resolve inside the agent folder")
            if prompt_path.suffix.lower() != ".md":
                raise ConfigError("'prompt' must point to a Markdown file")

        try:
            prompt = prompt_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"Cannot read agent prompt '{prompt_path}': {exc}") from exc
        if not prompt:
            raise ConfigError(f"Agent prompt '{prompt_path}' must not be empty")
        return prompt

    def create_agent(self) -> "Agent":
        from livekit.agents import Agent

        agent = self._create_agent(self.config, self.instructions)
        if not isinstance(agent, Agent):
            raise ConfigError(
                f"'create_agent' in '{self.agent_dir / 'agent.py'}' must return a LiveKit Agent"
            )
        return agent

    async def run(self, ctx: "JobContext") -> "AgentSession":
        configure_logging()
        store = DashboardStore()
        call_id = ctx.room.name
        store.create_call(call_id, self.agent_dir.parent.name, self.agent_dir.name)
        transcript_lock = asyncio.Lock()
        pending_transcripts: set[asyncio.Task[None]] = set()

        async def save_and_publish_transcript(
            role: str,
            text: str,
            transcript_id: str | None,
            created_at: float | None,
        ) -> None:
            payload = {
                "role": role,
                "text": text,
                "id": transcript_id or uuid4().hex,
                "final": True,
                "created_at": created_at,
            }
            async with transcript_lock:
                try:
                    await asyncio.to_thread(store.add_transcript, call_id, payload)
                except Exception:
                    logger.exception("A %s transcript turn could not be saved.", role)
            try:
                await ctx.room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic=TRANSCRIPT_TOPIC,
                )
            except Exception:
                logger.exception("A %s transcript turn could not be sent to the dashboard.", role)

        def schedule_transcript(
            role: str,
            text: str,
            transcript_id: str | None,
            created_at: float | None,
        ) -> None:
            task = asyncio.create_task(
                save_and_publish_transcript(role, text, transcript_id, created_at)
            )
            pending_transcripts.add(task)
            task.add_done_callback(pending_transcripts.discard)

        async def on_shutdown(_reason: str) -> None:
            if pending_transcripts:
                await asyncio.gather(*pending_transcripts)
            store.end_call(call_id)

        ctx.add_shutdown_callback(on_shutdown)

        session = self.session_builder.create()
        agent = self.create_agent()
        language_tts: dict[str, object] = {}
        tts_spec = self.config.models.tts
        if tts_spec.provider == "sarvam":
            default_language = tts_spec.language or "en-IN"
            language_tts[default_language] = session.tts
            for language in tts_spec.languages:
                if language != default_language:
                    language_tts[language] = self.session_builder.create_tts(language)

        def on_user_transcribed(event: object) -> None:
            if not getattr(event, "is_final", False):
                return
            transcript = getattr(event, "transcript", "").strip()
            if not transcript:
                return
            if language_tts:
                default_language = tts_spec.language or "en-IN"
                language = _tts_language_for_transcript(transcript, default_language)
                selected_tts = language_tts.get(language)
                if selected_tts is not None and session.current_agent.tts is not selected_tts:
                    session.current_agent.update_options(tts=selected_tts)
                    logger.info("Sarvam TTS language selected: %s.", language)
            logger.info("The speech recognizer returned a caller turn for room %s.", call_id)
            schedule_transcript(
                "user",
                transcript,
                getattr(event, "item_id", None),
                getattr(event, "created_at", None),
            )

        def on_conversation_item(event: object) -> None:
            item = getattr(event, "item", None)
            if item is None:
                return
            role = getattr(item, "role", None)
            if role in {"user", "assistant"}:
                try:
                    store.add_metrics(call_id, role, getattr(item, "metrics", None))
                except Exception:
                    logger.exception("Metrics could not be saved for room %s.", call_id)
            if role != "assistant":
                return
            transcript = getattr(item, "text_content", None)
            if not isinstance(transcript, str) or not transcript.strip():
                return
            logger.info("The agent generated a response for room %s.", call_id)
            schedule_transcript(
                "agent",
                transcript.strip(),
                getattr(item, "id", None),
                getattr(item, "created_at", None),
            )

        def on_user_state_changed(event: object) -> None:
            new_state = getattr(event, "new_state", None)
            if new_state == "speaking":
                logger.info("Caller speech was detected in room %s.", call_id)
            elif new_state == "listening":
                logger.info("The caller stopped speaking in room %s.", call_id)

        session.on("user_input_transcribed", on_user_transcribed)
        session.on("conversation_item_added", on_conversation_item)
        session.on("user_state_changed", on_user_state_changed)
        logger.info("Starting the LiveKit voice session for room %s.", call_id)
        await session.start(agent=agent, room=ctx.room)
        logger.info("The LiveKit voice session is ready for room %s.", call_id)
        return session

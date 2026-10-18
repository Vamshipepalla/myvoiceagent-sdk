from typing import TYPE_CHECKING

from .config import AgentConfig

if TYPE_CHECKING:
    from livekit.agents import Agent, AgentSession, JobContext


class AgentSessionBuilder:
    """Create and start a LiveKit session from an agent's validated config."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

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

        return AgentSession(
            stt=self.config.models.stt,
            llm=self.config.models.llm,
            tts=self.config.models.tts,
            user_away_timeout=session_config.user_away_timeout,
            turn_handling=turn_handling,
        )

    async def start(self, ctx: "JobContext", agent: "Agent") -> "AgentSession":
        session = self.create()
        await session.start(agent=agent, room=ctx.room)
        return session

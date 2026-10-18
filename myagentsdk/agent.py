from livekit.agents import Agent as LiveKitAgent
from .config import AgentConfig


class AgentBase(LiveKitAgent):
    """Base LiveKit agent that carries validated SDK config and prompt text."""

    def __init__(self, config: AgentConfig, instructions: str, *, id: str | None = None) -> None:
        if not instructions.strip():
            raise ValueError("Agent instructions must not be empty")
        self.config = config
        super().__init__(instructions=instructions, id=id)

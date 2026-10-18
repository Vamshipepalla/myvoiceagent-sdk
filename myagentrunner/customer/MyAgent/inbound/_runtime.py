from pathlib import Path
from typing import Callable

from livekit.agents import Agent, JobContext

from myagentsdk import AgentConfig, AgentSessionBuilder


def load_prompt() -> str:
    prompt_path = Path(__file__).with_name("prompt.md")
    try:
        instructions = prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read agent prompt '{prompt_path}': {exc}") from exc
    if not instructions:
        raise RuntimeError(f"Agent prompt '{prompt_path}' must not be empty")
    return instructions


async def start(
    ctx: JobContext,
    create_agent: Callable[[AgentConfig, str], Agent],
    config: AgentConfig,
) -> None:
    agent = create_agent(config, load_prompt())
    await AgentSessionBuilder(config).start(ctx, agent)

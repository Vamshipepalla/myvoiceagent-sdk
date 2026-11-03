import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .runtime import AgentRuntime

if TYPE_CHECKING:
    from livekit.agents import AgentServer, JobContext

logger = logging.getLogger(__name__)


def create_server(runtime: AgentRuntime) -> "AgentServer":
    """Register a configured agent runtime with a LiveKit AgentServer."""
    from livekit.agents import AgentServer

    runtime.session_builder.register_plugins()
    server = AgentServer(num_idle_processes=1)

    def setup_process(_proc: object) -> None:
        runtime.session_builder.load_vad()
        ready_file = os.getenv("MYVOICEAGENT_WORKER_READY_FILE")
        if ready_file:
            path = Path(ready_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        logger.info("A warm agent process is ready for calls.")

    server.setup_fnc = setup_process

    async def entrypoint(ctx: "JobContext") -> None:
        await runtime.run(ctx)

    server.rtc_session(agent_name=runtime.config.name)(entrypoint)
    return server
